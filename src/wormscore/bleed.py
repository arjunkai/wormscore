"""Bleed-through check (1.20.0) from single-color control images, for projects with an identity channel.

Bleed-through (crosstalk) is light from one fluorophore recorded in the other's channel. With two
markers in the same cells it cannot be told apart from real signal in the project's own images (both
channels are bright in the same places), so it is measured on controls that carry one marker only,
imaged at the project's settings (single-label controls, as Bolte and Cordelieres 2006, J Microsc
224:213, recommend before any two-channel analysis):

- identity-only controls: the identity marker's light in the reporter channel. This one matters for
  results: it would add to every measured reporter brightness.
- reporter-only controls: the reporter's light in the identity channel, which would make the identity
  channel brighter where the reporter is bright, so bright cells would be easier to find.

For each control image, every plane is used. The background of each channel is its median; pixels
with signal are those in the source channel above background + half the range to its 99.9th
percentile, leaving out pixels saturated in either channel. The bleed-through is the least-squares
slope, through the origin, of the target channel above its background against the source channel
above its background, as a percentage. The median over images is the project's value.

Nothing here touches the blind set or the scores: the controls are other animals, read only.
"""
import datetime as dt
import json
from pathlib import Path

from . import config as C
from .files import append_log, write_csv

KINDS = {
    # kind: (source channel role, target channel role)
    "identity-only": ("IDENTITY", "MARKER"),
    "reporter-only": ("MARKER", "IDENTITY"),
}
RESULTS = "bleed_through.json"
LOG_TITLE = "bleed-through check"
MAX_PIXELS = 4_000_000       # a sample of the signal pixels is enough for a slope, and keeps it quick


def _names(cfg, kind):
    rep, ident = cfg["marker"]["label"], cfg["identity"]["label"]
    return (ident, rep) if kind == "identity-only" else (rep, ident)


def control_files(folder, cfg):
    """(ImageJ source settings or None, image files in folder) as Scan data decides it: ImageJ TIFFs
    only if the project's images are ImageJ TIFFs. Also used for flat-field reference images."""
    from . import readers as R
    source = next((s for s in cfg["sources"] if s.get("imagej_tiff")), None)
    def usable(p):
        role = R.file_role(p)
        return role == "raw image" or (role == R.NOT_USED and source is not None
                                       and p.suffix.lower() in (".tif", ".tiff") and R.is_imagej_stack(p))
    return source, sorted(p for p in Path(folder).rglob("*") if p.is_file() and usable(p))


def slope(src, tgt):
    """(bleed-through %, signal pixels used) for one image: (z, y, x) arrays of the same shape."""
    import numpy as np
    src = src.astype(np.float64, copy=False)
    tgt = tgt.astype(np.float64, copy=False)
    sat = (src >= src.max()) | (tgt >= tgt.max())
    bs, bt = np.median(src), np.median(tgt)
    top = np.percentile(src, 99.9)
    if top <= bs:
        return None, 0
    mask = (src > bs + 0.5 * (top - bs)) & ~sat
    n = int(mask.sum())
    if n < 20:
        return None, n
    x, y = src[mask] - bs, tgt[mask] - bt
    if x.size > MAX_PIXELS:
        idx = np.random.default_rng(0).choice(x.size, MAX_PIXELS, replace=False)
        x, y = x[idx], y[idx]
    return float(100 * (x * y).sum() / (x * x).sum()), n


def measure(project, folder, kind, log=print):
    """Measure the controls in folder; save, log and return the result."""
    from . import readers as R
    import numpy as np
    project, folder = Path(project), Path(folder)
    if kind not in KINDS:
        raise SystemExit("Unknown kind of control.")
    cfg = C.load(project)
    if not cfg.get("identity"):
        raise SystemExit("This project has no identity channel, so there is no bleed-through to check.")
    # only the project's own image folders are refused (their images would be read as controls)
    from .flatfield import check_folder
    check_folder(project, folder, cfg, "control images")
    src_role, tgt_role = KINDS[kind]
    src_name, tgt_name = _names(cfg, kind)
    source, files = control_files(folder, cfg)
    rows, skipped = [], []
    for path in files:
        rel = str(path.relative_to(folder))
        try:
            images = R.images_in(path, cfg, source)
        except Exception as exc:       # one unreadable file is reported, not fatal
            skipped.append(f"{rel}: could not be read ({type(exc).__name__})")
            continue
        for img in images:
            where = f"{rel} {img.get('location_in_file', '')}".strip()
            roles = img["channel_roles"]
            si, ti = R.pick(roles, src_role), R.pick(roles, tgt_role)
            if si is None or ti is None:
                skipped.append(f"{where}: the {src_name} or {tgt_name} channel could not be identified from metadata")
                continue
            pct, n = slope(R.read(img, si), R.read(img, ti))
            if pct is None:
                skipped.append(f"{where}: too little {src_name} signal to measure")
                continue
            rows.append({"image": where, "bleed_through_pct": round(pct, 2), "signal_pixels": n})
            log(f"{where}: {pct:.2f}%")
    if not rows:
        raise SystemExit("No control image could be measured. " + ("; ".join(skipped[:3]) if skipped else
                                                                   "The folder has no images wormscore can read."))
    vals = sorted(r["bleed_through_pct"] for r in rows)
    med = float(np.median(vals))
    res = {"kind": kind, "from": src_name, "into": tgt_name, "folder": folder.name, "images": len(rows),
           "median_pct": round(med, 2), "min_pct": vals[0], "max_pct": vals[-1], "skipped": len(skipped),
           "measured": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
    out = project / "outputs"
    out.mkdir(exist_ok=True)
    write_csv(out / f"bleed_through_{kind}.csv", rows)
    saved = results(project)
    saved[kind] = res
    (out / RESULTS).write_text(json.dumps(saved, indent=1), encoding="utf-8")
    append_log(project, LOG_TITLE, [line(res)] + [f"Not measured: {s}" for s in skipped[:20]])
    log(line(res))
    return res


def line(res):
    return (f"{res['from']} into the {res['into']} channel, from {res['images']} {res['kind']} control "
            f"image{'s' if res['images'] != 1 else ''} (folder {res['folder']}): median {res['median_pct']}% "
            f"of the {res['from']} signal (range {res['min_pct']} to {res['max_pct']}%).")


def results(project):
    """The latest result of each kind, {kind: result}."""
    f = Path(project) / "outputs" / RESULTS
    try:
        got = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except ValueError:
        return {}
    return {k: v for k, v in got.items() if k in KINDS and isinstance(v, dict)}


def methods_sentence(project):
    """For the Methods draft, or "" if no check was made (the Methods then read as before)."""
    got = results(project)
    if not got:
        return ""
    parts = [f"{r['from']} into the {r['into']} channel was {r['median_pct']}% of the {r['from']} signal "
             f"(median of {r['images']} {r['kind']} control image{'s' if r['images'] != 1 else ''}, range "
             f"{r['min_pct']} to {r['max_pct']}%)" for r in (got[k] for k in KINDS if k in got)]
    return ("Bleed-through was measured on single-label control images taken at the same settings, as the "
            "least-squares slope of the other channel against the labelled channel over pixels with signal "
            "(saturated pixels left out): " + "; ".join(parts) + ". ")
