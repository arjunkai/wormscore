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
import random
import re
import secrets
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__
from . import config as C
from . import measure as M
from . import readers as R
from .files import append_log, now, read_csv, write_csv  # noqa: F401  (re-exported)
from .browse import browse_log_line
from .rescan import next_letter, scan_changes  # noqa: F401  (re-exported)
from .scoreform import form_log_lines

INCOMPLETE = "file is incomplete"       # inventory flag (readers.ome_images), an exclusion reason at Build


INVENTORY_FIELDS = [
    "source", "genotype", "system", "format", "file", "location_in_file", "relative_path",
    "local_path", "original_path", "file_sha256", "objective", "n_z", "n_t", "width", "height", "pixel_um",
    "z_step_um", "bits", "channel_names", "channel_roles", "marker_channel", "bf_channel", "settings", "zoom",
    "acquired", "stage_x", "stage_y", "flags", "reader_kind", "reader_index", "reader_axes", "pixel_sha256",
]


def software_versions():
    import czifile, liffile, oiffile, PIL, scipy, tifffile  # noqa: E401
    from importlib.metadata import PackageNotFoundError, version
    try:
        nd2 = version("nd2")          # read from the package record; the reader itself loads only for .nd2 files
    except PackageNotFoundError:
        nd2 = "not installed"
    return {"wormscore": __version__, "python": sys.version.split()[0], "numpy": np.__version__,
            "scipy": scipy.__version__, "pillow": PIL.__version__, "tifffile": tifffile.__version__,
            "liffile": liffile.__version__, "czifile": czifile.__version__, "oiffile": oiffile.__version__,
            "nd2": nd2}


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
                     "channel_names": "/".join(img["channel_names"]), "channel_roles": "/".join(roles),
                     "marker_channel": "" if mi is None else mi, "bf_channel": "" if bi is None else bi,
                     **({"identity_channel": "" if ii is None else ii} if ident else {}),
                     "stage_x": "" if img["stage_x"] is None else round(img["stage_x"], 2),
                     "stage_y": "" if img["stage_y"] is None else round(img["stage_y"], 2),
                     "flags": flags, "reader_kind": rk[0], "reader_index": rk[2],
                     "reader_axes": rk[3] if len(rk) > 3 else "", "pixel_sha256": pixels})
    # Settings that differ from the most common value on the same microscope.
    for system in {r["system"] for r in rows}:
        same = [r for r in rows if r["system"] == system]
        for key in ("objective", "settings", "bits", "pixel_um"):
            mode = Counter(str(r[key]) for r in same).most_common(1)[0][0]
            for r in same:
                if str(r[key]) != mode:
                    r["flags"].append(f"{key} differs from most images on this microscope")
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


def include_reason(img, cfg):
    """'' if the image passes the inclusion rules, otherwise why it fails."""
    inc = cfg["inclusion"]
    rule = C.exclude_rule_for(cfg, img["source"], img["relative_path"])
    if rule:
        return f"excluded by config rule '{rule['match']}': {rule.get('reason') or 'no reason given'}"
    if img["marker_channel"] is None:
        return f"{cfg['marker']['label']} channel could not be identified from metadata"
    ident = cfg.get("identity")
    if ident and ident["required"] and img.get("identity_channel") is None:
        return (f"{ident['label']} identity channel could not be identified from metadata (identity.required is "
                "true, so every scored image has its cells found the same way)")
    # 1.26.5: images Build cannot use are left out with the reason. They used to stop the whole build
    # with a Python error that named no file.
    flags = str(img.get("flags") or "")
    if "pixels could not be read" in flags:
        return "the image could not be read (damaged or incomplete file)"
    if INCOMPLETE in flags:
        return "the file is incomplete: some planes are missing (damaged, or still being copied)"
    if not img["pixel_um"] or img["pixel_um"] <= 0:
        return "the file does not record its pixel size, which the measurements and the display scale need"
    # 1.26.5: "63.0x" was read as 0x (the digits before the x), so such images were left out as below the minimum
    mag = re.search(r"(\d+(?:\.\d+)?)\s*x", img["objective"] or "")
    if mag and float(mag.group(1)) < inc["min_objective_magnification"]:
        return f"objective is {img['objective']} (minimum {inc['min_objective_magnification']}x)"
    if img["n_t"] > inc["max_time_points"]:
        return f"time series ({img['n_t']} time points)"
    extent = (img["n_z"] - 1) * img["z_step_um"]
    if extent < inc["min_z_extent_um"]:
        return f"z-stack covers {extent:.1f} um ({img['n_z']} planes; minimum {inc['min_z_extent_um']} um)"
    return ""


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


def assign_codes(project, images, reassign, add=False, cfg=None):
    """Random codes. An existing key is reused so codes never change while scoring.
    add: images not yet in the key get new codes after the highest existing one, shuffled
    among themselves with a new seed; existing codes are kept. Returns the images to render,
    with hidden repeats (records with "_orig", the image they repeat) after the images."""
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
            raise SystemExit("No new images: every included image already has a code.")
        for s in images:
            s["code"] = old.get(ident(s))
        seed = secrets.randbits(32)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        (key_dir / f"seed_added_{stamp}.txt").write_text(f"{seed}\n")
        random.Random(seed).shuffle(new)
        prefix, width = all_codes[0][0], len(all_codes[0]) - 1
        start = max(int(c[1:]) for c in all_codes) + 1
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
    if key_path.exists():
        old_dir = key_dir / f"superseded_{dt.datetime.now():%Y%m%d_%H%M%S}"
        old_dir.mkdir()
        for f in ("key.csv", "seed.txt", "settings_groups.csv"):
            if (key_dir / f).exists():
                (key_dir / f).rename(old_dir / f)
    # Unsaved answers belong to the old codes; with new codes they would open on other images.
    drafts = project / "scoring_drafts" / "drafts.json"
    if drafts.exists():
        drafts.rename(drafts.with_name(f"superseded_{dt.datetime.now():%Y%m%d_%H%M%S}.json"))
    seed = secrets.randbits(32)
    (key_dir / "seed.txt").write_text(f"{seed}\n")
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


def prepare(project, reassign=False, add=False, log=print):
    """Build the blind set. add=True codes and renders only images not yet in the key, keeping
    every existing code, image, settings-group letter and display range."""
    project = Path(project)
    if add and reassign:
        raise SystemExit("Use either --add-new-images or --reassign-codes, not both.")
    from .scorers import refuse_in_package
    refuse_in_package(project, "Building the blind set")
    cfg = C.load(project)
    C.check(cfg)
    images, excluded = [], []
    for img in load_images(project):
        reason = include_reason(img, cfg)
        if reason:
            excluded.append({k: img[k] for k in ("system", "genotype", "original_path", "local_path",
                                                  "location_in_file")} | {"reason": reason})
        else:
            images.append(img)
    images, copies = drop_identical(images, cfg)
    excluded += copies
    write_csv(project / "outputs" / "excluded.csv", excluded or [{"reason": "none excluded"}])
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
    if corr is not None:
        # never stops the build: images without their own session's slide images use another's, or are
        # used as recorded, each with a warning here, in log.md and in key.csv (column flat_field)
        ff_lines = corr.warnings(render)
        for line in ff_lines:
            log(line)
    else:
        ff_lines = []

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
        if not add and ch["lines"]:
            scan_lines = ["Warning: this build changes what was recorded for coded images, because Scan data now reads "
                          f"them differently: {'; '.join(ch['lines'])}. Scores are unchanged. Compare brightness only "
                          "within the new settings groups."]
        for line in scan_lines:
            log(line)
    todo = assign_codes(project, images, reassign, add, cfg)
    repeats = [s for s in todo if "_orig" in s]
    todo = [s for s in todo if "_orig" not in s]
    by_field = {}
    for s in images:
        by_field.setdefault(s["field_group"], []).append(s["code"])
    if add:
        # A new image of a field that already has a code is not counted by default.
        old_fields = {s["field_group"] for s in images if s not in todo}
        for s in todo:
            if s["field_group"] in old_fields:
                s["counted_by_default"] = False

    blind = project / "blind"
    if blind.exists() and not add:
        shutil.rmtree(blind)
    blind.mkdir(exist_ok=True)
    mc = cfg["measurement"]
    ident = cfg.get("identity")
    auto_rows, key_rows, stats, pending = [], [], {}, []
    for n, s in enumerate(sorted(todo, key=lambda s: s["code"]), start=1):
        code, d = s["code"], blind / s["code"]
        (d / "planes").mkdir(parents=True)
        sat = saturation_for(s, cfg)
        v = read_for_viewer(s, cfg, corr)
        sat_mask = v["sat_mask"]          # None unless corrected: then saturation is read from this mask
        if sat_mask is not None:
            Image.fromarray(sat_mask.astype(np.uint8) * 255).save(d / "saturated.png")
        raw, filt, mip, bf_disp, cands, has_identity = v["raw"], v["filt"], v["mip"], v["bf"], v["cands"], v["has_identity"]
        px = s["pixel_um"]
        stats.setdefault(s["system"], []).append((float(np.median(mip)), float(np.percentile(mip, 99.9))))

        for z in range(raw.shape[0]):
            Image.fromarray(M.pack16(raw[z])).save(d / "planes" / f"z{z:02d}.png")
        Image.fromarray(M.pack16(mip)).save(d / "mip.png")
        if bf_disp is not None:
            Image.fromarray(bf_disp).save(d / "brightfield.png")
        if has_identity:
            (d / "identity").mkdir()
            for z in range(v["id_planes"].shape[0]):
                Image.fromarray(v["id_planes"][z]).save(d / "identity" / f"z{z:02d}.png")
            Image.fromarray(v["id_mip"]).save(d / "identity_mip.png")
        for k, x in enumerate(v["extra"], start=2):     # only in projects with extra identity channels
            if x is not None:
                (d / f"identity{k}").mkdir()
                for z in range(x[0].shape[0]):
                    Image.fromarray(x[0][z]).save(d / f"identity{k}" / f"z{z:02d}.png")
                Image.fromarray(x[1]).save(d / f"identity{k}_mip.png")
        s["_extra"] = [x is not None for x in v["extra"]]
        del v

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
            "acquired": s["acquired"], "stage_x_um": "" if s["stage_x"] is None else round(s["stage_x"], 1),
            "stage_y_um": "" if s["stage_y"] is None else round(s["stage_y"], 1),
            "same_field_group": s["field_group"], "counted_by_default": s["counted_by_default"],
            **({"identity_channel_used": has_identity} if ident else {}),
            **(corr.key_columns(s) if corr is not None else {}),
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
    if add:
        key_rows = read_csv(project / "key" / "key.csv") + key_rows
        auto_rows = [r for r in read_csv(out / "auto_measurements.csv") if r.get("code") != "none"] + auto_rows
    write_csv(project / "key" / "key.csv", key_rows)
    if corr is not None:
        FF.record_build(project, key_rows)
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
