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


def current(project, cfg):
    """What the Analysis card shows."""
    from .scoreform import locked, packages_exist
    ref = cfg["unblinding"]["reference_genotype"] or ""
    genotypes = _genotypes(project, cfg)
    if ref and ref not in genotypes:
        genotypes.append(ref)          # shown even if no image has it (yet), so it is never lost
    frac = C.repeat_fraction(cfg)
    return {"reference_genotype": ref, "genotypes": genotypes, "repeat_fraction": frac,
            "repeat_choices": sorted(set(REPEAT_CHOICES) | {frac}),
            "repeats_in_key": repeats_in_key(project), "locked": locked(project),
            "packages": packages_exist(project)}


def clean(body, cfg):
    """The card's answers, checked: (reference genotype, repeat share)."""
    if not isinstance(body, dict):
        raise ValueError("No analysis settings were sent.")
    ref = body.get("reference_genotype", "")
    if not isinstance(ref, str) or len(ref) > 200 or any(ord(c) < 32 for c in ref):
        raise ValueError("The reference genotype must be one line of text.")
    frac = body.get("repeat_fraction", 0)
    if isinstance(frac, bool) or not isinstance(frac, (int, float)) or not 0 <= frac <= 0.5:
        raise ValueError("The share of hidden repeats must be between 0 and 50%.")
    return ref.strip(), frac


def apply(raw, ref, frac):
    """raw with these settings; a key is only written when it differs from the default or was
    already in config.json, so saving unchanged defaults leaves config.json as it was."""
    out = json.loads(json.dumps(raw))
    un = dict(out.get("unblinding") or {})
    if ref or "reference_genotype" in un:
        un["reference_genotype"] = ref
    if un or "unblinding" in out:
        out["unblinding"] = un
    rel = dict(out.get("reliability") or {})
    if frac or "repeat_fraction" in rel:
        rel["repeat_fraction"] = frac
    if rel or "reliability" in out:
        out["reliability"] = rel
    return out


def change(project, old_raw, raw, body):
    """(raw with the analysis settings applied, what changed: set of "reference", "repeats").
    Refused once scoring started or a package exists. A new share of hidden repeats with a blind set
    already built is refused unless body["rebuild"] is true (the page then rebuilds with new codes)."""
    if body is None:
        return raw, set()
    old = C.merge(C.DEFAULTS, old_raw)
    ref, frac = clean(body, old)
    changed = set()
    if ref != (old["unblinding"]["reference_genotype"] or ""):
        changed.add("reference")
    if frac != C.repeat_fraction(old):
        changed.add("repeats")
    if not changed:
        return raw, changed
    from .scoreform import locked
    why = locked(project)
    if why:
        raise ValueError(why.replace("scoring criteria are", "analysis settings are")
                         .replace("these criteria", "these settings")
                         + " Change them in config.json only if you must; unblinding then runs no planned test.")
    if "repeats" in changed and repeats_in_key(project) is not None and body.get("rebuild") is not True:
        raise ValueError("The blind set is already built. A new share of hidden repeats needs a rebuild with new codes.")
    return apply(raw, ref, frac), changed
