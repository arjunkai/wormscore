"""Browse images: look at images with their file names and genotypes, in the scoring viewer,
without scoring them. Nothing is saved from it except a record of what was viewed.

When it works (browse_state):
- before the blind set is built: every scanned image, read from its file when opened (nothing
  is written to disk). Each image viewed is recorded (log.md, outputs/browsed_images.csv), and
  the next Build and the Methods draft say so, because seeing genotypes before the scoring
  criteria are fixed should be on record (ARRIVE 2.0 item 5; Jost and Waters 2019).
- after unblinding: coded images with their code, genotype and file, plus the images left out.
- never between Build blind set and Unblind, and never in a second scorer's package. The lock
  is checked here on every request, not only by the home screen's button.

A separate server from the scoring one (server.py), which never sends genotypes or file names.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import re
import threading
import time
from collections import OrderedDict
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

from . import __version__
from . import config as C
from .files import append_log, read_csv
from .server import LOCAL_HOSTS, STATIC, STATIC_FILES, LocalServer, heavy_imports, measure_regions

ITEM = re.compile(r"^[WF]\d{4,6}$")        # W = a code (after unblinding), F = a scanned image
BROWSED = "browsed_images.csv"
BROWSED_FIELDS = ["viewed", "session", "genotype", "source", "file", "location_in_file", "relative_path", "system"]
LOCKED_WHY = ("Browsing is off while this project is scored blind: it shows file names and genotypes. "
              "It opens again after Unblind.")
PACKAGE_WHY = "This is a second scorer's package. It is scored blind, so browsing is off."
CACHE_SIZE = 3


def browse_state(project):
    """('before' | 'after' | 'locked' | 'none', why). Reads file names and times only."""
    p = Path(project)
    from .scorers import package_info
    if package_info(p):
        return "locked", PACKAGE_WHY
    key = p / "key" / "key.csv"
    if not key.is_file():
        if not (p / "outputs" / "inventory.csv").is_file():
            return "none", "Run Scan data first."
        return "before", ""
    summary = p / "outputs" / "unblinded" / "summary.txt"
    # A rebuild or added images after unblinding rewrite the key, which locks browsing again.
    if summary.is_file() and summary.stat().st_mtime_ns >= key.stat().st_mtime_ns:
        return "after", ""
    return "locked", LOCKED_WHY


def browsed_summary(project):
    """(images, sessions, first day, last day) viewed before the build, or None if never browsed."""
    f = Path(project) / "outputs" / BROWSED
    if not f.is_file():
        return None
    rows = [r for r in read_csv(f) if r.get("viewed")]
    if not rows:
        return None
    days = sorted(r["viewed"][:10] for r in rows)
    images = {(r["source"], r["relative_path"], r["location_in_file"]) for r in rows}
    return len(images), len({r["session"] for r in rows}), days[0], days[-1]


def browse_log_line(project):
    """The line Build adds to log.md when images were browsed before it (none otherwise)."""
    b = browsed_summary(project)
    if not b:
        return []
    n, k, first, last = b
    span = first if first == last else f"{first} to {last}"
    return [f"Viewed openly before this build (Browse images, {span}): {n} image{'s' if n != 1 else ''} in "
            f"{k} session{'s' if k != 1 else ''}, with file names and genotypes visible "
            f"(outputs/{BROWSED})."]


class Browse:
    def __init__(self, path):
        self.path = Path(path)
        self.cfg = C.load(self.path)
        self.lock = threading.Lock()
        self.last_seen = time.time()
        self.can_start = False
        self.id = hashlib.sha256(("browse:" + str(self.path.resolve()).lower()).encode()).hexdigest()[:16]
        self.session = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.viewed = set()          # images recorded in this session
        self.cache = OrderedDict()   # rendered images, newest last (memory only)
        state, why = browse_state(self.path)
        if state in ("locked", "none"):
            raise SystemExit(why)
        self.items = self.list_items()

    # ------------------------------------------------------------ what can be browsed
    def state(self):
        return browse_state(self.path)[0]

    def list_items(self):
        from .pipeline import include_reason, load_images
        cfg, p = self.cfg, self.path
        images = load_images(p) if (p / "outputs" / "inventory.csv").is_file() else []
        where = lambda r: (r["local_path"], r["location_in_file"])
        items = []
        if self.state() == "after":
            key = sorted(read_csv(p / "key" / "key.csv"), key=lambda r: r["code"])
            by_place = {where(s): s for s in images}
            for r in key:
                items.append({"id": r["code"], "coded": True, "row": r, "img": by_place.get(where(r)),
                              "note": f"Hidden repeat of {r['repeat_of']}" if r.get("repeat_of") else ""})
            coded = {where(r) for r in key}
            left = {where(r): r["reason"] for r in read_csv(p / "outputs" / "excluded.csv")
                    if r.get("local_path")} if (p / "outputs" / "excluded.csv").is_file() else {}
            rest = [s for s in images if where(s) not in coded]
        else:
            left, rest = {}, images
        width = max(4, len(str(len(rest))))
        for n, s in enumerate(rest, start=1):
            reason = left.get(where(s)) or include_reason(s, cfg)
            items.append({"id": f"F{n:0{width}d}", "coded": False, "row": s, "img": s,
                          "note": f"Left out: {reason}" if reason else ""})
        return {it["id"]: it for it in items}

    def summary(self, it):
        r = it["row"]
        return {"code": it["id"], "coded": it["coded"], "genotype": r.get("genotype", ""),
                "file": r.get("file", ""), "location": r.get("location_in_file", ""), "note": it["note"]}

    def info(self, it):
        r, s = it["row"], it["img"] or it["row"]
        n_z = int(s.get("n_z") or 0)
        z = float(s.get("z_step_um") or 0)
        rows = [("Code", it["id"]) if it["coded"] else None,
                ("Genotype", r.get("genotype", "")),
                ("File", r.get("file", "")),
                ("Image in file", r.get("location_in_file", "")),
                ("Path", s.get("relative_path", "")) if s.get("relative_path") != r.get("file") else None,
                ("Microscope", r.get("system", "")),
                ("Settings group", r.get("settings_group", "")) if it["coded"] else None,
                ("Settings", r.get("settings", "")),
                ("Objective", r.get("objective", "")),
                ("Pixel size", f"{float(s.get('pixel_um') or 0):.3f} µm"),
                ("Planes", f"{n_z}, {z:g} µm apart" if z else str(n_z)),
                ("Acquired", r.get("acquired", "")),
                ("Included", it["note"] or ("Yes" if not it["coded"] else
                                            "Yes" if r.get("counted_by_default") == "True" else
                                            "Yes (another image of this field is counted by default)"))]
        return [{"k": k, "v": str(v)} for k, v in filter(None, rows) if str(v) != ""]

    # ------------------------------------------------------------ images
    def get(self, item_id):
        """meta, image version and the files' bytes (or a folder) for one image."""
        it = self.items[item_id]
        if it["coded"]:
            d = self.path / "blind" / item_id
            meta = json.loads((d / "meta.json").read_text())
            v = hashlib.sha256(f"{self.id}:{(d / 'meta.json').stat().st_mtime_ns}".encode()).hexdigest()[:12]
            return {"meta": meta, "dir": d, "v": v}
        with self.lock:
            if item_id in self.cache:
                self.cache.move_to_end(item_id)
                return self.cache[item_id]
        got = self.render(item_id, it["img"])
        with self.lock:
            self.cache[item_id] = got
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
        return got

    def render(self, item_id, s):
        np, Image, _, M = heavy_imports()
        from .pipeline import read_for_viewer, saturation_for
        if s.get("marker_channel") is None:
            raise ValueError(f"The {self.cfg['marker']['label']} channel of this image could not be identified, "
                             "so it cannot be shown.")
        cfg = self.cfg
        v = read_for_viewer(s, cfg)
        raw, mip = v["raw"], v["mip"]
        sat = saturation_for(s, cfg)

        def png(a):
            buf = io.BytesIO()
            Image.fromarray(a).save(buf, format="PNG")
            return buf.getvalue()
        files = {f"planes/z{z:02d}.png": png(M.pack16(raw[z])) for z in range(raw.shape[0])}
        files["mip.png"] = png(M.pack16(mip))
        if v["bf"] is not None:
            files["brightfield.png"] = png(v["bf"])
        if v["has_identity"]:
            for z in range(v["id_planes"].shape[0]):
                files[f"identity/z{z:02d}.png"] = png(v["id_planes"][z])
            files["identity_mip.png"] = png(v["id_mip"])
        # Build sets one Enhanced range per microscope from every included image. Before the build
        # there is none yet, so Enhanced uses the same formula on this image alone (and says so).
        lo = round(float(np.median(mip)))
        hi = round(float(np.percentile(mip, 99.9)))
        cands = []
        for rank, c in enumerate(v["cands"], start=1):
            ys, xs = np.nonzero(c["mask"])
            cands.append({"rank": rank, "y": c["y"], "x": c["x"],
                          "r": float(max(np.hypot(ys - c["y"], xs - c["x"]).max(), 2.0))})
        meta = {"code": item_id, "n_z": int(raw.shape[0]), "width": int(raw.shape[2]), "height": int(raw.shape[1]),
                "pixel_um": s["pixel_um"], "z_step_um": s["z_step_um"],
                "display_um_per_px": cfg["display"]["display_um_per_px"], "frame_px": cfg["display"]["frame_px"],
                "has_brightfield": v["bf"] is not None, "full_range": [0, sat], "enhanced_range": [lo, max(hi, lo + 1)],
                "enhanced_this_image": True, "saturation_value": sat, "flags": [], "candidates": cands,
                "same_field_as": [], "counted_by_default": True}
        if cfg.get("identity"):
            meta["has_identity"] = v["has_identity"]
        masks = {f"arr_{i}": c["mask"] for i, c in enumerate(v["cands"])}
        return {"meta": meta, "files": files, "masks": masks, "v": hashlib.sha256(f"{self.id}:{self.session}".encode()).hexdigest()[:12]}

    def file(self, item_id, name):
        got = self.get(item_id)
        if "files" in got:
            return got["files"].get(name)
        p = (got["dir"] / name)
        if p.suffix == ".png" and p.is_file() and got["dir"].resolve() in p.resolve().parents:
            return p.read_bytes()
        return None

    def measure(self, item_id, rois):
        np, Image, _, M = heavy_imports()
        got = self.get(item_id)
        meta = got["meta"]
        if "files" in got:
            raw = np.stack([M.unpack16(np.array(Image.open(io.BytesIO(got["files"][f"planes/z{z:02d}.png"]))))
                            for z in range(meta["n_z"])])
            cands = got["masks"]
        else:
            d = got["dir"]
            raw = np.stack([M.unpack16(np.array(Image.open(d / "planes" / f"z{z:02d}.png"))) for z in range(meta["n_z"])])
            cands = np.load(d / "candidates.npz")
        return measure_regions(item_id, raw, cands, meta, rois, self.cfg)

    def saved(self, item_id):
        """After unblinding: the scorer's saved answers and regions for a code (read only)."""
        if not self.items[item_id]["coded"]:
            return None, []
        def load(name):
            try:
                return json.loads((self.path / "scoring" / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        return load("scores.json").get(item_id), load("rois.json").get(item_id, [])

    # ------------------------------------------------------------ the record
    def record_view(self, item_id):
        """Before the build, each image opened is written down (once per session)."""
        it = self.items[item_id]
        if it["coded"] or self.state() != "before" or item_id in self.viewed:
            return
        s = it["img"]
        out = self.path / "outputs"
        out.mkdir(exist_ok=True)
        with self.lock:
            if not self.viewed:
                append_log(self.path, "browse images", [
                    f"Software: wormscore {__version__}",
                    "Images viewed with file names and genotypes visible, before the blind set was built "
                    f"(each image viewed is listed in outputs/{BROWSED}).",
                ])
            self.viewed.add(item_id)
            f = out / BROWSED
            new = not f.exists()
            with open(f, "a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=BROWSED_FIELDS, extrasaction="ignore")
                if new:
                    w.writeheader()
                w.writerow({"viewed": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "session": self.session,
                            **{k: s.get(k, "") for k in BROWSED_FIELDS[2:]}})


def make_handler(b):
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

        def send_bytes(self, data, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in LOCAL_HOSTS

        def refused(self):
            """Another project's window, or the project is now being scored blind."""
            sent = self.headers.get("X-Wormscore-Project")
            if sent and sent != b.id:
                self.send_json({"ok": False, "error": "other project"}, 409)
                return True
            if b.state() not in ("before", "after"):
                self.send_json({"ok": False, "error": LOCKED_WHY}, 423)
                return True
            return False

        def failed(self, exc):
            msg = str(exc) if isinstance(exc, (ValueError, SystemExit)) else type(exc).__name__
            self.send_json({"ok": False, "error": f"wormscore could not do that ({msg})."}, 500)

        def do_GET(self):
            try:
                self.get()
            except (ConnectionError, BrokenPipeError):
                pass
            except (Exception, SystemExit) as exc:
                self.failed(exc)

        def do_POST(self):
            try:
                self.post()
            except (ConnectionError, BrokenPipeError):
                pass
            except (Exception, SystemExit) as exc:
                self.failed(exc)

        def get(self):
            if not self.local_request():
                return self.send_error(403)
            b.last_seen = time.time()
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if parts == ["api", "ping"]:
                return self.send_json({"ok": True})
            if not parts:
                return self.send_bytes((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if parts[0] == "static" and len(parts) == 2 and parts[1] in STATIC_FILES:
                return self.send_bytes((STATIC / parts[1]).read_bytes(), STATIC_FILES[parts[1]])
            if parts[0] == "favicon.ico":
                return self.send_bytes((STATIC / "icon.svg").read_bytes(), "image/svg+xml")
            if self.refused():
                return
            if parts == ["api", "config"]:
                cfg = b.cfg
                return self.send_json({"mode": "browse", "stage": b.state(), "project_name": cfg["project_name"],
                                       "project_id": b.id, "can_start": b.can_start,
                                       "marker": cfg["marker"]["label"], "marker_color": C.marker_color(cfg),
                                       "identity": ({"label": cfg["identity"]["label"], "color": C.identity_color(cfg)}
                                                    if cfg.get("identity") else None),
                                       "scoring": cfg["scoring"], "min_scored": 0})
            if parts == ["api", "worms"]:
                return self.send_json({"worms": [b.summary(it) for it in b.items.values()]})
            if len(parts) == 3 and parts[:2] == ["api", "worm"] and parts[2] in b.items:
                got = b.get(parts[2])
                b.record_view(parts[2])
                score, rois = b.saved(parts[2])
                return self.send_json({"meta": got["meta"], "img_v": got["v"], "info": b.info(b.items[parts[2]]),
                                       "score": score, "rois": rois, "draft": None})
            if parts[0] == "img" and 3 <= len(parts) <= 4 and parts[1] in b.items \
                    and all(re.fullmatch(r"[A-Za-z0-9_.]+", p) and ".." not in p for p in parts[2:]):
                data = b.file(parts[1], "/".join(parts[2:]))
                if data is not None:
                    return self.send_bytes(data, "image/png")
            self.send_error(404)

        def post(self):
            origin = self.headers.get("Origin")
            if not self.local_request() or self.headers.get("Content-Type", "").split(";")[0] != "application/json" \
                    or (origin and origin.split("://")[-1].rsplit(":", 1)[0] not in LOCAL_HOSTS):
                return self.send_error(403)
            if self.refused():
                return
            b.last_seen = time.time()
            parts = [p for p in self.path.split("/") if p]
            # Measuring is the only thing this page can ask for; nothing is saved.
            if len(parts) != 4 or parts[:2] != ["api", "worm"] or parts[2] not in b.items or parts[3] != "measure":
                return self.send_error(404)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except json.JSONDecodeError:
                return self.send_error(400)
            rois = body.get("rois")
            if not isinstance(rois, list) or len(rois) > 20:
                return self.send_error(400)
            return self.send_json({"ok": True, "rows": b.measure(parts[2], rois)})

    return Handler


def make_server(project_path, port=8765):
    b = Browse(project_path)
    srv = LocalServer(("127.0.0.1", port), make_handler(b))
    srv.project = b
    return srv


def serve(project_path, port=8765, open_browser=True, log=print):
    srv = make_server(project_path, port)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    log(f"Browse images running at {url}  (Ctrl+C to stop). File names and genotypes are visible.")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
