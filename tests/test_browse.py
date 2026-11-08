"""Browse images (browse.py): view images with file names and genotypes, never while a project is
scored blind; browsing before the build is recorded, and nothing else is saved."""
import http.client
import json
import shutil
import threading

import pytest

from wormscore import browse as B
from wormscore import home
from wormscore.pipeline import prepare, read_csv
from wormscore.scorers import export_package
from wormscore.server import Project
from wormscore.unblind import unblind

QUIET = lambda *a: None


def request(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    conn.request(method, path, body=json.dumps(body) if body is not None else None,
                 headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, data


def get_json(port, path):
    status, data = request(port, "GET", path)
    assert status == 200, (path, status, data[:200])
    return json.loads(data)


def unbuilt(project):
    """The project as it was after Scan data, before Build blind set."""
    shutil.rmtree(project / "key")
    shutil.rmtree(project / "blind")
    shutil.rmtree(project / "scoring", ignore_errors=True)   # nothing is scored before the build
    return project


def score_all(project):
    p = Project(project)
    for d in sorted(project.joinpath("blind").iterdir()):
        p.save_score(d.name, {"usable": "yes", "cells_seen": "2", "n1_call": "normal", "n2_call": "dim",
                              "confidence": "sure", "flag_answers": {}, "count_this_image": "yes"})


@pytest.fixture
def served():
    started = []

    def start(project):
        srv = B.make_server(project, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return srv.server_address[1]
    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def test_browse_is_off_between_build_and_unblind(fresh_project, tmp_path):
    p = fresh_project
    assert B.browse_state(p)[0] == "locked"
    with pytest.raises(SystemExit):
        B.make_server(p, port=0)
    # a second scorer's package is always blind
    export_package(p, "Second", tmp_path / "pkg", fraction=0.5, log=QUIET)
    assert B.browse_state(tmp_path / "pkg")[0] == "locked"
    score_all(p)
    unblind(p, finished=True, anyway=True, log=QUIET)
    assert B.browse_state(p)[0] == "after"
    prepare(p, log=QUIET)                 # a rebuild after unblinding rewrites the key: locked again
    assert B.browse_state(p)[0] == "locked"
    q = tmp_path / "q"
    shutil.copytree(p, q)
    unbuilt(q)
    assert B.browse_state(q)[0] == "before"
    (q / "outputs" / "inventory.csv").unlink()
    assert B.browse_state(q)[0] == "none"


def test_browsing_before_the_build_shows_genotypes_saves_nothing_and_is_recorded(fresh_project, served):
    p = unbuilt(fresh_project)
    log_before = (p / "log.md").read_text(encoding="utf-8")
    port = served(p)
    cfg = get_json(port, "/api/config")
    assert cfg["mode"] == "browse" and cfg["stage"] == "before"
    worms = get_json(port, "/api/worms")["worms"]
    assert len(worms) == len(read_csv(p / "outputs" / "inventory.csv"))
    assert all(w["genotype"] and w["file"] and not w["coded"] for w in worms)
    first, second = worms[0]["code"], worms[1]["code"]
    r = get_json(port, f"/api/worm/{first}")
    assert {"k": "Genotype", "v": worms[0]["genotype"]} in r["info"]
    assert r["meta"]["enhanced_this_image"] and r["meta"]["full_range"][0] == 0
    for name in ("mip.png", "planes/z00.png"):
        status, data = request(port, "GET", f"/img/{first}/{name}?v={r['img_v']}")
        assert status == 200 and data[:4] == b"\x89PNG"
    get_json(port, f"/api/worm/{first}")         # opened again in the same session: recorded once
    get_json(port, f"/api/worm/{second}")
    rows = read_csv(p / "outputs" / B.BROWSED)
    assert [(x["file"], x["genotype"]) for x in rows] == [(worms[0]["file"], worms[0]["genotype"]),
                                                         (worms[1]["file"], worms[1]["genotype"])]
    log = (p / "log.md").read_text(encoding="utf-8")
    assert log.startswith(log_before) and log[len(log_before):].count("browse images") == 1

    # nothing can be saved: no scores, regions or drafts
    for what in ("score", "rois", "draft"):
        status, _ = request(port, "POST", f"/api/worm/{first}/{what}", {"usable": "no", "rois": [], "values": {}})
        assert status == 404
    assert not (p / "scoring").exists() and not (p / "scoring_drafts").exists() and not (p / "blind").exists()

    # measuring shows numbers on screen
    c = r["meta"]["candidates"][0]
    rois = [{"type": "candidate", "rank": 1, "label": "Neuron 1"},
            {"type": "ellipse", "label": "Neuron 2", "cx": c["x"], "cy": c["y"], "rx": 6, "ry": 6}]
    status, data = request(port, "POST", f"/api/worm/{first}/measure", {"rois": rois})
    assert status == 200
    meas = json.loads(data)["rows"]
    assert {m["region"] for m in meas} == {"Neuron 1", "Neuron 2"} and all(m["area_um2"] > 0 for m in meas)
    assert not (p / "scoring").exists()

    # Build blind set: the window is refused from then on, and the build's log entry says what was seen
    prepare(p, log=QUIET)
    assert request(port, "GET", "/api/worms")[0] == 423
    assert request(port, "GET", f"/img/{first}/mip.png")[0] == 423
    assert request(port, "POST", f"/api/worm/{first}/measure", {"rois": rois})[0] == 423
    log = (p / "log.md").read_text(encoding="utf-8")
    assert "Viewed openly before this build (Browse images" in log and "2 images in 1 session" in log

    # ... and at unblinding the Methods draft says so too (ARRIVE 2.0 item 5)
    score_all(p)
    out = unblind(p, finished=True, anyway=True, log=QUIET)
    text = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "2 images were viewed with file names and genotypes visible" in text
    assert "| Before coding | 2 images were viewed" in text
    assert "—" not in text


def test_projects_never_browsed_say_nothing_about_it(fresh_project):
    p = fresh_project
    prepare(p, log=QUIET)
    score_all(p)
    out = unblind(p, finished=True, anyway=True, log=QUIET)
    log = (p / "log.md").read_text(encoding="utf-8")
    assert "Browse" not in log and "browse" not in log
    text = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "Before coding" not in text and "viewed with file names" not in text
    assert not (p / "outputs" / B.BROWSED).exists()


def test_browse_shows_exactly_what_the_build_made(fresh_project, tmp_path):
    p = fresh_project
    key = {(r["local_path"], r["location_in_file"]): r["code"] for r in read_csv(p / "key" / "key.csv")
           if not r.get("repeat_of")}
    q = tmp_path / "copy"
    shutil.copytree(p, q)
    b = B.Browse(unbuilt(q))
    assert len(b.items) == len(key)
    for item_id, it in b.items.items():
        code = key[(it["img"]["local_path"], it["img"]["location_in_file"])]
        got = b.get(item_id)
        d = p / "blind" / code
        meta = json.loads((d / "meta.json").read_text())
        assert got["files"]["mip.png"] == (d / "mip.png").read_bytes()
        for z in range(meta["n_z"]):
            assert got["files"][f"planes/z{z:02d}.png"] == (d / "planes" / f"z{z:02d}.png").read_bytes()
        assert got["files"].get("brightfield.png") == ((d / "brightfield.png").read_bytes()
                                                      if (d / "brightfield.png").exists() else None)
        assert [(c["x"], c["y"], c["r"]) for c in got["meta"]["candidates"]] == \
               [(c["x"], c["y"], c["r"]) for c in meta["candidates"]]
        assert got["meta"]["full_range"] == meta["full_range"]
    assert len(b.cache) == B.CACHE_SIZE      # only the last few images are kept, in memory only


def test_after_unblinding_a_code_can_be_looked_up(fresh_project, served):
    p = fresh_project
    score_all(p)
    unblind(p, finished=True, anyway=True, log=QUIET)
    log_before = (p / "log.md").read_text(encoding="utf-8")
    port = served(p)
    assert get_json(port, "/api/config")["stage"] == "after"
    worms = get_json(port, "/api/worms")["worms"]
    key = {r["code"]: r for r in read_csv(p / "key" / "key.csv")}
    coded = [w for w in worms if w["coded"]]
    assert {w["code"] for w in coded} == set(key)
    assert all(w["genotype"] == key[w["code"]]["genotype"] for w in coded)
    r = get_json(port, "/api/worm/W0001")
    assert r["score"]["n2_call"] == "dim"
    assert {"k": "Code", "v": "W0001"} in r["info"]
    status, data = request(port, "GET", f"/img/W0001/mip.png?v={r['img_v']}")
    assert status == 200 and data == (p / "blind" / "W0001" / "mip.png").read_bytes()
    assert request(port, "GET", "/img/W0001/../../key/key.csv")[0] == 404
    # looking after unblinding is not a "before the build" view
    assert (p / "log.md").read_text(encoding="utf-8") == log_before
    assert not (p / "outputs" / B.BROWSED).exists()


def test_home_reports_the_lock_and_refuses_to_open_browse(fresh_project, tmp_path, monkeypatch):
    monkeypatch.setattr(home, "SETTINGS", tmp_path / "settings.json")
    app = home.HomeApp(open_windows=False, pick=lambda title: None, opener=lambda p: None)
    srv = home.make_home_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        request(port, "POST", "/api/select", {"path": str(fresh_project)})
        st = get_json(port, "/api/state")
        assert st["project"]["browse"] == "locked"
        status, data = request(port, "POST", "/api/browse", {})
        assert status == 400 and app.scoring is None
        unbuilt(fresh_project)
        assert get_json(port, "/api/state")["project"]["browse"] == "before"
        status, data = request(port, "POST", "/api/browse", {})
        assert status == 200 and app.viewer == "browse"
        bport = int(json.loads(data)["url"].rsplit(":", 1)[1])
        assert get_json(bport, "/api/config")["mode"] == "browse"
        # opening scoring afterwards replaces the Browse server in the same window
        status, _ = request(port, "POST", "/api/score", {})
        assert status == 400                 # no blind set yet, so there is nothing to score
    finally:
        app.stop_scoring()
        srv.shutdown()
        srv.server_close()


def test_identity_channel_is_shown_as_the_build_shows_it(tmp_path):
    from wormscore.demo import make_demo
    from wormscore.pipeline import inventory
    p = tmp_path / "ident"
    make_demo(p, n_per_group=2, identity=True, log=QUIET)
    inventory(p, log=QUIET)
    b = B.Browse(p)
    item_id = next(iter(b.items))
    got = b.get(item_id)
    assert got["meta"]["has_identity"] is True
    shown = {k for k in got["files"] if k.startswith("identity")}
    assert "identity_mip.png" in shown and len(shown) == got["meta"]["n_z"] + 1
    q = tmp_path / "built"
    shutil.copytree(p, q)
    prepare(q, log=QUIET)
    it = b.items[item_id]["img"]
    code = next(r["code"] for r in read_csv(q / "key" / "key.csv")
                if (r["local_path"], r["location_in_file"]) == (it["local_path"], it["location_in_file"]))
    assert got["files"]["identity_mip.png"] == (q / "blind" / code / "identity_mip.png").read_bytes()


def test_browse_stays_off_when_the_key_is_moved_away_during_scoring(fresh_project, tmp_path):
    p = fresh_project
    shutil.move(str(p / "key"), str(tmp_path / "key_kept_elsewhere"))   # a lab keeping the key sealed
    state, why = B.browse_state(p)
    assert state == "locked" and "key" in why
    with pytest.raises(SystemExit):
        B.make_server(p, port=0)
    assert home.project_status(p)["browse"] == "locked"


def test_images_not_in_the_blind_set_are_recorded_after_unblinding(fresh_project, served):
    import os
    p = fresh_project
    score_all(p)
    unblind(p, finished=True, anyway=True, log=QUIET)
    # an image scanned after the build: it is in the inventory but has no code yet
    key_csv = p / "key" / "key.csv"
    lines = key_csv.read_text(encoding="utf-8").splitlines(keepends=True)
    key_csv.write_text("".join(lines[:-1]), encoding="utf-8")
    t = (p / "outputs" / "unblinded" / "summary.txt").stat().st_mtime
    os.utime(key_csv, (t - 5, t - 5))
    assert B.browse_state(p)[0] == "after"
    port = served(p)
    worms = get_json(port, "/api/worms")["worms"]
    new = [w for w in worms if not w["coded"] and not w.get("note")]
    assert new
    log_before = (p / "log.md").read_text(encoding="utf-8")
    get_json(port, f"/api/worm/{new[0]['code']}")
    get_json(port, f"/api/worm/{[w for w in worms if w['coded']][0]['code']}")   # coded images: not recorded
    rows = read_csv(p / "outputs" / B.BROWSED)
    assert [x["file"] for x in rows] == [new[0]["file"]]
    added = (p / "log.md").read_text(encoding="utf-8")[len(log_before):]
    assert "after unblinding, for images not in the blind set" in added
    assert "Viewed openly before this build" in "\n".join(B.browse_log_line(p))


def test_an_image_is_not_shown_until_its_view_is_written_down(fresh_project, served):
    p = unbuilt(fresh_project)
    port = served(p)
    first = get_json(port, "/api/worms")["worms"][0]["code"]
    (p / "outputs" / B.BROWSED).mkdir()            # the record cannot be written (as if locked by Excel)
    status, data = request(port, "GET", f"/api/worm/{first}")
    assert status == 500 and b"meta" not in data
    assert request(port, "GET", f"/img/{first}/mip.png")[0] == 500
    (p / "outputs" / B.BROWSED).rmdir()
    get_json(port, f"/api/worm/{first}")             # tried again: written, then shown
    assert len(read_csv(p / "outputs" / B.BROWSED)) == 1


def test_another_projects_window_does_not_keep_browse_alive(fresh_project, served):
    p = unbuilt(fresh_project)
    port = served(p)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("GET", "/api/ping", headers={"X-Wormscore-Project": "someotherproject"})
    res = conn.getresponse()
    res.read()
    conn.close()
    assert res.status == 409
