"""The Mac app's Dock behaviour, tested without a Mac: the Cocoa loop itself is replaced by a
stand-in that calls the same callbacks macOS would."""
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from wormscore import home, macapp
from wormscore import update as U


class Job:
    def __init__(self, title=None):
        self.running, self.title = title is not None, title


class App:
    def __init__(self, title=None):
        self.job = Job(title)
        self.stopped = 0

    def stop_scoring(self):
        self.stopped += 1


def test_only_the_built_mac_app_uses_the_dock_loop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert not macapp.available()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert not macapp.available()          # a pip install runs as before


def test_quit_asks_only_while_a_step_runs():
    assert macapp.quit_question(App()) is None
    assert '"Build blind set" is still running' in macapp.quit_question(App("Build blind set"))


class FakeMac:
    """Calls the callbacks the way macOS does: opened again, then Quit (or the app closes itself)."""
    def __init__(self, quit_from_dock=True):
        self.quit_from_dock = quit_from_dock
        self.stopped = threading.Event()

    def quit_question(self, app):
        return macapp.quit_question(app)

    def run(self, on_reopen, question, on_quit):
        on_reopen()
        assert question() is None
        if not self.quit_from_dock:
            assert self.stopped.wait(10)     # the server ended by itself: stop() was called
        on_quit()

    def stop(self):
        self.stopped.set()


@pytest.mark.parametrize("from_dock", [True, False])
def test_mac_app_reopens_and_quits_cleanly(monkeypatch, from_dock):
    logged, opened = [], []
    monkeypatch.setattr(home, "log_event", logged.append)          # never the real app.log
    monkeypatch.setattr(home, "open_window", opened.append)
    app = App()
    srv = home.make_home_server(home.HomeApp(open_windows=False))
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    mac = FakeMac(quit_from_dock=from_dock)
    if not from_dock:     # the watchdog (or an update) ends the server while the loop runs
        threading.Timer(0.5, srv.shutdown).start()
    home.run_mac_app(app, srv, url, mac)
    assert "opened again: showing the home page" in logged
    assert ("closing: quit from the Dock or menu" in logged) == from_dock
    assert app.stopped == 1
    with pytest.raises(OSError):
        urllib.request.urlopen(url + "/api/state", timeout=2)      # the port is closed
    for _ in range(50):
        if opened:
            break
        threading.Event().wait(0.05)
    assert opened == [url]


def test_mac_opens_config_in_the_text_editor(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delattr(os, "startfile", raising=False)
    monkeypatch.setattr(subprocess, "Popen", started.append)
    cfg = tmp_path / "config.json"
    home.open_path(cfg)
    home.open_path(tmp_path)
    assert started == [["open", "-t", str(cfg)], ["open", str(tmp_path)]]


def test_mac_app_run_from_the_download_is_told_to_move_it(monkeypatch):
    moved = Path("/private/var/folders/x/AppTranslocation/ABC/d/wormscore.app")
    monkeypatch.setattr(U, "install_kind", lambda: "mac")
    monkeypatch.setattr(U, "app_paths", lambda kind=None: {"current": moved, "previous": moved, "stage": moved,
                                                           "app": "wormscore.app"})
    with pytest.raises(U.UpdateError, match="Applications folder"):
        U.prepare_install({"version": "9.0.0", "asset": {"name": "wormscore-mac.zip"}})
