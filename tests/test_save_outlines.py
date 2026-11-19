"""Saving outlines (1.26.9): the same numbers as before, without filtering the whole stack, and without
holding up a score that is saved meanwhile."""
import copy
import http.client
import json
import threading

import numpy as np
import pytest

from wormscore import config as C
from wormscore import measure as M
from wormscore import server
from wormscore.pipeline import read_csv


def earlier_method(code, raw, cands, meta, rois, cfg, sat_mask=None):
    """measure_regions as it was up to 1.26.6: every plane median-filtered in full."""
    filt = M.median_filter_stack(raw, cfg["measurement"]["median_filter_px"])
    h, w = raw.shape[1:]
    masks = {r["label"]: server.roi_mask(r, h, w, cands) for r in rois}
    cells = {k: m for k, m in masks.items() if k != "background" and m.any()}
    exclude = np.zeros((h, w), bool)
    for m in cells.values():
        exclude |= m
    user_bg = masks.get("background")
    rows = []
    for label, m in cells.items():
        r = M.measure_region(raw, filt, m, exclude, meta["pixel_um"], meta["saturation_value"], cfg, sat_mask)
        if user_bg is not None and user_bg.any():
            bz = r["best_z"]
            r["user_bg_mip"] = float(np.median(raw.max(0)[user_bg]))
            r["user_bg_best_plane"] = float(np.median(raw[bz][user_bg]))
            r["mean_mip_minus_user_bg"] = round(r["mean_mip"] - r["user_bg_mip"], 2)
            r["mean_best_minus_user_bg"] = round(r["mean_best_plane"] - r["user_bg_best_plane"], 2)
        source = next((x.get("source", "drawn") for x in rois if x["label"] == label), "drawn")
        rows.append({"code": code, "region": label, "source": source, **r})
    return rows


def stack(seed, shape=(7, 60, 90)):
    rng = np.random.default_rng(seed)
    raw = rng.integers(90, 140, shape).astype(np.uint16)
    raw[rng.random(shape) < 0.02] = 4095            # hot pixels, which the median filter removes
    raw[3, 20:28, 30:40] += 900                     # a cell
    return raw


ROI_SETS = [
    # cells in the middle; one touching a corner, one an edge; one outline crossing the whole image
    [{"type": "ellipse", "label": "Neuron 1", "cx": 35, "cy": 24, "rx": 6, "ry": 5},
     {"type": "ellipse", "label": "Neuron 2", "cx": 70, "cy": 40, "rx": 4, "ry": 4}],
    [{"type": "ellipse", "label": "Neuron 1", "cx": 2, "cy": 1, "rx": 5, "ry": 4},
     {"type": "polygon", "label": "Neuron 2", "points": [[84, 50], [89, 50], [89, 59], [84, 59]]},
     {"type": "ellipse", "label": "background", "cx": 50, "cy": 10, "rx": 5, "ry": 5}],
    [{"type": "polygon", "label": "Neuron 1", "points": [[0, 0], [89, 0], [89, 59], [0, 59]]}],
    [{"type": "candidate", "rank": 1, "label": "Neuron 1", "source": "suggestion S1"},
     {"type": "ellipse", "label": "Neuron 2", "cx": 88, "cy": 30, "rx": 3, "ry": 9}],
    [{"type": "ellipse", "label": "background", "cx": 50, "cy": 10, "rx": 5, "ry": 5}],
    [],
]


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 7])
@pytest.mark.parametrize("dim_cutoff", [0, 3])
def test_outline_measurements_are_exactly_those_of_the_earlier_method(size, dim_cutoff):
    cfg = copy.deepcopy(C.DEFAULTS)
    cfg["measurement"]["median_filter_px"] = size
    cfg["unblinding"]["dim_cutoff_sd"] = dim_cutoff
    meta = {"pixel_um": 0.25, "saturation_value": 4095}
    cand = np.zeros((60, 90), bool)
    cand[19:29, 29:41] = True
    for seed in range(3):
        raw = stack(seed)
        for rois in ROI_SETS:
            new = server.measure_regions("W0001", raw, {"arr_0": cand}, meta, rois, cfg)
            old = earlier_method("W0001", raw, {"arr_0": cand}, meta, rois, cfg)
            assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True)


def test_only_the_area_around_the_outlines_is_filtered(monkeypatch):
    """Filtering all of a 2048 x 2048 x 50 stack took most of the 45 s a save needed."""
    seen = []
    real = M.median_filter_stack
    monkeypatch.setattr(M, "median_filter_stack", lambda s, size: seen.append(s.shape) or real(s, size))
    raw = stack(0, (5, 200, 300))
    rois = [{"type": "ellipse", "label": "Neuron 1", "cx": 150, "cy": 100, "rx": 6, "ry": 6}]
    server.measure_regions("W0001", raw, {}, {"pixel_um": 0.25, "saturation_value": 4095}, rois, C.DEFAULTS)
    (shape,) = seen
    assert shape[0] == 5 and shape[1] < 40 and shape[2] < 40


def post(port, path, body, timeout=60):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, data


def test_a_score_is_saved_while_outlines_are_being_measured(fresh_project):
    srv = server.make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    measuring, go = threading.Event(), threading.Event()
    real = srv.project.measure_rois

    def slow(code, rois):
        measuring.set()
        assert go.wait(60)
        return real(code, rois)
    srv.project.measure_rois = slow
    rois = [{"type": "candidate", "rank": 1, "label": "Neuron 1", "source": "suggestion S1"}]
    result = {}
    t = threading.Thread(target=lambda: result.update(rois=post(port, "/api/worm/W0001/rois", {"rois": rois})))
    t.start()
    try:
        assert measuring.wait(30)
        score = {"usable": "no", "unusable_reason": "out of focus", "flag_answers": {}}
        try:
            status, _ = post(port, "/api/worm/W0002/score", score, timeout=10)
        except OSError:
            status = "the score waited for the outlines"
        assert status == 200
        assert read_csv(fresh_project / "scoring" / "scores.csv")[0]["code"] == "W0002"
        assert not (fresh_project / "scoring" / "rois.json").exists()      # nothing written before it is measured
    finally:
        go.set()
        t.join(60)
        srv.shutdown()
        srv.server_close()
    assert result["rois"][0] == 200
    assert read_csv(fresh_project / "scoring" / "roi_measurements.csv")[0]["region"] == "Neuron 1"
    assert json.loads((fresh_project / "scoring" / "rois.json").read_text(encoding="utf-8"))["W0001"] == rois


# ------------------------------------------------------------------ two windows, one code

def held_server(project):
    """A scoring server whose first measurement waits until the test lets it go."""
    srv = server.make_server(project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    measuring, go, real, calls = threading.Event(), threading.Event(), srv.project.measure_rois, []

    def slow(code, rois):
        calls.append(code)
        if len(calls) == 1:
            measuring.set()
            assert go.wait(60)
        return real(code, rois)
    srv.project.measure_rois = slow
    return srv, measuring, go


FIRST = [{"type": "candidate", "rank": 1, "label": "Neuron 1", "source": "suggestion S1"}]
LATER = [{"type": "ellipse", "label": "Neuron 2", "cx": 190, "cy": 190, "rx": 5, "ry": 4}]


def test_the_later_click_wins_when_two_windows_save_outlines_for_one_code(fresh_project):
    """Outlines are measured without holding the lock (1.26.9), so the save clicked first could be written
    last, over the outlines of the later click, and both windows said "Saved"."""
    srv, measuring, go = held_server(fresh_project)
    port, result = srv.server_address[1], {}
    t = threading.Thread(target=lambda: result.update(first=post(port, "/api/worm/W0001/rois", {"rois": FIRST})))
    t.start()
    try:
        assert measuring.wait(30)
        status, data = post(port, "/api/worm/W0001/rois", {"rois": LATER})      # clicked later, finished first
        assert status == 200 and json.loads(data) == {"ok": True, "n_regions": 1}
        # another code's outlines and a score are not held up or refused meanwhile
        assert json.loads(post(port, "/api/worm/W0002/rois", {"rois": FIRST})[1])["ok"] is True
        assert post(port, "/api/worm/W0003/score", {"usable": "no", "unusable_reason": "x", "flag_answers": {}})[0] == 200
    finally:
        go.set()
        t.join(60)
        srv.shutdown()
        srv.server_close()
    assert result["first"][0] == 200 and json.loads(result["first"][1]) == {"ok": False, "superseded": True}
    saved = json.loads((fresh_project / "scoring" / "rois.json").read_text(encoding="utf-8"))
    assert saved["W0001"] == LATER and saved["W0002"] == FIRST
    # the numbers written are those of the later click's outlines, exactly as a save on its own writes them
    rows = json.loads((fresh_project / "scoring" / "roi_measurements.json").read_text(encoding="utf-8"))["W0001"]
    alone = server.Project(fresh_project).measure_rois("W0001", LATER)
    assert json.dumps(rows, sort_keys=True) == json.dumps(alone, sort_keys=True)
    assert [r["region"] for r in read_csv(fresh_project / "scoring" / "roi_measurements.csv") if r["code"] == "W0001"] == ["Neuron 2"]


def test_an_earlier_save_is_kept_when_the_later_one_fails(fresh_project):
    srv, measuring, go = held_server(fresh_project)
    port, result = srv.server_address[1], {}
    t = threading.Thread(target=lambda: result.update(first=post(port, "/api/worm/W0001/rois", {"rois": FIRST})))
    t.start()
    try:
        assert measuring.wait(30)
        bad = [{"type": "candidate", "rank": 99, "label": "Neuron 1"}]          # no such suggestion: this save fails
        assert post(port, "/api/worm/W0001/rois", {"rois": bad})[0] == 500
    finally:
        go.set()
        t.join(60)
        srv.shutdown()
        srv.server_close()
    assert json.loads(result["first"][1]) == {"ok": True, "n_regions": 1}
    assert json.loads((fresh_project / "scoring" / "rois.json").read_text(encoding="utf-8"))["W0001"] == FIRST


def test_saves_one_after_the_other_are_each_written(fresh_project):
    p = server.Project(fresh_project)
    for rois in (FIRST, LATER, FIRST):
        with p.lock:
            assert p.save_rois_in_order("W0001", rois, None, p.roi_ticket()) is True
        assert p.load("rois.json", {})["W0001"] == rois


# ------------------------------------------------------------------ two windows, one after the other

def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("GET", path)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, json.loads(data) if res.status == 200 else data


def running(project):
    srv = server.make_server(project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_a_save_says_when_it_replaced_outlines_another_window_saved_meanwhile(fresh_project):
    """Two scoring windows saved one image's outlines one after the other: the later save replaced the
    earlier one and neither window was told. The later save still wins. The window that saves now learns
    that it replaced outlines saved since it loaded them, and the other can ask which outlines are saved."""
    srv, port = running(fresh_project)
    save = lambda rois, loaded: json.loads(post(port, "/api/worm/W0001/rois", {"rois": rois, "loaded": loaded})[1])
    try:
        opened = get(port, "/api/worm/W0001")[1]["rois_v"]               # both windows open the image: no outlines yet
        assert get(port, "/api/worm/W0001/rois-version")[1] == {"version": opened}
        a = save(FIRST, opened)                                          # window A
        assert a == {"ok": True, "n_regions": 1, "version": a["version"]} and a["version"] != opened
        assert get(port, "/api/worm/W0001/rois-version")[1] == {"version": a["version"]}    # A looks again: still its own
        b = save(LATER, opened)                                          # window B, which never saw A's outlines
        assert b == {"ok": True, "n_regions": 1, "version": b["version"], "replaced": True}
        assert json.loads((fresh_project / "scoring" / "rois.json").read_text(encoding="utf-8"))["W0001"] == LATER
        assert get(port, "/api/worm/W0001/rois-version")[1]["version"] == b["version"] != a["version"]   # A looks again: replaced
        assert get(port, "/api/worm/W0001")[1]["rois_v"] == b["version"]
        again = save(FIRST, a["version"])                                # A saves anyway: it wins, and is told
        assert again["replaced"] is True and again["version"] == a["version"]       # the same outlines, the same version
        assert "replaced" not in save(LATER, again["version"])           # a window that saw the saved outlines
        assert "replaced" not in save(LATER, a["version"])               # the same outlines again replace nothing
        assert get(port, "/api/worm/W0002/rois-version")[1] == {"version": opened}          # another image: none saved
        assert get(port, "/api/worm/W9999/rois-version")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_what_is_stored_is_the_same_with_and_without_the_version(fresh_project, tmp_path):
    """One scoring window: the same files with the same bytes as before, and a page that sends no version
    (an earlier one left open) gets the answer it always got."""
    import shutil
    other = tmp_path / "other"
    shutil.copytree(fresh_project, other)
    stored = []
    for project, with_version in ((fresh_project, True), (other, False)):
        srv, port = running(project)
        try:
            for rois in (FIRST, LATER + FIRST, []):
                loaded = get(port, "/api/worm/W0001")[1]["rois_v"]
                answer = json.loads(post(port, "/api/worm/W0001/rois", {"rois": rois, **({"loaded": loaded} if with_version else {})})[1])
                assert "replaced" not in answer
                if not with_version:
                    assert answer == {"ok": True, "n_regions": len(rois)}
        finally:
            srv.shutdown()
            srv.server_close()
        stored.append({p.name: p.read_bytes() for p in sorted((project / "scoring").iterdir())})
    assert stored[0] == stored[1] and sorted(stored[0]) == ["roi_measurements.csv", "roi_measurements.json", "rois.json"]
