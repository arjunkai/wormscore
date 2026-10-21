"""The app's own windows (desktop.py), with a stand-in for pywebview."""
import pytest

from wormscore import desktop, home


class Events:
    def __init__(self):
        self.closing, self.closed = Hook(), Hook()


class Hook(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self

    def fire(self):
        return [fn() for fn in self]


class FakeWindow:
    def __init__(self, title, url):
        self.title, self.url, self.events = title, url, Events()
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
        w = FakeWindow(title, url)
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
