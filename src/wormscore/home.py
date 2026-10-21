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
from .server import CODE, LOCAL_HOSTS, STATIC

SETTINGS = Path.home() / ".wormscore.json"
HOME_FILES = {"home.js": "text/javascript; charset=utf-8", "home.css": "text/css; charset=utf-8",
              "icon.svg": "image/svg+xml"}
IDLE_EXIT_S = 180          # close the app this long after its windows have gone quiet
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
            "theme": s.get("theme") if s.get("theme") in ("dark", "light") else "dark"}


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


def open_window(url, size=(1200, 820)):
    """Open url in its own app window (no tabs or address bar); fall back to the default browser."""
    exe = edge_path()
    if exe:
        profile = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "wormscore" / "window"
        try:
            subprocess.Popen([str(exe), f"--app={url}", f"--window-size={size[0]},{size[1]}",
                              f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"])
            return
        except OSError:
            pass
    webbrowser.open(url)


_dialog_lock = threading.Lock()


def pick_folder(title):
    """The system folder picker. Windows and Linux: Tk's native dialog on its own thread.
    macOS allows Tk windows only on the main thread, so there the Finder dialog is used."""
    if sys.platform == "darwin":
        script = (f'POSIX path of (choose folder with prompt "{title}" '
                  f'default location (POSIX file "{start_folder()}"))')
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
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
    def __init__(self, open_windows=True, pick=pick_folder, opener=open_path):
        self.job = Job()
        self.scoring = None          # (project path, server, url)
        self.last_seen = time.time()
        self.open_windows = open_windows
        self.pick = pick
        self.opener = opener

    def current(self):
        return load_settings()["project"]

    def state(self, since):
        s = load_settings()
        return {"version": __version__, "theme": s["theme"],
                "recent": [project_status(p) for p in s["recent"]],
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
        elif route == "theme":
            s = load_settings()
            s["theme"] = "light" if body.get("theme") == "light" else "dark"
            save_settings(s)
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
    app = HomeApp()
    srv = make_home_server(app)
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    def watchdog():
        while True:
            time.sleep(10)
            if not app.job.running and app.quiet_for() > IDLE_EXIT_S:
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
