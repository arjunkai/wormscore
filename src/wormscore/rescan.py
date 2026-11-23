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


# 1.26.22: which channel of its file each coded image was built from (key/channels.csv, written by the build)
CHANNELS_FILE = "channels.csv"
CHANNEL_COLUMNS = ("channel_names", "channel_roles", "marker_channel", "identity_channel", "bf_channel")
ROLE_COLUMNS = CHANNEL_COLUMNS[1:]


def channel_record(s):
    """The channels an image is built from, as key/channels.csv holds them (all text). s: an inventory
    row or a build's image record (channel_names and channel_roles as text or lists)."""
    out = {}
    for k in CHANNEL_COLUMNS:
        v = s.get(k)
        out[k] = "/".join(v) if isinstance(v, list) else "" if v is None else str(v)
    return out


def _used(rec, by_name):
    """The channels a coded image is drawn from: every role but OTHER (OTHER channels feed only quality-check
    numbers, never the coded image), each with its position, or (by_name) its channel's name; None when the
    file does not name every channel used."""
    roles = (rec.get("channel_roles") or "").split("/")
    names = (rec.get("channel_names") or "").split("/")
    used = [(i, r) for i, r in enumerate(roles) if r and r != "OTHER"]
    if not by_name:
        return sorted((r, i) for i, r in used)
    if len(names) != len(roles) or not all(names[i] for i, _ in used):
        return None
    return sorted((r, names[i]) for i, r in used)


def channels_differ(rec, s):
    """True when a new reading would build a coded image from other channels than its record (rec, a row of
    key/channels.csv): another channel as the reporter, the identity channel, brightfield or an extra identity
    channel. A difference both by position and by channel name (when the file names its channels): a channel
    read in addition (positions move, names stay) or one only renamed (names change, positions stay) is not."""
    now = channel_record(s)
    if _used(rec, False) == _used(now, False):
        return False
    a, b = _used(rec, True), _used(now, True)
    return a is None or b is None or a != b


def _which(pairs_rec):
    """ "its reporter and identity channels" (one image), or "the reporter channel of 2, the identity
    channel of 1" (several)."""
    counts = Counter()
    for c, s in pairs_rec:
        now = channel_record(s)
        moved = [name for col, name in (("marker_channel", "reporter"), ("identity_channel", "identity"),
                                        ("bf_channel", "brightfield")) if (c.get(col) or "") != now[col]]
        for name in moved or ["extra identity"]:
            counts[name] += 1
    names = [k for k in ("reporter", "identity", "brightfield", "extra identity") if counts[k]]
    if len(pairs_rec) == 1:
        return "its " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]) +             f" channel{'s' if len(names) != 1 else ''}"
    return ", ".join(f"the {k} channel of {counts[k]}" for k in names)


def locked_codes(project):
    """(originals with a saved score, their own or a hidden repeat's; codes in a second-scorer package), for the
    Build card's channel lines. Anything unreadable counts as nothing, so the card never fails on it."""
    project = Path(project)
    scored, packaged = set(), set()
    try:
        repeat_of = {r["code"]: r.get("repeat_of") or r["code"] for r in read_csv(project / "key" / "key.csv")}
        if (project / "scoring" / "scores.csv").is_file():
            scored = {repeat_of.get(r.get("code"), r.get("code")) for r in read_csv(project / "scoring" / "scores.csv")}
    except (OSError, KeyError, ValueError):
        pass
    for f in (project / "scorers").glob("*/package.json") if (project / "scorers").is_dir() else []:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            packaged |= set(rec.get("originals") or []) | set(rec.get("order") or [])
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return scored, packaged


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
    none = {"lines": [], "kinds": [], "letters": None, "changed": False, "reworded": {}, "channel_codes": {}}
    if not key_path.is_file() or not inv_path.is_file():
        return none
    # the files' contents, not only their times: two writes within one tick of the clock (GitHub's Windows
    # machines; FAT32 and exFAT keep times to 2 seconds) kept the result of the first (1.26.18)
    stamp = tuple((p.stat().st_mtime_ns, hashlib.blake2b(p.read_bytes(), digest_size=16).digest())
                  for p in (key_path, inv_path, project / "config.json", project / "key" / CHANNELS_FILE) if p.is_file())
    chan_file = project / "key" / CHANNELS_FILE
    if _scan_changes.get(str(project), (None,))[0] == stamp:
        return _scan_changes[str(project)][1]
    cfg = cfg or C.load(project)
    images = {(s["local_path"], s["location_in_file"]): s for s in read_csv(inv_path)}
    rows = [r for r in read_csv(key_path) if not r.get("repeat_of") and (r["local_path"], r["location_in_file"]) in images]
    pairs = [(r, images[(r["local_path"], r["location_in_file"])]) for r in rows]
    rec = {r["code"]: r for r in read_csv(chan_file)} if chan_file.is_file() else {}    # 1.26.22
    out = {**none, "lines": [], "kinds": [], "channel_codes": {}}
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
    # 1.26.23: the objective of an image saved by LAS AF (TCS SP5) is read; it is named in the Methods draft
    # and decides the minimum magnification
    objs = Counter(" ".join(s["objective"].split()) for r, s in pairs
                   if not (r.get("objective") or "").strip() and (s.get("objective") or "").strip())
    if objs:
        from .inclusion import below_minimum
        n = sum(objs.values())
        low = sum(1 for r, s in pairs if not (r.get("objective") or "").strip() and (s.get("objective") or "").strip()
                  and below_minimum(s.get("objective"), cfg))
        out["lines"].append(f"the objective of {n} coded image{'s' if n != 1 else ''} is now read from the file "
                            f"({'; '.join(sorted(objs))})" + (
                                f"; {low} below the minimum ({cfg['inclusion']['min_objective_magnification']}x) "
                                f"stay{'s' if low == 1 else ''} in, as {'it was' if low == 1 else 'they were'} coded "
                                f"before it was read" if low else ""))
    # 1.26.23: a coded image the inclusion rules now leave out (a newer reading, or a rule changed): a rebuild
    # that keeps the codes stops ("The included images differ from the existing key"), and the card said
    # nothing about it before
    left = Counter()
    try:
        from .inclusion import include_reason, typed       # not pipeline: it loads the image libraries
        for r, s in pairs:
            reason = " ".join(include_reason(typed(s), cfg, coded=True).split()).rstrip(".")
            if reason:
                left[reason] += 1
    except (KeyError, ValueError, TypeError):
        left = Counter()            # an inventory the build could not read either: the build says so
    out["kinds"] = ["rebuild"] * len(out["lines"])      # each of these a rebuild that keeps the codes applies
    out["lines"] += [f"{n} coded image{'s' if n != 1 else ''} no longer pass{'' if n != 1 else 'es'} the inclusion "
                     f"rules: {reason}" for reason, n in sorted(left.items())]
    out["kinds"] += ["left"] * len(left)
    # 1.26.22: a scan that names other channels of a coded image's file as the reporter, the identity channel
    # or brightfield than the build used (key/channels.csv). Before, nothing compared them, so a rebuild after
    # a newer reading could show a scored image's other channel without a word.
    differ = [(r["code"], rec[r["code"]], s) for r, s in pairs if r["code"] in rec and channels_differ(rec[r["code"]], s)]
    if differ:
        n = len(differ)
        out["lines"].append(f"{n} coded image{'s' if n != 1 else ''} would be built from other channels than before, "
                            f"as Scan data now reads {'them' if n != 1 else 'it'} ({_which([(c, s) for _, c, s in differ])})")
        out["kinds"].append("channels")
        out["channel_codes"][out["lines"][-1]] = sorted(code for code, _, _ in differ)
    if cfg.get("identity"):
        # built before 1.26.22 (or added to such a project): only whether each image had an identity channel is
        # recorded (key.csv), so that is what is compared for images without a record
        old = [(r, s) for r, s in pairs if r["code"] not in rec]
        gained = sorted(r["code"] for r, s in old if r.get("identity_channel_used") == "False" and s.get("identity_channel"))
        lost = sorted(r["code"] for r, s in old if r.get("identity_channel_used") == "True" and not s.get("identity_channel"))
        for codes, text in ((gained, "now finds an identity channel in {n} coded image{s} built without one"),
                            (lost, "no longer finds the identity channel of {n} coded image{s} built with one")):
            if codes:
                out["lines"].append("Scan data " + text.format(n=len(codes), s="s" if len(codes) != 1 else ""))
                out["kinds"].append("channels-unrecorded")
                out["channel_codes"][out["lines"][-1]] = codes
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
        zone = lambda a: (a or "")[-6:] if (a or "")[-6:-5] in ("+", "-") else ""
        # 1.26.20: a time written in UTC (Zeiss, FV3000 2.x, OME) is given in the computer's time zone; the
        # line stays until the slide images are read the same way (the flat-field summary says so)
        from .readers import UTC_FORMATS
        # (only such a time gets an offset in these formats; the build has not used its day yet, also after a
        # rebuild, until the slide images are read the same way: found by review)
        now_local = lambda r, s: s.get("format") in UTC_FORMATS and zone(s.get("acquired"))
        # the build corrected the image with another day's slide images ("nearest day, 2026-03-06")
        elsewhere = lambda r, s: (r.get("flat_field") or "same session") != "same session" and s.get(DAY_COLUMN)             and s[DAY_COLUMN] not in (r.get("flat_field") or "")
        other_zone = lambda r, s: zone(r.get("acquired")) and zone(r["acquired"]) != zone(s.get("acquired"))
        utc = 0 if (summary(project) or {}).get("utc_days") else sum(
            1 for r, s in pairs if now_local(r, s) and not other_zone(r, s) and (lacks(r, s) or elsewhere(r, s)))
        # (a date with a time zone is never one written day first)
        days = sum(1 for r, s in pairs if s.get("format") != "Leica LIF" and not zone(s.get("acquired")) and lacks(r, s)
                   and s[DAY_COLUMN] != acquired_day(r.get("acquired", "")))
        if leica:
            out["lines"] += [f"the flat-field imaging day of {leica} coded Leica image{'s' if leica != 1 else ''} is now "
                             f"read from the file; choose the slide images again so theirs are read the same way"]
            out["kinds"].append("slides")
        # 1.26.20: a Zeiss laser scanning microscope's scan zoom is read (before, a detector's: 1). An image
        # whose zoom the slide images were not read with: slide images of its settings and objective at
        # another zoom, none at its own; until they are chosen again (the summary then says scan_zoom), also
        # after a rebuild. Counted from the sessions, not the value, so a zoom that did not change is never
        # counted (found by review)
        from .flatfield import session_key
        sessions = [] if (summary(project) or {}).get("scan_zoom") else (summary(project) or {}).get("sessions") or []

        def other_zoom(s, among=None):
            system, settings, _ = session_key({k: v for k, v in s.items() if k != DAY_COLUMN})
            plain = " | ".join(x for x in (str(s.get("settings") or ""), str(s.get("objective") or "")) if x)
            mine = [x for x in (sessions if among is None else among) if x.get("system") == system]
            return not any(x.get("settings") == settings for x in mine) and any(
                x.get("settings") != settings and (not plain or x.get("settings") == plain
                                                    or str(x.get("settings")).startswith(plain + " | "))
                for x in mine)
        zoomed = sum(1 for r, s in pairs if s.get("format") in ("Zeiss CZI", "Olympus FluoView") and other_zoom(s))
        # 1.26.25: a Leica zoom is read to the two decimals LAS X shows; until the slide images are read the same
        # way (the summary then says leica_zoom), also after a rebuild
        # only after a new scan read them so (found by review: a project measured before 1.26.25 has the very
        # split this fixes, and its card would have said so without any change)
        from .flatfield import leica_zoom_from_scan
        held_ = summary(project) or {}
        leica_zoomed = 0 if held_.get("leica_zoom") or not leica_zoom_from_scan(project) else sum(
            1 for r, s in pairs if s.get("format") == "Leica LIF" and other_zoom(s, held_.get("sessions") or []))
        if leica_zoomed:
            out["lines"] += [f"the flat-field zoom of {leica_zoomed} coded Leica image{'s' if leica_zoomed != 1 else ''} "
                             f"is now read to two decimals, as LAS X shows it; choose the slide images again so "
                             f"theirs are read the same way"]
            out["kinds"].append("slides")
        if zoomed:
            out["lines"] += [f"the flat-field zoom of {zoomed} coded image{'s' if zoomed != 1 else ''} is now the "
                             f"scan zoom; choose the slide images again so theirs are read the same way"]
            out["kinds"].append("slides")
        # 1.26.23: an image saved by LAS AF (a TCS SP5; Scan data names its model) now has its objective and zoom,
        # which are part of its flat-field session; until the slide images are read the same way (the summary
        # then says lasaf_objective), also after a rebuild
        held = summary(project) or {}
        lasaf = 0 if not held.get("sessions") or held.get("lasaf_objective") else sum(
            1 for r, s in pairs if s.get("format") == "Leica LIF" and s.get("model")
            and ((s.get("objective") or "").strip() or (s.get("zoom") or "").strip()))
        if lasaf:
            out["lines"] += [f"the flat-field objective and zoom of {lasaf} coded Leica image{'s' if lasaf != 1 else ''} "
                             f"(LAS AF) are now read from the file; choose the slide images again so theirs are read "
                             f"the same way"]
            out["kinds"].append("slides")
        if utc:
            out["lines"] += [f"the flat-field imaging day of {utc} coded image{'s' if utc != 1 else ''} changes: the file "
                             f"records the time in UTC, now read in this computer's time zone; choose the slide images "
                             f"again so theirs are read the same way"]
            out["kinds"].append("slides")
        # a Leica (or UTC) date is given in the time zone of the computer that runs Scan data (found by
        # review): a scan elsewhere can move an image to the day before or after
        moved = sum(1 for r, s in pairs if s.get("format") in ("Leica LIF",) + UTC_FORMATS
                    and zone(r.get("acquired")) and zone(s.get("acquired"))
                    and zone(r["acquired"]) != zone(s["acquired"]) and s.get(DAY_COLUMN)
                    and s[DAY_COLUMN] != acquired_day(r["acquired"]))
        if moved:
            out["lines"] += [f"the flat-field imaging day of {moved} coded image{'s' if moved != 1 else ''} changes: "
                             f"this computer is in another time zone than the one that ran Scan data before; scan there, "
                             f"or choose the slide images again"]
            out["kinds"].append("zone")
        if days:
            out["lines"] += [f"the flat-field imaging day of {days} coded image{'s' if days != 1 else ''} is now read "
                             f"from {'their' if days != 1 else 'its'} date (written day first, with AM or PM); before "
                             f"scoring begins, this can change {'their' if days != 1 else 'its'} flat-field correction; choose "
                             f"the slide images again so theirs are read the same way"]
            out["kinds"].append("day")
    # 1.26.23: "left" (a coded image no longer passes the inclusion rules: a rebuild that keeps the codes
    # would stop until the image or the rule is restored)
    # 1.26.21: kinds says what each line asks for, for the Build card: "rebuild" (a rebuild that keeps the
    # codes applies it, and the line goes); "slides" (it stays, also after a rebuild, until the slide images
    # are chosen again, which rebuilds the blind set itself); "day" (a rebuild applies it with the nearest
    # day's slide images; choosing them again gives the day its own); "zone" (a rebuild would apply the
    # other time zone's day: scan on the computer that scanned before)
    assert len(out["kinds"]) == len(out["lines"])
    _scan_changes[str(project)] = (stamp, out)
    return out


def scan_change_lines(project, changes):
    """1.26.21: scan_changes' lines as the Build card shows them: every line, each a sentence with its own
    next step (scan_changes' kinds). Once scoring has begun, choosing the slide images again is no longer
    offered and a rebuild keeps every coded image's flat-field correction as recorded
    (Correction.keep_coded), so a line about the flat-field day or zoom asks for nothing and is left out
    (the Build log still says which images keep their correction)."""
    from .flatfield import lock_reason
    steps = {
        "rebuild": "Rebuild, keeping the current codes, to use it.",
        "slides": "Choose the slide images again to use it (Flat-field correction, Sessions..., Start again).",
        "day": "Choose the slide images again to use it (Flat-field correction, Sessions..., Start again). "
               "A rebuild alone uses it with the nearest day's slide images.",
        "zone": "Scan again on the computer that scanned before, or choose the slide images again. "
                "Do not rebuild before that.",
        "left": "Until every coded image passes again, a rebuild that keeps the codes, and Add new images, would "
                "stop. If a setting caused this, setting it back lets the build go on.",
    }
    # 1.26.23: while a coded image no longer passes, a rebuild that keeps the codes stops (choosing the slide
    # images again ends in one too), so every other step waits for it (found by review)
    if "left" in changes["kinds"]:
        for kind in ("rebuild", "slides", "day", "zone"):
            steps[kind] = "Once every coded image passes the inclusion rules again: " + steps[kind][:1].lower() + steps[kind][1:]
        if not lock_reason(project):
            steps["left"] += " Before scoring begins, Rebuild with NEW codes builds without them."
    locked = None
    out = []
    lefts = changes["kinds"].count("left")       # the "left" step is said once, after the last such line
    for line, kind in zip(changes["lines"], changes["kinds"]):
        if kind == "left":
            lefts -= 1
            if lefts:
                out.append(f"{line[:1].upper()}{line[1:]}.")
                continue
        if kind.startswith("channels"):
            # 1.26.22: a rebuild always uses the new reading; the line says how many of these images are already
            # scored or in a second scorer's package (counts only), as those would change
            codes = set(changes.get("channel_codes", {}).get(line, []))
            scored, packaged = locked_codes(project)
            ns, np_ = len(codes & scored), len(codes & packaged)
            step = steps["rebuild"]
            one = len(codes) == 1
            if ns:
                step += " It is already scored and would look different." if one else                     f" {ns} of them {'are' if ns != 1 else 'is'} already scored and would look different."
            if np_:
                step += (" It is" if one else f" {np_} {'are' if np_ != 1 else 'is'}") +                     " in a second scorer's package, which could then no longer be imported."
            if ns or np_:
                step += " The Build log will name the codes."
            out.append(f"{line[:1].upper()}{line[1:]}. {step}")
            continue
        if kind not in ("rebuild", "left"):
            if locked is None:
                locked = bool(lock_reason(project))
            if locked:
                continue
        what = line
        for cut in ("; before scoring begins,", "; choose the slide images again", "; scan there,"):
            what = what.split(cut)[0]
        out.append(f"{what[:1].upper()}{what[1:]}. {steps[kind]}")
    return out
