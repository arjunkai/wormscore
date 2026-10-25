import http.client
import json
import sys
import threading

import pytest

from wormscore.pipeline import read_csv
from wormscore.server import make_server
from wormscore.unblind import unblind


@pytest.fixture
def app(fresh_project):
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield fresh_project, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request(method, path, body=json.dumps(body) if body is not None else None,
                 headers={"Content-Type": "application/json", **(headers or {})})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, data


def test_pages_and_api_never_reveal_genotype(app):
    project, port = app
    status, _ = request(port, "GET", "/")
    assert status == 200
    status, data = request(port, "GET", "/api/worms")
    worms = json.loads(data)["worms"]
    assert len(worms) == 6
    for w in worms:
        status, data = request(port, "GET", f"/api/worm/{w['code']}")
        text = data.decode()
        assert status == 200
        for leak in ("control", "mutant", "demo_", "genotype", "path"):
            assert leak not in text


def test_key_and_raw_files_cannot_be_fetched(app):
    _, port = app
    for path in ("/img/W0001/../../key/key.csv", "/img/W0001/..%2F..%2Fkey%2Fkey.csv", "/key/key.csv",
                 "/img/W0001/meta.json", "/static/../config.json", "/static/..\\..\\config.py",
                 "/static/..\\server.py", "/img/W0001/..\\..\\key\\key.csv", "/static/index.html"):
        status, _ = request(port, "GET", path)
        assert status == 404, path


def test_requests_from_other_sites_are_refused(app):
    _, port = app
    score = {"usable": "no", "unusable_reason": "x", "flag_answers": {}}
    # DNS-rebinding style request: the browser sends the attacker's host name
    assert request(port, "GET", "/api/worms", headers={"Host": "evil.example:8765"})[0] == 403
    assert request(port, "POST", "/api/worm/W0001/score", score, headers={"Host": "evil.example"})[0] == 403
    # cross-site form post: wrong content type, or a foreign Origin
    assert request(port, "POST", "/api/worm/W0001/score", score, headers={"Content-Type": "text/plain"})[0] == 403
    assert request(port, "POST", "/api/worm/W0001/score", score, headers={"Origin": "https://evil.example"})[0] == 403
    assert request(port, "POST", "/api/worm/W0001/score", score,
                   headers={"Origin": f"http://127.0.0.1:{port}"})[0] == 200


def test_typed_text_cannot_become_a_spreadsheet_formula(app):
    project, port = app
    request(port, "POST", "/api/worm/W0003/score",
            {"usable": "no", "unusable_reason": "other", "notes": "=HYPERLINK(\"http://x\")", "flag_answers": {}})
    row = read_csv(project / "scoring" / "scores.csv")[0]
    assert row["notes"].startswith("'=")


def test_score_and_regions_are_saved_and_measured(app):
    project, port = app
    score = {"usable": "yes", "cells_seen": "2", "n1_call": "normal", "n2_call": "dim",
             "confidence": "sure", "flag_answers": {}, "count_this_image": "yes"}
    status, _ = request(port, "POST", "/api/worm/W0001/score", score)
    assert status == 200
    rows = read_csv(project / "scoring" / "scores.csv")
    assert rows[0]["code"] == "W0001" and rows[0]["n2_call"] == "dim"

    meta = json.loads((project / "blind" / "W0001" / "meta.json").read_text())
    c = meta["candidates"][0]
    rois = [{"type": "candidate", "rank": 1, "label": "Neuron 1", "source": "suggestion S1"},
            {"type": "ellipse", "label": "Neuron 2", "cx": c["x"], "cy": c["y"], "rx": 8, "ry": 8, "source": "drawn"}]
    status, data = request(port, "POST", "/api/worm/W0001/rois", {"rois": rois})
    assert status == 200
    assert "mean" not in data.decode()          # numbers are not sent back to the page
    meas = read_csv(project / "scoring" / "roi_measurements.csv")
    assert {r["region"] for r in meas} == {"Neuron 1", "Neuron 2"}
    assert all(float(r["area_um2"]) > 0 for r in meas)


def test_unblind_locks_and_traceability(app):
    project, port = app
    with pytest.raises(SystemExit):
        unblind(project, finished=False, log=lambda *a: None)
    request(port, "POST", "/api/worm/W0002/score",
            {"usable": "yes", "cells_seen": "2", "n1_call": "normal", "n2_call": "normal",
             "confidence": "sure", "flag_answers": {}, "count_this_image": "yes"})
    with pytest.raises(SystemExit):           # below the planned minimum of 5
        unblind(project, finished=True, log=lambda *a: None)
    out = unblind(project, finished=True, anyway=True, log=lambda *a: None)
    merged = read_csv(out / "merged.csv")
    assert len(merged) == 6
    scored = [r for r in merged if r.get("score_saved")]
    assert scored and all(r["original_path"] and r["file_sha256"] for r in scored)
    assert "override used" in (project / "log.md").read_text(encoding="utf-8")
    for name in ("per_neuron.csv", "penetrance.csv", "fisher_tests.csv"):
        assert (out / name).exists()
    assert len(read_csv(out / "per_neuron.csv")) == 2     # one counted animal x two neurons
    text = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "[name]" in text and "after 1 image had been scored" in text and "Fisher's exact" in text
    assert "—" not in text


def test_unsaved_answers_are_kept_as_a_draft_until_saved(app):
    """A crash or restart mid-image loses nothing: the page keeps a draft in the project."""
    project, port = app
    pid = json.loads(request(port, "GET", "/api/config")[1])["project_id"]
    h = {"X-Wormscore-Project": pid}
    values = {"usable": "yes", "n1_call": "dim", "flag_repeat": "count"}
    assert request(port, "POST", "/api/worm/W0001/draft", {"values": values}, h)[0] == 200
    draft = json.loads(request(port, "GET", "/api/worm/W0001", headers=h)[1])["draft"]
    assert draft["values"] == values and draft["at"]
    # drafts live outside scoring/, so they never count as scores or as "scoring started"
    assert (project / "scoring_drafts" / "drafts.json").is_file() and not (project / "scoring").exists()
    assert request(port, "POST", "/api/worm/W0001/draft", {"values": {"x": 5}}, h)[0] == 400
    assert request(port, "POST", "/api/worm/W0002/draft", {"values": {"usable": "no"}}, h)[0] == 200
    assert request(port, "POST", "/api/worm/W0002/draft", {"discard": True}, h)[0] == 200
    assert json.loads(request(port, "GET", "/api/worm/W0002", headers=h)[1])["draft"] is None
    score = {"usable": "no", "unusable_reason": "out of focus", "flag_answers": {}}
    assert request(port, "POST", "/api/worm/W0001/score", score, h)[0] == 200
    assert json.loads(request(port, "GET", "/api/worm/W0001", headers=h)[1])["draft"] is None


def test_window_from_another_project_cannot_save(app):
    project, port = app
    other = {"X-Wormscore-Project": "0" * 16}
    status, data = request(port, "POST", "/api/worm/W0001/score", {"usable": "no"}, other)
    assert status == 409
    assert request(port, "GET", "/api/worms", headers=other)[0] == 409
    assert not (project / "scoring" / "scores.json").exists()


def test_a_port_in_use_cannot_be_shared(fresh_project):
    """Python's default would let a second server bind the same port on Windows."""
    srv = make_server(fresh_project, port=0)
    try:
        with pytest.raises(OSError):
            make_server(fresh_project, port=srv.server_address[1])
    finally:
        srv.server_close()


def test_drafts_do_not_block_new_codes(fresh_project):
    """Only saved scores lock the codes; an unsaved draft does not."""
    from wormscore.pipeline import prepare
    (fresh_project / "scoring_drafts").mkdir()
    (fresh_project / "scoring_drafts" / "drafts.json").write_text("{}", encoding="utf-8")
    prepare(fresh_project, reassign=True, log=lambda *a: None)
    # the drafts belonged to the old codes, so they are set aside, not opened on other images
    assert not (fresh_project / "scoring_drafts" / "drafts.json").exists()
    assert list((fresh_project / "scoring_drafts").glob("superseded_*.json"))


def test_image_addresses_name_the_project_and_the_build(fresh_project, tmp_path):
    """Images are cached for an hour and every project scores on the same port, so the same code in
    another project, or after a rebuild, must get a different address."""
    import shutil
    other = tmp_path / "other"
    shutil.copytree(fresh_project, other)
    versions = []
    for project in (fresh_project, other):
        srv = make_server(project, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        v = json.loads(request(port, "GET", "/api/worm/W0001")[1])["img_v"]
        assert request(port, "GET", f"/img/W0001/mip.png?v={v}")[0] == 200
        versions.append(v)
        srv.shutdown()
        srv.server_close()
    assert versions[0] != versions[1]
    srv = make_server(fresh_project, port=0)
    meta = fresh_project / "blind" / "W0001" / "meta.json"
    meta.write_text(meta.read_text())        # a rebuild writes meta.json again
    import os
    os.utime(meta, ns=(meta.stat().st_atime_ns, meta.stat().st_mtime_ns + 10**9))
    assert srv.project.image_version("W0001") != versions[0]
    srv.server_close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file locks")
def test_a_results_file_open_in_excel_stops_the_save_and_says_why(app):
    """Excel locks a CSV it shows. Nothing is saved, and the page is told which file to close."""
    import ctypes
    from ctypes import wintypes
    project, port = app
    score = {"usable": "no", "unusable_reason": "out of focus", "flag_answers": {}}
    assert request(port, "POST", "/api/worm/W0001/score", score)[0] == 200
    before = (project / "scoring" / "scores.json").read_bytes()
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    handle = k32.CreateFileW(str(project / "scoring" / "scores.csv"), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        status, data = request(port, "POST", "/api/worm/W0002/score", score)
        assert status == 423 and "scores.csv is open in another program" in json.loads(data)["error"]
        assert (project / "scoring" / "scores.json").read_bytes() == before
    finally:
        k32.CloseHandle(wintypes.HANDLE(handle))
    assert request(port, "POST", "/api/worm/W0002/score", score)[0] == 200
    assert len(read_csv(project / "scoring" / "score_history.csv")) == 2


def test_an_unexpected_error_is_reported_not_dropped(app):
    """A dropped connection reads as "lost connection"; the page gets a short message instead,
    with no path."""
    project, port = app
    (project / "blind" / "W0001" / "meta.json").write_text("{broken")
    status, data = request(port, "GET", "/api/worm/W0001")
    body = json.loads(data)
    assert status == 500 and body["error"].startswith("wormscore could not do that")
    assert str(project) not in body["error"]
