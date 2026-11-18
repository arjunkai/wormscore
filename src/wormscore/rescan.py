"""What a new Scan data would change for images that already have a code (1.26.7).

Kept apart from pipeline.py because the home page asks every few seconds, and this loads no image library.
"""
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
NUMBER = re.compile(r"\d+\.\d+")


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
    return NUMBER.sub(lambda m: tidy_number(m.group(0)), settings)


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
    none = {"lines": [], "letters": None, "changed": False}
    if not key_path.is_file() or not inv_path.is_file():
        return none
    stamp = tuple(p.stat().st_mtime_ns for p in (key_path, inv_path, project / "config.json") if p.is_file())
    if _scan_changes.get(str(project), (None,))[0] == stamp:
        return _scan_changes[str(project)][1]
    cfg = cfg or C.load(project)
    images = {(s["local_path"], s["location_in_file"]): s for s in read_csv(inv_path)}
    rows = [r for r in read_csv(key_path) if not r.get("repeat_of") and (r["local_path"], r["location_in_file"]) in images]
    pairs = [(r, images[(r["local_path"], r["location_in_file"])]) for r in rows]
    out = dict(none)
    # 1.26.13: settings that only read differently (same_reading) are not a change
    out["changed"] = any((r["system"], same_reading(r["system"], r["settings"]))
                         != (s["system"], same_reading(s["system"], s["settings"])) for r, s in pairs)
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
    _scan_changes[str(project)] = (stamp, out)
    return out
