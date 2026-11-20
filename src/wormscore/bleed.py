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
from .files import append_log, plural, write_csv

KINDS = {
    # kind: (source channel role, target channel role)
    "identity-only": ("IDENTITY", "MARKER"),
    "reporter-only": ("MARKER", "IDENTITY"),
}
RESULTS = "bleed_through.json"
LOG_TITLE = "bleed-through check"
MAX_PIXELS = 4_000_000       # a sample of the signal pixels is enough for a slope, and keeps it quick
VIF_MAX = 10.0               # the usual limit for collinear predictors (Kutner et al. 2005, Applied Linear Statistical Models)


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


def joint(sources, tgt):
    """Several identity channels at once (1.26.0, for NeuroPAL): (list of bleed-through % per source channel,
    signal pixels used, largest variance inflation factor), or (None, n, vif) when they cannot be told apart.

    All sources label the same cells, so a slope against one alone would also pick up the others' light.
    The target above its background is fitted to every source above its background together (least squares
    through the origin) over pixels with signal in any source, leaving out pixels saturated in any channel.
    The channels can only be separated where their patterns differ (NeuroPAL's color code differs from
    neuron to neuron); with a variance inflation factor above VIF_MAX the fit is not used."""
    import numpy as np
    srcs = [s.astype(np.float64, copy=False) for s in sources]
    tgt = tgt.astype(np.float64, copy=False)
    sat = tgt >= tgt.max()
    mask = np.zeros(tgt.shape, bool)
    for s in srcs:
        sat |= s >= s.max()
        bs, top = np.median(s), np.percentile(s, 99.9)
        if top > bs:
            mask |= s > bs + 0.5 * (top - bs)
    mask &= ~sat
    n = int(mask.sum())
    if n < 20 * len(srcs):
        return None, n, None
    X = np.stack([s[mask] - np.median(s) for s in srcs], axis=1)
    y = tgt[mask] - np.median(tgt)
    if X.shape[0] > MAX_PIXELS:
        idx = np.random.default_rng(0).choice(X.shape[0], MAX_PIXELS, replace=False)
        X, y = X[idx], y[idx]
    vif = uncentred_vif(X)
    if not np.isfinite(vif) or vif > VIF_MAX:
        return None, n, vif
    coef = np.linalg.lstsq(X, y, rcond=None)[0]
    return [float(100 * c) for c in coef], n, vif


def uncentred_vif(X):
    """The largest variance inflation factor of the columns of X for a fit through the origin: X'X scaled to a
    unit diagonal, inverted, largest diagonal entry (Belsley 1991, Conditioning Diagnostics). Centred
    correlations would describe a fit with an intercept and miss columns that share an offset. inf if a
    column is all zero or the columns are exactly dependent."""
    import numpy as np
    g = X.T @ X
    d = np.sqrt(np.diag(g))
    if not np.all(d > 0):
        return float("inf")
    try:
        v = float(np.max(np.diag(np.linalg.inv(g / np.outer(d, d)))))
    except np.linalg.LinAlgError:
        return float("inf")
    return v if np.isfinite(v) and v > 0 else float("inf")


def _joint_roles(cfg):
    """[(role, label)] of every identity channel, primary first, or [] without extra channels."""
    extra = cfg.get("identity_extra") or []
    if not extra:
        return []
    return [("IDENTITY", cfg["identity"]["label"])] + [(f"IDENTITY{k}", x["label"]) for k, x in enumerate(extra, start=2)]


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
    jroles = _joint_roles(cfg) if kind == "identity-only" else []
    jvals, jlost = [[] for _ in jroles], {"not_separable": 0, "missing": 0, "too_little": 0}
    for path in files:
        rel = str(path.relative_to(folder))
        try:
            # 1.26.20: read as the project's images were scanned (see flatfield._images)
            from .flatfield import zoom_from_scan
            if zoom_from_scan(project):
                images = R.images_in(path, cfg, source)
            else:
                with R.as_before():
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
            row = {"image": where, "bleed_through_pct": round(pct, 2), "signal_pixels": n}
            if jroles:
                idx = [R.pick(roles, r) for r, _ in jroles]
                if any(i is None for i in idx):
                    jlost["missing"] += 1
                else:
                    got, _, vif = joint([R.read(img, i) for i in idx], R.read(img, ti))
                    if got is None:
                        jlost["not_separable" if vif is not None else "too_little"] += 1
                    else:
                        for k, v in enumerate(got):
                            jvals[k].append(v)
                            row[f"joint_{jroles[k][1]}_pct"] = round(v, 2)
                    if vif is not None:
                        row["joint_max_vif"] = round(vif, 1) if vif != float("inf") else "inf"
            rows.append(row)
            log(f"{where}: {pct:.2f}%")
    if not rows:
        raise SystemExit("No control image could be measured. " + ("; ".join(skipped[:3]) if skipped else
                                                                   "The folder has no images wormscore can read."))
    vals = sorted(r["bleed_through_pct"] for r in rows)
    med = float(np.median(vals))
    res = {"kind": kind, "from": src_name, "into": tgt_name, "folder": folder.name, "images": len(rows),
           "median_pct": round(med, 2), "min_pct": vals[0], "max_pct": vals[-1], "skipped": len(skipped),
           "measured": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
    if jroles:
        res["joint"] = {"images": len(jvals[0]), **jlost, "channels": [
            {"from": label, "median_pct": round(float(np.median(v)), 2), "min_pct": round(min(v), 2),
             "max_pct": round(max(v), 2)} for (_, label), v in zip(jroles, jvals)] if jvals[0] else []}
    out = project / "outputs"
    out.mkdir(exist_ok=True)
    write_csv(out / f"bleed_through_{kind}.csv", rows)
    saved = results(project)
    saved[kind] = res
    (out / RESULTS).write_text(json.dumps(saved, indent=1), encoding="utf-8")
    append_log(project, LOG_TITLE, [line(res)] + ([joint_line(res)] if res.get("joint") else [])
               + [f"Not measured: {s}" for s in skipped[:20]])
    log(line(res))
    if res.get("joint"):
        log(joint_line(res))
    return res


def line(res):
    return (f"{res['from']} into the {res['into']} channel, from {res['images']} {res['kind']} control "
            f"image{'s' if res['images'] != 1 else ''} (folder {res['folder']}): median {res['median_pct']}% "
            f"of the {res['from']} signal (range {res['min_pct']} to {res['max_pct']}%).")


def joint_line(res):
    """All identity channels fitted together (1.26.0): one sentence for log.md and the home page."""
    j, into = res["joint"], res["into"]
    lost = [f"{plural(j[k], 'image')} {why}" for k, why in (
        ("not_separable", "where the channels were too alike to separate"),
        ("missing", "without every identity channel"), ("too_little", "with too little signal")) if j.get(k)]
    if not j["channels"]:
        return (f"All identity channels together into the {into} channel: not measured ("
                + ("; ".join(lost) or "no image") + ").")
    parts = [f"{c['from']} {c['median_pct']}%" for c in j["channels"]]
    return (f"All identity channels fitted together into the {into} channel, from {plural(j['images'], 'image')}: "
            + ", ".join(parts) + " of each channel's signal (medians)" + (f"; left out: {'; '.join(lost)}" if lost else "") + ".")


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
    text = ("Bleed-through was measured on single-label control images taken at the same settings, as the "
            "least-squares slope of the other channel against the labelled channel over pixels with signal "
            "(saturated pixels left out): " + "; ".join(parts) + ". ")
    j = got.get("identity-only", {}).get("joint")
    if j and j["channels"]:
        text += (f"Because the identity channels label the same cells, the {got['identity-only']['into']} channel of the same "
                 "control images was also fitted to all of them together (multiple least squares through the origin; "
                 f"images with an uncentred variance inflation factor above {VIF_MAX:g} left out): "
                 + ", ".join(f"{c['from']} {c['median_pct']}%" for c in j["channels"])
                 + f" of each channel's signal (medians of {plural(j['images'], 'image')}). ")
    elif j:
        why = ("the identity channels were too alike to separate" if j.get("not_separable") else
               "not every identity channel was found" if j.get("missing") else "there was too little signal")
        text += f"Their joint bleed-through was not measured: in these control images {why}. "
    return text
