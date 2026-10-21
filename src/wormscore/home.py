"""Desktop app: a home screen in the same design as the scoring page.

This is what the Windows download opens. A small server on 127.0.0.1 serves the home
screen, which is shown in its own app window (Microsoft Edge in app mode when present,
otherwise the default browser). Every button calls the same functions as the command
line, so nothing here changes blinding, codes, scoring or statistics. The home screen
shows counts only; it never reads the key's contents.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import update as U
from .server import CODE, LOCAL_HOSTS, STATIC

SETTINGS = Path.home() / ".wormscore.json"
HOME_FILES = {"home.js": "text/javascript; charset=utf-8", "home.css": "text/css; charset=utf-8",
              "icon.svg": "image/svg+xml",
              "icon-192.png": "image/png", "icon-512.png": "image/png",
              "manifest.webmanifest": "application/manifest+json"}
GUIDE_URL = "https://github.com/arjunkai/wormscore#readme"
IDLE_EXIT_S = 180         # close the app this long after its windows have gone quiet
PROGRESS_LINE = re.compile(r"\((\d+)/(\d+)\)\s*$")


# ------------------------------------------------------------------ settings

def load_settings():
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    recent = [p for p in s.get("recent", []) if isinstance(p, str)]
    if s.get("project") and s["project"] not in recent:
        recent.insert(0, s["project"])
    return {"project": s.get("project", ""), "recent": recent[:8],
            "theme": s.get("theme") if s.get("theme") in ("dark", "light") else "dark",
            "update_check": s.get("update_check") is not False,
            "skip_version": s.get("skip_version") if isinstance(s.get("skip_version"), str) else ""}


def save_settings(s):
    try:
        SETTINGS.write_text(json.dumps(s, indent=1), encoding="utf-8")
    except OSError:
        pass


def remember(path):
    s = load_settings()
    path = str(Path(path))
    s["recent"] = [path] + [p for p in s["recent"] if os.path.normcase(p) != os.path.normcase(path)]
    s["recent"] = s["recent"][:8]
    s["project"] = path
    save_settings(s)


def start_folder():
    """Folder pickers open outside OneDrive: synced folders lag with large image files."""
    last = load_settings()["project"]
    if last and "onedrive" not in last.lower() and Path(last).parent.is_dir():
        return str(Path(last).parent)
    return str(Path.home())


# ------------------------------------------------------------------ project status

def _rows(csv_path):
    with open(csv_path, encoding="utf-8") as fh:
        return max(sum(1 for _ in fh) - 1, 0)


def project_status(path):
    """Counts for the step cards. Reads file listings and counts only, never the key's contents."""
    from . import config as C
    p = Path(path)
    st = {"path": str(p), "exists": p.is_dir(), "has_config": (p / "config.json").is_file()}
    if not st["has_config"]:
        return st
    try:
        cfg = C.load(p)
        C.check(cfg)
        st.update(name=cfg["project_name"], min_scored=cfg["unblinding"]["min_scored_worms"], config_error=None)
    except (Exception, SystemExit) as exc:
        st.update(name=p.name, min_scored=0, config_error=str(exc))
    inv = p / "outputs" / "inventory.csv"
    st["scanned"] = _rows(inv) if inv.is_file() else None
    blind = p / "blind"
    st["coded"] = (len([d for d in blind.iterdir() if d.is_dir() and CODE.match(d.name)])
                   if blind.is_dir() and (p / "key" / "key.csv").is_file() else None)
    scores = p / "scoring" / "scores.json"
    try:
        st["scored"] = len(json.loads(scores.read_text(encoding="utf-8"))) if scores.is_file() else 0
    except (OSError, ValueError):
        st["scored"] = 0
    st["scoring_started"] = (p / "scoring").is_dir() and any((p / "scoring").iterdir())
    st["unblinded"] = (p / "outputs" / "unblinded" / "summary.txt").is_file()
    return st


# ------------------------------------------------------------------ background steps

class Job:
    """One step at a time, run on a thread; the page polls its log and progress."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.title = ""
        self.project = ""
        self.lines = []
        self.frac = None
        self.started = 0.0
        self.outcome = ""

    def say(self, text):
        for line in str(text).splitlines() or [""]:
            self.lines.append(line)
            m = PROGRESS_LINE.search(line)
            if m and int(m.group(2)):
                self.frac = int(m.group(1)) / int(m.group(2))

    def progress(self, done, total):
        self.frac = done / total if total else None

    def start(self, title, project, fn):
        with self.lock:
            if self.running:
                return False
            self.running, self.title, self.project = True, title, str(project)
            self.frac, self.started, self.outcome = None, time.time(), ""
        self.say(f"--- {title} ---")

        def work():
            try:
                fn()
                self.say(f"--- {title}: done ---")
                self.outcome = "done"
            except SystemExit as exc:
                self.say(f"Stopped: {exc}")
                self.outcome = "stopped"
            except Exception as exc:  # shown to the user rather than lost
                self.say(f"Error: {type(exc).__name__}: {exc}")
                self.outcome = "error"
            finally:
                self.running = False
        threading.Thread(target=work, daemon=True).start()
        return True

    def snapshot(self, since):
        elapsed = time.time() - self.started if self.started else 0
        eta = elapsed * (1 - self.frac) / self.frac if self.running and self.frac and self.frac > 0.02 else None
        return {"running": self.running, "title": self.title, "project": self.project, "outcome": self.outcome,
                "lines": self.lines[since:], "next": len(self.lines), "frac": self.frac,
                "elapsed_s": round(elapsed), "eta_s": None if eta is None else round(eta)}


# ------------------------------------------------------------------ windows and dialogs

def edge_path():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            exe = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.is_file():
                return exe
    return None


_edge_pids = set()     # Edge processes this app started; only their windows get our icon


def open_window(url, size=(1200, 820)):
    """Open url in its own app window (no tabs or address bar); fall back to the default browser."""
    exe = edge_path()
    if exe:
        profile = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "wormscore" / "window"
        try:
            # Started from the home folder: a window started from the app folder would hold
            # that folder open, and an update could not replace it.
            p = subprocess.Popen([str(exe), f"--app={url}", f"--window-size={size[0]},{size[1]}",
                                  f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"],
                                 cwd=str(Path.home()))
            _edge_pids.add(p.pid)
            _start_icon_keeper()
            return
        except OSError:
            pass
    webbrowser.open(url)


_icon_keeper = None
APP_ID = "arjunkai.wormscore"


def _relaunch_command():
    """What a pinned taskbar button runs: the app itself, not Edge."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else sys.executable}" -c "from wormscore.home import main; main()"'


def _set_window_identity(hwnd, ico):
    """Give an app window wormscore's own taskbar identity: its own app ID (so wormscore windows
    group as one app instead of under Edge), the name, and the icon file. Without this the
    taskbar draws the button from Edge's 32-pixel copy of the page icon, which looks blurry
    on scaled displays. Uses the window's property store (System.AppUserModel.*)."""
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    class PKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3),
                    ("value", ctypes.c_void_p), ("pad", ctypes.c_void_p)]

    def guid(s):
        g = GUID()
        ctypes.memmove(g.data, uuid.UUID(s).bytes_le, 16)
        return g
    store = ctypes.c_void_p()
    if ctypes.windll.shell32.SHGetPropertyStoreForWindow(
            wintypes.HWND(hwnd), ctypes.byref(guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")), ctypes.byref(store)):
        return False
    vtable = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PKEY),
                                   ctypes.POINTER(PROPVARIANT))(vtable[6])         # IPropertyStore::SetValue
    commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[7])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    fmtid = guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3")                             # System.AppUserModel
    try:
        for pid, text in ((5, APP_ID), (3, f"{ico},0"), (4, "wormscore"), (2, _relaunch_command())):
            buf = ctypes.create_unicode_buffer(text)
            value = PROPVARIANT(vt=31, value=ctypes.addressof(buf))                   # VT_LPWSTR
            set_value(store, ctypes.byref(PKEY(fmtid, pid)), ctypes.byref(value))
        commit(store)
    finally:
        release(store)
    return True


def _start_icon_keeper():
    """Sharp icons for the app windows. Edge gives an app window at most a 32-pixel icon, which
    the taskbar stretches. Each new window gets wormscore's own taskbar identity and icon file,
    plus title-bar icons from wormscore.ico at the size the screen needs."""
    global _icon_keeper
    if sys.platform != "win32" or _icon_keeper is not None:
        return
    ico = STATIC / "wormscore.ico"
    if not ico.is_file():
        return

    def keep():
        import ctypes
        from ctypes import wintypes
        ctypes.windll.ole32.CoInitialize(None)
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        handles, done = {}, set()

        def icon(px):
            if px not in handles:   # loaded once per size; Windows picks the best image in the .ico
                handles[px] = user32.LoadImageW(None, str(ico), 1, px, px, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
            return handles[px]

        def each(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in _edge_pids and user32.IsWindowVisible(hwnd):
                if hwnd not in done:
                    try:
                        if _set_window_identity(hwnd, ico):
                            done.add(hwnd)
                    except OSError:
                        pass
                dpi = user32.GetDpiForWindow(hwnd) or 96
                user32.SendMessageW(hwnd, 0x80, 1, icon(user32.GetSystemMetricsForDpi(11, dpi)))  # ICON_BIG
                user32.SendMessageW(hwnd, 0x80, 0, icon(user32.GetSystemMetricsForDpi(49, dpi)))  # ICON_SMALL
            return True

        def profile_pids():
            """Edge processes using this app's own profile folder: Edge relaunches itself, and if an
            earlier app window is still open it hands new windows to that process."""
            profile = str(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "wormscore" / "window")
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
                  # the path is quoted on Edge's command line, so match the path alone
                  "Where-Object { $_.CommandLine -like '*" + profile.replace("'", "''") + "*' } | "
                  "ForEach-Object { $_.ProcessId }")
            try:
                out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                                     timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                return {int(x) for x in out.split() if x.isdigit()}
            except (OSError, subprocess.SubprocessError):
                return set()

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each)
        rounds = 0
        while True:
            if rounds % 10 == 0:        # at once, then every 30 s
                _edge_pids.update(profile_pids())
            try:
                user32.EnumWindows(callback, 0)
            except OSError:
                pass
            rounds += 1
            time.sleep(3 if rounds > 1 else 1)

    _icon_keeper = threading.Thread(target=keep, daemon=True)
    _icon_keeper.start()


_dialog_lock = threading.Lock()


def pick_folder(title):
    """The system folder picker. Windows and Linux: Tk's native dialog on its own thread.
    macOS allows Tk windows only on the main thread, so there the Finder dialog is used."""
    if sys.platform == "darwin":
        # Title and folder go in as arguments, never into the script text, so a folder name
        # with quotes in it cannot change the script.
        r = subprocess.run(["osascript", "-e", "on run argv",
                            "-e", "POSIX path of (choose folder with prompt (item 1 of argv) "
                                  "default location (POSIX file (item 2 of argv)))",
                            "-e", "end run", title, start_folder()], capture_output=True, text=True)
        return r.stdout.strip().rstrip("/") or None if r.returncode == 0 else None
    result = {}

    def run():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        result["path"] = filedialog.askdirectory(parent=root, title=title, initialdir=start_folder(), mustexist=False)
        root.destroy()
    with _dialog_lock:
        t = threading.Thread(target=run)
        t.start()
        t.join()
    return str(Path(result["path"])) if result.get("path") else None


def open_in_browser(url, title_part):
    """Open url in the default browser and bring that window to the front. Windows lets only
    the program the user last clicked raise a window, so the browser on its own opens the
    page behind the app window. Joining the input of the app window (which was just clicked)
    lets this process raise the browser window whose title shows the page."""
    webbrowser.open(url)
    if sys.platform != "win32":
        return

    def raise_it():
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        found = []

        def each(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if user32.IsWindowVisible(hwnd) and pid.value not in _edge_pids:
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buf, 512)
                if title_part in buf.value:
                    found.append(hwnd)
                    return False
            return True
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each)
        for _ in range(40):                  # the page title appears once it has loaded
            time.sleep(0.25)
            user32.EnumWindows(callback, 0)
            if found:
                break
        if not found:
            return
        hwnd = wintypes.HWND(found[0])
        fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(me, fg_thread, False)
    threading.Thread(target=raise_it, daemon=True).start()


def open_path(p):
    """Open a folder or file with the system's default program."""
    if hasattr(os, "startfile"):
        os.startfile(p)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])


def dpi_aware():
    """Sharp text on high-resolution screens (folder picker and any Tk window)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


# ------------------------------------------------------------------ the app

class HomeApp:
    def __init__(self, open_windows=True, pick=pick_folder, opener=open_path, check_updates=False):
        self.job = Job()
        self.scoring = None          # (project path, server, url)
        self.last_seen = time.time()
        self.open_windows = open_windows
        self.pick = pick
        self.opener = opener
        self.exit_app = lambda: None   # set by main(): closes the app so an update can replace it
        self.upd = {"info": None, "error": None, "checking": False, "dismissed": False,
                    "phase": "", "frac": None, "message": "", "result": None}
        if check_updates:
            self.upd["result"] = U.last_result()
            if load_settings()["update_check"]:
                self.check_for_update()

    # ---------------------------------------------------------------- updates

    def check_for_update(self, wait=False):
        """Ask GitHub for the latest release (no data about the user is sent)."""
        def run():
            try:
                self.upd.update(info=U.check(), error=None)
            except U.UpdateError as exc:
                self.upd["error"] = str(exc)
            finally:
                self.upd["checking"] = False
        self.upd["checking"] = True
        t = threading.Thread(target=run, daemon=True)
        t.start()
        if wait:
            t.join(10)

    def updating(self):
        return self.upd["phase"] in ("downloading", "restarting")

    def update_view(self, statuses):
        s = load_settings()
        u, info = self.upd, self.upd["info"]
        kind = U.install_kind()
        newer = bool(info and U.is_newer(info["version"]))
        projects = U.unfinished_projects(statuses) if newer else []
        return {"kind": kind, "enabled": s["update_check"], "checking": u["checking"], "error": u["error"],
                "latest": info["version"] if info else None, "newer": newer,
                "show": newer and not u["dismissed"] and s["skip_version"] != info["version"],
                "notes": info["notes"] if newer else "", "page": info["page"] if info else U.RELEASES_PAGE,
                "can_install": newer and kind in U.ASSETS and bool(info.get("asset")),
                "pip_command": U.PIP_COMMAND, "warning": U.warning_text(projects, info["version"]) if newer else "",
                "phase": u["phase"], "frac": u["frac"], "message": u["message"], "result": u["result"],
                "previous": U.previous_version() if kind in U.ASSETS else None}

    def update_blocked(self):
        if self.job.running:
            return "A step is still running. Update when it is done."
        if self.scoring and time.time() - self.scoring[1].project.last_seen < 45:
            return "Close the scoring window first, then update."
        if self.updating():
            return "An update is already under way."
        return None

    def update_action(self, body):
        action = body.get("action")
        if action == "later":
            self.upd["dismissed"] = True
        elif action == "skip":
            if self.upd["info"]:
                s = load_settings()
                s["skip_version"] = self.upd["info"]["version"]
                save_settings(s)
        elif action == "check":
            self.upd["dismissed"] = False
            self.check_for_update(wait=True)
        elif action == "dismiss":
            self.upd["result"] = None
            if self.upd["phase"] == "error":
                self.upd.update(phase="", message="")
        elif action == "releases":
            open_in_browser(U.RELEASES_PAGE, "arjunkai/wormscore")
        elif action in ("install", "rollback"):
            blocked = self.update_blocked()
            if blocked:
                raise ValueError(blocked)
            if action == "install":
                info = self.upd["info"]
                if not (info and U.is_newer(info["version"])):
                    raise ValueError("No newer version found.")
                s = load_settings()
                if U.unfinished_projects([project_status(p) for p in s["recent"]]) and body.get("confirmed") is not True:
                    raise ValueError("Confirm the update first: a project's Methods would cite two versions.")
            elif not U.previous_version():
                raise ValueError("There is no previous version to go back to.")
            self.upd.update(phase="downloading" if action == "install" else "restarting", frac=None, message="")
            threading.Thread(target=self._run_update, args=(action,), daemon=True).start()
        else:
            return None
        return {"ok": True}

    def _run_update(self, action):
        def say(text):
            self.upd["message"] = text

        def progress(done, total):
            self.upd["frac"] = done / total if total else None
        try:
            if action == "install":
                U.install(self.upd["info"], say=say, progress=progress)
            else:
                back = U.rollback()
                s = load_settings()
                s["skip_version"] = __version__      # the older copy should not offer this one again
                save_settings(s)
                say(f"Going back to wormscore {back}.")
        except (U.UpdateError, OSError) as exc:
            self.upd.update(phase="error", message=str(exc))
            return
        self.upd["phase"] = "restarting"
        time.sleep(2)            # lets the page show "Restarting" before the app closes
        self.stop_scoring()
        self.exit_app()

    def current(self):
        return load_settings()["project"]

    def state(self, since):
        s = load_settings()
        recent = [project_status(p) for p in s["recent"]]
        return {"version": __version__, "theme": s["theme"], "recent": recent,
                "update": self.update_view(recent),
                "project": project_status(s["project"]) if s["project"] else None,
                "scoring_url": self.scoring[2] if self.scoring and self.scoring[0] == s["project"] else None,
                "job": self.job.snapshot(since)}

    def need_project(self):
        p = self.current()
        if not p or not (Path(p) / "config.json").is_file():
            raise ValueError("Choose a project folder that has a config.json, or make a new one.")
        return Path(p)

    def run_step(self, body):
        from .pipeline import inventory, prepare
        from .unblind import unblind
        p = self.need_project()
        step = body.get("step")
        if step == "scan":
            fn = lambda: inventory(p, log=self.job.say, progress=self.job.progress)
            title = "Scan data"
        elif step == "build":
            mode = body.get("mode", "new")
            if mode not in ("new", "keep", "reassign", "add"):
                raise ValueError("unknown build mode")
            if self.scoring and self.scoring[0] == str(p):
                self.stop_scoring()   # no scoring while the blind set is rebuilt
            fn = lambda: prepare(p, reassign=mode == "reassign", add=mode == "add", log=self.job.say)
            title = {"new": "Build blind set", "keep": "Rebuild, keeping codes",
                     "reassign": "Rebuild with new codes", "add": "Add new images"}[mode]
        elif step == "unblind":
            if body.get("finished") is not True:
                raise ValueError("Tick 'I have finished scoring' first. Unblinding reveals genotypes.")
            fn = lambda: unblind(p, finished=True, anyway=body.get("anyway") is True, log=self.job.say)
            title = "Unblind"
        else:
            raise ValueError("unknown step")
        if not self.job.start(title, p, fn):
            raise ValueError("Another step is still running.")
        return {"ok": True}

    def start_scoring(self):
        from .server import make_server
        p = str(self.need_project())
        if self.job.running:
            raise ValueError("A step is still running. Open scoring when it is done.")
        if not (self.scoring and self.scoring[0] == p):
            self.stop_scoring()
            try:
                srv = make_server(p, port=0)
            except SystemExit as exc:
                raise ValueError(str(exc))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.scoring = (p, srv, f"http://127.0.0.1:{srv.server_address[1]}")
        if self.open_windows:
            open_window(self.scoring[2], (1500, 900))
        return {"ok": True, "url": self.scoring[2]}

    def stop_scoring(self):
        if self.scoring:
            self.scoring[1].shutdown()
            self.scoring[1].server_close()
            self.scoring = None

    def quiet_for(self):
        seen = self.last_seen
        if self.scoring:
            seen = max(seen, self.scoring[1].project.last_seen)
        return time.time() - seen

    def post(self, route, body):
        if route == "select":
            p = str(body.get("path", ""))
            if not (Path(p) / "config.json").is_file():
                raise ValueError("That folder has no config.json.")
            remember(p)
        elif route == "forget":
            s = load_settings()
            s["recent"] = [p for p in s["recent"] if p != body.get("path")]
            if s["project"] == body.get("path"):
                s["project"] = s["recent"][0] if s["recent"] else ""
            save_settings(s)
        elif route == "guide":
            # Opened in the system's default browser, not in the app window's Edge.
            open_in_browser(GUIDE_URL, "arjunkai/wormscore")
        elif route == "theme":
            s = load_settings()
            s["theme"] = "light" if body.get("theme") == "light" else "dark"
            save_settings(s)
        elif route == "settings":
            s = load_settings()
            if isinstance(body.get("update_check"), bool):
                s["update_check"] = body["update_check"]
            save_settings(s)
        elif route == "update":
            return self.update_action(body)
        elif route == "open-project":
            d = self.pick("Choose a project folder (one with a config.json)")
            if not d:
                return {"ok": False}
            if not (Path(d) / "config.json").is_file():
                raise ValueError(f"{d} has no config.json. Use New project to make one.")
            remember(d)
        elif route == "new-project":
            d = self.pick("Choose an empty folder for the new project")
            if not d:
                return {"ok": False}
            from .config import template
            cfg = Path(d) / "config.json"
            if not cfg.exists():
                cfg.write_text(json.dumps(template(Path(d).name), indent=2), encoding="utf-8")
            remember(d)
            self.opener(cfg)
        elif route == "demo":
            d = self.pick("Choose an empty folder for the demo project")
            if not d:
                return {"ok": False}
            remember(d)
            if body.get("real") is True:
                from .realdemo import make_real_demo
                fn = lambda: make_real_demo(d, log=self.job.say)
            else:
                from .demo import make_demo
                fn = lambda: make_demo(d, log=self.job.say)
            if not self.job.start("Make demo", d, fn):
                raise ValueError("Another step is still running.")
        elif route == "run":
            return self.run_step(body)
        elif route == "score":
            return self.start_scoring()
        elif route == "open":
            p = self.need_project()
            target = {"folder": p, "config": p / "config.json", "results": p / "outputs" / "unblinded"}.get(body.get("what"))
            if target is None or not target.exists():
                raise ValueError("Nothing to open yet.")
            self.opener(target)
        else:
            return None
        return {"ok": True}


def make_handler(app):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path, ctype):
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in LOCAL_HOSTS

        def do_GET(self):
            if not self.local_request():
                return self.send_error(403)
            app.last_seen = time.time()
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            if not parts:
                return self.send_file(STATIC / "home.html", "text/html; charset=utf-8")
            if parts[0] == "static" and len(parts) == 2 and parts[1] in HOME_FILES:
                return self.send_file(STATIC / parts[1], HOME_FILES[parts[1]])
            if parts == ["favicon.ico"]:
                return self.send_file(STATIC / "icon.svg", "image/svg+xml")
            if parts == ["api", "state"]:
                since = parse_qs(url.query).get("since", ["0"])[0]
                return self.send_json(app.state(int(since) if since.isdigit() else 0))
            self.send_error(404)

        def do_POST(self):
            # Same rules as the scoring server: a local Host, a JSON body, and a local origin.
            origin = self.headers.get("Origin")
            if not self.local_request() or self.headers.get("Content-Type", "").split(";")[0] != "application/json" \
                    or (origin and origin.split("://")[-1].rsplit(":", 1)[0] not in LOCAL_HOSTS):
                return self.send_error(403)
            app.last_seen = time.time()
            parts = [p for p in self.path.split("/") if p]
            if len(parts) != 2 or parts[0] != "api":
                return self.send_error(404)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except json.JSONDecodeError:
                return self.send_error(400)
            try:
                result = app.post(parts[1], body if isinstance(body, dict) else {})
            except ValueError as exc:
                return self.send_json({"ok": False, "error": str(exc)}, 400)
            if result is None:
                return self.send_error(404)
            self.send_json(result)

    return Handler


def make_home_server(app, port=0):
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--classic" in argv:
        from .launcher import main as classic
        return classic()
    dpi_aware()
    app = HomeApp(check_updates=True)
    srv = make_home_server(app)
    app.exit_app = lambda: threading.Thread(target=srv.shutdown, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    def watchdog():
        while True:
            time.sleep(10)
            if not app.job.running and not app.updating() and app.quiet_for() > IDLE_EXIT_S:
                srv.shutdown()
                return
    threading.Thread(target=watchdog, daemon=True).start()
    open_window(url)
    try:
        srv.serve_forever()
    finally:
        app.stop_scoring()
        srv.server_close()


if __name__ == "__main__":
    main()
