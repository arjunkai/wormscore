"""Local scoring app. Listens only on 127.0.0.1, so nothing leaves this computer.

Serves the blind image set and saves scores and drawn regions. It never reads the
key/ folder and never sends brightness numbers to the page, so the numbers cannot
influence the calls while scoring.
"""
import csv
import datetime as dt
import json
import re
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import config as C
from . import measure as M

STATIC = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8",
                "icon.svg": "image/svg+xml",
                "icon-192.png": "image/png", "icon-512.png": "image/png",
                "manifest.webmanifest": "application/manifest+json"}
CODE = re.compile(r"^W\d{4,6}$")
LOCAL_HOSTS = ("127.0.0.1", "localhost")
ROI_FIELDS = ["code", "region", "source", "area_um2", "peak_raw", "peak_filtered", "mean_mip",
              "background_mip", "peak_minus_bg", "mean_mip_minus_bg", "best_z", "n_z",
              "best_z_at_stack_edge", "mean_best_plane", "background_best_plane", "mean_best_minus_bg",
              "integrated_best_minus_bg", "stack_sum_minus_bg", "saturated_px_mip", "ring_px", "user_bg_mip",
              "user_bg_best_plane", "mean_mip_minus_user_bg", "mean_best_minus_user_bg"]


def score_fields(cfg):
    sc = cfg["scoring"]
    fields = ["code", "saved", "usable", "unusable_reason", "count_this_image", "cells_seen"]
    for i in range(1, len(sc["neurons"]) + 1):
        fields.append(f"n{i}_call")
        if sc["sides"]:
            fields.append(f"n{i}_side")
        fields += [f"n{i}_{q['id']}" for q in sc["neuron_questions"]]
    return fields + ["confidence", "display_preset_when_saved", "flag_answers", "notes"]


def safe_cell(v):
    """Stop spreadsheet programs from running text typed into the app as a formula."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def roi_mask(roi, h, w, cands):
    if roi["type"] == "candidate":
        return cands[f"arr_{roi['rank'] - 1}"]
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    if roi["type"] == "ellipse":
        cx, cy, rx, ry = roi["cx"], roi["cy"], roi["rx"], roi["ry"]
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=1)
    else:
        d.polygon([tuple(p) for p in roi["points"]], fill=1)
    return np.array(im, bool)


class Project:
    def __init__(self, path):
        self.path = Path(path)
        self.cfg = C.load(self.path)
        self.blind = self.path / "blind"
        self.scoring = self.path / "scoring"
        self.lock = threading.Lock()
        self.last_seen = time.time()   # the desktop app closes only after the page has gone quiet
        if not self.blind.is_dir():
            raise SystemExit(f"No blind/ folder in {self.path}. Run 'wormscore prepare' first.")

    def load(self, name, default):
        p = self.scoring / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def save(self, name, obj):
        self.scoring.mkdir(exist_ok=True)
        tmp = self.scoring / (name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        tmp.replace(self.scoring / name)

    def meta(self, code):
        return json.loads((self.blind / code / "meta.json").read_text())

    def save_score(self, code, body):
        body["saved"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scores = self.load("scores.json", {})
        scores[code] = body
        self.save("scores.json", scores)
        fields = score_fields(self.cfg)

        def flat(c, s):
            row = {**s, "code": c, "flag_answers": json.dumps(s.get("flag_answers", {}))}
            return {k: safe_cell(v) for k, v in row.items()}
        with open(self.scoring / "scores.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(flat(c, scores[c]) for c in sorted(scores))
        hist = self.scoring / "score_history.csv"
        new = not hist.exists()
        with open(hist, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(flat(code, body))
        return body["saved"]

    def measure_rois(self, code, rois):
        meta = self.meta(code)
        d = self.blind / code
        raw = np.stack([M.unpack16(np.array(Image.open(d / "planes" / f"z{z:02d}.png"))) for z in range(meta["n_z"])])
        filt = M.median_filter_stack(raw, self.cfg["measurement"]["median_filter_px"])
        h, w = raw.shape[1:]
        cands = np.load(d / "candidates.npz")
        masks = {r["label"]: roi_mask(r, h, w, cands) for r in rois}
        cells = {k: m for k, m in masks.items() if k != "background" and m.any()}
        exclude = np.zeros((h, w), bool)
        for m in cells.values():
            exclude |= m
        user_bg = masks.get("background")
        rows = []
        for label, m in cells.items():
            r = M.measure_region(raw, filt, m, exclude, meta["pixel_um"], meta["saturation_value"], self.cfg)
            if user_bg is not None and user_bg.any():
                bz = r["best_z"]
                r["user_bg_mip"] = float(np.median(raw.max(0)[user_bg]))
                r["user_bg_best_plane"] = float(np.median(raw[bz][user_bg]))
                r["mean_mip_minus_user_bg"] = round(r["mean_mip"] - r["user_bg_mip"], 2)
                r["mean_best_minus_user_bg"] = round(r["mean_best_plane"] - r["user_bg_best_plane"], 2)
            source = next((x.get("source", "drawn") for x in rois if x["label"] == label), "drawn")
            rows.append({"code": code, "region": label, "source": source, **r})
        return rows

    def save_rois(self, code, rois):
        all_rois = self.load("rois.json", {})
        all_rois[code] = rois
        self.save("rois.json", all_rois)
        meas = self.load("roi_measurements.json", {})
        meas[code] = self.measure_rois(code, rois) if rois else []
        self.save("roi_measurements.json", meas)
        with open(self.scoring / "roi_measurements.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=ROI_FIELDS, extrasaction="ignore")
            w.writeheader()
            for c in sorted(meas):
                w.writerows(meas[c])


def make_handler(project):
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
            self.send_header("Cache-Control", "no-store" if ctype.startswith("text") else "max-age=3600")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            """Only answer requests addressed to this computer. A web page on another site that
            tries to reach the app through a DNS trick arrives with a different Host header."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in LOCAL_HOSTS

        def do_GET(self):
            if not self.local_request():
                return self.send_error(403)
            project.last_seen = time.time()
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if parts == ["api", "ping"]:
                return self.send_json({"ok": True})
            if not parts:
                return self.send_file(STATIC / "index.html", "text/html; charset=utf-8")
            if parts[0] == "static" and len(parts) == 2 and parts[1] in STATIC_FILES:
                return self.send_file(STATIC / parts[1], STATIC_FILES[parts[1]])
            if parts[0] == "favicon.ico":
                return self.send_file(STATIC / "icon.svg", "image/svg+xml")
            if parts[0] == "img" and 3 <= len(parts) <= 4 and CODE.match(parts[1]) \
                    and all(re.fullmatch(r"[A-Za-z0-9_.]+", p) and ".." not in p for p in parts[2:]):
                p = (project.blind / parts[1]).joinpath(*parts[2:])
                if p.suffix == ".png" and p.is_file() and project.blind.resolve() in p.resolve().parents:
                    return self.send_file(p, "image/png")
            if parts == ["api", "config"]:
                sc = project.cfg["scoring"]
                return self.send_json({"project_name": project.cfg["project_name"],
                                       "marker": project.cfg["marker"]["label"], "scoring": sc,
                                       "min_scored": project.cfg["unblinding"]["min_scored_worms"]})
            if parts == ["api", "worms"]:
                scores, rois = project.load("scores.json", {}), project.load("rois.json", {})
                worms = []
                for d in sorted(project.blind.iterdir()):
                    if not (d / "meta.json").exists():
                        continue
                    meta = json.loads((d / "meta.json").read_text())
                    answered = scores.get(d.name, {}).get("flag_answers", {})
                    worms.append({"code": d.name, "scored": d.name in scores, "has_roi": bool(rois.get(d.name)),
                                  "unanswered_flags": len([f for f in meta["flags"] if f["id"] not in answered])})
                return self.send_json({"worms": worms})
            if parts[:2] == ["api", "worm"] and len(parts) == 3 and CODE.match(parts[2]) \
                    and (project.blind / parts[2]).is_dir():
                code = parts[2]
                return self.send_json({"meta": project.meta(code), "score": project.load("scores.json", {}).get(code),
                                       "rois": project.load("rois.json", {}).get(code, [])})
            self.send_error(404)

        def do_POST(self):
            # Saves must come from the app's own page: a local Host, a JSON body (other sites
            # cannot send one without a permission check this server never grants), and, if the
            # browser says where the request came from, that origin must be this server.
            origin = self.headers.get("Origin")
            if not self.local_request() or self.headers.get("Content-Type", "").split(";")[0] != "application/json" \
                    or (origin and origin.split("://")[-1].rsplit(":", 1)[0] not in LOCAL_HOSTS):
                return self.send_error(403)
            parts = [p for p in self.path.split("/") if p]
            if len(parts) != 4 or parts[:2] != ["api", "worm"] or not CODE.match(parts[2]) \
                    or not (project.blind / parts[2]).is_dir():
                return self.send_error(404)
            code, what = parts[2], parts[3]
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except json.JSONDecodeError:
                return self.send_error(400)
            with project.lock:
                if what == "score":
                    return self.send_json({"ok": True, "saved": project.save_score(code, body)})
                if what == "rois":
                    project.save_rois(code, body.get("rois", []))
                    # Only confirm the save; the numbers stay in the CSV, not on screen.
                    return self.send_json({"ok": True, "n_regions": len(body.get("rois", []))})
            self.send_error(404)

    return Handler


def make_server(project_path, port=8765):
    project = Project(project_path)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(project))
    srv.project = project
    return srv


def serve(project_path, port=8765, open_browser=True, log=print):
    srv = make_server(project_path, port)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    log(f"Scoring app running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
