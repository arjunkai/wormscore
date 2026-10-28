"""The app's own windows (desktop.py), with a stand-in for pywebview."""
import sys
from pathlib import Path

import pytest

from wormscore import desktop, home


class Events:
    def __init__(self):
        self.closing, self.closed = Hook(), Hook()
        self.resized, self.maximized, self.restored = Hook(), Hook(), Hook()


class Hook(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self

    def fire(self, *args):
        return [fn(*args) for fn in self]


class FakeWindow:
    def __init__(self, title, url, **kw):
        self.title, self.url, self.events, self.kw = title, url, Events(), kw
        self.loaded, self.shown = [], 0

    def load_url(self, url):
        self.loaded.append(url)
        self.url = url

    def set_title(self, title):
        self.title = title

    def restore(self):
        pass

    def show(self):
        self.shown += 1

    def destroy(self):
        if all(r is not False for r in self.events.closing.fire()):
            self.events.closed.fire()


class FakeWebview:
    def __init__(self):
        self.created = []

    def create_window(self, title, url, **kw):
        w = FakeWindow(title, url, **kw)
        self.created.append(w)
        return w


@pytest.fixture
def shell():
    app = home.HomeApp(open_windows=False)
    asked = []
    d = desktop.Desktop(app, webview=FakeWebview(), ask=lambda w, text: asked.append(text) or False)
    return d, app, asked


def test_windows_are_reused_not_duplicated(shell):
    d, app, _ = shell
    a = d.open("home", "http://127.0.0.1:1", "wormscore", (1200, 820))
    assert d.open("home", "http://127.0.0.1:1", "wormscore", (1200, 820)) is a and a.shown == 1
    s = d.open("scoring", "http://127.0.0.1:2", "Scoring: A - wormscore", (1500, 900))
    d.open("scoring", "http://127.0.0.1:2", "Scoring: B - wormscore", (1500, 900), reload=True)
    assert len(d.webview.created) == 2 and s.loaded == ["http://127.0.0.1:2"] and s.title == "Scoring: B - wormscore"


def test_closing_the_last_window_during_a_step_asks_first(shell):
    d, app, asked = shell
    w = d.open("home", "http://127.0.0.1:1", "wormscore", (1200, 820))
    app.job.running, app.job.title = True, "Build blind set"
    w.destroy()                                   # the user says No: the window stays
    assert asked and "Build blind set" in asked[0] and "home" in d.windows
    d.close_all()                                 # an update: no question, everything closes
    assert d.windows == {} and len(asked) == 1


def test_closing_the_scoring_window_stops_its_server(shell):
    d, app, asked = shell
    stopped = []
    app.stop_scoring = lambda: stopped.append(True)
    d.open("home", "http://127.0.0.1:1", "wormscore", (1200, 820))
    d.open("scoring", "http://127.0.0.1:2", "Scoring: A - wormscore", (1500, 900)).destroy()
    assert stopped == [True] and list(d.windows) == ["home"] and not asked


def test_home_route_show_needs_own_windows(monkeypatch):
    app = home.HomeApp(open_windows=False)
    monkeypatch.setattr(home, "DESKTOP", None)
    assert app.post("show", {}) is None           # Edge windows: the second start finds the window itself
    opened = []
    monkeypatch.setattr(home, "DESKTOP", type("D", (), {"open": lambda self, *a, **k: opened.append(a)})())
    app.home_url = "http://127.0.0.1:47311"
    assert app.post("show", {}) == {"ok": True} and opened[0][0] == "home"


def test_windows_open_maximized_then_as_they_were_left():
    app = home.HomeApp(open_windows=False)
    saved = {}
    d = desktop.Desktop(app, webview=FakeWebview(), ask=lambda w, t: True, sizes=lambda: dict(saved),
                        remember=lambda kind, size: saved.__setitem__(kind, size))
    w = d.open("home", "http://127.0.0.1:1", "wormscore", (1200, 820))
    assert w.kw["maximized"] is True                     # first time: full screen
    w.events.restored.fire()                             # the user un-maximizes and resizes
    w.events.resized.fire(1400, 900)
    w.destroy()
    assert saved["home"] == {"w": 1400, "h": 900, "max": False}
    w2 = d.open("home", "http://127.0.0.1:1", "wormscore", (1200, 820))
    assert (w2.kw["width"], w2.kw["height"], w2.kw["maximized"]) == (1400, 900, False)
    assert home.window_sizes({"home": {"w": 5, "h": 900, "max": False}, "x": {}}) == {}


@pytest.mark.skipif(sys.platform != "win32", reason="the downloaded-file mark is an NTFS stream")
def test_unzipped_download_is_unblocked(tmp_path, monkeypatch):
    """Explorer copies the browser's "from the internet" mark onto every unzipped file, and .NET
    then refuses the bridge the own windows need. The app removes the mark from its own files."""
    app = tmp_path / "wormscore"
    dll = app / "_internal" / "pythonnet" / "runtime" / "Python.Runtime.dll"
    other = app / "_internal" / "other.dll"
    for f in (dll, other):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        Path(f"{f}:Zone.Identifier").write_text("[ZoneTransfer]\nZoneId=3\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "wormscore.exe"))
    logged = []
    desktop.unblock_app_files(logged.append)
    assert not Path(f"{dll}:Zone.Identifier").exists() and not Path(f"{other}:Zone.Identifier").exists()
    assert dll.read_bytes() == b"x" and logged
    desktop.unblock_app_files(logged.append)          # nothing marked: nothing done, nothing logged
    assert len(logged) == 1


def test_edge_window_gets_a_running_server_when_own_windows_fail(monkeypatch):
    """If the own window cannot start, its server is closed on the way out; the Edge window must
    get a fresh one, not the closed one (which crashed with "Invalid file descriptor: -1")."""
    logged, served = [], []
    make_app = home.HomeApp
    monkeypatch.setattr(make_app, "start_updates", lambda self: None)  # no update check over the network
    monkeypatch.setattr(home, "HomeApp", lambda: make_app(open_windows=False))
    monkeypatch.setattr(home, "HOME_PORT", 0)
    monkeypatch.setattr(home, "log_event", logged.append)            # never the real app.log
    monkeypatch.setattr(home, "register_launch_link", lambda: False)  # never the real registry
    monkeypatch.setattr(home, "fix_shortcuts", lambda: None)
    monkeypatch.setattr(home, "heavy_imports", lambda: None)
    monkeypatch.setattr(desktop, "available", lambda: True)

    def failing_desktop(app, srv, url, d):
        srv.server_close()
        raise RuntimeError("Failed to resolve Python.Runtime.Loader.Initialize")

    def edge(app, srv, url, started):
        served.append((srv.socket.fileno(), url))
        srv.server_close()
    monkeypatch.setattr(home, "run_desktop", failing_desktop)
    monkeypatch.setattr(home, "run_edge_window", edge)
    home.main([])
    (fd, url), = served
    assert fd != -1 and url.startswith("http://127.0.0.1:")
    assert any("own windows failed" in line for line in logged)
