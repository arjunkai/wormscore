import http.client
import json
import threading
import time

import pytest

from wormscore import home


@pytest.fixture
def desk(fresh_project, tmp_path, monkeypatch):
    """The home server on a free port, with its settings file in tmp_path and no real windows."""
    monkeypatch.setattr(home, "SETTINGS", tmp_path / "settings.json")
    opened, picks = [], []
    app = home.HomeApp(open_windows=False, pick=lambda title: picks.pop(0) if picks else None, opener=opened.append)
    srv = home.make_home_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"project": fresh_project, "port": srv.server_address[1], "app": app, "opened": opened, "picks": picks}
    app.stop_scoring()
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request(method, path, body=json.dumps(body) if body is not None else None,
                 headers={"Content-Type": "application/json", **(headers or {})})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, (json.loads(data) if data and res.getheader("Content-Type") == "application/json" else data)


def state(port):
    return call(port, "GET", "/api/state")[1]


def wait_for_job(port, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        job = state(port)["job"]
        if not job["running"]:
            return job
        time.sleep(0.2)
    raise AssertionError("step did not finish")


def test_home_page_and_state_show_counts_never_genotypes(desk):
    port = desk["port"]
    assert call(port, "GET", "/")[0] == 200
    assert call(port, "GET", "/static/home.js")[0] == 200
    assert state(port)["project"] is None
    assert call(port, "POST", "/api/select", {"path": str(desk["project"])})[0] == 200
    s = state(port)
    p = s["project"]
    assert (p["coded"], p["scored"], p["unblinded"], p["config_error"]) == (6, 0, False, None)
    assert p["scanned"] == 6
    text = json.dumps(s)
    for leak in ("control", "mutant", "genotype", "key.csv"):
        assert leak not in text


def test_only_home_files_are_served(desk):
    port = desk["port"]
    for path in ("/static/app.js", "/static/../server.py", "/static/..%2Fhome.py", "/key/key.csv", "/api/nothing"):
        assert call(port, "GET", path)[0] == 404, path


def test_requests_from_other_sites_are_refused(desk):
    port = desk["port"]
    body = {"path": str(desk["project"])}
    assert call(port, "GET", "/api/state", headers={"Host": "evil.example"})[0] == 403
    assert call(port, "POST", "/api/select", body, headers={"Host": "evil.example"})[0] == 403
    assert call(port, "POST", "/api/select", body, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(port, "POST", "/api/select", body, headers={"Content-Type": "text/plain"})[0] == 403
    assert state(port)["project"] is None


def test_scan_runs_in_the_background_with_progress(desk):
    port = desk["port"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    assert call(port, "POST", "/api/run", {"step": "scan"})[0] == 200
    job = wait_for_job(port)
    assert job["outcome"] == "done" and job["frac"] == 1.0
    assert any("raw images" in line for line in job["lines"])


def test_new_codes_are_refused_once_scoring_started(desk):
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    (project / "scoring").mkdir(exist_ok=True)
    (project / "scoring" / "scores.json").write_text(json.dumps({"W0001": {"usable": "no"}}))
    assert state(port)["project"]["scoring_started"] is True
    call(port, "POST", "/api/run", {"step": "build", "mode": "reassign"})
    job = wait_for_job(port)
    assert job["outcome"] == "stopped"
    assert any("Refusing to reassign codes" in line for line in job["lines"])
    assert call(port, "POST", "/api/run", {"step": "build", "mode": "shuffle"})[0] == 400


def test_unblind_needs_the_finished_box(desk):
    port = desk["port"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    status, body = call(port, "POST", "/api/run", {"step": "unblind"})
    assert status == 400 and "finished scoring" in body["error"]
    assert not (desk["project"] / "outputs" / "unblinded").exists()


def test_scoring_server_starts_and_stops_for_a_rebuild(desk):
    port, app = desk["port"], desk["app"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    status, body = call(port, "POST", "/api/score", {})
    assert status == 200
    sport = int(body["url"].rsplit(":", 1)[1])
    assert call(sport, "GET", "/api/ping")[0] == 200
    assert state(port)["scoring_url"] == body["url"]
    call(port, "POST", "/api/run", {"step": "build", "mode": "keep"})
    assert app.scoring is None                      # no scoring while the blind set is rebuilt
    wait_for_job(port, timeout=300)


def test_open_only_targets_inside_the_project(desk):
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    assert call(port, "POST", "/api/open", {"what": "config"})[0] == 200
    assert desk["opened"] == [project / "config.json"]
    assert call(port, "POST", "/api/open", {"what": "results"})[0] == 400       # not unblinded yet
    assert call(port, "POST", "/api/open", {"what": "C:/Windows/System32/calc.exe"})[0] == 400


def test_new_project_writes_a_template_and_recent_list_is_kept(desk, tmp_path):
    port = desk["port"]
    new = tmp_path / "new_project"
    new.mkdir()
    desk["picks"].append(str(new))
    assert call(port, "POST", "/api/new-project", {})[0] == 200
    assert (new / "config.json").is_file()
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    recent = [p["path"] for p in state(port)["recent"]]
    assert recent[:2] == [str(desk["project"]), str(new)]
    call(port, "POST", "/api/forget", {"path": str(new)})
    assert [p["path"] for p in state(port)["recent"]] == [str(desk["project"])]


def test_icons_and_manifest_are_served(desk):
    port = desk["port"]
    for path in ("/static/icon-192.png", "/static/icon-512.png", "/static/manifest.webmanifest"):
        assert call(port, "GET", path)[0] == 200, path


def test_guide_opens_in_the_default_browser(desk, monkeypatch):
    opened = []
    monkeypatch.setattr(home.webbrowser, "open", opened.append)
    assert call(desk["port"], "POST", "/api/guide", {})[0] == 200
    assert opened == [home.GUIDE_URL]


def test_app_icon_ships_with_every_size_the_taskbar_needs():
    from PIL import Image
    ico = Image.open(home.STATIC / "wormscore.ico")
    assert {(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)} <= set(ico.info["sizes"])
    assert "wormscore.home" in home._relaunch_command() or home._relaunch_command().endswith('.exe"')
