"""What a new Scan data would change for images that already have a code (1.26.7).

Kept apart from pipeline.py because the home page asks every few seconds, and this loads no image library.
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from . import config as C
from .files import read_csv


def _saturation(row, cfg):
    """As pipeline.saturation_for, from an inventory.csv row."""
    return cfg["saturation_value"].get(row["system"], 2 ** int(row["bits"]) - 1)


def _group_letter(i):
    return chr(ord("A") + i) if i < 26 else f"G{i}"


def next_letter(letters):
    """The first settings-group letter no group has yet. Counting the groups (as before 1.26.10) gave a
    letter already in use when the letters had a gap, which a new scan can leave by merging two groups."""
    i = 0
    while _group_letter(i) in letters.values():
        i += 1
    return _group_letter(i)


LEICA_GAIN = re.compile(r"^[^;]*? gain (?=-?\d)")
OBJECTIVE_NUMBER = re.compile(r"\d+\.\d+(?=x/)|(?<=x/)\d+\.\d+")     # the two numbers of "40.0x/1.2000000000000002"


def tidy_number(text):
    """A number without the noise of how a program stored it: "40.0" is "40" and "1.2000000000000002" is
    "1.2". A number written as a person would ("1.40") is left as it is."""
    text = str(text)
    m = re.fullmatch(r"-?\d+\.(\d+)", text.strip())
    if m and (len(m.group(1)) > 6 or not m.group(1).strip("0")):
        short = repr(round(float(text), 6))
        return short[:-2] if short.endswith(".0") else short
    return text


def same_reading(system, settings):
    """One text for settings that 1.26.13 writes in another way than earlier versions, with the same
    meaning, so that a project scanned before and after is not split by the wording alone:
    - Leica: the detector is named as the file names it ("PMT gain 625.0"); it was always "HyD gain".
    - the objective of an OME-TIFF (in a flat-field session's text): "40.0x/1.2000000000000002" is "40x/1.2".
    Used only to compare; what is recorded is the text as Scan data read it."""
    settings = str(settings or "")
    if str(system or "").startswith("Leica"):
        settings = LEICA_GAIN.sub("HyD gain ", settings, count=1)
    return OBJECTIVE_NUMBER.sub(lambda m: tidy_number(m.group(0)), settings)


def reworded(pairs):
    """{(system, settings in the key): (system, settings as now scanned)} for coded images whose settings
    a new scan only words differently, or None if the scan changes more than the wording. It is only
    wording if each text in the key became exactly one text now and the reverse: "HyD gain 500" in the
    key that is now "PMT gain 500" for some images and "HyD gain 500" for others was two detectors
    (1.26.16; the two then shared a group when images were added, found by review)."""
    now, was = {}, {}
    for r, s in pairs:
        old, new = (r["system"], r["settings"]), (s["system"], s["settings"])
        if old[0] != new[0] or same_reading(*old) != same_reading(*new):
            return None
        now.setdefault(old, set()).add(new)
        was.setdefault(new, set()).add(old)
    if any(len(v) != 1 for v in now.values()) or any(len(v) != 1 for v in was.values()):
        return None
    return {old: next(iter(new)) for old, new in now.items() if old != next(iter(new))}


_scan_changes = {}


def scan_changes(project, cfg=None):
    """1.26.7: what the next build would change for images that already have a code, because Scan data
    now reads them differently than the key records (a newer wormscore reads more of the microscope
    settings and the bit depth, or the markers changed). Nothing changes for a project until it scans
    again, and then never silently: the Build card, the Build log and log.md say what changes.

    Returns {"lines": sentences, "letters": {(system, settings): letter} or None, "changed": bool}.
    letters keeps each settings group's letter where most of its images stay together, so only images
    that really change group are named. It holds counts and group letters only, never a genotype."""
    project = Path(project)
    key_path, inv_path = project / "key" / "key.csv", project / "outputs" / "inventory.csv"
    none = {"lines": [], "letters": None, "changed": False, "reworded": {}}
    if not key_path.is_file() or not inv_path.is_file():
        return none
    # the files' contents, not only their times: two writes within one tick of the clock (GitHub's Windows
    # machines; FAT32 and exFAT keep times to 2 seconds) kept the result of the first (1.26.18)
    stamp = tuple((p.stat().st_mtime_ns, hashlib.blake2b(p.read_bytes(), digest_size=16).digest())
                  for p in (key_path, inv_path, project / "config.json") if p.is_file())
    if _scan_changes.get(str(project), (None,))[0] == stamp:
        return _scan_changes[str(project)][1]
    cfg = cfg or C.load(project)
    images = {(s["local_path"], s["location_in_file"]): s for s in read_csv(inv_path)}
    rows = [r for r in read_csv(key_path) if not r.get("repeat_of") and (r["local_path"], r["location_in_file"]) in images]
    pairs = [(r, images[(r["local_path"], r["location_in_file"])]) for r in rows]
    out = dict(none)
    # 1.26.13: settings that are only worded differently (reworded) are not a change
    out["reworded"] = reworded(pairs)
    out["changed"] = out["reworded"] is None
    if pairs:      # also when nothing changed: a rebuild that keeps the codes takes its letters from the key (1.26.10)
        members = {}
        for r, s in pairs:
            members.setdefault((s["system"], s["settings"]), []).append(r["settings_group"])
        letters = {}
        for combo in sorted(members, key=lambda c: (-len(members[c]), c)):
            for old, _ in sorted(Counter(members[combo]).items(), key=lambda x: (-x[1], x[0])):
                if old not in letters.values():
                    letters[combo] = old
                    break
        i = 0
        for combo in sorted(members):
            while combo not in letters:
                if _group_letter(i) not in letters.values():
                    letters[combo] = _group_letter(i)
                i += 1
        out["letters"] = letters
        moves = Counter((r["settings_group"], letters[(s["system"], s["settings"])]) for r, s in pairs)
        out["lines"] += [f"{n} coded image{'s' if n != 1 else ''} move{'' if n != 1 else 's'} from settings group {a} to {b}"
                         for (a, b), n in sorted(moves.items()) if a != b]
    levels = Counter()
    for r, s in pairs:
        meta = project / "blind" / r["code"] / "meta.json"
        try:
            was = json.loads(meta.read_text())["saturation_value"]
        except (OSError, ValueError, KeyError):
            continue
        if was != _saturation(s, cfg):
            levels[(was, _saturation(s, cfg))] += 1
    sizes = Counter((float(r["pixel_um"]), float(s["pixel_um"] or 0)) for r, s in pairs
                    if r.get("pixel_um") not in (None, "") and abs(float(r["pixel_um"]) - float(s["pixel_um"] or 0)) > 1e-9)
    out["lines"] += [f"the pixel size of {n} coded image{'s' if n != 1 else ''} changes from {a:g} to {b:g} um"
                     for (a, b), n in sorted(sizes.items())]
    out["lines"] += [f"the saturation level of {n} coded image{'s' if n != 1 else ''} changes from {a} to {b}"
                     for (a, b), n in sorted(levels.items())]
    # 1.26.18: a Micro-Manager z-stack described only in its companion file is now read whole
    planes = Counter((int(r["n_z"]), int(s["n_z"])) for r, s in pairs
                     if str(r.get("n_z") or "").isdigit() and str(s.get("n_z") or "").isdigit() and int(r["n_z"]) != int(s["n_z"]))
    out["lines"] += [f"the number of planes of {n} coded image{'s' if n != 1 else ''} changes from {a} to {b}"
                     for (a, b), n in sorted(planes.items())]
    # 1.26.18: dates written day first with AM/PM have a flat-field day only from a new scan (acquired_day)
    if isinstance(cfg.get("flat_field"), dict):
        from .flatfield import DAY_COLUMN, acquired_day
        # the key's imaging_session names the day the build used ("system, 2009-07-20, settings ..."): an
        # image built from a scan that already read the day is not counted again
        from .flatfield import summary
        lacks = lambda r, s: s.get(DAY_COLUMN) and f", {s[DAY_COLUMN]}" not in (r.get("imaging_session") or "")
        # 1.26.18: Leica images have a date at all only from a new scan; the line stays until the slide
        # images are read with theirs (the flat-field summary says so), also after a rebuild
        read = bool((summary(project) or {}).get("leica_days"))
        leica = 0 if read else sum(1 for r, s in pairs if s.get("format") == "Leica LIF" and lacks(r, s))
        days = sum(1 for r, s in pairs if s.get("format") != "Leica LIF" and lacks(r, s)
                   and s[DAY_COLUMN] != acquired_day(r.get("acquired", "")))
        if leica:
            out["lines"] += [f"the flat-field imaging day of {leica} coded Leica image{'s' if leica != 1 else ''} is now "
                             f"read from the file; choose the slide images again so theirs are read the same way"]
        # a Leica date is given in the time zone of the computer that runs Scan data (found by review): a
        # scan elsewhere can move an image to the day before or after
        zone = lambda a: (a or "")[-6:] if (a or "")[-6:-5] in ("+", "-") else ""
        moved = sum(1 for r, s in pairs if s.get("format") == "Leica LIF" and r.get("acquired") and s.get("acquired")
                    and zone(r["acquired"]) != zone(s["acquired"]) and s.get(DAY_COLUMN)
                    and s[DAY_COLUMN] != acquired_day(r["acquired"]))
        if moved:
            out["lines"] += [f"the flat-field imaging day of {moved} coded Leica image{'s' if moved != 1 else ''} changes: "
                             f"this computer is in another time zone than the one that ran Scan data before; scan there, "
                             f"or choose the slide images again"]
        if days:
            out["lines"] += [f"the flat-field imaging day of {days} coded image{'s' if days != 1 else ''} is now read "
                             f"from {'their' if days != 1 else 'its'} date (written day first, with AM or PM); before "
                             f"scoring begins, this can change {'their' if days != 1 else 'its'} flat-field correction; choose "
                             f"the slide images again so theirs are read the same way"]
    _scan_changes[str(project)] = (stamp, out)
    return out
