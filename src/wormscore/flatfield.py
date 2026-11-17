"""Flat-field correction (1.21.0) from reference-slide images taken in each imaging session.

Optional, and set before scoring. A uniform fluorescent slide imaged at the project's settings shows
how the light falls across the field (brighter in the middle, dimmer at the edges and corners) and,
from one session to the next, how bright the lamp or laser was that day. With the correction on,
Build blind set corrects the reporter planes of every coded image before anything is drawn or
measured, so a cell at the edge of the field is shown and measured as bright as one in the middle:

    corrected = (raw - dark) / flat * factor + mean(dark)

- flat: the session's reference images (each averaged over its planes, then averaged together),
  less the dark image, smoothed and divided by its mean (1 = the field's average light). Used only
  with "across_field".
- factor: the median, over that microscope's sessions with the same settings, of the reference
  brightness, divided by this session's reference brightness. Used only with "between_sessions".
- dark: images taken with the light off at the same settings (optional). Without them the camera
  offset is taken as 0, and the Methods draft says so. mean(dark) is added back, so the background
  stays at its usual level and the display ranges keep their meaning.

An imaging session is one microscope, one settings group and one acquisition day (from metadata).
Formats that record no date (Leica LIF, ImageJ TIFF) use the microscope's only reference session; if
it has several, Build stops and names the images, so the correction is never applied to some
images and not to others. Pixels at the detector's maximum stay marked as saturated.

Raw files are never changed, and the scores are never read. A project without "flat_field" in
config.json is built, shown and measured exactly as before.
"""
import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from . import config as C
from .files import append_log, write_csv

KEY = "flat_field"                    # config.json key; absent unless the correction is on
FOLDER = "flat_field"                 # outputs/flat_field/
LOG_TITLE = "flat-field correction"
SMOOTH = 0.02                         # Gaussian sigma, as a share of the shorter image side
MIN_LIGHT = 0.2                       # a flat dimmer than this anywhere (share of its mean) is refused
MAX_SATURATED = 1e-4                  # reference images with more saturated pixels than this are skipped


def lock_reason(project):
    """Why the correction can no longer be changed, or "": from the first score, or once a second-scorer
    package holds images from this blind set."""
    from .editconfig import scoring_started
    from .scoreform import packages_exist
    if scoring_started(project):
        return "Scoring has started, so the flat-field correction is locked."
    if packages_exist(project):
        return "A second-scorer package was made from this blind set, so the flat-field correction is locked."
    return ""


def session_key(img):
    """(microscope, settings, day) of an image or a reference image."""
    return (img.get("system", ""), img.get("settings", "") or "", (img.get("acquired", "") or "")[:10])


def _label(key):
    system, settings, day = key
    return f"{system}, {day or 'no date'}" + (f", settings {settings}" if settings else "")


def settings_of(cfg):
    """The correction settings, or None if it is off (config.json has no flat_field)."""
    ff = cfg.get(KEY)
    return ff if isinstance(ff, dict) and (ff.get("across_field") or ff.get("between_sessions")) else None


def _read_plane(img, sat, R, np):
    """Mean over planes of the marker channel, as float64 (y, x), and the share of saturated pixels."""
    ci = R.pick(img["channel_roles"], "MARKER")
    if ci is None:
        return None, None
    stack = R.read(img, ci)
    return stack.astype(np.float64).mean(0), float((stack >= sat).mean())


def _images(folder, cfg, log, skipped):
    """Every readable image in folder, with its marker plane averaged over z."""
    from . import readers as R
    from .bleed import control_files
    from .pipeline import saturation_for
    import numpy as np
    folder = Path(folder)
    source, files = control_files(folder, cfg)
    out = []
    for path in files:
        rel = str(path.relative_to(folder))
        try:
            images = R.images_in(path, cfg, source)
        except Exception as exc:
            skipped.append(f"{rel}: could not be read ({type(exc).__name__})")
            continue
        for img in images:
            where = f"{rel} {img.get('location_in_file', '')}".strip()
            plane, sat_share = _read_plane(img, saturation_for(img, cfg), R, np)
            if plane is None:
                skipped.append(f"{where}: the {cfg['marker']['label']} channel could not be identified from metadata")
                continue
            out.append({"where": where, "path": path, "key": session_key(img), "plane": plane,
                        "saturated": sat_share})
            log(f"{where}: read")
    return out


def _sha(paths):
    h = hashlib.sha256()
    for p in sorted(set(paths)):
        h.update(Path(p).read_bytes())
    return h.hexdigest()


def measure(project, folder, dark_folder=None, across_field=True, between_sessions=True, log=print):
    """Make the correction from reference-slide (and dark) images; save, log and turn it on."""
    import numpy as np
    from scipy import ndimage
    project = Path(project)
    why = lock_reason(project)
    if why:
        raise SystemExit(why)
    if not (across_field or between_sessions):
        raise SystemExit("Choose what to correct: the light across the field, between sessions, or both.")
    for f in (folder, dark_folder):
        if f is None:
            continue
        f = Path(f)
        if not f.is_dir():
            raise SystemExit(f"{f} is not a folder.")
        try:
            f.resolve().relative_to(project.resolve())
            raise SystemExit("Choose a folder of reference images outside the project.")
        except ValueError:
            pass
    cfg = C.load(project)
    skipped = []
    refs = _images(folder, cfg, log, skipped)
    if not refs:
        raise SystemExit("No reference image could be read. " + ("; ".join(skipped[:3]) if skipped else
                                                                "The folder has no images wormscore can read."))
    darks = _images(dark_folder, cfg, log, skipped) if dark_folder else []
    if dark_folder and not darks:
        raise SystemExit("No dark image could be read. " + "; ".join(skipped[-3:]))

    # dark: per microscope and settings (it does not change from day to day)
    dark = {}
    for k, items in _group(darks, lambda r: r["key"][:2]).items():
        shapes = {r["plane"].shape for r in items}
        if len(shapes) > 1:
            raise SystemExit(f"The dark images of {k[0]} have different sizes: {sorted(shapes)}.")
        dark[k] = np.median([r["plane"] for r in items], axis=0)

    sessions, flats = [], {}
    for k, items in sorted(_group(refs, lambda r: r["key"]).items()):
        ok = [r for r in items if r["saturated"] <= MAX_SATURATED]
        for r in items:
            if r["saturated"] > MAX_SATURATED:
                skipped.append(f"{r['where']}: saturated ({100 * r['saturated']:.2g}% of pixels at the detector's "
                               "maximum); image the slide with less light")
        if not ok:
            continue
        shapes = {r["plane"].shape for r in ok}
        if len(shapes) > 1:
            raise SystemExit(f"The reference images of {_label(k)} have different sizes: {sorted(shapes)}.")
        d = dark.get(k[:2])
        if d is not None and d.shape != ok[0]["plane"].shape:
            raise SystemExit(f"The dark images of {k[0]} are {d.shape[1]} x {d.shape[0]} pixels, the reference "
                             f"images {ok[0]['plane'].shape[1]} x {ok[0]['plane'].shape[0]}.")
        avg = np.mean([r["plane"] for r in ok], axis=0) - (d if d is not None else 0.0)
        level = float(avg.mean())
        if level <= 0:
            raise SystemExit(f"The reference images of {_label(k)} are no brighter than the dark images.")
        flat = ndimage.gaussian_filter(avg, max(2.0, SMOOTH * min(avg.shape)), mode="nearest") / level
        if flat.min() < MIN_LIGHT:
            raise SystemExit(f"The reference images of {_label(k)} are unlit in places (down to "
                             f"{100 * flat.min():.0f}% of the average light). Use a slide that fills the field.")
        i = len(sessions)
        flats[f"flat_{i}"] = flat.astype(np.float32)
        flats[f"dark_{i}"] = (d if d is not None else np.zeros_like(avg)).astype(np.float32)
        sessions.append({"index": i, "system": k[0], "settings": k[1], "day": k[2], "images": len(ok),
                         "width": int(avg.shape[1]), "height": int(avg.shape[0]), "brightness": round(level, 2),
                         "dark": d is not None, "dark_level": round(float(d.mean()), 2) if d is not None else 0.0,
                         "light_min_pct": round(100 * float(flat.min()), 1),
                         "light_max_pct": round(100 * float(flat.max()), 1)})
    if not sessions:
        raise SystemExit("No reference image could be used. " + "; ".join(skipped[:3]))
    by_group = _group(sessions, lambda s: (s["system"], s["settings"]))
    for group in by_group.values():
        mid = float(np.median([s["brightness"] for s in group]))
        for s in group:
            s["factor"] = round(mid / s["brightness"], 4)

    out = project / "outputs" / FOLDER
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "flats.npz", **flats)
    write_csv(out / "sessions.csv", [{k: v for k, v in s.items() if k != "index"} for s in sessions])
    summary = {"sessions": sessions, "reference_folder": Path(folder).name,
               "dark_folder": Path(dark_folder).name if dark_folder else "",
               "reference_images": sum(s["images"] for s in sessions),
               "reference_sha256": _sha([r["path"] for r in refs]),
               "dark_sha256": _sha([r["path"] for r in darks]) if darks else "", "skipped": skipped[:50],
               "measured": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
    (out / "sessions.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    checksum = hashlib.sha256((out / "flats.npz").read_bytes() + (out / "sessions.json").read_bytes()).hexdigest()
    _write_config(project, {"across_field": bool(across_field), "between_sessions": bool(between_sessions),
                            "checksum": checksum})
    cfg = C.load(project)
    append_log(project, LOG_TITLE, log_lines(project, cfg) + [f"Not used: {s}" for s in skipped[:20]])
    for line in log_lines(project, cfg):
        log(line)
    return summary


def turn_off(project, log=print):
    """Remove the correction from config.json (before scoring only); the measured files are kept."""
    project = Path(project)
    why = lock_reason(project)
    if why:
        raise SystemExit(why)
    _write_config(project, None)
    append_log(project, LOG_TITLE, ["Turned off: the next Build uses the images as recorded."])
    log("Flat-field correction turned off.")


def _write_config(project, value):
    """Only the flat_field key changes (with a backup, as Project settings saves)."""
    from .editconfig import write
    raw = json.loads((Path(project) / "config.json").read_text(encoding="utf-8"))
    if value is None:
        raw.pop(KEY, None)
    else:
        raw[KEY] = value
    write(project, raw)


def _group(items, key):
    out = defaultdict(list)
    for it in items:
        out[key(it)].append(it)
    return out


def summary(project):
    """What was measured, or None."""
    f = Path(project) / "outputs" / FOLDER / "sessions.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    except ValueError:
        return None


# ------------------------------------------------------------------ used by Build

class Correction:
    """The correction of one project, as Build applies it."""

    def __init__(self, project, cfg):
        import numpy as np
        self.ff = settings_of(cfg)
        out = Path(project) / "outputs" / FOLDER
        try:
            data = (out / "flats.npz").read_bytes() + (out / "sessions.json").read_bytes()
        except OSError:
            raise SystemExit("The flat-field correction is on, but its reference measurements are missing "
                             "(outputs/flat_field). Choose the reference images again, or turn it off.")
        if hashlib.sha256(data).hexdigest() != self.ff.get("checksum"):
            raise SystemExit("The flat-field reference measurements in outputs/flat_field were changed after they "
                             "were made. Choose the reference images again.")
        self.summary = json.loads((out / "sessions.json").read_text(encoding="utf-8"))
        with np.load(out / "flats.npz") as z:
            self.arrays = {k: z[k] for k in z.files}
        self.sessions = self.summary["sessions"]

    def session_for(self, img):
        """The reference session for an image, or None. Same microscope and settings; the same day,
        or, when either has no date, the only session of that microscope and settings."""
        system, settings, day = session_key(img)
        same = [s for s in self.sessions if s["system"] == system and s["settings"] == settings]
        hit = [s for s in same if s["day"] == day] if day else []
        if hit:
            return hit[0]
        if len(same) == 1 and (not day or not same[0]["day"]):
            return same[0]
        return None

    def check(self, images):
        """Stop, naming the images, if any has no reference session or another size."""
        missing, sizes = [], []
        for s in images:
            ses = self.session_for(s)
            where = f"{s['relative_path']} {s['location_in_file']}".strip()
            if ses is None:
                missing.append(f"{where} ({_label(session_key(s))})")
            elif (int(s["height"]), int(s["width"])) != (ses["height"], ses["width"]):
                sizes.append(f"{where} ({s['width']} x {s['height']}; reference {ses['width']} x {ses['height']})")
        if missing:
            raise SystemExit(f"The flat-field correction is on, but {len(missing)} image(s) have no reference images "
                             "from the same microscope, settings and day:\n  " + "\n  ".join(missing[:8])
                             + "\nAdd reference images for those sessions, or turn the correction off.")
        if sizes:
            raise SystemExit(f"{len(sizes)} image(s) are not the size of their reference images:\n  "
                             + "\n  ".join(sizes[:8]))

    def apply(self, raw, img):
        """The corrected reporter stack (uint16, clipped to 0 to 65535). Saturation is found on the raw
        stack by the caller (saturated.png), since corrected values no longer show it."""
        import numpy as np
        ses = self.session_for(img)
        flat = self.arrays[f"flat_{ses['index']}"] if self.ff.get("across_field") else 1.0
        dark = self.arrays[f"dark_{ses['index']}"]
        factor = ses["factor"] if self.ff.get("between_sessions") else 1.0
        out = (raw.astype(np.float64) - dark) / flat * factor + ses["dark_level"]
        return np.clip(np.rint(out), 0, 65535).astype(np.uint16)

    def key_columns(self, img):
        ses = self.session_for(img)
        return {"imaging_session": _label((ses["system"], ses["settings"], ses["day"])),
                "session_factor": ses["factor"] if self.ff.get("between_sessions") else 1.0}


def load(project, cfg):
    """The project's Correction, or None if it is off."""
    return Correction(project, cfg) if settings_of(cfg) else None


def _what(ff):
    return " and ".join(x for x, on in (("across the field", ff.get("across_field")),
                                         ("between imaging sessions", ff.get("between_sessions"))) if on)


def log_lines(project, cfg):
    """For log.md at each setting and at Build; [] when the correction is off."""
    ff, sm = settings_of(cfg), summary(project)
    if not ff or not sm:
        return []
    ses = sm["sessions"]
    lines = [f"Flat-field correction: {_what(ff)}, from {sm['reference_images']} reference image(s) in "
             f"{len(ses)} imaging session(s) (folder {sm['reference_folder']}, sha256 {sm['reference_sha256'][:16]}"
             + (f"; dark images from folder {sm['dark_folder']}, sha256 {sm.get('dark_sha256', '')[:16]}" if sm["dark_folder"] else "; no dark images, "
                "camera offset taken as 0") + f"; checksum {ff['checksum'][:16]})."]
    for s in ses:
        lines.append(f"Session {_label((s['system'], s['settings'], s['day']))}: {s['images']} image(s), light across "
                     f"the field {s['light_min_pct']} to {s['light_max_pct']}% of its mean"
                     + (f", brightness factor {s['factor']}" if ff.get("between_sessions") else "") + ".")
    return lines


def methods_sentence(project, cfg):
    """For the Methods draft, or "" when the correction is off (the Methods then read as before)."""
    ff, sm = settings_of(cfg), summary(project)
    if not ff or not sm:
        return ""
    ses = sm["sessions"]
    lows = [s["light_min_pct"] for s in ses]
    facs = [s["factor"] for s in ses]
    return ("Before scoring, the reporter channel of every image was corrected " + _what(ff) + " using images of a "
            f"uniform fluorescent reference slide taken at the same settings in each imaging session ({len(ses)} "
            f"session{'s' if len(ses) != 1 else ''}, {sm['reference_images']} reference images"
            + ("; " + ("dark images taken with the light off were subtracted" if sm["dark_folder"]
                       else "no dark images were taken, so the camera offset was taken as zero")) + "). "
            + (f"The reference light across the field ranged down to {min(lows)}% of its mean; " if ff.get("across_field")
               else "")
            + (f"session brightness factors ranged from {min(facs)} to {max(facs)}; " if ff.get("between_sessions") else "")
            + "the scorer saw the corrected planes and all measurements used them; pixels at the detector's "
            "maximum were found before correction and still counted as saturated. ")
