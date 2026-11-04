"""Image folders and genotype rules, edited from the home page instead of by hand in config.json.

Only these fields of each source are edited here: name, path, original_path, genotypes,
default_genotype and exclude. Every other setting of a source (note, imagej_tiff) and every other
part of config.json is kept exactly as it was. The whole file is checked with config.check before
it is written, so the app can never save a config that the next step would refuse.

Once scoring has started, the folders and rules that built the blind set are locked: changing
them would change which images belong to which genotype after someone has looked at them. New
folders can still be added, for the Add new images step.
"""
import json
from pathlib import Path

from . import config as C

FIELDS = ("name", "path", "original_path", "genotypes", "default_genotype", "exclude")
MAX_TEXT = 500


def scoring_started(project):
    d = Path(project) / "scoring"
    return d.is_dir() and any(d.iterdir())


def _raw(project):
    return json.loads((Path(project) / "config.json").read_text(encoding="utf-8"))


def current(project):
    """What the settings dialog shows: the project name and the editable part of each source."""
    from .scorers import package_info
    project = Path(project)
    if package_info(project):
        raise ValueError("This is a second scorer's package. Its settings come from the original project.")
    raw = _raw(project)
    sources = []
    for s in raw.get("sources", []):
        sources.append({k: s.get(k, [] if k in ("genotypes", "exclude") else "") for k in FIELDS}
                       | {"default_genotype": s.get("default_genotype", "UNKNOWN")})
    from . import scoreform
    return {"project_name": raw.get("project_name", ""), "sources": sources,
            "locked": len(sources) if scoring_started(project) else 0, "form": scoreform.current(project)}


def _text(v, what, required=True):
    if not isinstance(v, str):
        raise ValueError(f"{what} must be text.")
    v = v.strip()
    if required and not v:
        raise ValueError(f"{what} is empty.")
    if len(v) > MAX_TEXT or any(ord(c) < 32 for c in v):
        raise ValueError(f"{what} is too long or has a line break in it.")
    return v


def _rules(rows, a, b, where):
    if not isinstance(rows, list):
        raise ValueError(f"{where}: the rules must be a list.")
    out = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise ValueError(f"{where}: rule {i} is not filled in.")
        out.append({a: _text(r.get(a), f"{where}, rule {i}: the pattern"),
                    b: _text(r.get(b), f"{where}, rule {i}: the {'genotype' if b == 'genotype' else 'reason'}")})
    return out


def clean(body):
    """The dialog's answers, checked field by field. Returns (project_name, sources)."""
    name = _text(body.get("project_name"), "The project name")
    rows = body.get("sources")
    if not isinstance(rows, list):
        raise ValueError("No image folders were sent.")
    sources, seen = [], set()
    for i, s in enumerate(rows, 1):
        if not isinstance(s, dict):
            raise ValueError(f"Image folder {i} is not filled in.")
        label = _text(s.get("name"), f"Image folder {i}: the name")
        if label.lower() in seen:
            raise ValueError(f"Two image folders are called \"{label}\". Give each its own name.")
        seen.add(label.lower())
        where = f"Folder \"{label}\""
        sources.append({
            "name": label,
            "path": _text(s.get("path"), f"{where}: the folder"),
            "original_path": _text(s.get("original_path", ""), f"{where}: where the files first came from", False),
            "genotypes": _rules(s.get("genotypes", []), "match", "genotype", where),
            "default_genotype": _text(s.get("default_genotype") or "UNKNOWN", f"{where}: the default genotype"),
            "exclude": _rules(s.get("exclude", []), "match", "reason", where),
        })
    return name, sources


def merged(project, body):
    """The new raw config.json: edited fields replaced, everything else kept."""
    project = Path(project)
    info = current(project)              # refuses packages
    raw = _raw(project)
    name, sources = clean(body)
    old = raw.get("sources", [])
    locked = info["locked"]
    if locked:
        if len(sources) < locked or any(_editable(old[i]) != _editable(sources[i]) for i in range(locked)):
            raise ValueError("Scoring has started, so the folders and rules that built the blind set cannot be "
                             "changed or removed. You can add a new folder for Add new images.")
    new_sources = []
    for i, s in enumerate(sources):
        frm = _origin(body, i, len(old))
        keep = dict(old[frm]) if frm is not None else {}
        for k in FIELDS:
            keep.pop(k, None)
        entry = {"name": s["name"], "path": s["path"]}
        if s["original_path"]:
            entry["original_path"] = s["original_path"]
        entry["genotypes"] = s["genotypes"]
        entry["default_genotype"] = s["default_genotype"]
        if s["exclude"]:
            entry["exclude"] = s["exclude"]
        new_sources.append(entry | keep)
    out = dict(raw)
    out["project_name"] = name
    out["sources"] = new_sources
    return out


def _editable(s):
    return {"name": s.get("name", ""), "path": s.get("path", ""), "original_path": s.get("original_path", ""),
            "genotypes": [{"match": r.get("match"), "genotype": r.get("genotype")} for r in s.get("genotypes", [])],
            "default_genotype": s.get("default_genotype", "UNKNOWN"),
            "exclude": [{"match": r.get("match"), "reason": r.get("reason")} for r in s.get("exclude", [])]}


def _origin(body, i, n_old):
    """The position in config.json that row i of the dialog was loaded from, or None for a new
    folder. A row keeps that folder's other settings (note, imagej_tiff)."""
    rows = body.get("sources") or []
    frm = rows[i].get("from") if i < len(rows) and isinstance(rows[i], dict) else None
    return frm if isinstance(frm, int) and not isinstance(frm, bool) and 0 <= frm < n_old else None


MAX_REASON = 500


def unlock_reason(body):
    """The reason typed when a locked form is changed anyway, or "" if none was given."""
    r = body.get("unlock_reason")
    return " ".join(r.split())[:MAX_REASON] if isinstance(r, str) else ""


def form_change(project, old_raw, raw, form, reason=""):
    """raw with the score form from the builder applied, or raw unchanged if the form was not
    changed. Refused if the builder cannot show this form, and once the form is locked unless a
    reason is given for changing it anyway (never while a second-scorer package exists: its
    scores must come back with the form it was made with)."""
    from . import scoreform as S
    if form is None:
        return raw, False
    old_cfg = S._cfg_from_raw(old_raw)
    new = S.clean(form)
    try:
        same = new == S.clean(S.to_model_cfg(old_cfg))
    except ValueError:
        same = False
    if same:
        return raw, False
    why = S.locked(project)
    if why and (not reason or S.packages_exist(project)):
        raise ValueError(f"{why} The criteria were not changed; only the other settings can be saved.")
    if S.unsupported(old_cfg):
        raise ValueError("These criteria use settings the builder cannot show. Change them in config.json.")
    return S.apply(raw, form), True


def save(project, body):
    from . import scoreform as S
    from .pipeline import append_log
    project = Path(project)
    old_raw = _raw(project)
    raw = merged(project, body)
    was_locked = bool(S.locked(project))
    reason = unlock_reason(body) if was_locked else ""
    raw, form_changed = form_change(project, old_raw, raw, body.get("form"), reason)
    try:
        cfg = S._cfg_from_raw(raw)
        C.check(cfg)
    except SystemExit as exc:
        raise ValueError(f"config.json would not be valid: {exc}")
    # Opened from the Score form step and saved unchanged: the scorer checked the form and kept
    # it, so it is recorded too and the step counts as done.
    checked = (not form_changed and body.get("form_checked") is True and body.get("form") is not None
               and not S.locked(project) and not S.form_recorded(project, cfg))
    if raw != old_raw:
        write(project, raw)
    if form_changed and was_locked:
        same = S.form_record(cfg) == S.form_record(S._cfg_from_raw(old_raw))
        append_log(project, S.CHANGE_TITLE,
                   [f"Reason given: {reason}",
                    "Only wording changed (question text or rules), so the planned tests still run." if same else
                    "The answers, neurons, required or skipped questions, or what counts as a defect changed. "
                    "Unblinding writes only descriptive tables and runs no planned test while the criteria differ "
                    "from the one recorded before the first score; changing it back runs them again.",
                    # not the "set before scoring" prefix: unblinding must keep checking the earlier form
                    "Score form after this change: " + S.record_text(cfg)])
    elif form_changed:
        append_log(project, "score form saved in Project settings",
                   ["Saved before the first score; the last form recorded before the first score is the one "
                    "unblinding checks."] + S.form_log_lines(cfg))
    elif checked:
        append_log(project, "score form checked in Project settings",
                   ["Checked before the first score and kept as it was."] + S.form_log_lines(cfg))
    return {"ok": True, "changed": raw != old_raw, "form_changed": form_changed or checked,
            "after_lock": bool(form_changed and was_locked)}


def write(project, raw):
    """Back up config.json to config_backups/, then replace it in one step, so a crash while
    writing cannot leave half a file."""
    import datetime as dt
    import shutil
    path = Path(project) / "config.json"
    backups = path.parent / "config_backups"
    backups.mkdir(exist_ok=True)
    shutil.copy2(path, backups / f"config-{dt.datetime.now():%Y%m%d-%H%M%S-%f}.json")
    tmp = path.with_name("config.json.tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def preview(project, body):
    """How the rules sort the images already scanned: per folder, images per genotype and images
    left out. Works on the unsaved answers, so mistakes show before saving."""
    from .pipeline import read_csv
    project = Path(project)
    name, sources = clean(body)
    inv = project / "outputs" / "inventory.csv"
    rows = read_csv(inv) if inv.is_file() else None
    old = _raw(project).get("sources", [])
    out = []
    for i, s in enumerate(sources):
        frm = _origin(body, i, len(old))
        old_name = old[frm].get("name") if frm is not None else None
        item = {"name": s["name"], "folder_found": Path(s["path"]).is_dir(), "images": None, "genotypes": {}, "left_out": 0}
        if rows is not None and old_name is not None:
            mine = [r for r in rows if r.get("source") == old_name]
            item["images"] = len(mine)
            for r in mine:
                rel = r.get("relative_path", "")
                if any(C.matches(x["match"], rel) for x in s["exclude"]):
                    item["left_out"] += 1
                    continue
                g = C.genotype_for(s, rel)
                item["genotypes"][g] = item["genotypes"].get(g, 0) + 1
        out.append(item)
    return {"ok": True, "scanned": rows is not None, "sources": out}
