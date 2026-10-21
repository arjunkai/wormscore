import hashlib
import json
import subprocess
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wormscore import __version__, home
from wormscore import update as U

from test_home import call, state


# ------------------------------------------------------------------ a stand-in for GitHub

@pytest.fixture
def github(tmp_path, monkeypatch):
    """A local server answering like GitHub's latest-release API, with a downloadable zip."""
    served = {"release": None, "files": {}, "requests": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            served["requests"].append({"path": self.path, "headers": dict(self.headers)})
            if self.path == "/latest":
                body = json.dumps(served["release"]).encode()
            elif self.path in served["files"]:
                body = served["files"][self.path]
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("WORMSCORE_UPDATE_URL", base + "/latest")
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path / "data")

    def release(version, zip_bytes=None, digest=None, name="wormscore-windows.zip"):
        assets = []
        if zip_bytes is not None:
            served["files"]["/" + name] = zip_bytes
            assets.append({"name": name, "browser_download_url": f"{base}/{name}", "size": len(zip_bytes),
                           "digest": digest if digest is not None else "sha256:" + hashlib.sha256(zip_bytes).hexdigest()})
        served["release"] = {"tag_name": f"v{version}", "body": "## Changes\n- **Faster** scans\n- New `update` command",
                             "html_url": f"{base}/page", "assets": assets}
    served["release_fn"] = release
    yield served
    srv.shutdown()
    srv.server_close()


def app_zip(tmp_path, files):
    z = tmp_path / "made.zip"
    with zipfile.ZipFile(z, "w") as fh:
        for name, data in files.items():
            fh.writestr(name, data)
    return z.read_bytes()


# ------------------------------------------------------------------ versions and notes

def test_versions_compare_as_numbers():
    assert U.parse_version("v0.10.2") == (0, 10, 2)
    assert U.is_newer("0.10.0", "0.9.9") and U.is_newer("v1.0.0", "0.8.0")
    assert not U.is_newer("0.8.0", "0.8.0") and not U.is_newer("0.7.9", "0.8.0")
    assert not U.is_newer("nightly", "0.8.0")


def test_release_notes_become_plain_text():
    assert U.clean_notes("## New\n- **bold** and `code` and [link](http://x)") == "New\n- bold and code and link"


# ------------------------------------------------------------------ check and download

def test_check_sends_nothing_about_the_user(github, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "windows")
    github["release_fn"]("9.0.0", b"zip")
    info = U.check()
    assert info["version"] == "9.0.0" and info["asset"]["name"] == "wormscore-windows.zip"
    assert "Faster" in info["notes"] and "**" not in info["notes"]
    req = github["requests"][-1]
    assert req["path"] == "/latest"                       # no query string, no project names
    assert req["headers"]["User-Agent"] == f"wormscore/{__version__}"
    assert not any(k.lower() in ("cookie", "authorization") for k in req["headers"])


def test_check_without_internet_raises_update_error(monkeypatch):
    monkeypatch.setenv("WORMSCORE_UPDATE_URL", "http://127.0.0.1:9/latest")
    with pytest.raises(U.UpdateError):
        U.check(timeout=2)


def test_download_must_match_githubs_checksum(github, tmp_path):
    github["release_fn"]("9.0.0", b"good bytes")
    url = U.latest_url().replace("/latest", "/wormscore-windows.zip")
    good = {"url": url, "size": 10, "digest": "sha256:" + hashlib.sha256(b"good bytes").hexdigest()}
    out = U.download(good, tmp_path / "dl" / "w.zip")
    assert out.read_bytes() == b"good bytes"
    bad = dict(good, digest="sha256:" + "0" * 64)
    with pytest.raises(U.UpdateError, match="checksum"):
        U.download(bad, tmp_path / "dl" / "x.zip")
    assert not (tmp_path / "dl" / "x.zip").exists() and not (tmp_path / "dl" / "x.part").exists()
    with pytest.raises(U.UpdateError, match="no checksum"):
        U.download(dict(good, digest=""), tmp_path / "dl" / "y.zip")


def test_unpack_refuses_bad_zips(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(app_zip(tmp_path, {"wormscore/wormscore.exe": "x", "wormscore/_internal/a.txt": "y"}))
    assert U.unpack(z, tmp_path / "stage", "windows") == tmp_path / "stage" / "wormscore"
    z.write_bytes(app_zip(tmp_path, {"wormscore/README.txt": "no exe here"}))
    with pytest.raises(U.UpdateError, match="no wormscore.exe"):
        U.unpack(z, tmp_path / "stage", "windows")
    z.write_bytes(app_zip(tmp_path, {"../evil.exe": "x", "wormscore/wormscore.exe": "x"}))
    with pytest.raises(U.UpdateError, match="outside"):
        U.unpack(z, tmp_path / "stage", "windows")
    assert not (tmp_path / "evil.exe").exists()


def test_new_version_must_report_the_expected_version(github, tmp_path, monkeypatch):
    """Everything before the swap: download, checksum, unpack, run --version."""
    monkeypatch.setattr(U, "install_kind", lambda: "windows")
    cur = tmp_path / "apps" / "wormscore"
    cur.mkdir(parents=True)
    monkeypatch.setattr(U, "app_paths", lambda kind=None: {"current": cur, "previous": cur.with_name("wormscore-previous"),
                                                         "stage": cur.with_name("wormscore-update"), "app": "wormscore.exe"})
    github["release_fn"]("9.0.0", app_zip(tmp_path, {"wormscore/wormscore.exe": "x"}))
    info = U.check()
    monkeypatch.setattr(U, "new_version", lambda app, kind: "8.9.9")
    with pytest.raises(U.UpdateError, match="expected 9.0.0"):
        U.prepare_install(info)
    assert not cur.with_name("wormscore-update").exists()
    monkeypatch.setattr(U, "new_version", lambda app, kind: "9.0.0")
    paths, new = U.prepare_install(info)
    assert new == cur.with_name("wormscore-update") / "wormscore" and (new / "wormscore.exe").is_file()


def test_pip_installs_are_told_to_use_pip(monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "pip")
    with pytest.raises(U.UpdateError, match="pip install -U wormscore"):
        U.prepare_install({"version": "9.0.0", "asset": None})


# ------------------------------------------------------------------ the swap helper

def finished_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def run_helper(tmp_path, cur, new, prev):
    paths = {"current": cur, "previous": prev, "stage": new.parent, "app": "wormscore.exe"}
    U.start_helper(paths, new, pid=finished_pid(), relaunch=False).wait(timeout=180)
    return U.last_result()


def make_app(folder, version):
    folder.mkdir(parents=True)
    (folder / "VERSION").write_text(version)


def test_helper_swaps_and_keeps_the_previous_version(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path / "data")
    apps = tmp_path / "apps"
    cur, prev = apps / "wormscore", apps / "wormscore-previous"
    make_app(cur, "1")
    make_app(prev, "0")
    make_app(apps / "wormscore-update" / "wormscore", "2")
    assert run_helper(tmp_path, cur, apps / "wormscore-update" / "wormscore", prev) == {"ok": True, "message": ""}
    assert (cur / "VERSION").read_text() == "2" and (prev / "VERSION").read_text() == "1"
    assert not (apps / "wormscore-previous.old").exists()
    assert not (apps / "wormscore-update").exists()          # the empty unpack folder is removed


def test_helper_puts_everything_back_when_a_move_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path / "data")
    apps = tmp_path / "apps"
    cur, prev = apps / "wormscore", apps / "wormscore-previous"
    make_app(cur, "1")
    make_app(prev, "0")
    missing = apps / "wormscore-update" / "wormscore"       # the new copy is not there
    (apps / "wormscore-update").mkdir()
    monkeypatch.setattr(U, "WINDOWS_HELPER", U.WINDOWS_HELPER.replace("$i -ge 60", "$i -ge 1"))
    monkeypatch.setattr(U, "POSIX_HELPER", U.POSIX_HELPER.replace("-ge 60", "-ge 1"))
    r = run_helper(tmp_path, cur, missing, prev)
    assert not r["ok"] and "previous version was kept" in r["message"]
    assert (cur / "VERSION").read_text() == "1" and (prev / "VERSION").read_text() == "0"


def test_rollback_swaps_the_kept_copy_back(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(U, "install_kind", lambda: "windows")
    apps = tmp_path / "apps"
    cur, prev = apps / "wormscore", apps / "wormscore-previous"
    paths = {"current": cur, "previous": prev, "stage": apps / "wormscore-update", "app": "wormscore.exe"}
    monkeypatch.setattr(U, "app_paths", lambda kind=None: paths)
    make_app(cur, "2")
    assert U.previous_version() is None
    make_app(prev, "1")
    U._remember_previous("0.7.0")
    assert U.previous_version() == "0.7.0"
    started = []
    monkeypatch.setattr(U, "start_helper", lambda p, new, relaunch=True: started.append(new))
    assert U.rollback() == "0.7.0"
    assert started == [apps / "wormscore-update" / "wormscore-previous"] and (started[0] / "VERSION").read_text() == "1"
    assert json.loads((tmp_path / "data" / "previous.json").read_text())["version"] == __version__


# ------------------------------------------------------------------ projects between build and unblind

def test_projects_between_build_and_unblind_are_named():
    statuses = [{"path": "/a", "name": "A", "coded": 6, "scoring_started": True, "unblinded": False},
                {"path": "/b", "name": "B", "coded": 6, "scoring_started": False, "unblinded": False},
                {"path": "/c", "name": "C", "coded": 6, "scoring_started": True, "unblinded": True},
                {"path": "/d", "name": "D", "coded": None, "unblinded": False}]
    found = U.unfinished_projects(statuses)
    assert [p["name"] for p in found] == ["A", "B"]
    text = U.warning_text(found, "9.0.0")
    assert "Scoring is in progress in A" in text and "built but not unblinded in B" in text and "two wormscore versions" in text
    assert U.warning_text([], "9.0.0") == ""


# ------------------------------------------------------------------ the home screen

@pytest.fixture
def desk_upd(fresh_project, tmp_path, monkeypatch):
    monkeypatch.setattr(home, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(U, "install_kind", lambda: "windows")
    info = {"version": "9.0.0", "notes": "Faster scans", "page": "http://x/page",
            "asset": {"name": "wormscore-windows.zip", "url": "http://x/z", "size": 1, "digest": "sha256:00"}}
    monkeypatch.setattr(U, "check", lambda timeout=5: dict(info))
    app = home.HomeApp(open_windows=False, pick=lambda title: None, opener=lambda p: None, check_updates=True)
    exits = []
    app.exit_app = lambda: exits.append(True)
    srv = home.make_home_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(50):
        if not app.upd["checking"]:
            break
        time.sleep(0.05)
    yield {"port": srv.server_address[1], "app": app, "project": fresh_project, "exits": exits}
    srv.shutdown()
    srv.server_close()


def test_banner_shows_a_newer_version_with_notes(desk_upd):
    u = state(desk_upd["port"])["update"]
    assert u["show"] and u["latest"] == "9.0.0" and u["can_install"] and u["notes"] == "Faster scans"
    assert u["warning"] == ""


def test_not_now_and_skip_this_version(desk_upd):
    port = desk_upd["port"]
    call(port, "POST", "/api/update", {"action": "later"})
    assert not state(port)["update"]["show"]
    desk_upd["app"].upd["dismissed"] = False
    call(port, "POST", "/api/update", {"action": "skip"})
    assert not state(port)["update"]["show"]
    assert home.load_settings()["skip_version"] == "9.0.0"
    call(port, "POST", "/api/select", {"path": str(desk_upd["project"])})    # other saves keep the setting
    assert home.load_settings()["skip_version"] == "9.0.0"


def test_off_switch_stops_the_check(desk_upd, monkeypatch):
    port = desk_upd["port"]
    call(port, "POST", "/api/settings", {"update_check": False})
    assert home.load_settings()["update_check"] is False
    assert state(port)["update"]["enabled"] is False
    monkeypatch.setattr(U, "check", lambda timeout=5: pytest.fail("checked although switched off"))
    home.HomeApp(open_windows=False, check_updates=True)


def test_unfinished_project_needs_confirmation_and_not_now_is_default(desk_upd, monkeypatch):
    port = desk_upd["port"]
    call(port, "POST", "/api/select", {"path": str(desk_upd["project"])})   # blind set built, not unblinded
    u = state(port)["update"]
    assert "two wormscore versions" in u["warning"]
    calls = []
    monkeypatch.setattr(U, "install", lambda info, say, progress: calls.append(info["version"]))
    status, r = call(port, "POST", "/api/update", {"action": "install"})
    assert status == 400 and "Confirm" in r["error"] and calls == []
    assert call(port, "POST", "/api/update", {"action": "install", "confirmed": True})[0] == 200
    for _ in range(100):
        if desk_upd["exits"]:
            break
        time.sleep(0.05)
    assert calls == ["9.0.0"] and desk_upd["exits"] == [True]


def test_update_waits_for_running_steps_and_open_scoring(desk_upd):
    app = desk_upd["app"]
    app.job.running = True
    assert "step is still running" in call(desk_upd["port"], "POST", "/api/update", {"action": "install"})[1]["error"]
    app.job.running = False


def test_failed_update_is_shown_and_nothing_closes(desk_upd, monkeypatch):
    port = desk_upd["port"]

    def fail(info, say, progress):
        raise U.UpdateError("The download does not match GitHub's checksum. Nothing was changed.")
    monkeypatch.setattr(U, "install", fail)
    call(port, "POST", "/api/update", {"action": "install"})
    for _ in range(100):
        if state(port)["update"]["phase"] == "error":
            break
        time.sleep(0.05)
    u = state(port)["update"]
    assert u["phase"] == "error" and "checksum" in u["message"] and desk_upd["exits"] == []
    call(port, "POST", "/api/update", {"action": "dismiss"})
    assert state(port)["update"]["phase"] == ""


def test_pip_install_gets_the_pip_command(desk_upd, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "pip")
    u = state(desk_upd["port"])["update"]
    assert u["show"] and not u["can_install"] and u["pip_command"] == "pip install -U wormscore"


def test_last_update_result_is_shown_once(tmp_path, monkeypatch):
    monkeypatch.setattr(home, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path / "data")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "update-result.txt").write_text("failed\nAccess denied. The previous version was kept.\n")
    s = home.load_settings()
    s["update_check"] = False
    home.save_settings(s)
    app = home.HomeApp(open_windows=False, check_updates=True)
    assert app.upd["result"] == {"ok": False, "message": "Access denied. The previous version was kept."}
    assert U.last_result() is None
