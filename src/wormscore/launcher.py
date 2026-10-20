"""Desktop launcher: a small window that runs each step without typing commands.

This is what the Windows download opens. It does the same thing as the command line.
"""
import json
import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__

SETTINGS = Path.home() / ".wormscore.json"


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"wormscore {__version__}")
        self.geometry("760x560")
        self.minsize(640, 460)
        self.project = tk.StringVar(value=self._last_project())
        self.finished = tk.BooleanVar(value=False)
        self.msgs = queue.Queue()
        self.server = None
        self.busy = False
        self._build()
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # -------------------------------------------------------------- layout
    def _build(self):
        pad = {"padx": 12, "pady": 6}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Project folder").pack(side="left")
        ttk.Entry(top, textvariable=self.project).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="Open...", command=self._choose).pack(side="left")
        ttk.Button(top, text="New...", command=self._new).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Demo...", command=self._demo).pack(side="left", padx=(6, 0))

        steps = ttk.LabelFrame(self, text="Steps")
        steps.pack(fill="x", **pad)
        rows = [
            ("1. Scan data", "List every file and image; identify channels from metadata.", self._inventory),
            ("2. Build blind set", "Apply inclusion rules, assign random codes, measure suggested spots.", self._prepare),
            ("3. Score", "Open the scoring app in your browser.", self._app),
            ("4. Unblind", "Join scores, numbers and the key. Only after scoring is finished.", self._unblind),
        ]
        for i, (label, hint, fn) in enumerate(rows):
            ttk.Button(steps, text=label, width=20, command=fn).grid(row=i, column=0, sticky="w", padx=8, pady=4)
            ttk.Label(steps, text=hint, foreground="#555").grid(row=i, column=1, sticky="w")
        ttk.Checkbutton(steps, text="I have finished scoring", variable=self.finished).grid(
            row=3, column=2, sticky="w", padx=8)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=12)
        ttk.Button(bottom, text="Open project folder", command=self._open_folder).pack(side="left")
        ttk.Button(bottom, text="Edit config.json", command=self._edit_config).pack(side="left", padx=6)

        self.log = tk.Text(self, height=14, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, **pad)

    # -------------------------------------------------------------- helpers
    def _last_project(self):
        try:
            return json.loads(SETTINGS.read_text()).get("project", "")
        except (OSError, ValueError):
            return ""

    def _remember(self):
        try:
            SETTINGS.write_text(json.dumps({"project": self.project.get()}))
        except OSError:
            pass

    def say(self, text):
        self.msgs.put(str(text))

    def _drain(self):
        while not self.msgs.empty():
            self.log.configure(state="normal")
            self.log.insert("end", self.msgs.get() + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(100, self._drain)

    def _need_project(self):
        p = Path(self.project.get())
        if not (p / "config.json").exists():
            messagebox.showwarning("wormscore", "Choose a project folder that has a config.json (or make a new one).")
            return None
        self._remember()
        return p

    def _run(self, title, fn):
        if self.busy:
            messagebox.showinfo("wormscore", "Another step is still running.")
            return
        self.busy = True
        self.say(f"--- {title} ---")

        def work():
            try:
                fn()
                self.say(f"--- {title}: done ---")
            except SystemExit as exc:
                self.say(f"Stopped: {exc}")
            except Exception as exc:  # shown to the user rather than lost
                self.say(f"Error: {type(exc).__name__}: {exc}")
            finally:
                self.busy = False
        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------------- actions
    def _choose(self):
        d = filedialog.askdirectory(title="Choose a project folder")
        if d:
            self.project.set(d)
            self._remember()

    def _new(self):
        d = filedialog.askdirectory(title="Choose an empty folder for the new project")
        if not d:
            return
        from .config import template
        cfg = Path(d) / "config.json"
        if cfg.exists():
            messagebox.showinfo("wormscore", "That folder already has a config.json; opening it.")
        else:
            cfg.write_text(json.dumps(template(Path(d).name), indent=2), encoding="utf-8")
            self.say(f"Created {cfg}. Edit 'sources' so it points at your images, then run step 1.")
        self.project.set(d)
        self._remember()
        self._edit_config()

    def _demo(self):
        d = filedialog.askdirectory(title="Choose an empty folder for the demo project")
        if not d:
            return
        self.project.set(d)
        self._remember()
        from .demo import make_demo
        self._run("make demo", lambda: make_demo(d, log=self.say))

    def _inventory(self):
        p = self._need_project()
        if p:
            from .pipeline import inventory
            self._run("scan data", lambda: inventory(p, log=self.say))

    def _prepare(self):
        p = self._need_project()
        if not p:
            return
        from .pipeline import prepare
        reassign = False
        if (p / "key" / "key.csv").exists() and not ((p / "scoring").exists() and any((p / "scoring").iterdir())):
            reassign = messagebox.askyesno(
                "wormscore", "A blind set already exists and nothing has been scored.\n\n"
                "Yes: assign new codes (needed if images were added or removed).\nNo: keep the current codes.")
        self._run("build blind set", lambda: prepare(p, reassign=reassign, log=self.say))

    def _app(self):
        p = self._need_project()
        if not p:
            return
        if self.server is not None:
            webbrowser.open(self.server_url)
            return
        from .server import make_server
        try:
            self.server = make_server(p, port=0)
        except SystemExit as exc:
            self.say(f"Stopped: {exc}")
            return
        self.server_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.say(f"Scoring app running at {self.server_url} (keep this window open while scoring)")
        webbrowser.open(self.server_url)

    def _unblind(self):
        p = self._need_project()
        if not p:
            return
        if not self.finished.get():
            messagebox.showinfo("wormscore", "Tick 'I have finished scoring' first. Unblinding reveals genotypes.")
            return
        from .unblind import unblind
        anyway = messagebox.askyesno("wormscore", "Unblind even if fewer images are scored than the project plan says?"
                                     "\n\n(Choosing Yes is recorded in log.md.)")
        self._run("unblind", lambda: unblind(p, finished=True, anyway=anyway, log=self.say))

    def _open_folder(self):
        p = Path(self.project.get())
        if p.is_dir():
            os.startfile(p) if hasattr(os, "startfile") else webbrowser.open(p.as_uri())

    def _edit_config(self):
        p = Path(self.project.get()) / "config.json"
        if p.exists():
            os.startfile(p) if hasattr(os, "startfile") else webbrowser.open(p.as_uri())

    def _close(self):
        if self.server is not None:
            self.server.shutdown()
        self.destroy()


def main():
    Launcher().mainloop()


if __name__ == "__main__":
    main()
