import hashlib
import json
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
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
    monkeypatch.setattr(U, "start_helper", lambda p, new, **kw: started.append((new, kw)))
    assert U.rollback() == "0.7.0"
    new, kw = started[0]
    assert new == apps / "wormscore-update" / "wormscore-previous" and (new / "VERSION").read_text() == "1"
    # the helper names this version as the kept copy once the swap has worked, and puts the kept copy back if not
    assert kw["prev_version"] == __version__ and kw["restore"] == prev
    assert json.loads((tmp_path / "data" / "previous.json").read_text())["version"] == "0.7.0"


# ------------------------------------------------------------------ a swap that fails (1.26.9)

def whole_app(folder, version):
    """An app folder with the program, a library folder and a version, like the packaged app."""
    (folder / "_internal").mkdir(parents=True)
    for name in ("wormscore.exe", "VERSION", "_internal/lib.dll"):
        (folder / name).write_text(version)


def contents(folder):
    return {p.relative_to(folder).as_posix(): p.read_text() for p in folder.rglob("*") if p.is_file()}


def swap_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(U, "install_kind", lambda: "windows")
    monkeypatch.setattr(U, "WINDOWS_HELPER", U.WINDOWS_HELPER.replace("$i -ge 60", "$i -ge 1"))
    monkeypatch.setattr(U, "POSIX_HELPER", U.POSIX_HELPER.replace("-ge 60", "-ge 1"))
    apps = tmp_path / "apps"
    paths = {"current": apps / "wormscore", "previous": apps / "wormscore-previous", "stage": apps / "wormscore-update",
             "app": "wormscore.exe", "check": "wormscore.exe"}
    monkeypatch.setattr(U, "app_paths", lambda kind=None: paths)
    return paths


def swap(paths, new, **kw):
    U.start_helper(paths, new, pid=finished_pid(), relaunch=False, **kw).wait(timeout=180)
    return U.last_result()


def go_back_until_the_helper(monkeypatch):
    """U.rollback() up to the point where the helper would start: returns what it would be started with."""
    calls = []
    with monkeypatch.context() as m:
        m.setattr(U, "start_helper", lambda p, new, **kw: calls.append((new, kw)))
        U.rollback(relaunch=False)
    new, kw = calls[0]
    return new, {k: kw[k] for k in ("prev_version", "restore")}


@pytest.mark.skipif(sys.platform != "win32", reason="an open file stops a folder from being moved on Windows only")
def test_failed_swap_with_a_file_held_open_leaves_every_copy_whole_and_go_back_works(tmp_path, monkeypatch):
    """Audit item 9: with a file in the app folder open in another program, the helper moved the files it
    could one by one, so the app folder was left without wormscore.exe and Go back had nothing whole."""
    paths = swap_setup(tmp_path, monkeypatch)
    cur, prev, new = paths["current"], paths["previous"], paths["stage"] / "wormscore"
    whole_app(cur, "1")
    whole_app(prev, "0")
    whole_app(new, "2")
    U._remember_previous("0.0.1")
    one = {"wormscore.exe": "1", "VERSION": "1", "_internal/lib.dll": "1"}
    zero = {k: "0" for k in one}
    with open(cur / "_internal" / "lib.dll", "rb"):
        r = swap(paths, new, prev_version="1.0.0")
    assert not r["ok"] and "previous version was kept" in r["message"]
    assert contents(cur) == one                                    # the app folder still holds the whole app
    assert contents(prev) == zero and contents(new) == {k: "2" for k in one}
    assert not Path(str(prev) + ".old").exists()
    assert U.previous_version() == "0.0.1"                         # Go back still names the copy that is kept

    # Go back with a file still open: nothing moves, and the kept copy is back in its place for another try
    staged, kw = go_back_until_the_helper(monkeypatch)
    with open(cur / "wormscore.exe", "rb"):
        r = swap(paths, staged, **kw)
    assert not r["ok"]
    assert contents(cur) == one and contents(prev) == zero and U.previous_version() == "0.0.1"

    # the file is closed: Go back swaps the kept copy in, and this version becomes the kept one
    staged, kw = go_back_until_the_helper(monkeypatch)
    assert swap(paths, staged, **kw) == {"ok": True, "message": ""}
    assert contents(cur) == zero and contents(prev) == one and U.previous_version() == __version__


def test_kept_version_is_recorded_only_after_a_swap_that_worked(tmp_path, monkeypatch):
    paths = swap_setup(tmp_path, monkeypatch)
    cur, prev = paths["current"], paths["previous"]
    whole_app(cur, "1")
    whole_app(prev, "0")
    U._remember_previous("0.9.0")
    r = swap(paths, paths["stage"] / "wormscore", prev_version="1.0.0")      # the new copy is not there
    assert not r["ok"] and U.previous_version() == "0.9.0" and contents(prev)["VERSION"] == "0"
    whole_app(paths["stage"] / "wormscore", "2")
    assert swap(paths, paths["stage"] / "wormscore", prev_version="1.0.0")["ok"]
    assert U.previous_version() == "1.0.0" and contents(prev)["VERSION"] == "1" and contents(cur)["VERSION"] == "2"


def test_a_new_copy_without_the_program_is_not_left_in_place(tmp_path, monkeypatch):
    paths = swap_setup(tmp_path, monkeypatch)
    cur, prev, new = paths["current"], paths["previous"], paths["stage"] / "wormscore"
    whole_app(cur, "1")
    whole_app(prev, "0")
    make_app(new, "2")                      # no wormscore.exe
    r = swap(paths, new, prev_version="1.0.0")
    assert not r["ok"] and "previous version was kept" in r["message"]
    assert contents(cur)["wormscore.exe"] == "1" and contents(prev)["wormscore.exe"] == "0"
    assert (new / "VERSION").read_text() == "2"


def test_a_failed_go_back_puts_the_kept_copy_back(tmp_path, monkeypatch):
    paths = swap_setup(tmp_path, monkeypatch)
    prev, staged = paths["previous"], paths["stage"] / "wormscore-previous"
    whole_app(staged, "0")                  # where Go back puts the kept copy before the swap
    r = swap(paths, staged, prev_version="1.0.0", restore=prev)      # the app folder cannot be moved (it is not there)
    assert not r["ok"] and contents(prev)["wormscore.exe"] == "0" and not staged.exists()


# ------------------------------------------------------------------ the latest release from a local file (1.26.9)

def local_release(tmp_path, monkeypatch, zip_bytes, digest=None):
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(U, "install_kind", lambda: "windows")
    z = tmp_path / "release" / "wormscore-windows.zip"
    z.parent.mkdir()
    z.write_bytes(zip_bytes)
    rel = {"tag_name": "v9.0.0", "body": "", "assets": [{
        "name": z.name, "browser_download_url": z.as_uri(), "size": len(zip_bytes),
        "digest": digest or "sha256:" + hashlib.sha256(zip_bytes).hexdigest()}]}
    (tmp_path / "release" / "latest.json").write_text(json.dumps(rel))
    monkeypatch.setenv("WORMSCORE_UPDATE_FILE", str(tmp_path / "release" / "latest.json"))


def test_latest_release_can_be_read_from_a_local_file(tmp_path, monkeypatch):
    body = app_zip(tmp_path, {"wormscore/wormscore.exe": "x"})
    local_release(tmp_path, monkeypatch, body)
    info = U.check()
    assert info["version"] == "9.0.0" and info["asset"]["url"].startswith("file:")
    got = U.download(info["asset"], tmp_path / "data" / "updates" / "wormscore-windows.zip")
    assert got.read_bytes() == body


def test_a_local_release_must_still_match_its_checksum(tmp_path, monkeypatch):
    local_release(tmp_path, monkeypatch, app_zip(tmp_path, {"wormscore/wormscore.exe": "x"}), digest="sha256:" + "0" * 64)
    with pytest.raises(U.UpdateError, match="does not match"):
        U.download(U.check()["asset"], tmp_path / "data" / "updates" / "wormscore-windows.zip")
    monkeypatch.delenv("WORMSCORE_UPDATE_FILE")
    monkeypatch.delenv("WORMSCORE_UPDATE_URL", raising=False)
    assert U.latest_url() == U.LATEST_URL            # without the test setting: GitHub, as before


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


# ------------------------------------------------------------------ the app on another volume than the data folder (macOS)

import errno
import os
import shutil


def two_volumes(monkeypatch, a, b):
    """Folders a and b behave as two volumes: a rename between them is refused, as the system does."""
    real = os.rename
    side = lambda p: "a" if a in Path(p).parents or Path(p) == a else "b" if b in Path(p).parents or Path(p) == b else ""

    def rename(src, dst, **kw):
        if side(src) and side(dst) and side(src) != side(dst):
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(src))
        return real(src, dst, **kw)
    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(U, "same_volume", lambda x, y: side(x) == side(y))


def mac_paths(monkeypatch, app_volume, data):
    app = app_volume / "Applications" / "wormscore.app"
    monkeypatch.setattr(U, "data_dir", lambda: data)
    monkeypatch.setattr(sys, "executable", str(app / "Contents" / "MacOS" / "wormscore"))
    return U.app_paths("mac")


def mac_app(folder, version):
    (folder / "Contents" / "MacOS").mkdir(parents=True)
    (folder / "Contents" / "Resources").mkdir()
    for name in ("Contents/MacOS/wormscore", "Contents/Info.plist", "Contents/Resources/lib.dylib"):
        (folder / name).write_text(version)


def test_the_mac_copies_stay_in_the_data_folder_when_it_is_on_the_apps_volume(tmp_path, monkeypatch):
    vol = tmp_path / "volA"
    two_volumes(monkeypatch, vol, tmp_path / "volB")
    data = vol / "Users" / "me" / "Library" / "Application Support" / "wormscore"
    p = mac_paths(monkeypatch, vol, data)
    assert p["previous"] == data / "previous" / "wormscore.app" and p["stage"] == data / "update-stage"   # as before
    assert "previous_before" not in p


def test_the_mac_copies_are_kept_next_to_the_app_when_the_data_folder_is_on_another_volume(tmp_path, monkeypatch):
    """~/Library/Application Support on one volume, the app on another: every move of the exchange was a
    copy there, which can stop halfway. The copies now stay on the app's volume."""
    a, b = tmp_path / "volA", tmp_path / "volB"
    two_volumes(monkeypatch, a, b)
    data = b / "Users" / "me" / "Library" / "Application Support" / "wormscore"
    p = mac_paths(monkeypatch, a, data)
    apps = a / "Applications"
    assert p["current"] == apps / "wormscore.app"
    assert p["previous"] == apps / ".wormscore-previous" / "wormscore.app" and p["stage"] == apps / ".wormscore-update"
    assert all(U.same_volume(p["current"], p[k]) for k in ("previous", "stage"))
    assert p["previous_before"] == data / "previous" / "wormscore.app"


def test_same_volume_reads_the_device_of_the_nearest_folder_that_exists(tmp_path):
    assert U.same_volume(tmp_path, tmp_path / "not" / "made" / "yet")


def test_a_move_across_volumes_is_whole_or_not_at_all(tmp_path, monkeypatch):
    a, b = tmp_path / "volA", tmp_path / "volB"
    two_volumes(monkeypatch, a, b)
    src, dst = b / "kept" / "wormscore.app", a / "stage" / "wormscore.app"
    mac_app(src, "1")
    dst.parent.mkdir(parents=True)
    whole = contents(src)

    def half_copy(s, d):                    # the disk fills after the first file
        d.mkdir()
        shutil.copy(next(p for p in sorted(s.rglob("*")) if p.is_file()), d / "first")
        raise OSError(errno.ENOSPC, "No space left on device", str(d))
    with monkeypatch.context() as m:
        m.setattr(U, "copy_whole", half_copy)
        with pytest.raises(OSError):
            U.move_whole(src, dst)
    assert contents(src) == whole and list(dst.parent.iterdir()) == []          # nothing half-made is left
    U.move_whole(src, dst)
    assert contents(dst) == whole and not src.exists() and [p.name for p in dst.parent.iterdir()] == ["wormscore.app"]


def test_go_back_brings_a_copy_kept_on_the_other_volume_over_whole(tmp_path, monkeypatch):
    """A previous version kept by 1.26.12 or earlier in the data folder, on another volume than the app."""
    a, b = tmp_path / "volA", tmp_path / "volB"
    two_volumes(monkeypatch, a, b)
    data = b / "Users" / "me" / "Library" / "Application Support" / "wormscore"
    paths = mac_paths(monkeypatch, a, data)
    monkeypatch.setattr(U, "install_kind", lambda: "mac")
    monkeypatch.setattr(U, "app_paths", lambda kind=None: paths)
    mac_app(paths["current"], "2")
    mac_app(paths["previous_before"], "1")
    U._remember_previous("1.26.12")
    assert U.previous_version() == "1.26.12"
    whole = contents(paths["previous_before"])
    started = []
    monkeypatch.setattr(U, "start_helper", lambda p, new, **kw: started.append((new, kw)))

    def half_copy(s, d):
        d.mkdir()
        raise OSError(errno.ENOSPC, "No space left on device", str(d))
    with monkeypatch.context() as m:
        m.setattr(U, "copy_whole", half_copy)
        with pytest.raises(U.UpdateError, match="Nothing was changed"):
            U.rollback()
    assert not started and contents(paths["previous_before"]) == whole
    assert contents(paths["current"])["Contents/Info.plist"] == "2"
    assert list(paths["stage"].iterdir()) == [] and U.previous_version() == "1.26.12"

    assert U.rollback() == "1.26.12"
    new, kw = started[0]
    assert new == paths["stage"] / "wormscore.app" and contents(new) == whole
    # the helper now moves only on the app's volume, and a failed exchange puts the copy back next to the app
    assert U.same_volume(new, paths["current"]) and kw["restore"] == paths["previous"]


def test_a_spare_copy_in_the_data_folder_is_removed_after_an_exchange_that_worked(tmp_path, monkeypatch):
    a, b = tmp_path / "volA", tmp_path / "volB"
    two_volumes(monkeypatch, a, b)
    data = b / "Users" / "me" / "Library" / "Application Support" / "wormscore"
    paths = mac_paths(monkeypatch, a, data)
    monkeypatch.setattr(U, "app_paths", lambda kind=None: paths)
    mac_app(paths["previous_before"], "0")
    (data / "update-result.txt").write_text("failed\nThe previous version was kept.\n")
    assert U.last_result()["ok"] is False and paths["previous_before"].exists()
    (data / "update-result.txt").write_text("ok\n\n")
    assert U.last_result()["ok"] is True and paths["previous_before"].exists()     # no copy next to the app yet: kept
    mac_app(paths["previous"], "1")
    (data / "update-result.txt").write_text("ok\n\n")
    assert U.last_result()["ok"] is True and not paths["previous_before"].exists() and paths["previous"].exists()


SH = shutil.which("sh")


def posix_helper(tmp_path, helper, cur, new, prev, stage="", shims=None):
    """Run the macOS helper text with sh (Git's sh on Windows). shims: a folder of stand-in commands."""
    script = tmp_path / "helper.sh"
    script.write_text(helper.replace("-ge 60", "-ge 1"), encoding="utf-8", newline="\n")
    result = tmp_path / "result.txt"
    result.unlink(missing_ok=True)
    env = dict(os.environ)
    if shims:
        env["PATH"] = str(shims) + os.pathsep + env["PATH"]
    px = lambda p: Path(p).as_posix() if p else ""
    subprocess.run([SH, px(script), str(finished_pid()), px(cur), px(new), px(prev), px(result), "", "", "", "",
                    "Contents/MacOS/wormscore", px(stage)], env=env, timeout=180, check=True)
    lines = result.read_text(encoding="utf-8").splitlines()
    return lines[0] == "ok", " ".join(lines[1:])


def volume_shims(tmp_path):
    """Stand-ins that make .../volB another volume for the helper: stat reports another device for it, and
    mv between the two copies one file and then fails, as a copy to a full disk does."""
    d = tmp_path / "shims"
    d.mkdir()
    real_mv = Path(shutil.which("mv")).as_posix()
    (d / "stat").write_text('#!/bin/sh\ncase "$3" in */volB|*/volB/*) echo 2;; *) echo 1;; esac\n', newline="\n")
    (d / "mv").write_text('#!/bin/sh\nside() { case "$1" in */volB|*/volB/*) echo 2;; *) echo 1;; esac; }\n'
                          'if [ "$(side "$1")" != "$(side "$2")" ]; then\n'
                          '  mkdir -p "$2/Contents"; cp "$1/Contents/Info.plist" "$2/Contents/"; exit 1\nfi\n'
                          f'exec "{real_mv}" "$@"\n', newline="\n")
    for f in d.iterdir():
        f.chmod(0o755)
    return d


@pytest.mark.skipif(not SH, reason="needs sh")
def test_the_mac_helper_moves_nothing_between_volumes(tmp_path):
    """The layout up to 1.26.12 with the app on another volume: mv copied file by file, so a copy that
    stopped left a part of an app as the kept copy, or in the app's place."""
    a, b = tmp_path / "volA", tmp_path / "volB"
    cur, prev, new = a / "wormscore.app", b / "previous" / "wormscore.app", b / "update-stage" / "wormscore.app"
    mac_app(cur, "1")
    mac_app(prev, "0")
    mac_app(new, "2")
    before = [contents(x) for x in (cur, prev, new)]
    ok, message = posix_helper(tmp_path, U.POSIX_HELPER, cur, new, prev, shims=volume_shims(tmp_path))
    assert not ok and "different disks" in message and "previous version was kept" in message
    assert [contents(x) for x in (cur, prev, new)] == before
    assert not Path(str(prev) + ".old").exists()


@pytest.mark.skipif(not SH, reason="needs sh")
def test_the_mac_helper_exchanges_copies_kept_next_to_the_app(tmp_path):
    a = tmp_path / "volA"
    cur, prev = a / "wormscore.app", a / ".wormscore-previous" / "wormscore.app"
    stage = a / ".wormscore-update"
    new = stage / "wormscore" / "wormscore.app"                # as the release zip unpacks
    mac_app(cur, "1")
    mac_app(prev, "0")
    mac_app(new, "2")
    (stage / "wormscore" / "README.txt").write_text("read me")
    ok, message = posix_helper(tmp_path, U.POSIX_HELPER, cur, new, prev, stage, shims=volume_shims(tmp_path))
    assert ok, message
    assert contents(cur)["Contents/MacOS/wormscore"] == "2" and contents(prev)["Contents/MacOS/wormscore"] == "1"
    assert sorted(p.name for p in a.iterdir()) == [".wormscore-previous", "wormscore.app"]     # the unpack folder is gone
