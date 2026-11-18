"""Flat-field correction (1.21.0) from reference-slide images taken in each imaging session.

Optional, and set before scoring. A uniform fluorescent slide imaged at the project's settings shows
how the light falls across the field (brighter in the middle, dimmer at the edges and corners) and,
from one session to the next, how bright the lamp or laser was that day. With the correction on,
Build blind set corrects the reporter planes of every coded image before anything is drawn or
measured, so a cell at the edge of the field is shown and measured as bright as one in the middle:

    corrected = (raw - dark) / flat * factor + mean(dark)

- flat: the session's slide images (each averaged over its planes, then averaged together), less the
  dark image, smoothed and divided by its mean (1 = the field's average light). Only with "across_field".
- factor: the median, over that microscope's sessions with the same settings, of the slide brightness,
  divided by this session's. Only with "between_sessions".
- dark: images taken with the light off at the same settings (optional). Without them the camera
  offset is taken as 0, and the Methods draft says so. mean(dark) is added back, so the background
  stays at its usual level and the display ranges keep their meaning.

An imaging session is one microscope, settings, objective and zoom, and one acquisition day (from metadata).
Nothing that can be worked around stops the build (1.21.1); each fallback is a warning in the Build log,
a column of key.csv, log.md and the Methods draft. The pattern of light across the field changes little
unless the microscope is realigned, so for an image without slide images from its own session:
- a dated image uses the nearest day's slide images (same microscope and settings);
- an image without a date (Leica LIF, ImageJ TIFF), when the microscope has several sessions, uses
  their average;
- an image whose microscope has slide images only at other settings uses those of the same size;
in each case the light across the field is corrected and the brightness between sessions is not (the
day's slide brightness is unknown). An image with no usable slide images at all is used as recorded.
Pixels at the detector's maximum are found before correction and still counted as saturated.

Slide images for later sessions can be added at any time, also after scoring began: the sessions in
use are not changed, so every coded image keeps the planes it was scored on. Starting again or turning
the correction off would change images already scored, so those are only possible before scoring.

Raw files are never changed, and the scores are never read. A project without "flat_field" in
config.json is built, shown and measured exactly as before.
"""
import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import config as C
from .files import append_log, plural, write_csv
from .rescan import same_reading

KEY = "flat_field"                    # config.json key; absent unless the correction is on
FOLDER = "flat_field"                 # outputs/flat_field/
LOG_TITLE = "flat-field correction"
SMOOTH = 0.02                         # Gaussian sigma, as a share of the shorter image side
MIN_LIGHT = 0.2                       # a session whose flat is dimmer than this anywhere is not used
MAX_SATURATED = 1e-3                  # slide images with more saturated pixels than this are not used
SAME = "same session"                 # key.csv flat_field value for an image with its own session's slides


def lock_reason(project):
    """Why the correction can no longer be started again or turned off, or "": from the first score, or
    once a second-scorer package holds images from this blind set (either would change scored images)."""
    from .editconfig import scoring_started
    from .scoreform import packages_exist
    if scoring_started(project):
        return "Scoring has started, so the slide images already in use stay as they are."
    if packages_exist(project):
        return "A second-scorer package was made from this blind set, so the slide images already in use stay as they are."
    return ""


DATE_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S")


def acquired_day(acquired):
    """The acquisition day as YYYY-MM-DD, or "" if unknown. ISO dates (OME, Zeiss, Leica) are read directly;
    Nikon and Olympus write the date as the acquiring computer's locale shows it, for example
    "9/24/2020 4:01:38 PM", so a few common forms are tried (US month/day first)."""
    import re
    a = " ".join(str(acquired or "").split())
    if re.match(r"\d{4}-\d{2}-\d{2}", a):
        return a[:10]
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(a, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


UNCLEAR_DATE = "day or month first: not known"


def imaging_date(acquired):
    """The acquisition day for the imaging_date column of per_neuron.csv (1.26.13). ISO dates give their
    first 10 characters, as before. Nikon and Olympus write the date as the acquiring computer shows it
    ("11/3/2009 12:20:54 PM"); the column held its first 10 characters ("11/3/2009 1"). Such a date is now
    given as YYYY-MM-DD when only one reading is possible (a number above 12 is the day). When two are
    (3 November or 11 March), the date is given as written and marked, not guessed. Text that is no date
    is given whole. This is a label: nothing is grouped or tested by it."""
    import re
    raw = str(acquired or "")
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    a = " ".join(raw.split())
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)", a)
    if not m:
        # text from the file that is no date: kept whole, but never as a formula in a spreadsheet
        return acquired_day(a) or (("'" + a) if a[:1] in ("=", "+", "-", "@") else a)
    p, q, year = (int(x) for x in m.groups())
    if p != q and p <= 12 and q <= 12:
        return f"{m.group(0)} ({UNCLEAR_DATE})"
    try:
        return dt.date(year, *((p, q) if q > 12 else (q, p))).isoformat()
    except ValueError:
        return a


def session_key(img):
    """(microscope, settings, day) of an image or a slide image. "settings" includes the objective and the
    zoom, which change the light across the field but are not in every format's settings string."""
    settings = " | ".join(x for x in (str(img.get("settings", "") or ""), str(img.get("objective", "") or ""),
                                      str(img.get("zoom", "") or "")) if x)
    return (img.get("system", ""), settings, acquired_day(img.get("acquired", "")))


def _label(key):
    system, settings, day = key
    return f"{system}, {day or 'no date'}" + (f", settings {settings}" if settings else "")


def settings_of(cfg):
    """The correction settings, or None if it is off (config.json has no flat_field)."""
    ff = cfg.get(KEY)
    return ff if isinstance(ff, dict) and (ff.get("across_field") or ff.get("between_sessions")) else None


def check_folder(project, folder, cfg, what="slide images"):
    """Stop only if the folder holds (or is inside) the project's own image folders: those images would
    be read as slide images. Any other folder, inside the project or not, is fine."""
    folder = Path(folder)
    if not folder.is_dir():
        raise SystemExit(f"{folder} is not a folder.")
    f = folder.resolve()
    for s in cfg["sources"]:
        if not isinstance(s.get("path"), str) or not s["path"]:
            continue
        src = Path(s["path"]).resolve()
        if f == src or src in f.parents or f in src.parents:
            raise SystemExit(f"That folder holds the project's own images ({s.get('name') or src.name}). "
                             f"Put the {what} in a folder of their own.")


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
            ci = R.pick(img["channel_roles"], "MARKER")
            if ci is None:
                skipped.append(f"{where}: the {cfg['marker']['label']} channel could not be identified from metadata")
                continue
            try:
                stack = R.read(img, ci)
            except Exception as exc:          # one unreadable image is reported, not fatal
                skipped.append(f"{where}: could not be read ({type(exc).__name__})")
                continue
            out.append({"where": where, "path": path, "key": session_key(img),
                        "plane": stack.astype(np.float64).mean(0),
                        "saturated": float((stack >= saturation_for(img, cfg)).mean())})
            log(f"{where}: read")
    return out


def _sha(paths):
    h = hashlib.sha256()
    for p in sorted(set(paths)):
        h.update(Path(p).read_bytes())
    return h.hexdigest()


def _most_common_size(items, what, skipped):
    """The items of the most common plane size; the others are reported and left out."""
    sizes = Counter(r["plane"].shape for r in items)
    keep = sizes.most_common(1)[0][0]
    for r in items:
        if r["plane"].shape != keep:
            skipped.append(f"{r['where']}: {r['plane'].shape[1]} x {r['plane'].shape[0]} pixels, unlike the other "
                           f"{what} ({keep[1]} x {keep[0]}); not used")
    return [r for r in items if r["plane"].shape == keep]


def _darks(darks, skipped):
    """Dark image per microscope and settings (it does not change from day to day)."""
    import numpy as np
    return {k: np.median([r["plane"] for r in _most_common_size(items, "dark images", skipped)], axis=0)
            for k, items in _group(darks, lambda r: r["key"][:2]).items()}


def _make_sessions(refs, dark, skipped, start=0, skip_keys=()):
    """(sessions without factors, their arrays) from slide images grouped by session. A session that
    cannot be used is reported in skipped; its images then use another session's slide images."""
    import numpy as np
    from scipy import ndimage
    sessions, flats = [], {}
    for k, items in sorted(_group(refs, lambda r: r["key"]).items()):
        if k in skip_keys:
            skipped.append(f"{_label(k)}: already has slide images, kept as it was")
            continue
        ok = []
        for r in items:
            if r["saturated"] > MAX_SATURATED:
                skipped.append(f"{r['where']}: saturated ({100 * r['saturated']:.2g}% of pixels at the detector's "
                               "maximum); image the slide with less light")
            else:
                ok.append(r)
        if not ok:
            continue
        ok = _most_common_size(ok, f"slide images of {_label(k)}", skipped)
        d = dark.get(k[:2])
        if d is not None and d.shape != ok[0]["plane"].shape:
            skipped.append(f"dark images of {k[0]}: not the size of the slide images of {_label(k)}; that session "
                           "is corrected without them")
            d = None
        avg = np.mean([r["plane"] for r in ok], axis=0) - (d if d is not None else 0.0)
        level = float(avg.mean())
        if level <= 0:
            skipped.append(f"{_label(k)}: the slide images are no brighter than the dark images; session not used")
            continue
        flat = ndimage.gaussian_filter(avg, max(2.0, SMOOTH * min(avg.shape)), mode="nearest") / level
        if flat.min() < MIN_LIGHT:
            skipped.append(f"{_label(k)}: the slide images are unlit in places (down to {100 * flat.min():.0f}% of the "
                           "average light), so the slide did not fill the field; session not used")
            continue
        i = start + len(sessions)
        flats[f"flat_{i}"] = flat.astype(np.float32)
        flats[f"dark_{i}"] = (d if d is not None else np.zeros_like(avg)).astype(np.float32)
        sessions.append({"index": i, "system": k[0], "settings": k[1], "day": k[2], "images": len(ok),
                         "width": int(avg.shape[1]), "height": int(avg.shape[0]), "brightness": round(level, 2),
                         "dark": d is not None, "dark_level": round(float(d.mean()), 2) if d is not None else 0.0,
                         "light_min_pct": round(100 * float(flat.min()), 1),
                         "light_max_pct": round(100 * float(flat.max()), 1)})
    return sessions, flats


def measure(project, folder, dark_folder=None, across_field=True, between_sessions=True, log=print):
    """Make the correction from slide (and dark) images; save, log and turn it on (before scoring)."""
    import numpy as np
    project = Path(project)
    why = lock_reason(project)
    if why:
        raise SystemExit(why + " To add slide images for later days, use Add slide images.")
    if not (across_field or between_sessions):
        raise SystemExit("Choose what to correct: the light across the field, between sessions, or both.")
    cfg = C.load(project)
    check_folder(project, folder, cfg)
    if dark_folder:
        check_folder(project, dark_folder, cfg, "dark images")
    skipped = []
    refs = _images(folder, cfg, log, skipped)
    if not refs:
        raise SystemExit("No slide image could be read. " + ("; ".join(skipped[:3]) if skipped else
                                                            "The folder has no images wormscore can read."))
    darks = _images(dark_folder, cfg, log, skipped) if dark_folder else []
    if dark_folder and not darks:
        skipped.append("No dark image could be read, so the camera offset is taken as 0.")
    sessions, flats = _make_sessions(refs, _darks(darks, skipped), skipped)
    if not sessions:
        raise SystemExit("None of the slide images could be used: " + "; ".join(skipped[:3]))
    for group in _group(sessions, lambda s: (s["system"], s["settings"])).values():
        mid = float(np.median([s["brightness"] for s in group]))
        for s in group:
            s["group_brightness"] = round(mid, 2)
            s["factor"] = round(mid / s["brightness"], 4)
    summary_ = {"sessions": sessions, "reference_folder": Path(folder).name,
                "dark_folder": Path(dark_folder).name if darks else "",
                "reference_images": sum(s["images"] for s in sessions),
                "reference_sha256": _sha([r["path"] for r in refs]),
                "dark_sha256": _sha([r["path"] for r in darks]) if darks else "", "skipped": skipped[:50],
                "measured": f"{dt.datetime.now():%Y-%m-%d %H:%M}"}
    _save(project, summary_, flats, {"across_field": bool(across_field), "between_sessions": bool(between_sessions)})
    cfg = C.load(project)
    lines = log_lines(project, cfg)
    append_log(project, LOG_TITLE, lines + [f"Not used: {s}" for s in skipped[:20]])
    for line in lines + [f"Warning: {s}" for s in skipped]:
        log(line)
    return summary_


def add_sessions(project, folder, log=print):
    """Add slide images for imaging sessions that have none yet (1.21.1). Allowed after scoring began:
    sessions in use are not changed, and a new session's brightness is brought to the level its
    microscope's sessions were brought to, so every coded image keeps the planes it was scored on.
    Dark images: the microscope's existing ones (none for a microscope new to the project)."""
    import numpy as np
    project, folder = Path(project), Path(folder)
    cfg = C.load(project)
    ff, sm = settings_of(cfg), summary(project)
    if not ff or not sm:
        raise SystemExit("The flat-field correction is off. Turn it on with Slide images... first.")
    corr = Correction(project, cfg)          # checks that the stored correction is intact
    check_folder(project, folder, cfg)
    skipped = []
    refs = _images(folder, cfg, log, skipped)
    if not refs:
        raise SystemExit("No slide image could be read. " + ("; ".join(skipped[:3]) if skipped else
                                                            "The folder has no images wormscore can read."))
    old = sm["sessions"]
    dark = {}
    for s in old:      # the dark image each microscope and settings already uses
        if s["dark"] and (s["system"], s["settings"]) not in dark:
            dark[(s["system"], s["settings"])] = corr.arrays[f"dark_{s['index']}"].astype(np.float64)
    have = {(s["system"], s["settings"], s["day"]) for s in old}
    new, arrays = _make_sessions(refs, dark, skipped, start=1 + max(s["index"] for s in old), skip_keys=have)
    if not new:
        raise SystemExit("No new session was added: " + "; ".join(skipped[:3]))
    stamp = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    started = bool(lock_reason(project))
    for k, group in _group(new, lambda s: (s["system"], s["settings"])).items():
        before = [s for s in old if (s["system"], s["settings"]) == k]
        mid = (before[0].get("group_brightness") or before[0]["brightness"] * before[0]["factor"]) if before \
            else float(np.median([s["brightness"] for s in group]))
        for s in group:
            s.update(group_brightness=round(mid, 2), factor=round(mid / s["brightness"], 4), added=stamp,
                     added_after_scoring_began=started)
    sha = _sha([r["path"] for r in refs])
    summary_ = {**sm, "sessions": old + new, "reference_images": sm["reference_images"] + sum(s["images"] for s in new),
                "added": sm.get("added", []) + [{"folder": folder.name, "sha256": sha, "sessions": len(new), "when": stamp}]}
    _save(project, summary_, {**corr.arrays, **arrays}, {k: v for k, v in ff.items() if k != "checksum"})
    lines = [f"Slide images added for {plural(len(new), 'new imaging session')} (folder {folder.name}, sha256 {sha[:16]})"
             + ("; scoring had begun" if started else "") + ". Sessions already in use were not changed."]
    lines += [session_line(s, ff) for s in new]
    append_log(project, LOG_TITLE, lines + [f"Not used: {s}" for s in skipped[:20]])
    for line in lines + [f"Warning: {s}" for s in skipped]:
        log(line)
    return new


def turn_off(project, log=print):
    """Remove the correction from config.json (before scoring only); the measured files are kept."""
    project = Path(project)
    why = lock_reason(project)
    if why:
        raise SystemExit(why)
    _write_config(project, None)
    append_log(project, LOG_TITLE, ["Turned off: the next Build uses the images as recorded."])
    log("Flat-field correction turned off.")


def _save(project, summary_, flats, settings):
    """Write outputs/flat_field and the flat_field key (with the checksum of what was written)."""
    import numpy as np
    out = Path(project) / "outputs" / FOLDER
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "flats.npz", **flats)
    write_csv(out / "sessions.csv", [{k: v for k, v in s.items() if k != "index"} for s in summary_["sessions"]])
    (out / "sessions.json").write_text(json.dumps(summary_, indent=1), encoding="utf-8")
    checksum = hashlib.sha256((out / "flats.npz").read_bytes() + (out / "sessions.json").read_bytes()).hexdigest()
    _write_config(project, {**settings, "checksum": checksum})


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


def build_note(project):
    """How the last Build corrected the images: {"same": n, "other": n, "none": n}, or None."""
    f = Path(project) / "outputs" / FOLDER / "build.json"
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
            raise SystemExit("The flat-field correction is on, but its measurements are missing (outputs/flat_field). "
                             "Choose the slide images again, or turn the correction off.")
        if hashlib.sha256(data).hexdigest() != self.ff.get("checksum"):
            raise SystemExit("The flat-field measurements in outputs/flat_field were changed after they were made, so "
                             "they no longer match the record in log.md. Restore them from a backup, or (before "
                             "scoring) choose the slide images again.")
        self.summary = json.loads((out / "sessions.json").read_text(encoding="utf-8"))
        with np.load(out / "flats.npz") as z:
            self.arrays = {k: z[k] for k in z.files}
        self.sessions = self.summary["sessions"]

    def session_for(self, img):
        """The image's own session, or None: same microscope and settings, and the same day, or the only
        session of that microscope and settings when either has no date."""
        system, settings, day = session_key(img)
        # 1.26.13: settings that this version only words differently than the one that measured the slide
        # images (same_reading: a Leica detector's name, an OME-TIFF objective's numbers) are the same session
        same = [s for s in self.sessions if s["system"] == system and s["settings"] == settings] \
            or [s for s in self.sessions if s["system"] == system
                and same_reading(system, s["settings"]) == same_reading(system, settings)]
        hit = [s for s in same if s["day"] == day] if day else []
        if hit:
            return hit[0]
        if len(same) == 1 and (not day or not same[0]["day"]):
            return same[0]
        return None

    def resolve(self, img):
        """How this image is corrected: a session (its own, or one standing in for it, with "how" saying
        which), or None (used as recorded; "why" from not_corrected)."""
        import numpy as np
        size = (int(img["height"]), int(img["width"])) if "height" in img else None
        fits = lambda s: size is None or (s["height"], s["width"]) == size
        own = self.session_for(img)
        if own is not None and fits(own):
            return {**own, "how": SAME}
        system, settings, day = session_key(img)
        group = [s for s in self.sessions if s["system"] == system and s["settings"] == settings and fits(s)] \
            or [s for s in self.sessions if s["system"] == system and fits(s)
                and same_reading(system, s["settings"]) == same_reading(system, settings)]
        others = [s for s in self.sessions if s["system"] == system and fits(s)]
        # 1.26.12: among sessions with other settings, those with the image's objective and zoom come first.
        # The light across the field depends on the objective; a session's settings end with them
        # (session_key). Since 1.26.7 reads more of the settings for Zeiss, Nikon and OME-TIFF, images
        # scanned again no longer match slide images measured before, and the nearest day alone could
        # pick the slide images of another objective taken the same day.
        optics = " | ".join(x for x in (str(img.get("objective", "") or ""), str(img.get("zoom", "") or "")) if x)
        optics = same_reading("", optics)
        same_optics = [s for s in others if optics and (same_reading("", s["settings"]) == optics
                                                        or same_reading("", s["settings"]).endswith(" | " + optics))]
        pool = group or same_optics or others
        if not pool:
            return None
        where = "" if group else " (other settings)"
        dated = [s for s in pool if s["day"]]
        if day and dated:
            when = _date(day)
            near = min(dated, key=lambda s: (abs((_date(s["day"]) - when).days) if when and _date(s["day"]) else 0, s["day"]))
            return {**near, "how": f"nearest day, {near['day']}{where}"}
        if len(pool) == 1:
            return {**pool[0], "how": f"slide images of {pool[0]['day'] or 'no date'}{where}"}
        key = f"avg:{system}:{settings if group else '*'}"
        if f"flat_{key}" not in self.arrays:
            self.arrays[f"flat_{key}"] = np.mean([self.arrays[f"flat_{s['index']}"] for s in pool], axis=0)
        return {**pool[0], "index": key, "dark_from": pool[0]["index"], "how": f"average of {len(pool)} sessions{where}"}

    def not_corrected(self, img):
        system = session_key(img)[0]
        if any(s["system"] == system for s in self.sessions):
            return "slide images of another size"
        return "no slide images from this microscope"

    def apply(self, raw, img):
        """The corrected reporter stack (uint16, clipped to 0 to 65535), or raw if no slide images fit.
        Saturation is found on the raw stack by the caller (saturated.png)."""
        import numpy as np
        ses = self.resolve(img)
        if ses is None:
            return raw
        flat = self.arrays[f"flat_{ses['index']}"] if self.ff.get("across_field") else 1.0
        dark = self._dark(ses)
        factor = ses["factor"] if self.ff.get("between_sessions") and ses["how"] == SAME else 1.0
        out = (raw.astype(np.float64) - dark) / flat * factor + ses["dark_level"]
        return np.clip(np.rint(out), 0, 65535).astype(np.uint16)

    def _dark(self, ses):
        """The dark image of a session (an average stands in with its first session's)."""
        return self.arrays[f"dark_{ses.get('dark_from', ses['index'])}"]

    def key_columns(self, img):
        ses = self.resolve(img)
        if ses is None:
            return {"imaging_session": _label(session_key(img)), "flat_field": f"not corrected: {self.not_corrected(img)}",
                    "session_factor": "not corrected"}
        same = ses["how"] == SAME
        return {"imaging_session": _label(session_key(img) if not same else (ses["system"], ses["settings"], ses["day"])),
                "flat_field": ses["how"],
                "session_factor": (ses["factor"] if same else "not corrected") if self.ff.get("between_sessions") else 1.0}

    def warnings(self, images):
        """For the Build log and log.md: images corrected with another session's slide images, or not at all."""
        other, none = defaultdict(int), defaultdict(int)
        for s in images:
            ses = self.resolve(s)
            if ses is None:
                none[(_label(session_key(s)), self.not_corrected(s))] += 1
            elif ses["how"] != SAME:
                other[(_label(session_key(s)), ses["how"])] += 1
        tail = "; brightness between sessions is not corrected for them" if self.ff.get("between_sessions") else ""
        out = [f"Warning: {plural(n, 'image')} from {k} used {_how_words(how)}{tail}. Add that session's slide "
               "images to correct them fully." for (k, how), n in sorted(other.items())]
        out += [f"Warning: {plural(n, 'image')} from {k} could not be corrected ({why}) and {'is' if n == 1 else 'are'} "
                "used as recorded, not comparable in brightness with the others. Add that session's slide images "
                "to correct them." for (k, why), n in sorted(none.items())]
        return out


def _how_words(how):
    """A key.csv flat_field value as words for a sentence: 'nearest day, 2026-03-01' -> 'the slide images of
    the nearest day (2026-03-01)'."""
    where = " taken at other settings" if how.endswith(" (other settings)") else ""
    how = how.replace(" (other settings)", "")
    if how.startswith("nearest day, "):
        return f"the slide images of the nearest day ({how[len('nearest day, '):]}){where}"
    if how.startswith("average of "):
        return f"the average of the slide images of {how[len('average of '):]}{where}"
    return f"the {how}{where}"


def _date(day):
    try:
        return dt.date.fromisoformat(day)
    except ValueError:
        return None


def load(project, cfg):
    """The project's Correction, or None if it is off."""
    return Correction(project, cfg) if settings_of(cfg) else None


def record_build(project, key_rows):
    """outputs/flat_field/build.json: how many coded images were corrected with their own session's slide
    images, with another's, or not at all (for the home page, which never reads key.csv)."""
    rows = [r for r in key_rows if not r.get("repeat_of")]
    note = {"same": sum(r.get("flat_field") == SAME for r in rows),
            "other": sum(r.get("flat_field", "") not in (SAME, "") and not r["flat_field"].startswith("not corrected")
                         for r in rows),
            "none": sum(r.get("flat_field", "").startswith("not corrected") for r in rows)}
    (Path(project) / "outputs" / FOLDER / "build.json").write_text(json.dumps(note), encoding="utf-8")


def _what(ff):
    return " and ".join(x for x, on in (("across the field", ff.get("across_field")),
                                         ("between imaging sessions", ff.get("between_sessions"))) if on)


def log_lines(project, cfg):
    """For log.md at each setting and at Build; [] when the correction is off."""
    ff, sm = settings_of(cfg), summary(project)
    if not ff or not sm:
        return []
    ses = sm["sessions"]
    lines = [f"Flat-field correction: {_what(ff)}, from {plural(sm['reference_images'], 'slide image')} in "
             f"{plural(len(ses), 'imaging session')} (folder {sm['reference_folder']}, sha256 {sm['reference_sha256'][:16]}"
             + (f"; dark images from folder {sm['dark_folder']}, sha256 {sm.get('dark_sha256', '')[:16]}"
                if sm["dark_folder"] else "; no dark images, camera offset taken as 0")
             + f"; checksum {ff['checksum'][:16]})."]
    return lines + [session_line(s, ff) for s in ses]


def session_line(s, ff):
    return (f"Session {_label((s['system'], s['settings'], s['day']))}: {plural(s['images'], 'image')}, light across "
            f"the field {s['light_min_pct']} to {s['light_max_pct']}% of its mean"
            + (f", brightness factor {s['factor']}" if ff.get("between_sessions") else "")
            + (" (added after scoring began)" if s.get("added_after_scoring_began") else "") + ".")


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
            "maximum were found before correction and still counted as saturated. " + _later_sentence(project, ses, ff))


def _later_sentence(project, ses, ff):
    """Sessions added after scoring began, and images corrected with another session's slides or not at all."""
    out = ""
    late = sum(1 for s in ses if s.get("added_after_scoring_began"))
    if late:
        out += (f"Slide images for {late} later imaging session{'s were' if late != 1 else ' was'} added after "
                "scoring began, without changing the sessions already used. ")
    note = build_note(project) or {}
    if note.get("other"):
        n = note["other"]
        out += (f"{n} image{'s' if n != 1 else ''} from sessions without slide images {'were' if n != 1 else 'was'} "
                "corrected across the field with the slide images of the nearest session on the same microscope"
                + (" (their brightness between sessions was not corrected)" if ff.get("between_sessions") else "")
                + "; the pattern of light across the field changes little unless the microscope is realigned. ")
    if note.get("none"):
        n = note["none"]
        out += (f"{n} image{'s' if n != 1 else ''} had no usable slide images and {'were' if n != 1 else 'was'} "
                "used as recorded (listed in key.csv, column flat_field). ")
    return out
