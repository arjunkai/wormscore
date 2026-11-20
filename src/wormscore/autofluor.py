"""Gut autofluorescence check (1.22.0), after unblinding, for images with a channel that carries no label.

C. elegans gut granules (lysosome-related organelles) fluoresce across the spectrum: brightest in blue
(a DAPI-type filter set), and also in green, where they overlap GFP (Pincus et al. 2016, Aging 8:889;
Coburn et al. 2013, PLoS Biol 11:e1001613). A GFP-labelled neuron is bright in the reporter channel only.
So in images that also recorded a channel with no label (role "other": for example blue), a scored region
that is bright there too may be a gut granule rather than the cell.

For each counted image: each outline the scorer saved (or, without outlines, the first suggested cell) is
measured in that channel, in the reporter's sharpest plane for the region and the planes either side of
it (to allow for a small depth shift between colours). Signal to noise = (unfiltered mean in the
region - median of the background ring) / robust SD of the ring (1.4826 x median absolute deviation),
the ring as for every measurement. A region at or above CUTOFF_SD is flagged "possibly gut
autofluorescence". Flags are reported, never used to change a call or leave an image out.

It runs only after unblinding: a per-image flag during scoring could hint at a genotype (autofluorescence
differs between strains and ages). It reads the original image files and writes
outputs/unblinded/autofluorescence.csv, log.md and a paragraph of the Methods draft. Nothing else changes.
"""
import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path

from . import config as C
from .files import append_log, plural, read_csv, write_csv

CUTOFF_SD = 3.0
BLUE = re.compile(r"dapi|hoechst|blue|bfp|cfp|405|uv|auto", re.I)
RESULTS = "autofluorescence.csv"
SUMMARY = "autofluorescence.json"
LOG_TITLE = "gut autofluorescence check"
METHODS_HEAD = "**Gut autofluorescence check.**"


def unlabelled_channel(img):
    """(index, name) of the channel to check in one image, or None: a channel with role OTHER, a blue one
    (by name) first."""
    roles = img["channel_roles"]
    names = (img.get("channel_names") or "").split("/") if isinstance(img.get("channel_names"), str) \
        else list(img.get("channel_names") or [])
    names += [""] * (len(roles) - len(names))
    other = [i for i, r in enumerate(roles) if r == "OTHER"]
    if not other:
        return None
    blue = [i for i in other if BLUE.search(names[i] or "")]
    i = (blue or other)[0]
    return i, names[i] or f"channel {i + 1}"


_available = {}


def available(project):
    """The name of the unlabelled channel most images have, or None (then the check is not offered).
    Kept until inventory.csv changes, since the home page asks every few seconds."""
    from .pipeline import load_images
    p = Path(project) / "outputs" / "inventory.csv"
    if not p.is_file():
        return None
    stamp = (str(p.resolve()), p.stat().st_mtime_ns)
    if stamp in _available:
        return _available[stamp]
    _available[stamp] = _available_now(project, load_images)
    return _available[stamp]


def _available_now(project, load_images):
    names = defaultdict(int)
    for img in load_images(project):
        ch = unlabelled_channel(img)
        if ch:
            names[ch[1]] += 1
    return max(names, key=names.get) if names else None


def _snr(plane, mask, ring):
    import numpy as np
    if not ring.any() or not mask.any():
        return None
    bg = plane[ring]
    sd = 1.4826 * float(np.median(np.abs(bg - np.median(bg))))
    if sd <= 0:
        return None
    return (float(plane[mask].mean()) - float(np.median(bg))) / sd


def check(project, log=print):
    """Measure every counted image's outlines in its unlabelled channel; save, log and return the summary."""
    import numpy as np
    from PIL import Image
    from . import measure as M
    from . import readers as R
    from .pipeline import load_images
    from .server import roi_mask
    project = Path(project)
    out = project / "outputs" / "unblinded"
    if not (out / "summary.txt").is_file():
        raise SystemExit("The gut autofluorescence check runs after Unblind (a flag per image could otherwise "
                         "hint at a genotype while scoring).")
    cfg = C.load(project)
    key = [r for r in read_csv(project / "key" / "key.csv") if not r.get("repeat_of")]
    merged = {r["code"]: r for r in read_csv(out / "merged.csv")} if (out / "merged.csv").is_file() else {}
    counted = {c for c, r in merged.items()
               if r.get("score_count_this_image") == "yes" and r.get("score_usable") == "yes"
               and not r.get("left_out_after_scoring")}
    images = {(i["local_path"], i["location_in_file"]): i for i in load_images(project)}
    rois = json.loads((project / "scoring" / "rois.json").read_text(encoding="utf-8")) \
        if (project / "scoring" / "rois.json").is_file() else {}
    rows, skipped, channel_names = [], defaultdict(int), defaultdict(int)
    todo = [k for k in key if k["code"] in counted]
    for n, k in enumerate(todo, start=1):
        img = images.get((k["local_path"], k["location_in_file"]))
        ch = unlabelled_channel(img) if img else None
        if ch is None:
            skipped["no channel without a label" if img else "image not in the last scan"] += 1
            continue
        d = project / "blind" / k["code"]
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        try:
            other = R.read(img, ch[0]).astype(float)      # unfiltered: filtering would shrink the noise SD
        except Exception as exc:                     # a file moved or unreadable: reported, not fatal
            skipped[f"could not be read ({type(exc).__name__})"] += 1
            continue
        marker = np.stack([M.unpack16(np.array(Image.open(d / "planes" / f"z{z:02d}.png")))
                           for z in range(meta["n_z"])]).astype(float)
        if other.shape != marker.shape:
            skipped["the channel is another size than the reporter"] += 1
            continue
        h, w = marker.shape[1:]
        cands = np.load(d / "candidates.npz")
        regions = [(r["label"], r.get("source", "drawn"), roi_mask(r, h, w, cands))
                   for r in rois.get(k["code"], []) if r.get("label") != "background"]
        if not regions and len(cands.files):
            regions = [("suggested cell 1", "suggested", cands["arr_0"])]
        exclude = np.zeros((h, w), bool)
        for _, _, m in regions:
            exclude |= m
        for label, source, mask in regions:
            if not mask.any():
                continue
            ring = M.background_ring(mask, exclude, meta["pixel_um"], cfg)
            best = int(np.argmax(marker[:, mask].mean(1)))
            planes = range(max(0, best - 1), min(marker.shape[0], best + 2))
            snrs = [s for s in (_snr(other[z], mask, ring) for z in planes) if s is not None]
            if not snrs:
                skipped["background ring without measurable noise"] += 1
                continue
            snr = max(snrs)
            rows.append({"code": k["code"], "genotype": k["genotype"], "system": k["system"], "region": label,
                         "source": source, "channel": ch[1], "sharpest_plane": best,
                         "signal_to_noise": round(snr, 2), "possibly_gut_autofluorescence": snr >= CUTOFF_SD})
            channel_names[ch[1]] += 1
        log(f"{k['code']} ({n}/{len(todo)})")
    if not rows:
        raise SystemExit("No region could be checked: " + ("; ".join(f"{plural(v, 'image')}: {k}" for k, v in skipped.items())
                                                           or "no counted image has outlines or suggested cells."))
    write_csv(out / RESULTS, rows)
    by_geno = defaultdict(lambda: [0, 0])
    for r in rows:
        by_geno[r["genotype"]][0] += 1
        by_geno[r["genotype"]][1] += r["possibly_gut_autofluorescence"]
    summ = {"channel": max(channel_names, key=channel_names.get), "regions": len(rows),
            "flagged": sum(r["possibly_gut_autofluorescence"] for r in rows), "cutoff_sd": CUTOFF_SD,
            "by_genotype": {g: {"regions": v[0], "flagged": v[1]} for g, v in sorted(by_geno.items())},
            "skipped": dict(skipped), "checked": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
    (out / SUMMARY).write_text(json.dumps(summ, indent=1), encoding="utf-8")
    lines = [line(summ)] + [f"Warning: {v} {'image was' if v == 1 else 'images were'} not checked: {k}."
                            for k, v in skipped.items()]
    append_log(project, LOG_TITLE, lines)
    _methods_paragraph(project, summ)
    for x in lines:
        log(x)
    return summ


def line(s):
    per = "; ".join(f"{g} {v['flagged']} of {v['regions']}" for g, v in s["by_genotype"].items())
    return (f"{s['flagged']} of {plural(s['regions'], 'region')} also bright in the {s['channel']} channel (signal at least "
            f"{s['cutoff_sd']} times the background noise): possibly gut autofluorescence. By genotype: {per}. "
            f"Details: outputs/unblinded/{RESULTS}.")


def summary(project):
    f = Path(project) / "outputs" / "unblinded" / SUMMARY
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    except ValueError:
        return None


def methods_sentence(project):
    """The Methods paragraph, or "" if the check was not run (the Methods then read as before)."""
    s = summary(project)
    if not s:
        return ""
    per = "; ".join(f"{g}, {v['flagged']} of {v['regions']}" for g, v in s["by_genotype"].items())
    return (f"{METHODS_HEAD} Gut granules fluoresce across the spectrum, brightest with a DAPI-type "
            "filter set (Pincus et al. 2016, Aging 8:889; Coburn et al. 2013, PLoS Biol 11:e1001613), "
            "while the reporter does not. Each scored "
            f"region was therefore also measured in the {s['channel']} channel, which carries no label, in the "
            "reporter's sharpest plane for the region and the planes either side of it, without filtering: the "
            "background-subtracted mean divided by the robust SD of the background ring. Regions at or above "
            f"{s['cutoff_sd']} were flagged as possibly gut autofluorescence ({s['flagged']} of {s['regions']} "
            f"regions; {per}). Flags were reported, not used to change calls or leave images out.")


def _methods_paragraph(project, s):
    """Put the paragraph into the Methods draft written at unblinding, where Unblind again would put it
    (before the table of images), replacing an earlier one."""
    f = Path(project) / "outputs" / "unblinded" / "methods_text.md"
    if not f.is_file():
        return
    paras = [p for p in f.read_text(encoding="utf-8").split("\n\n") if not p.startswith(METHODS_HEAD)]
    at = next((i for i, p in enumerate(paras) if p.startswith(("**Limitations.**", "## Images per genotype"))), len(paras))
    paras.insert(at, methods_sentence(project))
    f.write_text("\n\n".join(paras), encoding="utf-8")
