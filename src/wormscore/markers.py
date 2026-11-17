"""The reporter and identity channels, chosen in Project settings instead of by hand in config.json (1.24.0).

Only presets are offered here. A channel with its own rules (dye names, wavelengths, a changed color) is
shown as "custom" and kept exactly as it is unless another choice is made. Nothing is written to
config.json unless a choice changes, so a project saved without touching the markers keeps its
config.json byte for byte.

Channel roles are worked out at Scan data, so a change takes effect after Scan data and a rebuild. The
time of the last change is kept in outputs/channels_changed.json, and the home page asks for those two
steps until they have run. Once scoring has started, the markers are locked; "Change anyway..." with a
reason allows a change, which goes to log.md with the reason.
"""
import json
import os
from pathlib import Path

from . import config as C

CUSTOM = "custom"
NEUROPAL = "NeuroPAL"
CHANGED_FILE = ("outputs", "channels_changed.json")
CHANGE_TITLE = "markers changed after scoring began"
MAX_REASON = 500
_IGNORE = ("preset", "spots_from", "required")


def _same(a, b):
    return {k: v for k, v in a.items() if k not in _IGNORE} == {k: v for k, v in b.items() if k not in _IGNORE}


def reporter_choice(raw):
    """The preset the reporter matches exactly, or "custom"."""
    try:
        have = C.resolve_marker(raw.get("marker"))
    except SystemExit:
        return CUSTOM
    have.setdefault("color", [0, 1, 0])   # the defaults have none: green is drawn (config.marker_color)
    for name in C.PRESETS:
        if _same(have, C.resolve_marker({"preset": name})):
            return name
    return CUSTOM


def identity_choice(raw):
    """(choice, spots_from): choice is "" (none), a preset, "NeuroPAL" or "custom"."""
    ident = raw.get("identity")
    if not ident:
        return "", "identity"
    if isinstance(ident, dict) and str(ident.get("preset", "")).lower() == "neuropal":
        extra = set(ident) - {"preset", "spots_from"}
        return (CUSTOM if extra else NEUROPAL), ident.get("spots_from", "identity")
    if isinstance(ident, dict):
        try:
            have = C.resolve_identity(ident)
        except SystemExit:
            return CUSTOM, ident.get("spots_from", "identity")
        if not (set(ident) - {"preset", "spots_from", "required"}) and ident.get("required", True) is True:
            for name in C.PRESETS:
                if _same(have, C.resolve_identity({"preset": name})):
                    return name, ident.get("spots_from", "identity")
        return CUSTOM, ident.get("spots_from", "identity")
    return CUSTOM, "identity"


def current(project, raw):
    """What the Markers card shows."""
    rep = reporter_choice(raw)
    ident, spots = identity_choice(raw)
    return {"reporter": rep, "identity": ident, "spots_from": spots, "presets": list(C.PRESETS),
            "locked": _scoring_started(project)}


def _scoring_started(project):
    d = Path(project) / "scoring"
    return d.is_dir() and any(d.iterdir())


def describe(rep, ident):
    i = "none" if not ident else ("NeuroPAL (4 channels)" if ident == NEUROPAL else ident)
    return rep, i


def unlock_reason(body):
    r = body.get("unlock_reason") if isinstance(body, dict) else None
    return " ".join(r.split())[:MAX_REASON] if isinstance(r, str) else ""


def change(project, old_raw, raw, body):
    """raw with the Markers card's choices applied, and the log.md lines ([] if nothing changed).
    body is {"reporter", "identity", "spots_from", "unlock_reason"?} or None (card not sent)."""
    if not isinstance(body, dict):
        return raw, []
    rep0 = reporter_choice(old_raw)
    id0, sp0 = identity_choice(old_raw)
    rep, ident = body.get("reporter"), body.get("identity", "")
    spots = body.get("spots_from", sp0)
    names = set(C.PRESETS)
    if rep not in names | {rep0}:
        raise ValueError("Choose the reporter from the list.")
    if ident not in names | {"", NEUROPAL, id0}:
        raise ValueError("Choose the identity channel from the list.")
    if spots not in C.SPOTS_FROM:
        raise ValueError("Choose where suggested cells come from.")
    if not ident or ident == CUSTOM:
        spots = sp0                       # nothing to choose without a preset identity channel
    if (rep, ident, spots) == (rep0, id0, sp0):
        return raw, []
    if _scoring_started(project) and not unlock_reason(body):
        raise ValueError("Scoring has started, so the markers are locked. Use Change anyway... to change them "
                         "with a reason, which goes into log.md.")
    used = [ident] if ident in names else ([x["preset"] for x in C.NEUROPAL] if ident == NEUROPAL else [])
    if rep in used:
        raise ValueError(f"The identity channel uses {rep}, which is also the reporter. Choose another for one of them.")
    out = dict(raw)
    if rep != rep0:
        out["marker"] = {"preset": rep}
    if (ident, spots) != (id0, sp0):
        if not ident:
            out.pop("identity", None)
        elif ident == CUSTOM:
            pass
        else:
            out["identity"] = {"preset": ident} | ({"spots_from": spots} if spots != "identity" else {})
    a, b = describe(rep0, id0), describe(rep, ident)
    lines = []
    if rep != rep0:
        lines.append(f"Reporter: {a[0]} -> {b[0]}.")
    if ident != id0:
        lines.append(f"Identity channel: {a[1]} -> {b[1]}.")
    if spots != sp0 and ident:
        lines.append(f"Suggested cells from: {sp0} -> {spots}.")
    return out, lines


def record_change(project, lines, reason=""):
    """Write the change to log.md and mark Scan data and Build as needing a new run."""
    from .files import append_log
    project = Path(project)
    if reason:
        append_log(project, CHANGE_TITLE, [f"Reason given: {reason}"] + lines
                   + ["Takes effect after Scan data and a rebuild that keeps the codes. Scores already saved do not change."])
    else:
        append_log(project, "markers changed in Project settings", lines)
    f = project.joinpath(*CHANGED_FILE)
    if (project / "outputs" / "inventory.csv").is_file():
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps({"lines": lines}) + "\n", encoding="utf-8")
    elif f.is_file():
        f.unlink()                        # nothing scanned yet: the first scan uses the new markers


def stale(project):
    """{"scan": bool, "build": bool} if the markers changed after the last Scan data or Build, else None."""
    project = Path(project)
    f = project.joinpath(*CHANGED_FILE)
    if not f.is_file():
        return None
    t = os.path.getmtime(f)
    inv, key = project / "outputs" / "inventory.csv", project / "key" / "key.csv"
    scan = not inv.is_file() or os.path.getmtime(inv) < t
    build = key.is_file() and os.path.getmtime(key) < t
    return {"scan": scan, "build": build} if scan or build else None
