"""Analysis settings in Project settings (1.15.0): the reference genotype and the share of hidden repeats.

They are recorded and locked the way the scoring criteria are:
- Each save in Project settings, each Build blind set and the first score write them to log.md; the
  last record before the first score is the one unblinding checks.
- From the first score (or once a second-scorer package exists) the app refuses to change them. A
  change made by hand in config.json is caught at unblinding: no planned test runs.

The reference genotype decides which pairs of genotypes are tested, but it was never part of the
recorded analysis plan (config.analysis_plan), so it gets its own line, "Reference genotype set before
scoring". Projects that scored before this line existed have none, and are checked as before.

The share of hidden repeats decides the codes, so it is used when the blind set is built. Changing it
once a blind set exists is only saved together with a rebuild with new codes (allowed before scoring),
so the blind set and the Methods draft never disagree about it.
"""
import json
from pathlib import Path

from . import config as C

REF_LOG = "Reference genotype set before scoring: "
REPEAT_CHOICES = (0, 0.05, 0.1, 0.15, 0.2, 0.25)


def ref_record(cfg):
    """The reference genotype as log.md records it: a JSON string, "" for every pair."""
    return json.dumps(cfg["unblinding"]["reference_genotype"] or "")


def ref_log_lines(cfg):
    return [REF_LOG + ref_record(cfg)]


def ref_status(project, cfg, history):
    """'changed' if a reference genotype was recorded before scoring and config.json now names
    another, '' otherwise (also for projects that never recorded one)."""
    from .files import recorded
    first = min((r["saved"] for r in history if r.get("saved")), default="")
    rec = recorded(Path(project), first, REF_LOG)
    return "changed" if rec is not None and rec != ref_record(cfg) else ""


def _genotypes(project, cfg):
    """The genotypes the project's images have: from the key once built, otherwise from the last
    scan sorted by the current rules (images left out by a rule are not counted)."""
    from .files import read_csv
    project = Path(project)
    key = project / "key" / "key.csv"
    if key.is_file():
        return sorted({r["genotype"] for r in read_csv(key) if r.get("genotype")})
    inv = project / "outputs" / "inventory.csv"
    if not inv.is_file():
        return []
    sources = {s.get("name"): s for s in cfg["sources"]}
    out = set()
    for r in read_csv(inv):
        s = sources.get(r.get("source"))
        rel = r.get("relative_path", "")
        if s is None or any(C.matches(x["match"], rel) for x in s.get("exclude", [])):
            continue
        out.add(C.genotype_for(s, rel))
    return sorted(out)


def repeats_in_key(project):
    """How many hidden repeats the blind set has, or None if no blind set is built."""
    from .files import read_csv
    key = Path(project) / "key" / "key.csv"
    return sum(1 for r in read_csv(key) if r.get("repeat_of")) if key.is_file() else None


# ------------------------------------------------------------------ the rest of the plan (part 2)

# Settings of the analysis plan (config.analysis_plan) set on the card, by config.json key under
# "unblinding". Each is written only when its value changes, so a recorded plan stays byte for byte
# the same when nothing was changed on the card.
PLAN_FIELDS = ("comparisons", "alternative", "interaction", "secondary_defect_calls", "test_secondary",
               "dim_cutoff_sd", "allow_cross_microscope", "cross_microscope_reason")
MAX_TEXT = 500


def _line(v, what, limit=200, required=True):
    if not isinstance(v, str) or len(v) > limit or any(ord(c) < 32 for c in v):
        raise ValueError(f"{what} must be one line of text.")
    v = v.strip()
    if required and not v:
        raise ValueError(f"{what} is empty.")
    return v


def outcome_names(cfg):
    """The yes/no outcomes a share of affected animals can be built from: "calls" and each
    question outcome (scale and count outcomes are tested with a rank test instead)."""
    return ["calls"] + [oc["name"] for oc in cfg["unblinding"]["question_outcomes"]]


def _norm(key, v, cfg):
    """A setting as the card sends it, checked and in the form compared and stored."""
    calls = cfg["scoring"]["calls"]
    if key == "comparisons":
        if not isinstance(v, list):
            raise ValueError("Planned comparisons: expected a list of pairs.")
        out = []
        for j, pair in enumerate(v, 1):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"Planned comparison {j}: pick two genotypes.")
            a, b = (_line(x, f"Planned comparison {j}") for x in pair)
            if a == b:
                raise ValueError(f"Planned comparison {j}: pick two different genotypes.")
            if [a, b] in out:
                raise ValueError(f"Planned comparison {j}: {a} vs {b} is listed twice.")
            out.append([a, b])
        return out
    if key == "alternative":
        if v not in C.ALTERNATIVES:
            raise ValueError("Pick a test direction.")
        return v
    if key == "interaction":
        if not v:
            return {}
        if not isinstance(v, dict):
            raise ValueError("Interaction test: not filled in.")
        g = {k: _line(v.get(k, ""), f"Interaction test: the {what}") for k, what in
             (("single_a", "first single mutant"), ("single_b", "second single mutant"), ("double", "double mutant"))}
        if len(set(g.values())) < 3:
            raise ValueError("Interaction test: pick three different genotypes.")
        out = {**g, "model": v.get("model", "product"), "outcome": v.get("outcome", "calls"),
               "expected_direction": v.get("expected_direction", ""), "test": v.get("test", "binomial")}
        if out["model"] not in C.INTERACTION_MODELS:
            raise ValueError("Interaction test: pick a model.")
        if out["test"] not in C.INTERACTION_TESTS:
            raise ValueError("Interaction test: pick a test.")
        if out["expected_direction"] not in ("", "higher", "lower"):
            raise ValueError("Interaction test: pick the expected direction, or none.")
        if out["outcome"] not in outcome_names(cfg):
            raise ValueError("Interaction test: pick what counts as affected.")
        return out
    if key == "secondary_defect_calls":
        if not isinstance(v, list) or any(c not in calls for c in v):
            raise ValueError("Second definition of affected: pick calls from the scoring criteria.")
        return [c for c in calls if c in v]
    if key == "test_secondary":
        if not isinstance(v, bool):
            raise ValueError("Second definition of affected: test it or not.")
        return v
    if key == "dim_cutoff_sd":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
            raise ValueError("Check of dim calls: give a number from 0 (off) to 100.")
        return v
    if key == "allow_cross_microscope":
        names = outcome_names(cfg)
        if not isinstance(v, list) or any(m not in names for m in v):
            raise ValueError("Pooling across microscopes: pick from the neuron calls and the yes/no outcomes.")
        return [m for m in names if m in v]
    if key == "cross_microscope_reason":
        return _line(v, "The reason for pooling microscopes", MAX_TEXT, required=False)
    raise KeyError(key)


def _old(key, cfg):
    """The current value in the form _norm returns, for comparing."""
    v = cfg["unblinding"][key]
    if key == "interaction":
        return {**C.interaction(cfg), "test": v.get("test", "binomial")} if v else {}
    if key == "comparisons":
        return [list(c) for c in v]
    return v


def current(project, cfg):
    """What the Analysis card shows."""
    from .scoreform import locked, packages_exist
    un = cfg["unblinding"]
    ref = un["reference_genotype"] or ""
    genotypes = _genotypes(project, cfg)
    named = {ref} | {g for c in un["comparisons"] for g in c} \
        | {un["interaction"].get(k) for k in ("single_a", "single_b", "double")}
    genotypes += sorted(g for g in named if g and g not in genotypes)   # never lost, even if no image has it yet
    frac = C.repeat_fraction(cfg)
    return {"reference_genotype": ref, "genotypes": genotypes, "repeat_fraction": frac,
            "repeat_choices": sorted(set(REPEAT_CHOICES) | {frac}),
            "repeats_in_key": repeats_in_key(project), "locked": locked(project),
            "packages": packages_exist(project),
            **{k: _old(k, cfg) for k in PLAN_FIELDS},
            "calls": list(cfg["scoring"]["calls"]), "defect_calls": list(cfg["scoring"]["defect_calls"]),
            "outcomes": outcome_names(cfg)}


def clean(body, cfg):
    """The card's answers, checked: {setting: value} for the settings the card sent."""
    if not isinstance(body, dict):
        raise ValueError("No analysis settings were sent.")
    out = {}
    if "reference_genotype" in body:
        out["reference_genotype"] = _line(body["reference_genotype"], "The reference genotype", required=False)
    if "repeat_fraction" in body:
        frac = body["repeat_fraction"]
        if isinstance(frac, bool) or not isinstance(frac, (int, float)) or not 0 <= frac <= 0.5:
            raise ValueError("The share of hidden repeats must be between 0 and 50%.")
        out["repeat_fraction"] = frac
    for k in PLAN_FIELDS:
        if k in body:
            out[k] = _norm(k, body[k], cfg)
    return out


def _changed(new, cfg):
    """The settings whose value differs from config.json's."""
    out = set()
    for k, v in new.items():
        if k == "reference_genotype":
            old = cfg["unblinding"]["reference_genotype"] or ""
        elif k == "repeat_fraction":
            old = C.repeat_fraction(cfg)
        else:
            old = _old(k, cfg)
        if v != old:
            out.add(k)
    return out


def apply(raw, new, changed):
    """raw with the changed settings written; nothing else in config.json is touched."""
    out = json.loads(json.dumps(raw))
    for k in sorted(changed):
        section = "reliability" if k == "repeat_fraction" else "unblinding"
        sec = dict(out.get(section) or {})
        v = new[k]
        if k == "interaction" and v:     # only what differs from the defaults, as written by hand
            v = {key: val for key, val in v.items()
                 if key in ("single_a", "single_b", "double") or val not in ("", "product", "calls", "binomial")}
        sec[k] = v
        out[section] = sec
    return out


def change(project, old_raw, raw, body):
    """(raw with the analysis settings applied, the names of the settings that changed).
    Refused once scoring started or a package exists. A new share of hidden repeats with a blind set
    already built is refused unless body["rebuild"] is true (the page then rebuilds with new codes)."""
    if body is None:
        return raw, set()
    from .scoreform import _cfg_from_raw, locked
    old = _cfg_from_raw(old_raw)
    new = clean(body, old)
    changed = _changed(new, old)
    if not changed:
        return raw, changed
    why = locked(project)
    if why:
        raise ValueError(why.replace("scoring criteria are", "analysis settings are")
                         .replace("these criteria", "these settings")
                         + " Change them in config.json only if you must; unblinding then runs no planned test.")
    if "repeat_fraction" in changed and repeats_in_key(project) is not None and body.get("rebuild") is not True:
        raise ValueError("The blind set is already built. A new share of hidden repeats needs a rebuild with new codes.")
    return apply(raw, new, changed), changed
