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
  the count question's label, rules text, unusable reasons) may still be corrected.
"""
import copy
import datetime as dt
import json
import re
import shutil
from pathlib import Path

from . import config as C

FORM_LOG = "Score form set before scoring: "
MAX_TEXT = 200
MAX_QUESTIONS = 12           # per neuron; the page warns above WARN_QUESTIONS
WARN_QUESTIONS = 6
TYPES = ("yesno", "choice")


# ------------------------------------------------------------------ record and lock

def form_record(cfg):
    """The structural part of the form: everything that decides what is stored and how it is
    analysed. Labels and rules text are left out, so a wording fix does not stop the tests."""
    sc, un = cfg["scoring"], cfg["unblinding"]
    return {
        "neurons": list(sc["neurons"]),
        "calls": list(sc["calls"]),
        "defect_calls": list(sc["defect_calls"]),
        "sides": list(sc["sides"]),
        "count_options": list(sc["count_question"]["options"]),
        "neuron_questions": [{"id": q["id"], "options": list(q["options"]), "required": bool(q.get("required")),
                              "skip_when_calls": list(q.get("skip_when_calls") or [])}
                             for q in sc["neuron_questions"]],
        "question_outcomes": copy.deepcopy(un["question_outcomes"]),
        "primary_outcome": un.get("primary_outcome") or "",
    }


def record_text(cfg):
    return json.dumps(form_record(cfg), sort_keys=True)


def form_log_lines(cfg):
    return [FORM_LOG + record_text(cfg)]


def packages_exist(project):
    d = Path(project) / "scorers"
    return d.is_dir() and any(x.is_dir() for x in d.iterdir())


def locked(project):
    """Why the form can no longer be changed in the app, or "" if it still can."""
    from .editconfig import scoring_started
    if scoring_started(project):
        return "Scoring has started, so the form is locked."
    if packages_exist(project):
        return "A second-scorer package was made with this form, so the form is locked."
    return ""


def record_first_score(project, cfg):
    """Called by the scoring page just before the first score is saved: the form it is about to
    lock goes into log.md, even if it was edited by hand after Build."""
    from .pipeline import append_log
    append_log(project, "first score: score form locked", form_log_lines(cfg))


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
    from .unblind import recorded
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

PRESETS = {
    "fate": {
        "title": "Fate reporter",
        "desc": "Each neuron called normal, dim or missing; a count of marker-positive cells. "
                "The defaults, as used before the builder existed.",
    },
    "axon": {
        "title": "Axon guidance",
        "desc": "Call each neuron, then yes/no questions about its axon. The animal has a process defect "
                "if any neuron does; neurons not seen are not asked.",
    },
    "blank": {
        "title": "Blank",
        "desc": "One neuron and a seen / not seen call. Add your own questions.",
    },
}


def preset_model(name):
    if name not in PRESETS:
        raise ValueError("Unknown starting form.")
    cfg = C.merge(C.DEFAULTS, {})
    m = to_model_cfg(cfg)
    if name == "axon":
        m["rules"] = ["Seen: the cell body is visible. Not seen: no cell body in any plane.",
                      "Answer the axon questions only for neurons you can see.",
                      "Score each neuron separately. The animal is the unit for statistics."]
        m["calls"] = [{"text": "seen", "defect": False}, {"text": "not seen", "defect": True}]
        # Every question is worded so that "yes" is the defect: questions combined in one outcome
        # share one list of defect answers.
        qs = [("stops_early", "Axon stops before its target?"), ("wrong_path", "Axon takes a wrong path?"),
              ("extra_branch", "Extra branch?")]
        m["questions"] = [{"id": qid, "label": lab, "type": "yesno", "options": ["yes", "no"],
                           "cant_tell": "can't tell", "defect": ["yes"], "required": True,
                           "skip_when": ["not seen"], "outcome": "axon defect"} for qid, lab in qs]
        m["primary"] = "axon defect"
    elif name == "blank":
        m["neurons"] = ["Neuron 1"]
        m["calls"] = [{"text": "seen", "defect": False}, {"text": "not seen", "defect": True}]
        m["count"] = {"label": "Marker-positive cell bodies seen", "options": ["0", "1", "2+"]}
        m["rules"] = []
        m["primary"] = ""
    return m


# ------------------------------------------------------------------ config <-> builder model

def _raw(project):
    return json.loads((Path(project) / "config.json").read_text(encoding="utf-8"))


def _cfg_from_raw(raw):
    cfg = C.merge(C.DEFAULTS, raw)
    cfg["identity"] = C.resolve_identity(raw.get("identity"))
    if isinstance(raw.get("marker"), dict) and raw["marker"].get("preset"):
        cfg["marker"] = C.resolve_marker(raw["marker"])
    return cfg


def unsupported(cfg):
    """Why the builder cannot show this form faithfully (then it is shown read-only), or ""."""
    sc, un = cfg["scoring"], cfg["unblinding"]
    seen = {}
    for oc in un["question_outcomes"]:
        for qid in oc.get("questions", []):
            if qid in seen:
                return f"question {qid!r} is used in two outcomes ({seen[qid]!r} and {oc.get('name')!r})"
            seen[qid] = oc.get("name")
    qs = {q["id"]: q for q in sc["neuron_questions"]}
    for oc in un["question_outcomes"]:
        for qid in oc.get("questions", []):
            q = qs.get(qid)
            if q is None:
                continue
            unclear = [o for o in q["options"] if o in oc.get("unclear_answers", [])]
            if len(unclear) > 1:
                return f"question {qid!r} has more than one \"can't tell\" answer"
            if unclear and q["options"][-1] != unclear[0]:
                return f"question {qid!r} has its \"can't tell\" answer before other answers"
            skip = q.get("skip_when_calls")
            nv = oc.get("not_visible_calls", ["missing"])
            if skip is not None and sorted(skip) != sorted(nv):
                return f"question {qid!r} is skipped for other calls than its outcome leaves out"
    if any(not isinstance(q.get("options"), list) or not q.get("id") for q in sc["neuron_questions"]):
        return "a question has no id or options"
    return ""


def to_model_cfg(cfg):
    sc, un = cfg["scoring"], cfg["unblinding"]
    owner = {qid: oc for oc in un["question_outcomes"] for qid in oc.get("questions", [])}
    questions = []
    for q in sc["neuron_questions"]:
        oc = owner.get(q["id"])
        opts = list(q["options"])
        cant = ""
        if oc and opts and opts[-1] in oc.get("unclear_answers", []):
            cant = opts.pop()
        skip = q.get("skip_when_calls")
        if skip is None:
            skip = [c for c in (oc.get("not_visible_calls", ["missing"]) if oc else []) if c in sc["calls"]]
        questions.append({
            "id": q["id"], "label": q.get("label", q["id"]),
            "type": "yesno" if opts == ["yes", "no"] else "choice",
            "options": opts, "cant_tell": cant,
            "defect": [o for o in opts if oc and o in oc.get("defect_answers", [])],
            "required": bool(q.get("required")), "skip_when": list(skip),
            "outcome": oc.get("name", "") if oc else "",
        })
    return {
        "neurons": list(sc["neurons"]),
        "calls": [{"text": c, "defect": c in sc["defect_calls"]} for c in sc["calls"]],
        "sides": bool(sc["sides"]),
        "count": {"label": sc["count_question"]["label"], "options": list(sc["count_question"]["options"])},
        "questions": questions,
        "primary": un.get("primary_outcome") or "",
        "unusable_reasons": list(sc["unusable_reasons"]),
        "rules": list(sc["rules"]),
    }


def current(project):
    """What the Scoring form section shows."""
    cfg = _cfg_from_raw(_raw(project))
    why = unsupported(cfg)
    return {"model": to_model_cfg(cfg), "locked": locked(project), "readonly": why,
            "presets": [{"id": k, **v} for k, v in PRESETS.items()],
            "warn_questions": WARN_QUESTIONS, "max_questions": MAX_QUESTIONS}


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


def _new_id(label, taken):
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:24] or "q"
    if not re.match(r"[a-z]", base):
        base = "q_" + base
    qid, n = base, 2
    while qid in taken:
        qid, n = f"{base}_{n}", n + 1
    return qid


def clean(model):
    """The builder's answers, checked. Returns a cleaned model with ids filled in."""
    if not isinstance(model, dict):
        raise ValueError("No score form was sent.")
    neurons = _texts(model.get("neurons"), "Neuron", 1)
    if len(neurons) > 8:
        raise ValueError("At most 8 neurons per image.")
    calls_in = model.get("calls")
    if not isinstance(calls_in, list):
        raise ValueError("Calls: expected a list.")
    calls = _texts([c.get("text") if isinstance(c, dict) else None for c in calls_in], "Call", 2)
    defect = [c for c, raw in zip(calls, calls_in) if raw.get("defect") is True]
    if not defect:
        raise ValueError("Calls: tick at least one call that counts as a defect.")
    count = model.get("count") if isinstance(model.get("count"), dict) else {}
    count = {"label": _text(count.get("label"), "The count question"),
             "options": _texts(count.get("options"), "Count answer", 2)}
    questions, taken, ids = [], set(), []
    qs = model.get("questions") or []
    if not isinstance(qs, list):
        raise ValueError("Questions: expected a list.")
    if len(qs) > MAX_QUESTIONS:
        raise ValueError(f"At most {MAX_QUESTIONS} questions per neuron.")
    for q in qs:
        if isinstance(q, dict) and isinstance(q.get("id"), str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", q["id"]):
            taken.add(q["id"])
    for i, q in enumerate(qs, 1):
        if not isinstance(q, dict):
            raise ValueError(f"Question {i} is not filled in.")
        label = _text(q.get("label"), f"Question {i}: the question")
        where = f"Question \"{label}\""
        typ = q.get("type")
        if typ not in TYPES:
            raise ValueError(f"{where}: pick Yes / No or Choice.")
        qid = q.get("id") if isinstance(q.get("id"), str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", q["id"]) else None
        if qid is None or qid in ids:
            qid = _new_id(label, taken | set(ids))
        ids.append(qid)
        opts = ["yes", "no"] if typ == "yesno" else _texts(q.get("options"), f"{where}: answer", 2)
        cant = _text(q.get("cant_tell") or "", f"{where}: the can't tell answer", required=False)
        if cant and cant.lower() in [o.lower() for o in opts]:
            raise ValueError(f"{where}: the can't tell answer is also one of the answers.")
        dfx = q.get("defect") or []
        if not isinstance(dfx, list) or any(d not in opts for d in dfx):
            raise ValueError(f"{where}: a defect answer is not one of its answers.")
        outcome = _text(q.get("outcome") or "", f"{where}: the outcome name", required=False, limit=60)
        if outcome and not dfx:
            raise ValueError(f"{where}: tick the answers that count as a defect, or set it to not analysed.")
        if outcome.lower() == "calls":
            raise ValueError(f"{where}: the outcome cannot be called \"calls\".")
        skip = q.get("skip_when") or []
        if not isinstance(skip, list) or any(s not in calls for s in skip):
            raise ValueError(f"{where}: a skip call is not one of the calls.")
        if not isinstance(q.get("required", False), bool):
            raise ValueError(f"{where}: required must be true or false.")
        questions.append({"id": qid, "label": label, "type": typ, "options": opts, "cant_tell": cant,
                          "defect": [o for o in opts if o in dfx], "required": q.get("required", False),
                          "skip_when": [c for c in calls if c in skip], "outcome": outcome})
    names = []
    for q in questions:
        if q["outcome"] and q["outcome"] not in names:
            names.append(q["outcome"])
    for name in names:
        group = [q for q in questions if q["outcome"] == name]
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
    return {"neurons": neurons, "calls": [{"text": c, "defect": c in defect} for c in calls],
            "sides": bool(model.get("sides")), "count": count, "questions": questions, "primary": primary,
            "unusable_reasons": reasons, "rules": rules}


def apply(raw, model):
    """The new raw config.json with the form from the builder. Keys the builder does not manage are
    kept; a key is only written when it differs from the default or was already in config.json, so
    saving an unchanged default form leaves config.json as it was."""
    m = clean(model)
    out = copy.deepcopy(raw)
    old_cfg = _cfg_from_raw(raw)
    d_sc, d_un = C.DEFAULTS["scoring"], C.DEFAULTS["unblinding"]
    old_q = {q["id"]: q for q in old_cfg["scoring"]["neuron_questions"]}
    calls = [c["text"] for c in m["calls"]]
    new_sc = {
        "neurons": m["neurons"],
        "calls": calls,
        "defect_calls": [c["text"] for c in m["calls"] if c["defect"]],
        "sides": (old_cfg["scoring"]["sides"] or d_sc["sides"]) if m["sides"] else [],
        "count_question": {**old_cfg["scoring"]["count_question"], **m["count"]},
        "neuron_questions": [],
        "unusable_reasons": m["unusable_reasons"],
        "rules": m["rules"],
    }
    for q in m["questions"]:
        entry = {k: v for k, v in old_q.get(q["id"], {}).items()
                 if k not in ("id", "label", "options", "required", "skip_when_calls")}
        entry = {"id": q["id"], "label": q["label"], "options": q["options"] + ([q["cant_tell"]] if q["cant_tell"] else []),
                 **({"required": True} if q["required"] else {}),
                 **({"skip_when_calls": q["skip_when"]} if q["skip_when"] else {}), **entry}
        new_sc["neuron_questions"].append(entry)
    old_oc = {oc.get("name"): oc for oc in old_cfg["unblinding"]["question_outcomes"]}
    outcomes = []
    for q in m["questions"]:
        if not q["outcome"]:
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
    if raw_sc:
        out["scoring"] = raw_sc
    raw_un = dict(out.get("unblinding") or {})
    if outcomes or "question_outcomes" in raw_un:
        raw_un["question_outcomes"] = outcomes
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
