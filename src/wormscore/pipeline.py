"""The two batch steps: inventory (what is there) and prepare (blind set + measurements).

inventory -> outputs/manifest.csv   every file in every source folder, with its role
          -> outputs/inventory.csv  one row per raw image: metadata, channel identity, flags
prepare   -> blind/<code>/          images for scoring, named only by code
          -> key/key.csv            code -> paths, location in file, checksum, genotype, settings
          -> outputs/excluded.csv, outputs/auto_measurements.csv, outputs/display_ranges.csv
Both append what they did, with every parameter, to log.md.
"""
import csv
import datetime as dt
import json
import os
import random
import re
import secrets
import shutil
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__
from . import config as C
from . import measure as M
from . import readers as R
from .files import append_log, now, read_csv, write_csv  # noqa: F401  (re-exported)
from .browse import browse_log_line
from .rescan import CHANNEL_COLUMNS, CHANNELS_FILE, channel_record, next_letter, same_reading, scan_changes  # noqa: F401  (re-exported)
from .scoreform import form_log_lines
from .inclusion import INCOMPLETE, below_minimum, include_reason  # noqa: F401  (moved in 1.26.23; re-exported)


INVENTORY_FIELDS = [
    "source", "genotype", "system", "format", "file", "location_in_file", "relative_path",
    "local_path", "original_path", "file_sha256", "objective", "n_z", "n_t", "width", "height", "pixel_um",
    "z_step_um", "bits", "channel_names", "channel_roles", "marker_channel", "bf_channel", "settings", "zoom",
    "acquired", "acquired_day", "stage_x", "stage_y", "flags", "reader_kind", "reader_index", "reader_axes",
    "pixel_sha256", "scanned_with", "model", "scan_settings",
    # 1.26.24: NIS-Elements' names of a Nikon image's camera values (labels only; readers._nikon_camera_fields)
    "camera_fields",
]


def software_versions():
    import czifile, liffile, oiffile, oirfile, PIL, scipy, tifffile  # noqa: E401
    from importlib.metadata import PackageNotFoundError, version
    try:
        nd2 = version("nd2")          # read from the package record; the reader itself loads only for .nd2 files
    except PackageNotFoundError:
        nd2 = "not installed"
    return {"wormscore": __version__, "python": sys.version.split()[0], "numpy": np.__version__,
            "scipy": scipy.__version__, "pillow": PIL.__version__, "tifffile": tifffile.__version__,
            "liffile": liffile.__version__, "czifile": czifile.__version__, "oiffile": oiffile.__version__,
            "oirfile": oirfile.__version__, "nd2": nd2}


# ------------------------------------------------------------------ inventory

def inventory_fields(cfg):
    """The inventory columns; identity_channel only when an identity channel is configured, so other
    projects' tables keep exactly their columns."""
    if not cfg.get("identity"):
        return INVENTORY_FIELDS
    i = INVENTORY_FIELDS.index("bf_channel") + 1
    return INVENTORY_FIELDS[:i] + ["identity_channel"] + INVENTORY_FIELDS[i:]


def inventory(project, log=print, progress=None):
    project = Path(project)
    from .scorers import refuse_in_package
    refuse_in_package(project, "Scanning")
    from .flatfield import scanned_day
    cfg = C.load(project)
    ident = cfg.get("identity")
    # 1.26.8: progress covers both halves of the scan, the file checksums and then the pixel checksum of
    # each image. It used to cover the first only and stood at 100% for most of the run.
    manifest, images = R.scan(cfg, log=log, progress=(lambda done, total: progress(done, 2 * total)) if progress else None)
    rows, by_pixels = [], {}
    weights = [max(1, int(img.get("n_z") or 1) * int(img.get("width") or 1) * int(img.get("height") or 1)) for img in images]
    read_so_far, to_read = 0, sum(weights) or 1
    for img, weight in zip(images, weights):
        roles = img["channel_roles"]
        mi, bi = R.pick(roles, "MARKER"), R.pick(roles, "BF")
        ii = R.pick(roles, "IDENTITY") if ident else None
        flags = list(img["flags"])
        if mi is None:
            flags.append(f"{cfg['marker']['label']} channel UNRESOLVED")
        if ident and ii is None:
            flags.append(f"{ident['label']} identity channel UNRESOLVED")
        if bi is None:
            flags.append("no brightfield channel identified")
        try:
            pixels = R.pixel_sha256(img)
        except Exception as exc:  # unreadable pixels are reported, not fatal here
            pixels = ""
            flags.append(f"pixels could not be read ({type(exc).__name__})")
        if pixels in by_pixels:
            flags.append(f"identical pixels to {by_pixels[pixels]}")
        elif pixels:
            by_pixels[pixels] = f"{img['relative_path']} {img['location_in_file']}"
        read_so_far += weight
        if progress:
            progress(to_read + read_so_far, 2 * to_read)
        rk = img["_reader"]
        rows.append({**{k: img.get(k, "") for k in INVENTORY_FIELDS},
                     # 1.26.18: the day as the flat-field correction reads it (also day first with AM/PM)
                     "acquired_day": scanned_day(img.get("acquired", "")),
                     "channel_names": "/".join(img["channel_names"]), "channel_roles": "/".join(roles),
                     "marker_channel": "" if mi is None else mi, "bf_channel": "" if bi is None else bi,
                     **({"identity_channel": "" if ii is None else ii} if ident else {}),
                     "stage_x": "" if img["stage_x"] is None else round(img["stage_x"], 2),
                     "stage_y": "" if img["stage_y"] is None else round(img["stage_y"], 2),
                     "flags": flags, "reader_kind": rk[0], "reader_index": rk[2],
                     "reader_axes": rk[3] if len(rk) > 3 else "", "pixel_sha256": pixels,
                     # 1.26.20: the version that scanned, so a later reading rule can tell an inventory from before it
                     "scanned_with": __version__})
    # Settings that differ from the most common value on the same microscope.
    for system in {r["system"] for r in rows}:
        same = [r for r in rows if r["system"] == system]
        for key in ("objective", "settings", "bits", "pixel_um"):
            mode = Counter(str(r[key]) for r in same).most_common(1)[0][0]
            for r in same:
                if str(r[key]) != mode:
                    r["flags"].append(f"{key} differs from most images on this microscope")
    # 1.26.23: scan speed, pinhole and accumulation change how bright an image is but are not in the settings
    # text (Leica: readers.LEICA_SCAN). An image whose values differ from most images with the same settings
    # is flagged, so the settings group can be checked; the group itself does not change.
    # Each value is compared on its own, among the images that record it, with the value a strict majority of
    # them has (a value an image does not record, or a group split evenly, is not a difference; found by review).
    # 1.26.27: a detector's operating mode ("Counting mode", "Analog mode") is one setting, compared as such
    def scan_parts(text):
        return {"mode" if x.endswith(" mode") else re.sub(r"[\d.]+", "#", x): x for x in text.split("; ") if x}
    for combo in sorted({(r["system"], r["settings"]) for r in rows if r.get("scan_settings")}):
        same = [(r, scan_parts(r["scan_settings"])) for r in rows
                if (r["system"], r["settings"]) == combo and r.get("scan_settings")]
        most = {}
        for kind in {k for _, parts in same for k in parts}:
            values = Counter(parts[kind] for _, parts in same if kind in parts)
            value, n = values.most_common(1)[0]
            if n * 2 > sum(values.values()):
                most[kind] = value
        for r, parts in same:
            theirs = [v for k, v in parts.items() if k in most and v != most[k]]
            if theirs:
                usual = "; ".join(most[k] for k in parts if k in most and parts[k] != most[k])
                r["flags"].append(f"scan settings differ from most images with the same settings ({', '.join(theirs)} "
                                  f"here; most: {usual.replace('; ', ', ')})")
    for r in rows:
        r["flags"] = "; ".join(r["flags"])
    write_csv(project / "outputs" / "manifest.csv", manifest)
    write_csv(project / "outputs" / "inventory.csv", rows, inventory_fields(cfg))
    roles = Counter(m["role"] for m in manifest)
    rule = ("Channel rule: marker = channel whose metadata matches the configured excitation line, emission "
            "band, dye or channel name (and Leica LUT, if set); if several match, the one channel named by a "
            "configured dye; brightfield = transmitted-light channel. Otherwise UNRESOLVED.")
    if ident:
        rule += (f" Identity channel ({ident['label']}) = a channel that is not the marker and matches the identity "
                 "settings by the same rule.")
    append_log(project, "inventory", [
        f"Software: {json.dumps(software_versions())}",
        f"{len(manifest)} files scanned; roles: {dict(roles)}",
        f"{len(rows)} raw images listed in outputs/inventory.csv; "
        f"{sum(1 for r in rows if r['flags'])} with flags",
        rule,
    ])
    log(f"{len(manifest)} files, {len(rows)} raw images. See outputs/manifest.csv and outputs/inventory.csv.")
    return rows


# ------------------------------------------------------------------ prepare

def load_images(project):
    """Image records from inventory.csv, ready for reading."""
    out = []
    for r in read_csv(Path(project) / "outputs" / "inventory.csv"):
        img = dict(r)
        for k in ("n_z", "n_t", "width", "height", "bits"):
            img[k] = int(r[k])
        for k in ("pixel_um", "z_step_um"):
            img[k] = float(r[k] or 0)
        for k in ("stage_x", "stage_y"):
            img[k] = float(r[k]) if r[k] != "" else None
        for k in ("marker_channel", "bf_channel"):
            img[k] = int(r[k]) if r[k] != "" else None
        img["identity_channel"] = int(r["identity_channel"]) if r.get("identity_channel", "") != "" else None
        img["channel_roles"] = r["channel_roles"].split("/")
        # extra identity channels (1.23.0): IDENTITY2, IDENTITY3, IDENTITY4 in the roles, set only by projects with them
        if any(x.startswith("IDENTITY") and x[8:].isdigit() for x in img["channel_roles"]):
            img["identity_extra_channels"] = [R.pick(img["channel_roles"], f"IDENTITY{k}") for k in range(2, 2 + 3)]
        img["_reader"] = (r["reader_kind"], r["local_path"], int(r["reader_index"]), r["reader_axes"])
        out.append(img)
    return out


def drop_identical(images, cfg):
    """The same image saved inside two files (identical pixels) is kept once: the copy whose path
    contains the first matching duplicate_preference text, otherwise the shortest path."""
    def rank(s):
        pref = next((i for i, t in enumerate(cfg["duplicate_preference"]) if t in s["local_path"]), 99)
        return pref, len(s["local_path"]), s["local_path"], s["location_in_file"]
    groups = {}
    for s in images:
        groups.setdefault(s.get("pixel_sha256") or id(s), []).append(s)
    keep, copies = [], []
    for members in groups.values():
        members.sort(key=rank)
        keep.append(members[0])
        for s in members[1:]:
            copies.append({k: s[k] for k in ("system", "genotype", "original_path", "local_path", "location_in_file")}
                          | {"reason": f"identical pixels to {members[0]['relative_path']} "
                                       f"{members[0]['location_in_file']} (same image saved in two files)"})
    return [s for s in images if any(s is k for k in keep)], copies


def same_field_groups(images, tol_um):
    """Images in the same file at the same stage position are the same field imaged again.
    Images without a stage position each get their own group."""
    groups, next_id = {}, 1
    for s in images:
        for t in images:
            if t is s or id(t) not in groups or s["stage_x"] is None or t["stage_x"] is None:
                continue
            if t["local_path"] == s["local_path"] and abs(t["stage_x"] - s["stage_x"]) <= tol_um \
                    and abs(t["stage_y"] - s["stage_y"]) <= tol_um:
                groups[id(s)] = groups[id(t)]
                break
        else:
            groups[id(s)] = next_id
            next_id += 1
    return groups


REPEAT_GAP = 0.15     # a hidden repeat is at least this share of the set away from its original


def pick_repeats(images, fraction, rng):
    """Images to show a second time: the given share of each genotype's images (at least one),
    drawn from images that are counted by default and have no other image of the same field,
    so a repeat carries no flag or default that would tell it apart."""
    sizes = Counter(s["field_group"] for s in images)
    by_geno = {}
    for s in images:
        by_geno.setdefault(s["genotype"], []).append(s)
    picked = []
    for geno in sorted(by_geno):
        members = by_geno[geno]
        ok = sorted((s for s in members if s["counted_by_default"] and sizes[s["field_group"]] == 1),
                    key=lambda s: (s["local_path"], s["location_in_file"]))
        picked += rng.sample(ok, min(len(ok), max(1, round(fraction * len(members)))))
    return picked


def spaced_order(images, repeats, rng):
    """Shuffled images with each repeat inserted at a random place at least REPEAT_GAP of the
    set away from its original (and from every earlier repeat's original)."""
    order = list(images)
    rng.shuffle(order)
    gap = max(2, round(REPEAT_GAP * (len(images) + len(repeats))))
    start = list(order)

    def spaced(seq):
        pos = {id(s): i for i, s in enumerate(seq)}
        return all(abs(pos[id(r)] - pos[id(r["_orig"])]) >= gap for r in repeats if id(r) in pos)
    # 1.21.2: if they do not fit at the planned distance (a small set with many repeats), they are placed
    # as far apart as they fit, and Build warns with the distance reached, instead of stopping.
    while True:
        order, placed = list(start), True
        for r in repeats:
            for _ in range(2000):
                at = rng.randrange(len(order) + 1)
                trial = order[:at] + [r] + order[at:]
                if spaced(trial):
                    order = trial
                    break
            else:
                placed = False
                break
        if placed or gap <= 1:
            return order, gap
        gap -= 1


def _has_files(folder):
    return folder.is_dir() and any(folder.iterdir())


def missing_files(images):
    """The image files of these records that cannot be found (a drive not connected, a folder moved)."""
    return sorted({s["local_path"] for s in images if not Path(s["local_path"]).is_file()})


def _codes_in_use(project, prefix):
    """Codes that the key may not list but that are taken: those with a saved score or outlines.
    An Add new images that failed in 1.26.12 or earlier could leave coded images in blind/ with no key row;
    they could be scored. A new image must never get such a code (1.26.16, found by review). A folder
    left that way with nothing saved for it is removed when its code is given out."""
    import json
    import re
    taken = set()
    for name in ("scores.json", "rois.json"):
        try:
            taken |= set(json.loads((project / "scoring" / name).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass
    return [c for c in taken if re.fullmatch(re.escape(prefix) + r"\d+", str(c))]


def assign_codes(project, images, reassign, add=False, cfg=None, later=None, undo=None):
    """Random codes. An existing key is reused so codes never change while scoring.
    add: images not yet in the key get new codes after the highest existing one, shuffled
    among themselves with a new seed; existing codes are kept. Returns the images to render,
    with hidden repeats (records with "_orig", the image they repeat) after the images.
    later: a list that collects what this would change in the project (the old key set aside, the new
    seed, drafts set aside) as calls to make once the images are rendered, so a build that fails midway
    leaves the key as it was. Without it they are made here, as before."""
    do = later.append if later is not None else (lambda f: f())
    key_dir = project / "key"
    key_path = key_dir / "key.csv"
    if not key_path.exists():
        # 1.26.3: the key is the only link between codes and images. Without it a build would assign new
        # codes, and the saved scores (or a second scorer's package) would then belong to other images.
        if _has_files(project / "scoring"):
            raise SystemExit("Scores are saved for this blind set, but its key is not in the key folder. Put "
                             "key.csv back in the project's key folder, then build again. Building without it "
                             "would give the images new codes, and the saved scores would belong to other images.")
        if _has_files(project / "scorers"):
            raise SystemExit("A second-scorer package was made from this blind set, but its key is not in the key "
                             "folder. Put key.csv back in the project's key folder, then build again.")
    key_dir.mkdir(exist_ok=True)
    ident = lambda s: (s["local_path"], s["location_in_file"])
    if add:
        if not key_path.exists():
            raise SystemExit("There is no existing key to add images to. Run prepare without --add-new-images.")
        all_codes = [r["code"] for r in read_csv(key_path)]
        old = {(r["local_path"], r["location_in_file"]): r["code"] for r in read_csv(key_path)
               if not r.get("repeat_of")}
        dropped = set(old) - {ident(s) for s in images}
        if dropped:
            raise SystemExit(f"{len(dropped)} image(s) in the existing key no longer pass the inclusion rules or "
                             "are missing. Adding images never removes coded images; restore them or the rules.")
        new = [s for s in images if ident(s) not in old]
        if not new:
            # 1.26.16: an Add new images that was cut off after it wrote the key (the computer shut down)
            lost = [c for c in all_codes if not (project / "blind" / c).is_dir()]
            if lost:
                raise SystemExit(f"{len(lost)} code{'s' if len(lost) != 1 else ''} in the key {'have' if len(lost) != 1 else 'has'} "
                                 "no images in the blind folder: an earlier Add new images was cut off. Choose "
                                 "Rebuild, keeping the current codes, to make them.")
            raise SystemExit("No new images: every included image already has a code.")
        for s in images:
            s["code"] = old.get(ident(s))
        seed = secrets.randbits(32)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        do(lambda: (key_dir / f"seed_added_{stamp}.txt").write_text(f"{seed}\n"))
        random.Random(seed).shuffle(new)
        prefix, width = all_codes[0][0], len(all_codes[0]) - 1
        start = max(int(c[1:]) for c in all_codes + _codes_in_use(project, prefix)) + 1
        for n, s in enumerate(new, start=start):
            s["code"] = f"{prefix}{n:0{width}d}"
            s["added_on"] = stamp
        return new
    if key_path.exists() and not reassign:
        rows = read_csv(key_path)
        old = {(r["local_path"], r["location_in_file"]): r["code"] for r in rows if not r.get("repeat_of")}
        if set(old) != {ident(s) for s in images}:
            raise SystemExit("The included images differ from the existing key, so codes would change.\n"
                             "If nothing has been scored yet, run prepare again with --reassign-codes.\n"
                             "If scoring has started, add the new images with --add-new-images "
                             "(existing codes are kept).")
        for s in images:
            s["code"] = old[ident(s)]
        # Hidden repeats already in the key keep their codes, whatever the setting says now.
        by_code = {s["code"]: s for s in images}
        repeats = [{**by_code[r["repeat_of"]], "code": r["code"], "_orig": by_code[r["repeat_of"]]}
                   for r in rows if r.get("repeat_of")]
        return list(images) + repeats
    scoring = project / "scoring"
    if reassign and scoring.exists() and any(scoring.iterdir()):
        raise SystemExit("scoring/ already has saved scores. Refusing to reassign codes.")
    stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"

    def set_aside():
        if key_path.exists():
            old_dir = key_dir / f"superseded_{stamp}"
            old_dir.mkdir()
            for f in ("key.csv", "seed.txt", "settings_groups.csv", "coded_on.txt", CHANNELS_FILE):
                if (key_dir / f).exists():
                    (key_dir / f).rename(old_dir / f)
        # Unsaved answers belong to the old codes; with new codes they would open on other images.
        drafts = project / "scoring_drafts" / "drafts.json"
        if drafts.exists():
            drafts.rename(drafts.with_name(f"superseded_{stamp}.json"))
    def put_back():
        """Undo set_aside and the new seed, when the new images could not be put in place."""
        old_dir = key_dir / f"superseded_{stamp}"
        (key_dir / "seed.txt").unlink(missing_ok=True)
        (key_dir / "coded_on.txt").unlink(missing_ok=True)
        if old_dir.is_dir():
            for f in ("key.csv", "seed.txt", "settings_groups.csv", "coded_on.txt", CHANNELS_FILE):
                if (old_dir / f).exists():
                    (old_dir / f).replace(key_dir / f)
            if not any(old_dir.iterdir()):
                old_dir.rmdir()
        drafts = project / "scoring_drafts" / "drafts.json"
        aside = drafts.with_name(f"superseded_{stamp}.json")
        if aside.exists() and not drafts.exists():
            aside.rename(drafts)
    do(set_aside)
    if undo is not None:
        undo.append(put_back)
    seed = secrets.randbits(32)
    do(lambda: (key_dir / "seed.txt").write_text(f"{seed}\n"))
    # 1.26.21: the day the codes were assigned, for the Methods draft. It was the seed file's modified time
    # only, which a copy to another disk or a restore from a backup can reset (found on a copy of a real
    # project: the draft gave the day of the copy)
    do(lambda: (key_dir / "coded_on.txt").write_text(f"{dt.date.today():%Y-%m-%d}\n"))
    fraction = C.repeat_fraction(cfg) if cfg else 0
    if not fraction:
        order = list(range(len(images)))
        random.Random(seed).shuffle(order)
        width = max(4, len(str(len(images))))
        for n, k in enumerate(order, start=1):
            images[k]["code"] = f"W{n:0{width}d}"
        return list(images)
    rng = random.Random(seed)
    repeats = [{**s, "_orig": s} for s in pick_repeats(images, fraction, rng)]
    order, gap = spaced_order(images, repeats, rng)
    for r in repeats:
        r["_gap"] = gap
    width = max(4, len(str(len(order))))
    for n, s in enumerate(order, start=1):
        s["code"] = f"W{n:0{width}d}"
    return list(images) + repeats


INTERACTION_LOG = "Interaction test set before scoring: "     # written by wormscore 0.3 to 0.4
PLAN_LOG = "Analysis plan set before scoring: "


def interaction_log_lines(cfg):
    """The analysis plan (tests, definitions, direction, comparisons), recorded so unblinding can
    check that it was chosen before scoring."""
    from .analysis import ref_log_lines
    plan = C.analysis_plan(cfg)
    # 1.15.0: the reference genotype too, which decides which pairs are tested
    return ([PLAN_LOG + json.dumps(plan, sort_keys=True)] if plan else []) + ref_log_lines(cfg)


def identity_log_lines(cfg):
    """What the identity channel did, for log.md (nothing when none is configured)."""
    ident = cfg.get("identity")
    if not ident:
        return []
    extra = cfg.get("identity_extra") or []
    more = ([f"Other identity channels, shown with it and never measured: {', '.join(x['label'] for x in extra)}, "
             "each scaled per image like the identity channel."] if extra else [])
    lo, hi = cfg["display"]["identity_contrast_percentiles"]
    where = (f"found on the {ident['label']} identity channel (median-filtered maximum projection) and the "
             f"{cfg['marker']['label']} reporter measured in those regions" if ident["spots_from"] == "identity"
             else f"found on the {cfg['marker']['label']} reporter, as without an identity channel")
    return [f"Identity channel: {ident['label']}, shown during scoring for finding cells, never measured; displayed "
            f"per image from the {lo}th to the {hi}th percentile of its maximum projection, so its brightness "
            f"cannot hint at the genotype. Suggested cells {where}. Images without it: "
            f"{'left out (identity.required)' if ident['required'] else 'kept, scored on the reporter alone'}."] + more


def saturation_for(img, cfg):
    return cfg["saturation_value"].get(img["system"], 2 ** img["bits"] - 1)


def read_for_viewer(s, cfg, corr=None):
    """Read one image and make what the viewer shows and measures: the marker stack (raw and
    median-filtered), its projection, brightfield, the identity channel and the suggested cells.
    Build blind set saves these as the coded image; Browse images shows them without saving.
    corr: the project's flat-field correction (1.21.0), applied to the marker stack before anything
    else, or None (the stack as recorded, as before)."""
    mc, ident = cfg["measurement"], cfg.get("identity")
    raw = R.read(s, s["marker_channel"])
    sat_mask = None
    if corr is not None:
        # pixels at the detector's maximum, found before correction (corrected values can be above or below it)
        sat_mask = (raw >= saturation_for(s, cfg)).any(0)
        raw = corr.apply(raw, s)
    filt = M.median_filter_stack(raw, mc["median_filter_px"])
    mip = raw.max(0)
    bf = M.brightfield_plane(R.read(s, s["bf_channel"]), cfg) if s["bf_channel"] is not None else None
    # Identity channel: shown for display only (8-bit, scaled per image), and, with spots_from
    # "identity", the place where suggested cells are found. The reporter is then measured in
    # those regions, so a dim or missing reporter cell is still found and measured.
    spot_mip = filt.max(0)
    has_identity = ident is not None and s.get("identity_channel") is not None
    id_planes = id_mip = None
    if has_identity:
        iraw = R.read(s, s["identity_channel"])
        if iraw.shape != raw.shape:
            raise SystemExit(f"{s['relative_path']} {s['location_in_file']}: the {ident['label']} identity channel "
                             f"has shape {iraw.shape}, the marker {raw.shape}.")
        id_planes, id_mip = M.identity_display(iraw, cfg)
        if ident["spots_from"] == "identity":
            spot_mip = M.median_filter_stack(iraw, mc["median_filter_px"]).max(0)
        del iraw
    # Extra identity channels (1.23.0, for example NeuroPAL): shown only, each scaled per image like the
    # identity channel, never measured and never used to find cells. None where an image lacks one.
    extra = []
    for k, _ in enumerate(cfg.get("identity_extra") or []):
        ci = (s.get("identity_extra_channels") or [None] * 3)[k]
        if ci is None:
            extra.append(None)
            continue
        xraw = R.read(s, ci)
        if xraw.shape != raw.shape:
            raise SystemExit(f"{s['relative_path']} {s['location_in_file']}: the {cfg['identity_extra'][k]['label']} "
                             f"channel has shape {xraw.shape}, the marker {raw.shape}.")
        extra.append(M.identity_display(xraw, cfg))
        del xraw
    cands = M.candidate_spots(spot_mip, s["pixel_um"], cfg)
    return {"raw": raw, "filt": filt, "mip": mip, "bf": bf, "has_identity": has_identity,
            "id_planes": id_planes, "id_mip": id_mip, "cands": cands, "sat_mask": sat_mask, "extra": extra}


def viewer_images(v):
    """The image files the viewer reads for one image, from what read_for_viewer returned: (name, a function
    that returns the pixels) for each, so a packed plane is in memory only while it is being encoded."""
    raw = v["raw"]
    out = []
    if v["sat_mask"] is not None:
        out.append(("saturated.png", lambda: v["sat_mask"].astype(np.uint8) * 255))
    out += [(f"planes/z{z:02d}.png", lambda z=z: M.pack16(raw[z])) for z in range(raw.shape[0])]
    out.append(("mip.png", lambda: M.pack16(v["mip"])))
    if v["bf"] is not None:
        out.append(("brightfield.png", lambda: v["bf"]))
    if v["has_identity"]:
        out += [(f"identity/z{z:02d}.png", lambda z=z: v["id_planes"][z]) for z in range(v["id_planes"].shape[0])]
        out.append(("identity_mip.png", lambda: v["id_mip"]))
    for k, x in enumerate(v["extra"], start=2):     # only in projects with extra identity channels
        if x is not None:
            out += [(f"identity{k}/z{z:02d}.png", lambda x=x, z=z: x[0][z]) for z in range(x[0].shape[0])]
            out.append((f"identity{k}_mip.png", lambda x=x: x[1]))
    return out


PNG_THREADS = min(8, os.cpu_count() or 1)


def encode_images(images, put):
    """put(name, picture) for each of viewer_images, several at a time on threads; returns what put
    returned, in the order given. Build blind set saves each picture as a PNG file, Browse images keeps
    the PNG's bytes. Each file is encoded exactly as it was one at a time (the same Pillow call, so the
    same bytes: a test compares them); only the waiting is shared, as Pillow's PNG encoder leaves the
    other threads free to run. On a 2048 x 2048 x 50 stack the planes took about 30 s of a build one at
    a time. An error (a full disk) is raised here once the files being written have ended, and no more
    are started."""
    Image.preinit()                 # Pillow's file formats, registered once before the threads start
    failed = threading.Event()

    def one(name, make):
        if failed.is_set():         # another file failed: start no more (a full disk)
            return None
        try:
            return put(name, Image.fromarray(make()))
        except BaseException:
            failed.set()
            raise

    pool = ThreadPoolExecutor(max_workers=PNG_THREADS)
    try:
        jobs = [pool.submit(one, *job) for job in images]
        for job in jobs:            # wait for all, then raise the first error in the order given
            exc = job.exception()
            if exc is not None:
                raise exc
        return [job.result() for job in jobs]
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


# 1.26.22: which channel of its file each coded image was built from (key/channels.csv; rescan.channel_record).
# A newer reading could name another channel the reporter (or the identity channel or brightfield); the Build
# card then says so before a rebuild (rescan.scan_changes), and the Build log names the codes it changes. In
# key/, not in key.csv, so merged.csv and every other table stay as before. A project built before 1.26.22 has
# no record until its next full build (adding images does not start one: it would cover only the new ones).


def channel_warning(ch, todo):
    """The Build log line naming the coded images this rebuild builds from other channels (rescan.scan_changes'
    channel lines, with or without a record), among the images it builds."""
    built = {s["code"] for s in todo}
    codes = sorted({c for cs in (ch.get("channel_codes") or {}).values() for c in cs} & built)
    if not codes:
        return []
    n = len(codes)
    return [f"Warning: {n} coded image{'s are' if n != 1 else ' is'} built from other channels than before, as Scan data "
            f"now reads {'them' if n != 1 else 'it'}: {', '.join(codes[:20])}{', ...' if n > 20 else ''}. "
            f"{'Those already scored look' if n != 1 else 'If it was already scored, it looks'} different now."]


BUILDING = "blind_building"     # the coded images of a build under way; moved into blind/ when all are made
PREVIOUS = "blind_previous"     # the blind set a rebuild replaces, for the moment of the exchange


def tidy_unfinished_build(project, log=lambda *a: None):
    """What a build that was cut off left behind (the computer shut down, the app was closed): its
    unfinished images are removed, and a blind set caught in the moment of the exchange is put back."""
    building, previous, blind = project / BUILDING, project / PREVIOUS, project / "blind"
    if previous.is_dir():
        if blind.is_dir():
            shutil.rmtree(previous, ignore_errors=True)     # the exchange was made; only the old copy was left
        else:
            previous.rename(blind)
            log("An earlier build did not finish. The blind set it was replacing is back in place.")
    if building.is_dir():
        shutil.rmtree(building)
        log("An earlier build did not finish. Its unfinished images were removed.")


def _rename(src, dst, tries=10):
    """One rename of a whole folder. On Windows a virus scanner can hold a file for a moment."""
    import time
    for n in range(tries):
        try:
            return src.rename(dst)
        except PermissionError:
            if n == tries - 1:
                raise
            time.sleep(0.3)


def _stopped(exc, run, add):
    """What to tell the user when a build fails after it began to make the coded images. The file is not
    named: next to the progress lines it would show which image a code is. Scan data names it."""
    if isinstance(exc, OSError) and exc.strerror:
        why = exc.strerror
    else:
        why = type(exc).__name__ if isinstance(exc, Exception) else str(exc)
    name = getattr(exc, "filename", None)
    if run["phase"] == "saving":
        return (f"The build stopped while its results were being saved ({why}). Close other programs that have the "
                "project's files open, then build again.")
    kept = ("No image was added: the blind set, key and scores are as they were." if add else
            "The blind set, key and scores are as they were." if run["had_blind"] else "No blind set was made.")
    if run["phase"] == "exchange":
        return (f"The build did not finish: the new coded images could not be put in place ({why}). {kept} Close "
                "other programs that have the project's files open, then build again.")
    if isinstance(exc, SystemExit):
        return f"{why} {kept}"
    if name and (BUILDING in Path(str(name)).parts or getattr(exc, "errno", None) == 28):
        return (f"The build did not finish: the coded images could not be written ({why}). {kept} Check the free "
                "space on the project's drive, then build again.")
    return (f"The build did not finish: an image could not be read ({why}). {kept} Scan data again to see which "
            "file, then build again.")


def prepare(project, reassign=False, add=False, log=print):
    """Build the blind set. add=True codes and renders only images not yet in the key, keeping
    every existing code, image, settings-group letter and display range.

    The coded images are made in blind_building/ and take the place of blind/ in one step when all are
    made; the key and the tables are written after that. A build that fails midway (a file that cannot
    be read, a full disk) used to leave a rebuilt project with part of its coded images, or an added
    batch that could not be added again; it now leaves the blind set, the key and the scores as they
    were, and says so. There is no resume: a failed build is run again from the start."""
    project = Path(project)
    run = {"phase": "", "had_blind": (project / "blind").is_dir()}
    try:
        return _build(project, reassign, add, log, run)
    except BaseException as exc:
        if run["phase"] in ("", "done"):
            raise                     # stopped by a check before anything was made: as before
        if run["phase"] in ("making", "exchange"):
            shutil.rmtree(project / BUILDING, ignore_errors=True)
        if isinstance(exc, (Exception, SystemExit)):
            raise SystemExit(_stopped(exc, run, add)) from exc
        raise


def _build(project, reassign, add, log, run):
    if add and reassign:
        raise SystemExit("Use either --add-new-images or --reassign-codes, not both.")
    from .scorers import refuse_in_package
    refuse_in_package(project, "Building the blind set")
    cfg = C.load(project)
    C.check(cfg)
    tidy_unfinished_build(project, log)
    images, excluded = [], []
    # 1.26.23: a coded image is not left out for its magnification when the codes are kept
    # (inclusion.include_reason); a rebuild with new codes applies the minimum to every image
    key_file = project / "key" / "key.csv"
    coded = set() if reassign or not key_file.is_file() else {
        (r["local_path"], r["location_in_file"]) for r in read_csv(key_file) if not r.get("repeat_of")}
    kept = ruled = 0
    for img in load_images(project):
        mine = (img["local_path"], img["location_in_file"]) in coded
        reason = include_reason(img, cfg, mine)
        # each counted on its own: an image can be both (found by review)
        if not reason and mine and C.exclude_rule_for(cfg, img["source"], img["relative_path"]):
            ruled += 1
        if not reason and mine and below_minimum(img["objective"], cfg):
            kept += 1
        if reason:
            excluded.append({k: img[k] for k in ("system", "genotype", "original_path", "local_path",
                                                  "location_in_file")} | {"reason": reason})
        else:
            images.append(img)
    images, copies = drop_identical(images, cfg)
    excluded += copies
    write_csv(project / "outputs" / "excluded.csv", excluded or [{"reason": "none excluded"}])
    if kept:
        log(f"Warning: {kept} coded image{'s are' if kept != 1 else ' is'} kept although {'their' if kept != 1 else 'its'} "
            f"objective is below the minimum ({cfg['inclusion']['min_objective_magnification']}x): the minimum decides "
            f"which images are coded, and a rebuild with new codes applies it.")
    if ruled:
        log(f"Warning: {ruled} coded image{'s match' if ruled != 1 else ' matches'} an exclusion rule added after "
            f"{'they were' if ruled != 1 else 'it was'} coded. {'They stay' if ruled != 1 else 'It stays'} in the blind set; "
            f"unblinding leaves {'them' if ruled != 1 else 'it'} out and lists {'them' if ruled != 1 else 'it'}. "
            f"Before scoring begins, a rebuild with new codes leaves {'them' if ruled != 1 else 'it'} out of the blind set.")
    if not images:
        raise SystemExit("No images passed the inclusion rules. See outputs/excluded.csv.")
    # only the images this run renders: when adding, coded images keep the planes they were scored on
    coded = {(r["local_path"], r["location_in_file"]) for r in read_csv(project / "key" / "key.csv")} \
        if add and (project / "key" / "key.csv").is_file() else set()
    render = [s for s in images if (s["local_path"], s["location_in_file"]) not in coded]
    # 1.26.3: checked before anything is written. A rebuild deletes blind/ first, so with the image drive
    # not connected it used to leave a project that was being scored without its coded images.
    gone = missing_files(render)
    if gone:
        raise SystemExit(f"{len(gone)} image file{'s' if len(gone) != 1 else ''} cannot be found, for example "
                         f"{gone[0]}. The blind set was not changed. Connect the drive, or correct the folder in Project "
                         "settings, then build again.")
    from . import flatfield as FF
    corr = FF.load(project, cfg)          # None unless the project turned the correction on (1.21.0)
    # A rebuild that keeps the codes once scoring has begun (or a second-scorer package exists) leaves
    # every coded image corrected as key.csv records it: slide images added since are used only for
    # images added after them. It used to correct every image again with all sessions, so images already
    # scored changed without a word. What cannot be kept (the setting or the measurements changed by hand)
    # is built as the setting now says, with a warning here and in log.md.
    ff_lines, ff_fixed = [], False
    key_file = project / "key" / "key.csv"
    if not add and not reassign and key_file.is_file() and FF.lock_reason(project):
        old_rows = [r for r in read_csv(key_file) if not r.get("repeat_of")]
        if corr is not None:
            ff_fixed = True
            ff_lines = FF.keep_lines(*corr.keep_coded(images, old_rows))
        else:
            ff_lines = FF.keep_lines(0, sum(1 for r in old_rows if (r.get("flat_field") or "not corrected")
                                            .startswith("not corrected") is False))
    if corr is not None:
        # never stops the build: images without their own session's slide images use another's, or are
        # used as recorded, each with a warning here, in log.md and in key.csv (column flat_field)
        ff_lines = corr.warnings(render, fixed=ff_fixed) + ff_lines
    for line in ff_lines:
        log(line)

    # 1.26.4: a genotype named in the analysis settings that no included image has (a typing mistake in
    # config.json) used to show only at unblinding, as "no comparison possible"
    un = cfg["unblinding"]
    named = {un["reference_genotype"]} | {g for c in un["comparisons"] for g in c} \
        | {un["interaction"].get(k) for k in ("single_a", "single_b", "double")}
    have = {s["genotype"] for s in images}
    plan_lines = [f"Warning: the analysis settings name the genotype {g!r}, which no included image has (genotypes "
                  f"here: {', '.join(sorted(have))}). Tests with it cannot run. Correct it in Project settings "
                  "before scoring." for g in sorted(x for x in named if x and x not in have)]
    for line in plan_lines:
        log(line)

    # 1.26.8: included images that Scan data flagged as read only in part, or with a placeholder pixel size
    for what, text in ((R.FIRST_ONLY, "have a dimension wormscore does not use (a tile scan, a lambda scan): only its "
                        "first entry is in the blind set. To score the others, export each as its own image."),
                       (R.NOT_CALIBRATED, "have no pixel size in the file and are read as 1 um per pixel, so the display "
                        "scale and the suggested cells are not reliable. Calibrate them in NIS-Elements and scan again.")):
        n = sum(what in str(s.get("flags") or "") for s in images)
        if n:
            plan_lines.append(f"Warning: {n} image{'s' if n != 1 else ''} {text}")
            log(plan_lines[-1])

    groups = same_field_groups(images, cfg["inclusion"]["same_field_tolerance_um"])
    for s in images:
        s["field_group"] = groups[id(s)]
    for g in set(groups.values()):
        members = [s for s in images if s["field_group"] == g]
        primary = max(members, key=lambda s: (s["n_z"], s["location_in_file"]))
        for s in members:
            s["counted_by_default"] = s is primary

    combos = sorted({(s["system"], s["settings"]) for s in images})
    letters = {}
    if add and (project / "key" / "settings_groups.csv").exists():
        letters = {(r["system"], r["settings"]): r["settings_group"]
                   for r in read_csv(project / "key" / "settings_groups.csv")}
        # 1.26.13: settings that a new scan only words differently (a Leica detector named as the file names
        # it) are the group they were, so added images join the coded images taken at the same settings.
        # The letter moves to the new wording; an added image that reads as the old wording did (a true
        # HyD at the gain of a PMT once written "HyD") is another setting and gets a letter of its own.
        for old, new in (scan_changes(project, cfg)["reworded"] or {}).items():
            if old in letters and new not in letters:
                letters[new] = letters.pop(old)
    for c in combos:
        if c not in letters:
            letters[c] = next_letter(letters)
    # 1.26.3: a rebuild that keeps the codes of a key with added batches keeps what Add new images
    # promised: the batch each image came in (added_on), the settings-group letters and each microscope's
    # display ranges. It used to work them out again over all images, so images already scored could look
    # different and the Methods draft no longer reported the batches.
    kept = {}
    if not add and not reassign and (project / "key" / "key.csv").is_file():
        kept = {r["code"]: r["added_on"] for r in read_csv(project / "key" / "key.csv") if r.get("added_on")}
    if kept and (project / "key" / "settings_groups.csv").exists():
        letters = {(r["system"], r["settings"]): r["settings_group"]
                   for r in read_csv(project / "key" / "settings_groups.csv")}
        for c in combos:
            if c not in letters:
                letters[c] = next_letter(letters)
    # 1.26.7: a scan that reads coded images differently than the key records (see scan_changes). A rebuild
    # that keeps the codes uses the new reading and says what it changes; adding images leaves coded
    # images as they are, so the new ones cannot share their groups until such a rebuild.
    scan_lines = []
    if not reassign and (project / "key" / "key.csv").is_file():
        ch = scan_changes(project, cfg)
        if add and ch["changed"]:
            scan_lines = ["Warning: Scan data now reads the microscope settings of coded images differently, so the "
                          "added images get settings groups of their own. Rebuild, keeping the current codes, to "
                          "put every image in the right group."]
        if not add and ch["letters"]:
            # 1.26.10: a rebuild that keeps the codes keeps each settings group's letter too, from the key.
            # Sorted order (as on a first build) could swap the letters on the rebuild after a warned change.
            letters = dict(ch["letters"])
        # 1.26.22: channel lines get a warning of their own (channel_warning), which names the codes
        said = [l for l, k in zip(ch["lines"], ch["kinds"]) if not k.startswith("channels") and k != "ruled"]
        if not add and said:
            scan_lines = ["Warning: this build changes what was recorded for coded images, because Scan data now reads "
                          f"them differently: {'; '.join(said)}. Scores are unchanged. Compare brightness only "
                          "within the new settings groups."]
        for line in scan_lines:
            log(line)
    key_changes = []          # the old key set aside, the new seed: made once the images are rendered
    key_undo = []
    todo = assign_codes(project, images, reassign, add, cfg, later=key_changes, undo=key_undo)
    repeats = [s for s in todo if "_orig" in s]
    todo = [s for s in todo if "_orig" not in s]
    if not reassign and not add and (project / "key" / "key.csv").is_file():   # 1.26.22: those built from other channels
        for line in channel_warning(scan_changes(project, cfg), todo):
            scan_lines.append(line)
            log(line)
    by_field = {}
    for s in images:
        by_field.setdefault(s["field_group"], []).append(s["code"])
    if add:
        # A new image of a field that already has a code is not counted by default.
        old_fields = {s["field_group"] for s in images if s not in todo}
        for s in todo:
            if s["field_group"] in old_fields:
                s["counted_by_default"] = False

    # The coded images are made in blind_building/ (named "blind" below). blind/ itself is not touched
    # until every image is made.
    blind = project / BUILDING
    blind.mkdir()
    run["phase"] = "making"
    mc = cfg["measurement"]
    ident = cfg.get("identity")
    auto_rows, key_rows, stats, pending = [], [], {}, []
    for n, s in enumerate(sorted(todo, key=lambda s: s["code"]), start=1):
        code, d = s["code"], blind / s["code"]
        (d / "planes").mkdir(parents=True)
        sat = saturation_for(s, cfg)
        v = read_for_viewer(s, cfg, corr)
        sat_mask = v["sat_mask"]          # None unless corrected: then saturation is read from this mask
        raw, filt, mip, bf_disp, cands, has_identity = v["raw"], v["filt"], v["mip"], v["bf"], v["cands"], v["has_identity"]
        px = s["pixel_um"]
        stats.setdefault(s["system"], []).append((float(np.median(mip)), float(np.percentile(mip, 99.9))))

        # the planes are saved several at a time (see encode_images); every file is as it was
        pictures = viewer_images(v)
        for folder in sorted({name.split("/")[0] for name, _ in pictures if "/" in name}):
            (d / folder).mkdir(exist_ok=True)
        encode_images(pictures, lambda name, picture: picture.save(d / name))
        s["_extra"] = [x is not None for x in v["extra"]]
        del v, pictures

        exclude = np.zeros(mip.shape, bool)
        for c in cands:
            exclude |= c["mask"]
        cand_meta = []
        for rank, c in enumerate(cands, start=1):
            m = M.measure_region(raw, filt, c["mask"], exclude, px, sat, cfg, sat_mask)
            auto_rows.append({"code": code, "candidate": rank, "y_px": c["y"], "x_px": c["x"],
                              "prominence": round(c["prominence"], 1),
                              **({"spots_found_on": ident["label"] if has_identity and ident["spots_from"] == "identity"
                                  else cfg["marker"]["label"]} if ident else {}), **m})
            ys, xs = np.nonzero(c["mask"])
            cand_meta.append({"rank": rank, "y": c["y"], "x": c["x"],
                              "r": float(max(np.hypot(ys - c["y"], xs - c["x"]).max(), 2.0)),
                              "best_z_at_stack_edge": m["best_z_at_stack_edge"],
                              "saturated": m["saturated_px_mip"] > 0})
        np.savez_compressed(d / "candidates.npz", *[c["mask"] for c in cands])

        flags = []
        others = [c for c in by_field[s["field_group"]] if c != code]
        if others:
            # (this text is in each coded image's meta.json, which the scorer sees: it stays as it is, so a
            # rebuild does not change images already scored)
            flags.append({"id": "repeat", "text": f"Same stage position as {', '.join(others)}: probably the same "
                          f"animal imaged again. Count only one image per animal. Default: "
                          f"{'count this one' if s['counted_by_default'] else 'do not count this one'}."})
        if cand_meta and cand_meta[0]["best_z_at_stack_edge"]:
            flags.append({"id": "edge", "text": "The brightest spot is sharpest in the first or last plane. "
                          "Part of a cell may be outside the stack. Page through the planes."})
        if any(c["saturated"] for c in cand_meta[:2]):
            flags.append({"id": "saturation", "text": "A suggested cell has pixels at the detector's maximum. "
                          "Its peak brightness is underestimated."})
        streaks = M.streak_rows(mip, sat, cfg)
        if streaks:
            flags.append({"id": "streak", "text": f"Horizontal scan-line artifact near row {streaks[0]}. "
                          "Ignore the line when judging brightness."})
        # mip and brightfield are already saved as PNGs (16-bit packing is lossless), so only
        # the paths are kept: holding every image in memory until the end ran out of RAM
        # on projects with many large images.
        if ident and not has_identity:
            flags.append({"id": "no_identity", "text": f"No {ident['label']} channel was recorded for this image, so "
                          f"it shows the {cfg['marker']['label']} channel only."})
        s["_has_identity"] = has_identity
        pending.append((s, d, flags, cand_meta, others, raw.shape, sat))

        extra = {}
        if mc["other_channel_qc"]:
            for ci, role in enumerate(s["channel_roles"]):
                if role == "OTHER":
                    omip = M.median_filter_stack(R.read(s, ci), 3).max(0)
                    extra[f"other_ch{ci}_max"] = int(omip.max())
                    extra[f"other_ch{ci}_p99_9"] = round(float(np.percentile(omip, 99.9)), 1)
        key_rows.append({
            "code": code, "genotype": s["genotype"], "source": s["source"], "system": s["system"],
            "original_path": s["original_path"], "local_path": s["local_path"],
            "location_in_file": s["location_in_file"], "file_sha256": s["file_sha256"], "file": s["file"],
            "objective": s["objective"], "settings_group": letters[(s["system"], s["settings"])],
            "settings": s["settings"], "pixel_um": px, "z_step_um": s["z_step_um"], "n_z": s["n_z"],
            "acquired": corr.recorded_acquired(s) if corr is not None else s["acquired"], "stage_x_um": "" if s["stage_x"] is None else round(s["stage_x"], 1),
            "stage_y_um": "" if s["stage_y"] is None else round(s["stage_y"], 1),
            "same_field_group": s["field_group"], "counted_by_default": s["counted_by_default"],
            **({"identity_channel_used": has_identity} if ident else {}),
            **(corr.key_columns(s, code=code) if corr is not None else {}),
            **({"added_on": s["added_on"]} if add else {"added_on": kept.get(code, "")} if kept else {}), **extra,
        })
        log(f"{code} ({n}/{len(todo)})")
        # Free this image's stacks before the next one is read, so two never sit in memory at once.
        del raw, filt, mip, cands, exclude, bf_disp

    # Fixed display ranges, one pair per microscope, computed over all included images.
    # When adding, microscopes already in the project keep their ranges, so images that
    # were already scored look exactly the same.
    ranges = {}
    if add or (kept and (project / "outputs" / "display_ranges.csv").is_file()):
        for r in read_csv(project / "outputs" / "display_ranges.csv"):
            ranges[r["system"]] = {"full": [int(float(r["standard_low"])), int(float(r["standard_high"]))],
                                   "enhanced": [int(float(r["enhanced_low"])), int(float(r["enhanced_high"]))]}
    for system, vals in stats.items():
        if system in ranges:
            continue
        sat = next(p[6] for p in pending if p[0]["system"] == system)
        lo = round(float(np.median([v[0] for v in vals])))
        hi = round(float(np.median([v[1] for v in vals])))
        ranges[system] = {"full": [0, sat], "enhanced": [lo, max(hi, lo + 1)]}

    for s, d, flags, cand_meta, others, shape, sat in pending:
        mip = M.unpack16(np.array(Image.open(d / "mip.png")))
        bf_disp = np.array(Image.open(d / "brightfield.png")) if (d / "brightfield.png").exists() else None
        r = ranges[s["system"]]
        for name in ("full", "enhanced"):
            g = M.window(mip, *r[name])
            Image.fromarray(g).save(d / f"marker_mip_{name}.png")
            if bf_disp is not None:
                Image.fromarray(M.overlay(g, bf_disp)).save(d / f"overlay_{name}.png")
        meta = {"code": s["code"], "n_z": int(shape[0]), "width": int(shape[2]), "height": int(shape[1]),
                "pixel_um": s["pixel_um"], "z_step_um": s["z_step_um"], "display_um_per_px": cfg["display"]["display_um_per_px"],
                "frame_px": cfg["display"]["frame_px"], "has_brightfield": bf_disp is not None,
                "full_range": r["full"], "enhanced_range": r["enhanced"], "saturation_value": sat,
                "flags": flags, "candidates": cand_meta, "same_field_as": others,
                "counted_by_default": s["counted_by_default"]}
        if ident:      # only in projects with an identity channel, so other blind sets stay byte-identical
            meta["has_identity"] = s["_has_identity"]
        if cfg.get("identity_extra"):
            meta["identity_extra"] = s["_extra"]
        (d / "meta.json").write_text(json.dumps(meta, indent=1))

    # Hidden repeats: the original's files under the repeat's code. Originals were picked so
    # that their meta.json holds nothing but the code that a repeat would need to change.
    # Their automatic measurements are not copied, so outputs/ shows no pair of identical rows.
    key_by_code = {r["code"]: r for r in key_rows}
    for s in sorted(repeats, key=lambda s: s["code"]):
        orig = s["_orig"]["code"]
        shutil.copytree(blind / orig, blind / s["code"])
        meta = json.loads((blind / orig / "meta.json").read_text())
        (blind / s["code"] / "meta.json").write_text(json.dumps({**meta, "code": s["code"]}, indent=1))
        key_rows.append({**key_by_code[orig], "code": s["code"], "repeat_of": orig})
    if repeats:
        key_rows.sort(key=lambda r: r["code"])

    out = project / "outputs"
    real, key_file = project / "blind", project / "key" / "key.csv"
    chan_file = project / "key" / CHANNELS_FILE
    if add:
        key_before = key_file.read_bytes()
        key_rows = read_csv(key_file) + key_rows
        auto_rows = [r for r in read_csv(out / "auto_measurements.csv") if r.get("code") != "none"] + auto_rows
    for f in (key_file, chan_file, project / "key" / "settings_groups.csv", out / "auto_measurements.csv", out / "display_ranges.csv"):
        if f.exists():       # a table open in Excel is found before anything changes
            try:
                with open(f, "r+b"):
                    pass
            except PermissionError:
                raise SystemExit(f"{f.name} is open in another program (Excel?). Close it, then build again.")
    # Every image is made. Only now does the project change, in an order that never leaves a code that
    # can be scored without its key row, also if the computer stops between two steps.
    if add:
        # the key first: a new code in blind/ without a key row could be scored, and the next Add new
        # images would give that code to another image
        moved = []
        try:
            for change in key_changes:
                change()
            write_csv(key_file, key_rows)
            for d in sorted(blind.iterdir()):
                if (real / d.name).exists():    # left by a failed Add new images of an earlier version: not in the
                    shutil.rmtree(real / d.name)        # key, and nothing saved for it (_codes_in_use skips those)
                _rename(d, real / d.name)
                moved.append(real / d.name)
        except BaseException:
            for d in moved:
                shutil.rmtree(d, ignore_errors=True)
            key_file.write_bytes(key_before)
            raise
        run["phase"] = "saving"
        blind.rmdir()
    else:
        previous = project / PREVIOUS
        if real.exists():
            try:
                _rename(real, previous)
            except OSError:
                raise SystemExit("The blind folder could not be replaced: a file in it is open in another program. "
                                 "Close it, then build again.") from None
        try:
            for change in key_changes:       # new codes: the old key is set aside before the new images are in place
                change()
            _rename(blind, real)
        except BaseException:
            if previous.exists() and not real.exists():
                _rename(previous, real)
            for back in key_undo:           # new codes: the key, seed and drafts that were set aside
                back()
            run["phase"] = "exchange"
            raise
        run["phase"] = "saving"
        shutil.rmtree(previous, ignore_errors=True)
        write_csv(key_file, key_rows)
    # 1.26.22: the channels each coded image was built from, right after the key. Adding keeps the record of the
    # coded ones; adding to a project without a record starts none (it would cover the new images only, and the
    # coded ones would lose the identity-channel comparison rescan.scan_changes makes without a record)
    if not add or chan_file.is_file():
        chan = {r["code"]: r for r in read_csv(chan_file)} if add else {}
        for s in todo:
            chan[s["code"]] = {"code": s["code"], **channel_record(s)}
        write_csv(chan_file, [chan[c] for c in sorted(chan)], fields=["code", *CHANNEL_COLUMNS])
    if corr is not None:
        FF.record_build(project, key_rows, corr.used_now)
    write_csv(project / "key" / "settings_groups.csv",
              [{"settings_group": l, "system": c[0], "settings": c[1]} for c, l in letters.items()])
    write_csv(out / "auto_measurements.csv", auto_rows or [{"code": "none"}])
    write_csv(out / "display_ranges.csv", [{"system": k, "standard_low": v["full"][0], "standard_high": v["full"][1],
                                            "enhanced_low": v["enhanced"][0], "enhanced_high": v["enhanced"][1]}
                                           for k, v in ranges.items()])
    if add:
        added = dict(Counter(s["system"] for s in todo))
        append_log(project, "prepare: images added", [
            f"Software: {json.dumps(software_versions())}",
            f"Added {len(todo)} images {added} to the existing {len(images) - len(todo)}. Existing codes, images, "
            "settings groups and display ranges are unchanged. New codes continue after the highest existing "
            "code and were shuffled among themselves; seed stored in key/ (not in this log). Which codes are "
            "new is recorded only in the key (added_on column).",
            "Images added after scoring began can be recognised as a later batch by the scorer; if a batch is "
            "one genotype, blinding is weaker for it.",
            f"Display ranges: {json.dumps(ranges)}",
        ] + identity_log_lines(cfg) + FF.log_lines(project, cfg) + ff_lines + scan_lines + plan_lines + interaction_log_lines(cfg)
          + form_log_lines(cfg, project), cfg)
        log(f"Added {len(todo)} images {added}; {len(images)} images in blind/ in total.")
        return ranges
    n_counted = sum(s["counted_by_default"] for s in images)
    per_system = dict(Counter(s["system"] for s in images))
    target = max(2, round(REPEAT_GAP * (len(images) + len(repeats))))
    gap_done = repeats[0].get("_gap") if repeats else target
    if repeats and gap_done is None:    # a kept key: the distance it has (keys built before 1.21.2 always met target)
        num = lambda c: int("".join(ch for ch in c if ch.isdigit()) or 0)
        have = min(abs(num(r["code"]) - num(r["_orig"]["code"])) for r in repeats)
        gap_done = target if have >= target else have
    hidden = [f"Hidden repeats for scorer consistency: {len(repeats)} images shown a second time under new "
              f"codes (repeat_fraction {C.repeat_fraction(cfg)} of each genotype, at least one; each at least "
              f"{gap_done} codes from its original). Which codes "
              "are repeats is recorded only in the key; repeats are never counted in results."] if repeats else []
    if repeats and gap_done < target:     # 1.21.2: placed as far apart as they fit
        warn = (f"Warning: hidden repeats are at least {gap_done} codes from their originals (planned: {target}), "
                "because the set is small for this share of repeats. A scorer may remember an image seen that "
                "recently. To space them further apart, choose a smaller share of hidden repeats.")
        hidden.append(warn[len("Warning: "):])
        log(warn)
    append_log(project, "prepare", [
        f"Software: {json.dumps(software_versions())}",
        f"Included images: {len(images)} {per_system} ({n_counted} counted by default, "
        f"{len(images) - n_counted} repeats of the same field). Excluded: {len(excluded)} (outputs/excluded.csv).",
        *hidden,
        "Codes assigned by random shuffle; seed stored in key/seed.txt (not in this log, to keep the key sealed).",
        f"Settings groups: {len(letters)} (key/settings_groups.csv).",
        f"Display ranges: {json.dumps(ranges)}",
    ] + browse_log_line(project) + identity_log_lines(cfg) + FF.log_lines(project, cfg) + ff_lines + scan_lines + plan_lines
      + interaction_log_lines(cfg) + form_log_lines(cfg, project), cfg)
    log(f"{len(images)} images in blind/ {per_system}" + (f" plus {len(repeats)} hidden repeats" if repeats else "")
        + f"; {len(excluded)} excluded.")
    return ranges
