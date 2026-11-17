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
# A project name is shown in window titles, the project list and package names, and is the
# default name of exported criteria (at most 80): a new or changed name is kept to 80 characters.
# A longer name already in config.json is kept as it is, so older projects still open and save.
MAX_NAME = 80


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
    from . import analysis
    from . import markers
    return {"project_name": raw.get("project_name", ""), "sources": sources,
            "locked": len(sources) if scoring_started(project) else 0, "form": scoreform.current(project),
            "analysis": analysis.current(project, scoreform._cfg_from_raw(raw)), "markers": markers.current(project, raw)}


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
    if "sources" not in body and "project_name" not in body:
        return raw                       # the Scoring criteria page: only the criteria change
    name, sources = clean(body)
    if len(name) > MAX_NAME and name != raw.get("project_name"):
        raise ValueError(f"The project name is longer than {MAX_NAME} characters. Shorten it; "
                         "the full title of the study can go in the Methods.")
    old = raw.get("sources", [])
    locked = info["locked"]
    if locked:
        if len(sources) < locked:
            raise ValueError("Scoring has started, so a folder whose images have codes cannot be removed. "
                             "Exclude images with a rule instead.")
        if any(_editable(old[i]) != _editable(sources[i]) for i in range(locked)) and not folders_reason(body):
            raise ValueError("Scoring has started, so the folders and rules that built the blind set are locked. "
                             "Use Change anyway... to change them with a reason, which goes into log.md.")
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


def folders_reason(body):
    """The reason typed for changing image folders after scoring began (1.21.2), or ""."""
    r = body.get("folders_reason")
    return " ".join(r.split())[:MAX_REASON] if isinstance(r, str) else ""


def folder_changes(project, old_raw, raw, reason):
    """After scoring began (1.21.2): apply changed folders and rules to the coded images and record them.
    - A moved or renamed folder: key.csv and inventory.csv follow (backups in key/backups/), so Browse,
      Add new images and the results still find the images.
    - Genotype rules: each coded image's genotype is worked out again; changes go to
      key/changes_after_scoring.csv (not to log.md, which never names a code's genotype).
    - Exclusion rules: applied at unblinding, which leaves matching images out and says so.
    Returns the log.md lines (none if nothing in a coded folder changed)."""
    import datetime as dt
    import shutil
    from .files import read_csv, write_csv
    project = Path(project)
    old_s, new_s = old_raw.get("sources", []), raw.get("sources", [])
    pairs = [(o, n) for o, n in zip(old_s, new_s) if _editable(o) != _editable(n)]
    if not pairs:
        return []
    key_path, inv_path = project / "key" / "key.csv", project / "outputs" / "inventory.csv"
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    rows = read_csv(key_path) if key_path.is_file() else []
    inv = read_csv(inv_path) if inv_path.is_file() else []
    if rows:
        (project / "key" / "backups").mkdir(exist_ok=True)
        shutil.copy2(key_path, project / "key" / "backups" / f"key-{stamp}.csv")
    changes, lines = [], [f"Reason given: {reason}"]
    for o, n in pairs:
        name_o, name_n = o.get("name", ""), n.get("name", "")
        base_o, base_n = Path(o.get("path", "")), Path(n.get("path", ""))
        orig_o, orig_n = o.get("original_path", ""), n.get("original_path", "")

        def rel(local):
            try:
                return str(Path(local).relative_to(base_o)).replace("\\", "/")
            except ValueError:
                return None
        moved = genos = 0
        for r in rows + inv:
            if r.get("source") != name_o:
                continue
            rp = rel(r.get("local_path", ""))
            if rp is None:
                continue
            is_key = "code" in r
            if base_n != base_o:
                r["local_path"] = str(base_n / rp)
                moved += is_key
            if orig_n != orig_o and orig_n:
                r["original_path"] = str(Path(orig_n) / rp)
            r["source"] = name_n
            g = C.genotype_for(n, rp)
            if g != r.get("genotype"):
                if is_key:
                    changes.append({"code": r["code"], "field": "genotype", "before": r.get("genotype", ""), "after": g,
                                    "changed": stamp})
                    genos += 1
                r["genotype"] = g
        what = []
        if name_n != name_o:
            what.append(f"renamed from {name_o!r}")
        if base_n != base_o:
            what.append(f"moved from {base_o} to {base_n} ({moved} coded images follow)")
        if orig_n != orig_o:
            what.append("original path changed")
        if genos or o.get("genotypes") != n.get("genotypes") or o.get("default_genotype") != n.get("default_genotype"):
            what.append(f"genotype rules changed: {genos} coded image(s) now have another genotype "
                        "(key/changes_after_scoring.csv)")
        if o.get("exclude", []) != n.get("exclude", []):
            what.append("exclusion rules changed: unblinding leaves matching coded images out and lists them")
        lines.append(f"Image folder {name_n!r}: " + "; ".join(what or ["changed"]) + ".")
    if rows:
        write_csv(key_path, rows)
    if inv:
        write_csv(inv_path, inv)
    if changes:
        f = project / "key" / "changes_after_scoring.csv"
        write_csv(f, (read_csv(f) if f.is_file() else []) + changes)
    return lines


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
FOLDERS_TITLE = "image folders changed after scoring began"


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
    from .files import append_log
    project = Path(project)
    old_raw = _raw(project)
    raw = merged(project, body)
    was_locked = bool(S.locked(project))
    reason = unlock_reason(body) if was_locked else ""
    raw, form_changed = form_change(project, old_raw, raw, body.get("form"), reason)
    from . import analysis
    raw, analysis_changed = analysis.change(project, old_raw, raw, body.get("analysis"))
    from . import markers
    raw, marker_lines = markers.change(project, old_raw, raw, body.get("markers"))
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
                    "Only wording changed (question text, rules or definitions), so the planned tests still run." if same else
                    "The answers, neurons, required or skipped questions, or what counts as a defect changed. "
                    "Unblinding writes only descriptive tables and runs no planned test while the criteria differ "
                    "from the one recorded before the first score; changing it back runs them again.",
                    # not the "set before scoring" prefix: unblinding must keep checking the earlier form
                    "Score form after this change: " + S.record_text(cfg)] + S.criteria_log_lines(cfg, project))
    elif form_changed:
        append_log(project, "score form saved in Project settings",
                   ["Saved before the first score; the last form recorded before the first score is the one "
                    "unblinding checks."] + S.form_log_lines(cfg, project))
    elif checked:
        append_log(project, "score form checked in Project settings",
                   ["Checked before the first score and kept as it was."] + S.form_log_lines(cfg, project))
    if was_locked and folders_reason(body):
        lines = folder_changes(project, old_raw, raw, folders_reason(body))
        if lines:
            append_log(project, FOLDERS_TITLE, lines)
    if marker_lines:
        markers.record_change(project, marker_lines,
                              markers.unlock_reason(body["markers"]) if scoring_started(project) else "")
    if analysis_changed and S.locked(project):
        from .pipeline import interaction_log_lines
        append_log(project, analysis.CHANGE_TITLE,
                   analysis.change_log_lines(cfg, analysis.unlock_reason(body.get("analysis")))
                   # not interaction_log_lines: its "set before scoring" lines must keep the plan from before scoring
                   + [f"Changed: {', '.join(sorted(analysis_changed))}. Unblinding runs the tests on this plan and marks "
                      "them as not set before scoring (exploratory); the summary and the Methods draft say so."])
    elif analysis_changed:
        from .pipeline import interaction_log_lines
        append_log(project, "analysis settings saved in Project settings",
                   ["Saved before the first score; the last settings recorded before the first score are the "
                    "ones unblinding checks.", f"Hidden repeats: {round(100 * C.repeat_fraction(cfg))}% of each "
                    "genotype's images (used when the blind set is built)."] + interaction_log_lines(cfg))
    return {"ok": True, "changed": raw != old_raw, "form_changed": form_changed or checked,
            "after_lock": bool(form_changed and was_locked), "analysis_changed": sorted(analysis_changed),
            "markers_changed": bool(marker_lines)}


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
    from .files import read_csv
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
