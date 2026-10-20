import http.client
import json
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
