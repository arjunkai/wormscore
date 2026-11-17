"""The score form: built on the Project settings page, recorded in log.md, locked at the first score.

The form is what the scorer answers for each image (neurons, the call for each neuron, the count
question, extra per-neuron questions) plus how those answers become results (which answers are
defects, which questions are combined into one outcome, the primary outcome). It lives in
config.json under "scoring" and "unblinding.question_outcomes"; this module only reads and writes
those keys, and leaves every other part of config.json as it was.

Integrity rules:
- Before the first score the form can change freely. Each save, each Build blind set and the first
  saved score write "Score form set before scoring: ..." to log.md; the last such line before the
  first score is the one that counts.
- From the first score (or once a second-scorer package exists) the app refuses to change it. A
  change made by hand in config.json is caught at unblinding (form_status): no test runs, the
  descriptive tables are still written, and every test file says the form changed.
- Only the structure is locked (what is stored and how it is analysed). Wording (question labels,
  the count question's label, rules text, unusable reasons, and the definition and reference image of
  each call and answer) may still be corrected.

Definitions (1.9.0): each call, and each answer of a Choice or Scale question, can have a short
definition and a reference image, shown on the scoring page. They live in scoring.call_definitions
({call: {"text", "image"}}) and in each question's "definitions" ({answer: {"text", "image"}}).
Images are copied into the project's criteria/ folder under their own checksum, and the definitions
with each image's SHA-256 are written to log.md with the form.
"""
import base64
import copy
import datetime as dt
import hashlib
import json
import re
import shutil
from pathlib import Path

from . import config as C

FORM_LOG = "Score form set before scoring: "
MAX_TEXT = 200
MAX_QUESTIONS = 12           # per neuron; the page warns above WARN_QUESTIONS
WARN_QUESTIONS = 6
TYPES = ("yesno", "choice", "scale", "count")
ASK = {"neuron": "per neuron", "side": "per side", "animal": "per animal"}   # how a question is asked
RANKED = ("scale", "count")
MAX_LEVELS = 21              # answers on a scale, or numbers on a count
MAX_DEFINITION = 300
CRITERIA_DIR = "criteria"
IMAGE_PATH = re.compile(r"criteria/[0-9a-f]{16}\.(png|jpg)")
IMAGE_TYPES = {"png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
DEFS_LOG = "Definitions shown while scoring: "


# ------------------------------------------------------------------ record and lock

def form_record(cfg):
    """The structural part of the form: everything that decides what is stored and how it is
    analysed. Labels and rules text are left out, so a wording fix does not stop the tests."""
    sc, un = cfg["scoring"], cfg["unblinding"]
    # Keys added in 1.4.0 appear only when used, so a form recorded by 1.3.0 still matches.
    extra = lambda q: {k: q[k] for k in ("type", "cant_tell") if q.get(k)}
    rec = {
        "neurons": list(sc["neurons"]),
        "calls": list(sc["calls"]),
        "defect_calls": list(sc["defect_calls"]),
        "sides": list(sc["sides"]),
        "count_options": list(sc["count_question"]["options"]),
        "neuron_questions": [{"id": q["id"], "options": list(q["options"]), "required": bool(q.get("required")),
                              "skip_when_calls": list(q.get("skip_when_calls") or []), **extra(q)}
                             for q in sc["neuron_questions"]],
        "question_outcomes": copy.deepcopy(un["question_outcomes"]),
        "primary_outcome": un.get("primary_outcome") or "",
    }
    if sc.get("animal_questions"):
        rec["animal_questions"] = [{"id": q["id"], "options": list(q["options"]), "required": bool(q.get("required")),
                                    **extra(q)} for q in sc["animal_questions"]]
    if sc.get("side_questions"):        # 1.14.0, only when used, so earlier forms still match
        rec["side_questions"] = [{"id": q["id"], "options": list(q["options"]), "required": bool(q.get("required")),
                                  **extra(q)} for q in sc["side_questions"]]
    if un.get("rank_outcomes"):
        rec["rank_outcomes"] = copy.deepcopy(un["rank_outcomes"])
    return rec


def record_text(cfg):
    return json.dumps(form_record(cfg), sort_keys=True)


def form_log_lines(cfg, project=None):
    from .criteriafile import log_lines
    return [FORM_LOG + record_text(cfg)] + definitions_log_lines(cfg, project) + log_lines(cfg, project)


def criteria_log_lines(cfg, project=None):
    """The definitions and the criteria checksum, without the form record: for a change made after
    the lock, whose record must not count as one made before scoring."""
    from .criteriafile import log_lines
    return definitions_log_lines(cfg, project) + log_lines(cfg, project)


# ------------------------------------------------------------------ definitions

def definitions(cfg):
    """Every definition in the form: {"call": {...}, question id: {...}}, each {answer: {"text", "image"}},
    only for answers that are still in the form and have a text or an image."""
    sc = cfg["scoring"]
    out = {}

    def keep(defs, answers):
        got = {}
        for a in answers:
            d = (defs or {}).get(a) if isinstance(defs, dict) else None
            if isinstance(d, dict) and (d.get("text") or d.get("image")):
                got[a] = {k: d[k] for k in ("text", "image") if d.get(k)}
        return got
    calls = keep(sc.get("call_definitions"), sc["calls"])
    if calls:
        out["call"] = calls
    for q in sc["neuron_questions"] + (sc.get("side_questions") or []) + (sc.get("animal_questions") or []):
        got = keep(q.get("definitions"), q.get("options") or [])
        if got:
            out[q["id"]] = got
    return out


def image_file(project, rel):
    """The file of a reference image inside the project, or None if the path is not one wormscore wrote."""
    if not isinstance(rel, str) or not IMAGE_PATH.fullmatch(rel):
        return None
    return Path(project) / rel


def definitions_log_lines(cfg, project=None):
    """The definitions, with the SHA-256 of each reference image, so a later swap of an image shows.
    Wording, not structure: they never decide whether the planned tests run."""
    defs = definitions(cfg)
    if not defs:
        return []
    for group in defs.values():
        for d in group.values():
            f = image_file(project, d.get("image")) if project and d.get("image") else None
            if f is not None:
                d["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else "missing"
    return [DEFS_LOG + json.dumps(defs, sort_keys=True)]


def definition_counts(cfg):
    """(answers with a definition, reference images), for the Methods draft."""
    defs = [d for group in definitions(cfg).values() for d in group.values()]
    return sum(1 for d in defs if d.get("text")), sum(1 for d in defs if d.get("image"))


def save_image(project, data, name=""):
    """Copy an uploaded reference image (base64 PNG or JPEG) into criteria/, named by its checksum
    so the same picture is stored once. Returns its path inside the project."""
    rel, raw = read_image(data, name)
    write_image(project, rel, raw)
    return rel


def write_image(project, rel, raw):
    """Write a checked reference image at rel (never over an existing file: the name is its checksum)."""
    dest = Path(project) / rel
    if not dest.is_file():
        dest.parent.mkdir(exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(dest)


def read_image(data, name=""):
    """A reference image (base64 PNG or JPEG) checked: (its path inside a project, its bytes)."""
    too_big = f"The image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB. Save a smaller copy and add that."
    if not isinstance(data, str) or len(data) > MAX_IMAGE_BYTES * 4 // 3 + 64:
        raise ValueError(too_big)
    try:
        raw = base64.b64decode(data.split(",", 1)[-1], validate=True)
    except ValueError:
        raise ValueError("The image could not be read.")
    ext = next((e for e, magic in IMAGE_TYPES.items() if raw.startswith(magic)), None)
    if ext is None:
        raise ValueError(f"{name or 'That file'} is not a PNG or JPEG image. Save the example as PNG or JPEG "
                         "(in Fiji: File > Save As > PNG) and add that.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(too_big)
    return f"{CRITERIA_DIR}/{hashlib.sha256(raw).hexdigest()[:16]}.{ext}", raw


_recorded_cache = {}


def form_recorded(project, cfg):
    """True if the last form recorded in log.md (saved or checked in Project settings, at Build,
    or at the first score) is this form. Cached on log.md's size and time, since the home page
    asks every few seconds."""
    from .files import recorded
    log_path = Path(project) / "log.md"
    try:
        s = log_path.stat()
        key = (str(log_path), s.st_size, s.st_mtime_ns)
    except OSError:
        return False
    if key not in _recorded_cache:
        _recorded_cache.clear()
        _recorded_cache[key] = recorded(Path(project), "", FORM_LOG)
    rec = _recorded_cache[key]
    try:
        return rec is not None and json.loads(rec) == json.loads(record_text(cfg))
    except ValueError:
        return False


CHANGE_TITLE = "score form changed after scoring began"


def changes_after_lock(project):
    """Every change made with "change it anyway...", oldest first: (time, reason, wording_only).
    Unblinding reports them, so a deviation from the form recorded before scoring is disclosed with
    its reason (Willroth & Atherton, 2024, on reporting deviations from a pre-registered plan)."""
    log_path = Path(project) / "log.md"
    if not log_path.exists():
        return []
    out, cur = [], None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = [line[3:19], "", False] if line.endswith(CHANGE_TITLE) else None
            if cur:
                out.append(cur)
        elif cur and line.startswith("- Reason given: "):
            cur[1] = line[len("- Reason given: "):]
        elif cur and line.startswith("- Only wording changed"):
            cur[2] = True
    return [tuple(c) for c in out]


_changed_cache = {}


def _changed_after_lock(project, cfg):
    """form_status(...) == "changed", read again only when log.md, the score history or the form
    changes (the home page asks every few seconds)."""
    from .files import read_csv
    hist = Path(project) / "scoring" / "score_history.csv"

    def stamp(f):
        try:
            st = f.stat()
            return st.st_size, st.st_mtime_ns
        except OSError:
            return None
    key = (str(project), stamp(Path(project) / "log.md"), stamp(hist), record_text(cfg))
    if key not in _changed_cache:
        _changed_cache.clear()
        _changed_cache[key] = form_status(project, cfg, read_csv(hist) if hist.is_file() else []) == "changed"
    return _changed_cache[key]


def form_summary(project, cfg):
    """What the Score form step card shows: how many questions the scorer answers, and whether
    the form is recorded in log.md and locked."""
    sc = cfg["scoring"]
    why = locked(project)
    changed = False
    if why:     # changed after the first score: unblinding runs no planned test (form_status)
        changed = _changed_after_lock(project, cfg)
    return {"calls": len(sc["calls"]),
            "neuron_questions": len(sc["neuron_questions"]),
            "side_questions": len(sc.get("side_questions") or []),
            "animal_questions": len(sc.get("animal_questions") or []),
            "recorded": form_recorded(project, cfg), "locked": why, "changed_after_lock": changed,
            # a second scorer's package must come back with the form it was made with
            "can_unlock": bool(why) and not packages_exist(project)}


def packages_exist(project):
    d = Path(project) / "scorers"
    return d.is_dir() and any(x.is_dir() for x in d.iterdir())


def locked(project):
    """Why the form can no longer be changed in the app, or "" if it still can."""
    from .editconfig import scoring_started
    if scoring_started(project):
        return "Scoring has started, so the scoring criteria are locked."
    if packages_exist(project):
        return "A second-scorer package was made with these criteria, so they are locked."
    return ""


def record_first_score(project, cfg):
    """Called by the scoring page just before the first score is saved: the form it is about to
    lock goes into log.md, even if it was edited by hand after Build."""
    from .files import append_log
    from .pipeline import interaction_log_lines
    # 1.15.0: the analysis settings too, so a change made after Build is the one that counts
    append_log(project, "first score: score form locked", form_log_lines(cfg, project) + interaction_log_lines(cfg))


def _logged_parameters(project, first_score):
    """The last merged config written to log.md (by Scan data or Build) before the first score."""
    log_path = Path(project) / "log.md"
    if not log_path.exists():
        return None
    found, ts, block, inside = None, "", [], False
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if inside:
            if line.startswith("```"):
                inside = False
                if not first_score or ts <= first_score[:16]:
                    try:
                        found = json.loads("\n".join(block))
                    except ValueError:
                        pass
            else:
                block.append(line)
        elif line.startswith("## "):
            ts = line[3:19]
        elif line.startswith("```json"):
            inside, block = True, []
    return found


def form_status(project, cfg, history):
    """'yes' if the form matches the one recorded before scoring began, 'changed' if it differs
    (then no test runs), 'NO (...)' if it was never recorded (old projects; tests still run)."""
    from .files import recorded
    first = min((r["saved"] for r in history if r.get("saved")), default="")
    rec = recorded(Path(project), first, FORM_LOG)
    if rec is not None:
        try:
            same = json.loads(rec) == json.loads(record_text(cfg))
        except ValueError:
            same = False
        return "yes" if same else "changed"
    # Projects built before wormscore 1.3.0: the settings Build wrote into log.md hold the form too.
    old = _logged_parameters(project, first)
    if old is not None:
        try:
            if form_record(C.merge(C.DEFAULTS, old)) == form_record(cfg):
                return "yes"
        except (KeyError, TypeError):
            pass
        return "NO (not recorded; it differs from the settings logged at Build)"
    return "NO (not in log.md before scoring)"


# ------------------------------------------------------------------ presets

# Each preset's wording follows published scoring in C. elegans (1.10.0); "source" is shown with it in
# the app and written into its rules, so the Methods can cite where the categories come from.
PRESETS = {
    "fate": {
        "title": "Fate reporter",
        "desc": "Each neuron called on, dim or off, with dim judged against wild type imaged at the same "
                "settings; a count of marker-positive cells. What a new project starts with.",
        "source": "Reilly et al. 2022, PLoS Genet",
    },
    "axon": {
        "title": "Axon guidance",
        "desc": "Call each neuron, then yes/no questions about its axon, phrased around its target, and "
                "whether it crosses the ventral midline. The animal has an axon defect if any neuron does; "
                "neurons not seen are not asked.",
        "source": "Schmitz, Kinge and Hutter 2007, PNAS; Xu et al. 2015, PLoS Genet",
    },
    "degeneration": {
        "title": "Neurodegeneration",
        "desc": "Each neuron seen or not seen, then its dendrite scored 0 (normal) to 6 (over 75% lost), "
                "giving the highest score that applies. Each animal's value is its worst dendrite; groups are "
                "compared with the Mann-Whitney test.",
        "source": "Bijwadia, Morton and Meyer 2021, JoVE 62894",
    },
    "dendrite": {
        "title": "Dendrite length",
        "desc": "Each neuron seen or not seen, then its dendrite called full length or short (add a partial "
                "level if yours often end midway). Each animal's value is its shortest dendrite; groups are "
                "compared with the Mann-Whitney test.",
        "source": "Heiman and Shaham 2009, Cell; Low et al. 2019, Development",
    },
    "counts": {
        "title": "Counts and position",
        "desc": "Marker-positive cells counted on the left and on the right side, and each neuron's cell body "
                "called in place, anterior or posterior. Each animal's value is its total over both sides, "
                "compared with the Mann-Whitney test.",
        "source": "Ch'ng et al. 2003, Genetics",
    },
    "blank": {
        "title": "Blank",
        "desc": "One neuron and a seen / not seen call. Add your own questions.",
        "source": "",
    },
}
WARN_LEVELS = 5     # above this many scale levels the builder warns that agreement usually drops


def preset_model(name):
    if name not in PRESETS:
        raise ValueError("Unknown template.")
    cfg = C.merge(C.DEFAULTS, {})
    m = to_model_cfg(cfg)
    seen = [_call("seen", False, "The cell body is visible."), _call("not seen", True, "No cell body in any plane.")]
    if name == "fate":
        fate = C.FATE_SCORING
        m["calls"] = [_call(c, c in fate["defect_calls"], fate["call_definitions"][c]["text"]) for c in fate["calls"]]
        m["rules"] = list(fate["rules"])
    elif name == "axon":
        m["rules"] = ["Answer the axon questions only for neurons you can see.",
                      "Ventral cord: the axon's normal target; change it to this neuron's target.",
                      "Midline crossing counts only for an axon that normally stays on its own side.",
                      "Score each neuron separately. The animal is the unit for statistics.",
                      "Questions after Schmitz, Kinge and Hutter 2007, PNAS; Xu et al. 2015, PLoS Genet."]
        m["calls"] = seen
        # Every question is worded so that "yes" is the defect: questions combined in one outcome
        # share one list of defect answers.
        qs = [("fails_target", "Axon fails to reach the ventral cord?"), ("wrong_path", "Axon takes a wrong path?"),
              ("crosses_midline", "Axon crosses the ventral midline?"), ("extra_branch", "Extra branch?")]
        m["questions"] = [{"id": qid, "label": lab, "type": "yesno", "options": ["yes", "no"],
                           "cant_tell": "can't tell", "defect": ["yes"], "required": True,
                           "skip_when": ["not seen"], "outcome": "axon defect", "defs": {}} for qid, lab in qs]
        m["primary"] = "axon defect"
    elif name in ("degeneration", "dendrite"):
        m["calls"] = seen
        defs = {}
        if name == "degeneration":
            levels = ["0 normal", "1 bends or kinks", "2 under 5 blebs", "3 5 to 10 blebs",
                      "4 over 10 blebs, or a break losing under 25%", "5 25 to 75% lost", "6 over 75% lost"]
            m["rules"] = ["Score the whole dendrite and give the highest score that applies.",
                          "Blebs are round swellings along the dendrite; a break is a gap in it.",
                          "Lost: the share of the dendrite's normal length that is missing.",
                          "Score each neuron separately. The animal is the unit for statistics.",
                          # the published scale, so the Methods can cite it (validated in strain BY200)
                          "Scale: Bijwadia, Morton and Meyer 2021, JoVE 62894."]
            label, qid, outcome = "Dendrite score", "dendrite_score", "dendrite score"
        else:
            levels = ["full length", "short"]
            defs = {"full length": {"text": "The dendrite reaches the nose tip.", "image": ""},
                    "short": {"text": "It ends before the nose tip.", "image": ""}}
            m["rules"] = ["Score each neuron separately. The animal is the unit for statistics.",
                          "Categories after Heiman and Shaham 2009, Cell; Low et al. 2019, Development."]
            label, qid, outcome = "Dendrite length", "dendrite_length", "dendrite length"
        m["questions"] = [{"id": qid, "label": label, "type": "scale", "ask": "neuron", "options": levels,
                           "count": None, "cant_tell": "can't tell", "defect": [], "required": True,
                           "skip_when": ["not seen"], "outcome": outcome, "rollup": "worst", "cutoff": "",
                           "defs": defs}]
        m["primary"] = outcome
    elif name == "counts":
        m["calls"] = seen
        m["count"] = {"label": m["count"]["label"], "options": []}     # counted for each side instead
        m["rules"] = ["Count every marker-positive cell body on that side, through all planes.",
                      "Position: compare the cell body with its usual landmark, such as a seam cell or the vulva. "
                      "Name the landmark here.",
                      "Score each neuron separately. The animal is the unit for statistics.",
                      "Left and right scored separately after Ch'ng et al. 2003, Genetics."]
        m["questions"] = [
            {"id": "position", "label": "Cell body position", "type": "choice", "ask": "neuron",
             "options": ["in place", "anterior", "posterior"], "count": None, "cant_tell": "can't tell",
             "defect": ["anterior", "posterior"], "required": True, "skip_when": ["not seen"],
             "outcome": "position defect", "rollup": "", "cutoff": "",
             "defs": {"in place": {"text": "At its usual place next to the landmark in the rules.", "image": ""},
                      "anterior": {"text": "Toward the head from its usual place.", "image": ""},
                      "posterior": {"text": "Toward the tail from its usual place.", "image": ""}}},
            {"id": "cells", "label": "Marker-positive cells", "type": "count", "ask": "side", "options": [],
             "count": {"min": 0, "max": 3, "top": True}, "cant_tell": "can't tell", "defect": [], "required": True,
             "skip_when": [], "outcome": "cell count", "rollup": "sum", "cutoff": "", "defs": {}}]
        m["primary"] = "cell count"
    elif name == "blank":
        m["neurons"] = ["Neuron 1"]
        m["calls"] = [_call("seen", False), _call("not seen", True)]
        m["count"] = {"label": "Marker-positive cell bodies seen", "options": ["0", "1", "2+"]}
        m["rules"] = []
        m["primary"] = ""
    return m


def _call(text, defect, definition=""):
    return {"text": text, "defect": defect, "def": definition, "image": ""}


# ------------------------------------------------------------------ config <-> builder model

def _raw(project):
    return json.loads((Path(project) / "config.json").read_text(encoding="utf-8"))


def _cfg_from_raw(raw):
    cfg = C.merge(C.DEFAULTS, raw)
    C.apply_identity(cfg, raw.get("identity"))
    if isinstance(raw.get("marker"), dict) and raw["marker"].get("preset"):
        cfg["marker"] = C.resolve_marker(raw["marker"])
    return cfg


def unsupported(cfg):
    """Why the builder cannot show this form faithfully (then it is shown read-only), or ""."""
    from . import ranks
    sc, un = cfg["scoring"], cfg["unblinding"]
    aqs = (sc.get("animal_questions") or []) + (sc.get("side_questions") or [])    # no call, so never skipped
    seen = {}
    for oc in un["question_outcomes"]:
        for qid in oc.get("questions", []):
            if qid in seen:
                return f"question {qid!r} is used in two outcomes ({seen[qid]!r} and {oc.get('name')!r})"
            seen[qid] = oc.get("name")
    for ro in un.get("rank_outcomes") or []:
        qid = ro.get("question")
        if qid in seen:
            return f"question {qid!r} is used in two outcomes ({seen[qid]!r} and {ro.get('name')!r})"
        seen[qid] = ro.get("name")
        if "unclear_answers" in ro or "unclear" in ro:
            return f"outcome {ro.get('name')!r} sets its own unclear answers"
    if any(not isinstance(q.get("options"), list) or not q.get("id") for q in sc["neuron_questions"] + aqs):
        return "a question has no id or options"
    qs = {q["id"]: q for q in sc["neuron_questions"] + aqs}
    for oc in un["question_outcomes"]:
        for qid in oc.get("questions", []):
            q = qs.get(qid)
            if q is None:
                continue
            if q.get("type") in RANKED:
                return f"question {qid!r} is a {q['type']} used in a yes/no outcome"
            unclear = [o for o in q["options"] if o in oc.get("unclear_answers", [])]
            if len(unclear) > 1:
                return f"question {qid!r} has more than one \"can't tell\" answer"
            if unclear and q["options"][-1] != unclear[0]:
                return f"question {qid!r} has its \"can't tell\" answer before other answers"
            skip = q.get("skip_when_calls")
            nv = oc.get("not_visible_calls", ["missing"])
            if q in aqs:
                continue
            if skip is not None and sorted(skip) != sorted(nv):
                return f"question {qid!r} is skipped for other calls than its outcome leaves out"
    for q in sc["neuron_questions"] + aqs:
        if q.get("type") not in RANKED:
            continue
        opts = list(q["options"])
        if q.get("cant_tell"):
            if opts[-1] != q["cant_tell"]:
                return f"question {q['id']!r} has its \"can't tell\" answer before other answers"
            opts.pop()
        if q["type"] == "count" and not ranks.count_levels_ok(opts):
            return f"question {q['id']!r} has count answers the builder cannot show"
    return ""


def _count_spec(opts):
    """{"min", "max", "top"} of count answers such as 0, 1, 2, 3+ (top: the last means that many or more)."""
    from . import ranks
    if not ranks.count_levels_ok(opts):
        return {"min": 0, "max": 5, "top": True}
    return {"min": ranks.count_value(opts[0]), "max": ranks.count_value(opts[-1]), "top": opts[-1].endswith("+")}


def count_options(spec):
    lo, hi = spec["min"], spec["max"]
    return [str(v) for v in range(lo, hi)] + [f"{hi}+" if spec["top"] else str(hi)]


def _model_question(q, ask, owner, rank, calls):
    oc, ro = owner.get(q["id"]), rank.get(q["id"])
    opts = list(q["options"])
    cant = ""
    if q.get("type") in RANKED:
        typ = q["type"]
        if q.get("cant_tell") and opts and opts[-1] == q["cant_tell"]:
            cant = opts.pop()
    else:
        if oc and opts and opts[-1] in oc.get("unclear_answers", []):
            cant = opts.pop()
        elif not oc and len(opts) > 2 and opts[-1] == "can't tell":
            cant = opts.pop()        # a question not tested: its last "can't tell" is the can't-tell answer
        typ = "yesno" if opts == ["yes", "no"] else "choice"
    skip = q.get("skip_when_calls") if ask == "neuron" else []
    raw_defs = q.get("definitions") if isinstance(q.get("definitions"), dict) and typ in ("choice", "scale") else {}
    defs = {a: {"text": d.get("text") or "", "image": d.get("image") or ""}
            for a, d in raw_defs.items() if a in opts and isinstance(d, dict)}
    if skip is None:
        skip = [c for c in (oc.get("not_visible_calls", ["missing"]) if oc else []) if c in calls]
    return {
        "id": q["id"], "label": q.get("label", q["id"]), "type": typ, "ask": ask,
        "options": opts if typ != "count" else [], "count": _count_spec(opts) if typ == "count" else None,
        "cant_tell": cant,
        "defect": [o for o in opts if oc and o in oc.get("defect_answers", [])],
        "required": bool(q.get("required")), "skip_when": list(skip),
        "outcome": (oc or ro or {}).get("name", ""),
        "rollup": (ro.get("rollup") or "") if ro else "",
        "cutoff": ("" if ro.get("cutoff") in (None, "") else str(ro["cutoff"])) if ro else "",
        "defs": defs,
    }


def to_model_cfg(cfg):
    sc, un = cfg["scoring"], cfg["unblinding"]
    owner = {qid: oc for oc in un["question_outcomes"] for qid in oc.get("questions", [])}
    rank = {ro["question"]: ro for ro in un.get("rank_outcomes") or [] if isinstance(ro, dict) and "question" in ro}
    raw_cdefs = sc.get("call_definitions") if isinstance(sc.get("call_definitions"), dict) else {}
    cdefs = {k: v for k, v in raw_cdefs.items() if isinstance(v, dict)}
    questions = [_model_question(q, ask, owner, rank, sc["calls"])
                 for ask, key in (("neuron", "neuron_questions"), ("side", "side_questions"), ("animal", "animal_questions"))
                 for q in sc.get(key) or []]
    return {
        "neurons": list(sc["neurons"]),
        "calls": [{"text": c, "defect": c in sc["defect_calls"],
                   "def": (cdefs.get(c) or {}).get("text") or "", "image": (cdefs.get(c) or {}).get("image") or ""}
                  for c in sc["calls"]],
        "sides": bool(sc["sides"]),
        "count": {"label": sc["count_question"]["label"], "options": list(sc["count_question"]["options"])},
        "questions": questions,
        "primary": un.get("primary_outcome") or "",
        "unusable_reasons": list(sc["unusable_reasons"]),
        "rules": list(sc["rules"]),
        # where the criteria came from, if they were imported from a criteria file (1.10.0)
        "source": dict(sc["criteria_source"]) if isinstance(sc.get("criteria_source"), dict) else None,
    }


def current(project):
    """What the Scoring form section shows."""
    cfg = _cfg_from_raw(_raw(project))
    why = unsupported(cfg)
    why_locked = locked(project)
    return {"model": to_model_cfg(cfg), "locked": why_locked, "readonly": why,
            "can_unlock": bool(why_locked) and not packages_exist(project),
            "presets": [{"id": k, **v} for k, v in PRESETS.items()],
            "warn_questions": WARN_QUESTIONS, "max_questions": MAX_QUESTIONS, "max_levels": MAX_LEVELS,
            "warn_levels": WARN_LEVELS, "project_name": cfg.get("project_name") or ""}


def _text(v, what, required=True, limit=MAX_TEXT):
    if not isinstance(v, str):
        raise ValueError(f"{what} must be text.")
    v = " ".join(v.split())
    if required and not v:
        raise ValueError(f"{what} is empty.")
    if len(v) > limit:
        raise ValueError(f"{what} is longer than {limit} characters.")
    return v


def _texts(vs, what, minimum=0, unique=True, limit=MAX_TEXT):
    if not isinstance(vs, list):
        raise ValueError(f"{what}: expected a list.")
    out = [_text(v, f"{what} {i}", limit=limit) for i, v in enumerate(vs, 1)]
    if len(out) < minimum:
        raise ValueError(f"{what}: give at least {minimum}.")
    if unique:
        low = [x.lower() for x in out]
        dup = next((x for x in out if low.count(x.lower()) > 1), None)
        if dup:
            raise ValueError(f"{what}: \"{dup}\" is there twice.")
    return out


def _definition(text, image, where):
    """One answer's definition and reference image, checked: {"text", "image"}."""
    text = _text(text or "", f"{where}: the definition", required=False, limit=MAX_DEFINITION)
    image = image or ""
    if image and not (isinstance(image, str) and IMAGE_PATH.fullmatch(image)):
        raise ValueError(f"{where}: the reference image is not one added in Scoring criteria. Add it again.")
    return {"text": text, "image": image}


def _new_id(label, taken):
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:24] or "q"
    if not re.match(r"[a-z]", base):
        base = "q_" + base
    qid, n = base, 2
    while qid in taken:
        qid, n = f"{base}_{n}", n + 1
    return qid


def _clean_question(q, i, calls, taken, ids):
    """One question from the builder, checked."""
    if not isinstance(q, dict):
        raise ValueError(f"Question {i} is not filled in.")
    label = _text(q.get("label"), f"Question {i}: the question")
    where = f"Question \"{label}\""
    typ = q.get("type")
    if typ not in TYPES:
        raise ValueError(f"{where}: pick Yes / No, Choice, Scale or Count.")
    ask = q.get("ask") or "neuron"
    if ask not in ASK:
        raise ValueError(f"{where}: ask it for each neuron, for each side or once per animal.")
    qid = q.get("id") if isinstance(q.get("id"), str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", q["id"]) else None
    if qid is None or qid in ids:
        qid = _new_id(label, taken | set(ids))
    ids.append(qid)
    count = None
    if typ == "yesno":
        opts = ["yes", "no"]
    elif typ == "count":
        spec = q.get("count") if isinstance(q.get("count"), dict) else {}
        try:
            lo, hi = int(spec.get("min")), int(spec.get("max"))
        except (TypeError, ValueError):
            raise ValueError(f"{where}: give the lowest and highest count as whole numbers.")
        if lo < 0 or hi <= lo:
            raise ValueError(f"{where}: the highest count must be above the lowest, and the lowest 0 or more.")
        if hi - lo + 1 > MAX_LEVELS:
            raise ValueError(f"{where}: at most {MAX_LEVELS} counts. Use a last answer such as "
                             f"\"{lo + MAX_LEVELS - 1}+\" for that many or more.")
        count = {"min": lo, "max": hi, "top": bool(spec.get("top"))}
        opts = count_options(count)
    else:
        what = "level" if typ == "scale" else "answer"
        opts = _texts(q.get("options"), f"{where}: {what}", 2)
        if typ == "scale" and len(opts) > MAX_LEVELS:
            raise ValueError(f"{where}: at most {MAX_LEVELS} levels.")
    raw_defs = q.get("defs") if isinstance(q.get("defs"), dict) and typ in ("choice", "scale") else {}
    defs = {}
    for a in opts:
        d = raw_defs.get(a)
        if isinstance(d, dict):
            d = _definition(d.get("text"), d.get("image"), f"{where}, answer \"{a}\"")
            if d["text"] or d["image"]:
                defs[a] = d
    cant = _text(q.get("cant_tell") or "", f"{where}: the can't tell answer", required=False)
    if cant and cant.lower() in [o.lower() for o in opts]:
        raise ValueError(f"{where}: the can't tell answer is also one of the answers.")
    ranked = typ in RANKED
    dfx = [] if ranked else (q.get("defect") or [])
    if not isinstance(dfx, list) or any(d not in opts for d in dfx):
        raise ValueError(f"{where}: a defect answer is not one of its answers.")
    outcome = _text(q.get("outcome") or "", f"{where}: the outcome name", required=False, limit=60)
    if outcome and not ranked and not dfx:
        raise ValueError(f"{where}: tick the answers that count as a defect, or set it to not analysed.")
    if outcome.lower() == "calls":
        raise ValueError(f"{where}: the outcome cannot be called \"calls\".")
    skip = (q.get("skip_when") or []) if ask == "neuron" else []
    if not isinstance(skip, list) or any(s not in calls for s in skip):
        raise ValueError(f"{where}: a skip call is not one of the calls.")
    if not isinstance(q.get("required", False), bool):
        raise ValueError(f"{where}: required must be true or false.")
    rollup, cutoff = "", ""
    if ranked and outcome and ask in ("neuron", "side"):
        from . import ranks
        rollup = q.get("rollup") or ranks.DEFAULT_ROLLUP[typ]
        if rollup not in ranks.ROLLUPS[typ]:
            raise ValueError(f"{where}: pick how the {'sides' if ask == 'side' else 'neurons'} make one value per animal.")
    if ranked and outcome and q.get("cutoff") not in (None, ""):
        cutoff = str(q["cutoff"])
        if typ == "scale" and (cutoff not in opts or cutoff == opts[0]):
            raise ValueError(f"{where}: the cutoff must be one of the levels after the first.")
        if typ == "count" and (not cutoff.isdigit() or int(cutoff) < 1):
            raise ValueError(f"{where}: the cutoff must be a whole number of 1 or more.")
    return {"id": qid, "label": label, "type": typ, "ask": ask, "options": opts if typ != "count" else [],
            "count": count, "all_options": opts, "cant_tell": cant,
            "defect": [o for o in opts if o in dfx], "required": q.get("required", False),
            "skip_when": [c for c in calls if c in skip], "outcome": outcome, "rollup": rollup, "cutoff": cutoff,
            "defs": defs}


def clean(model):
    """The builder's answers, checked. Returns a cleaned model with ids filled in."""
    if not isinstance(model, dict):
        raise ValueError("No scoring criteria were sent.")
    neurons = _texts(model.get("neurons"), "Neuron", 1)
    if len(neurons) > 8:
        raise ValueError("At most 8 neurons per image.")
    calls_in = model.get("calls")
    if not isinstance(calls_in, list):
        raise ValueError("Calls: expected a list.")
    calls = _texts([c.get("text") if isinstance(c, dict) else None for c in calls_in], "Call", 2)
    defect = [c for c, raw in zip(calls, calls_in) if raw.get("defect") is True]
    call_defs = [_definition(raw.get("def"), raw.get("image"), f"Call \"{c}\"") for c, raw in zip(calls, calls_in)]
    if not defect:
        raise ValueError("Calls: tick at least one call that counts as a defect.")
    count = model.get("count") if isinstance(model.get("count"), dict) else {}
    # no answers: no count is asked for each image (1.14.0)
    count = {"label": _text(count.get("label"), "The count question"),
             "options": _texts(count.get("options"), "Count answer", 2) if count.get("options") != [] else []}
    qs = model.get("questions") or []
    if not isinstance(qs, list):
        raise ValueError("Questions: expected a list.")
    for kind in ASK:
        n = sum(1 for q in qs if isinstance(q, dict) and (q.get("ask") or "neuron") == kind)
        if n > MAX_QUESTIONS:
            raise ValueError(f"At most {MAX_QUESTIONS} questions {ASK[kind]}.")
    taken, ids = set(), []
    for q in qs:
        if isinstance(q, dict) and isinstance(q.get("id"), str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", q["id"]):
            taken.add(q["id"])
    questions = [_clean_question(q, i, calls, taken, ids) for i, q in enumerate(qs, 1)]
    names = []
    for q in questions:
        if q["outcome"] and q["outcome"] not in names:
            names.append(q["outcome"])
    for name in names:
        group = [q for q in questions if q["outcome"] == name]
        if any(q["type"] in RANKED for q in group):
            if len(group) > 1:
                raise ValueError(f"Outcome \"{name}\": a scale or count question is tested on its own, so give it "
                                 "an outcome name no other question uses.")
            continue
        if len({q["ask"] for q in group}) > 1:
            raise ValueError(f"Outcome \"{name}\": combine questions that are asked the same way (for each neuron, "
                             "for each side or once per animal), not a mix.")
        if len({tuple(q["skip_when"]) for q in group}) > 1:
            raise ValueError(f"Outcome \"{name}\": its questions must be skipped for the same calls, "
                             "because the outcome leaves those neurons out.")
        dfx = {d for q in group for d in q["defect"]}
        unclear = {q["cant_tell"] for q in group if q["cant_tell"]}
        for q in group:
            opts = set(q["options"]) | ({q["cant_tell"]} if q["cant_tell"] else set())
            if (dfx & opts) != set(q["defect"]) or (unclear & set(q["options"])):
                raise ValueError(f"Outcome \"{name}\": the same answer is a defect for one question and not for "
                                 "another. Put those questions in separate outcomes.")
    primary = model.get("primary") or ""
    if primary not in ["", "calls"] + names:
        raise ValueError("The primary outcome is not one of the outcomes.")
    reasons = _texts(model.get("unusable_reasons") or [], "Unusable reason", 1)
    rules = _texts(model.get("rules") or [], "Rule", 0, unique=False, limit=500)
    source = _source(model.get("source"))
    return {"neurons": neurons, "calls": [{"text": c, "defect": c in defect, "def": d["text"], "image": d["image"]}
                                          for c, d in zip(calls, call_defs)],
            "sides": bool(model.get("sides")), "count": count, "questions": questions, "primary": primary,
            "unusable_reasons": reasons, "rules": rules, "source": source}


def _source(src):
    """Where imported criteria came from: {"name", "version", "citation", "sha256"}, or None."""
    if not isinstance(src, dict) or not src.get("name"):
        return None
    sha = src.get("sha256") if isinstance(src.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", src["sha256"]) else ""
    return {"name": _text(src["name"], "The criteria's name", limit=80),
            "version": _text(src.get("version") or "", "The criteria's version", required=False, limit=20),
            "citation": _text(src.get("citation") or "", "The criteria's citation", required=False, limit=300),
            "sha256": sha}


def _config_question(q, old):
    """The config.json entry of one cleaned question; keys the builder does not manage are kept."""
    keep = {k: v for k, v in old.items()
            if k not in ("id", "label", "options", "required", "skip_when_calls", "type", "cant_tell", "definitions")}
    ranked = q["type"] in RANKED
    return {"id": q["id"], "label": q["label"],
            **({"type": q["type"]} if ranked else {}),
            "options": q["all_options"] + ([q["cant_tell"]] if q["cant_tell"] else []),
            **({"cant_tell": q["cant_tell"]} if ranked and q["cant_tell"] else {}),
            **({"required": True} if q["required"] else {}),
            **({"skip_when_calls": q["skip_when"]} if q["skip_when"] else {}),
            **({"definitions": {a: _stored(d["text"], d["image"]) for a, d in q["defs"].items()}} if q["defs"] else {}),
            **keep}


def _stored(text, image):
    """A definition as config.json keeps it: only the parts that are set."""
    return {k: v for k, v in (("text", text), ("image", image)) if v}


def apply(raw, model):
    """The new raw config.json with the form from the builder. Keys the builder does not manage are
    kept; a key is only written when it differs from the default or was already in config.json, so
    saving an unchanged default form leaves config.json as it was."""
    m = clean(model)
    out = copy.deepcopy(raw)
    old_cfg = _cfg_from_raw(raw)
    d_sc = C.DEFAULTS["scoring"]
    old_q = {q["id"]: q for q in old_cfg["scoring"]["neuron_questions"] + (old_cfg["scoring"].get("side_questions") or [])
             + (old_cfg["scoring"].get("animal_questions") or [])}
    calls = [c["text"] for c in m["calls"]]
    new_sc = {
        "neurons": m["neurons"],
        "calls": calls,
        "defect_calls": [c["text"] for c in m["calls"] if c["defect"]],
        "sides": (old_cfg["scoring"]["sides"] or d_sc["sides"]) if m["sides"] else [],
        "count_question": {**old_cfg["scoring"]["count_question"], **m["count"]},
        "neuron_questions": [_config_question(q, old_q.get(q["id"], {})) for q in m["questions"] if q["ask"] == "neuron"],
        "unusable_reasons": m["unusable_reasons"],
        "rules": m["rules"],
    }
    animal = [_config_question(q, old_q.get(q["id"], {})) for q in m["questions"] if q["ask"] == "animal"]
    side = [_config_question(q, old_q.get(q["id"], {})) for q in m["questions"] if q["ask"] == "side"]
    call_defs = {c["text"]: _stored(c["def"], c["image"]) for c in m["calls"] if c["def"] or c["image"]}
    old_oc = {oc.get("name"): oc for oc in old_cfg["unblinding"]["question_outcomes"]}
    outcomes, ranked = [], []
    for q in m["questions"]:
        if not q["outcome"]:
            continue
        if q["type"] in RANKED:
            ranked.append({"name": q["outcome"], "question": q["id"], **({"rollup": q["rollup"]} if q["rollup"] else {}),
                           **({"cutoff": int(q["cutoff"]) if q["type"] == "count" else q["cutoff"]} if q["cutoff"] else {})})
            continue
        oc = next((o for o in outcomes if o["name"] == q["outcome"]), None)
        if oc is None:
            prev = old_oc.get(q["outcome"], {})
            oc = {"name": q["outcome"], "questions": [], "defect_answers": [],
                  **({"unclear_answers": []}),
                  **{k: v for k, v in prev.items() if k not in ("name", "questions", "defect_answers",
                                                                "unclear_answers", "not_visible_calls")}}
            oc["not_visible_calls"] = list(q["skip_when"])
            outcomes.append(oc)
        oc["questions"].append(q["id"])
        oc["defect_answers"] += [d for d in q["defect"] if d not in oc["defect_answers"]]
        if q["cant_tell"] and q["cant_tell"] not in oc["unclear_answers"]:
            oc["unclear_answers"].append(q["cant_tell"])
    for oc in outcomes:
        if not oc["unclear_answers"]:
            del oc["unclear_answers"]
        # Outcomes written before the builder kept their own key order; keep it when nothing changed.
        prev = old_oc.get(oc["name"])
        if prev is not None and _same_outcome(prev, oc):
            oc.clear()
            oc.update(copy.deepcopy(prev))

    raw_sc = dict(out.get("scoring") or {})
    for k, v in new_sc.items():
        if k in raw_sc or v != d_sc[k]:
            raw_sc[k] = v
    if animal or "animal_questions" in raw_sc:
        raw_sc["animal_questions"] = animal
    if side or "side_questions" in raw_sc:
        raw_sc["side_questions"] = side
    if call_defs:
        raw_sc["call_definitions"] = call_defs
    else:
        raw_sc.pop("call_definitions", None)
    if m["source"]:
        raw_sc["criteria_source"] = m["source"]
    else:
        raw_sc.pop("criteria_source", None)
    if raw_sc:
        out["scoring"] = raw_sc
    raw_un = dict(out.get("unblinding") or {})
    if outcomes or "question_outcomes" in raw_un:
        raw_un["question_outcomes"] = outcomes
    if ranked or "rank_outcomes" in raw_un:
        raw_un["rank_outcomes"] = ranked
    if m["primary"]:
        raw_un["primary_outcome"] = m["primary"]
    else:
        raw_un.pop("primary_outcome", None)
    if raw_un or "unblinding" in out:
        out["unblinding"] = raw_un
    return out


def _same_outcome(prev, new):
    norm = lambda o: {"name": o.get("name"), "questions": list(o.get("questions", [])),
                      "defect_answers": sorted(o.get("defect_answers", [])),
                      "unclear_answers": sorted(o.get("unclear_answers", [])),
                      "not_visible_calls": sorted(o.get("not_visible_calls", ["missing"])),
                      "rest": {k: v for k, v in o.items() if k not in ("name", "questions", "defect_answers",
                                                                       "unclear_answers", "not_visible_calls")}}
    return norm(prev) == norm(new)


def changed_structure(raw_old, raw_new):
    return form_record(_cfg_from_raw(raw_old)) != form_record(_cfg_from_raw(raw_new))


def backup(project):
    """Copy config.json to config.backup-<time>.json before the app rewrites it."""
    src = Path(project) / "config.json"
    dst = src.with_name(f"config.backup-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
    shutil.copy2(src, dst)
    return dst
