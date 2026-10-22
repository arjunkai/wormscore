"""wormscore's own app windows on Windows (WebView2, the engine built into Windows 10 and 11).

The windows belong to this process, so the app sets its taskbar ID once at startup, before any
window exists (Microsoft's rule), and every window lands on the pinned button from its first
moment. The app also knows exactly when its windows close: it ends with its last window, with no
watching or guessing. When WebView2 or pywebview is missing, home.py falls back to Edge app
windows.

Pages, scoring, codes and results are the same in either kind of window.
"""
import ctypes
import sys
import threading
from pathlib import Path

APP_ID = "arjunkai.wormscore"


def set_app_id():
    """The process's taskbar ID. Must run before the first window (any window) is created."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


def available():
    """True when this computer can show wormscore's own windows."""
    if sys.platform != "win32":
        return False
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return webview2_installed()


def webview2_installed():
    """The WebView2 runtime ships with Windows 11 and current Windows 10; check it is there."""
    import winreg
    guid = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    keys = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}")]
    for root, key in keys:
        try:
            with winreg.OpenKey(root, key) as k:
                version = winreg.QueryValueEx(k, "pv")[0]
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            pass
    base = Path(r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application")
    return base.is_dir() and any(p.is_dir() and p.name[:1].isdigit() for p in base.iterdir())


def ask_yes_no(window, text):
    """A Yes / No question in front of the window (Windows message box)."""
    try:
        hwnd = window.native.Handle.ToInt64() if window is not None else 0
    except Exception:
        hwnd = 0
    return ctypes.windll.user32.MessageBoxW(ctypes.c_void_p(hwnd), text, "wormscore", 0x34) == 6   # Yes/No, warning


class Desktop:
    """The app's windows: one home window and at most one scoring window."""

    def __init__(self, app, log=lambda text: None, webview=None, ask=None, sizes=dict,
                 remember=lambda kind, size: None):
        if webview is None:
            import webview
        self.webview = webview
        self.ask = ask or ask_yes_no
        self.sizes = sizes            # how each window was left last time
        self.remember = remember
        self.state = {}               # this session: kind -> {"w", "h", "max"}
        self.app = app
        self.log = log
        self.lock = threading.Lock()
        self.windows = {}         # "home" / "scoring" -> pywebview window
        self.urls = {}
        self.leaving = False      # closing everything for an update: no questions asked

    # ---------------------------------------------------------------- opening

    def open(self, kind, url, title, size, reload=False):
        """Open a window, or bring the open one forward (loading url again if it changed or
        reload is set, for example when the scoring window switches to another project)."""
        with self.lock:
            w = self.windows.get(kind)
            if w is not None:
                if reload or self.urls.get(kind) != url:
                    self.urls[kind] = url
                    w.load_url(url)
                if w.title != title:
                    w.set_title(title)
                self.front(w)
                return w
            # The first time a window opens maximized; after that, as it was left (size and
            # maximized; not the position, which could be on a screen that is no longer there).
            last = self.sizes().get(kind) or {"w": size[0], "h": size[1], "max": True}
            w = self.webview.create_window(title, url, width=last["w"], height=last["h"], maximized=last["max"],
                                           min_size=(720, 520), background_color="#121417", text_select=True)
            self.windows[kind], self.urls[kind] = w, url
            self.state[kind] = dict(last)
        w.events.resized += lambda width, height: self.on_resized(kind, width, height)
        w.events.maximized += lambda: self.state[kind].update(max=True)
        w.events.restored += lambda: self.state[kind].update(max=False)
        w.events.closing += lambda: self.on_closing(kind)
        w.events.closed += lambda: self.on_closed(kind)
        return w

    def front(self, w):
        try:
            w.restore()
            w.show()
            hwnd = w.native.Handle.ToInt64()
            ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        except Exception:
            pass

    def close(self, kind):
        w = self.windows.get(kind)
        if w is not None:
            w.destroy()

    def close_all(self):
        """For an update or go back: the app ends when its last window closes."""
        self.leaving = True
        for kind in list(self.windows):
            self.close(kind)

    # ---------------------------------------------------------------- closing

    def on_closing(self, kind):
        """Returning False keeps the window open. Asked only when closing the last window
        would stop a step that is running."""
        if self.leaving or len(self.windows) > 1 or not self.app.job.running:
            return True
        text = (f'"{self.app.job.title}" is still running. Closing wormscore stops it, and it would '
                "have to be run again.\n\nClose anyway?")
        return self.ask(self.windows.get(kind), text)

    def on_resized(self, kind, width, height):
        """Keep the normal (not maximized or minimized) size, which a maximized window returns to."""
        w = self.windows.get(kind)
        try:
            normal = w is not None and int(w.native.WindowState) == 0          # FormWindowState.Normal
        except Exception:
            normal = not self.state.get(kind, {}).get("max", False)
        if normal and width >= 400 and height >= 400:
            self.state[kind].update(w=int(width), h=int(height))

    def on_closed(self, kind):
        with self.lock:
            self.windows.pop(kind, None)
            self.urls.pop(kind, None)
            last = self.state.pop(kind, None)
        if last:
            self.remember(kind, last)
        if kind == "scoring":
            self.app.stop_scoring()
        self.log(f"{kind} window closed")

    # ---------------------------------------------------------------- running

    def run(self, data_dir):
        """Show the windows until the last one closes (blocks; must run on the main thread)."""
        self.webview.start(gui="edgechromium", private_mode=False, storage_path=str(Path(data_dir) / "webview"))
