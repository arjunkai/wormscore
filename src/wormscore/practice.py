"""Practice sets (1.13.0): score a few images against an answer key before the real scoring.

Published scoring schemes come with training images and an answer key, so a scorer can check how
they judge each category before scoring for real (Bijwadia, Morton and Meyer 2021, JoVE 62894), and
agreement checks catch drift in how categories are judged (Gibson-Corley, Olivier and Meyerholz
2013, Vet Pathol).

- Made from a finished (unblinded) project: a share of its coded images, picked across genotypes,
  with the scorer's own saved answers as the key (by default only images marked "sure"). The set is
  a folder like a second-scorer package: coded images, the scoring criteria and display settings,
  and the answer key in practice_key.json. No genotypes, file names or analysis plan. Only finished
  projects, so nobody practises on images that are still being scored blind.
- Scored like a package (only Score; Browse and settings are off). Once every image is scored, the
  home page shows the agreement with the key for each answer and each image where the answers
  differ, side by side.
- Recorded in another project with "Add a practice result": the agreement is computed again from
  the set's own files (never typed in), stored in practice/<id>.json and log.md, and the Methods
  draft says the scorer practised, with the key's checksum, which also appears in the log of the
  project the set was made from. Projects that never add one report exactly as before.
"""
import datetime as dt
import hashlib
import json
import random
import re
import secrets
import shutil
from pathlib import Path

from . import config as C
from .files import append_log, read_csv
from .scorers import PACKAGE_SECTIONS, folder_hashes, form_hash, package_info, refuse_in_package

KEY_FILE = "practice_key.json"
SET_ID = re.compile(r"[0-9a-f]{12}")       # secrets.token_hex(6), as make_practice_set writes it
KEY_FORMAT = "wormscore-practice-key"
RESULTS_DIR = "practice"
MIN_IMAGES, MAX_IMAGES = 5, 100
# Answers compared with the key. Confidence, notes and the like describe the scorer, not the image.
NOT_COMPARED = {"code", "saved", "count_this_image", "confidence", "display_preset_when_saved", "flag_answers", "notes"}


def key_fields(cfg):
    from .server import score_fields
    return [f for f in score_fields(cfg) if f not in NOT_COMPARED]


def _checksum(key):
    """SHA-256 of the answer key (its id, form and answers), the same however the file is laid out."""
    core = {k: key[k] for k in ("id", "form", "answers")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _scores(folder):
    """The saved scores of a project or set, by code (scores.csv, as unblinding reads them)."""
    f = Path(folder) / "scoring" / "scores.csv"
    return {r["code"]: r for r in read_csv(f)} if f.is_file() else {}


def is_practice(folder):
    info = package_info(folder)
    return bool(info and info.get("practice"))


def _pick(candidates, n, rng):
    """n codes, taken in turn from each genotype (shuffled), so every group is represented."""
    by_geno = {}
    for r in candidates:
        by_geno.setdefault(r["genotype"], []).append(r["code"])
    pools = [rng.sample(sorted(v), len(v)) for _, v in sorted(by_geno.items())]
    picked = []
    while len(picked) < n and any(pools):
        for pool in pools:
            if pool and len(picked) < n:
                picked.append(pool.pop())
    return picked


def make_practice_set(project, destination, n=20, sure_only=True, log=print):
    """Make a practice set from a finished project. Returns the set's folder."""
    project, destination = Path(project), Path(destination)
    refuse_in_package(project, "Making a practice set")
    if not (project / "outputs" / "unblinded" / "summary.txt").is_file():
        raise SystemExit("Practice sets are made from a finished project: unblind it first. "
                         "(Then nobody practises on images that are still being scored blind.)")
    if isinstance(n, bool) or not isinstance(n, int) or not MIN_IMAGES <= n <= MAX_IMAGES:
        raise SystemExit(f"A practice set has {MIN_IMAGES} to {MAX_IMAGES} images.")
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"{destination} is not empty. Choose a new, empty folder for the practice set.")
    if project.resolve() in destination.resolve().parents or destination.resolve() == project.resolve():
        raise SystemExit("Make the practice set outside the project folder, so it can be handed over on its own.")
    cfg = C.load(project)
    C.check(cfg)
    raw = json.loads((project / "config.json").read_text(encoding="utf-8"))
    scores = _scores(project)
    key = [r for r in read_csv(project / "key" / "key.csv") if not r.get("repeat_of")]
    candidates = [r for r in key if r["code"] in scores and (not sure_only or scores[r["code"]].get("confidence") == "sure")
                  and (project / "blind" / r["code"] / "meta.json").is_file()]
    if len(candidates) < MIN_IMAGES:
        raise SystemExit(f"Only {len(candidates)} scored image(s)" + (" marked sure" if sure_only else "")
                         + f" can be used; a practice set needs at least {MIN_IMAGES}.")
    rng = random.Random(secrets.randbits(32))
    codes = _pick(candidates, n, rng)
    order = rng.sample(codes, len(codes))
    pid = secrets.token_hex(6)
    fields = key_fields(cfg)
    answers = {c: {f: str(scores[c].get(f, "")) for f in fields} for c in sorted(codes)}
    made = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
    key_doc = {"format": KEY_FORMAT, "id": pid, "made": made, "source": cfg["project_name"], "n": len(codes),
               "sure_only": bool(sure_only), "form": form_hash(cfg), "answers": answers}
    key_doc["sha256"] = _checksum(key_doc)

    destination.mkdir(parents=True, exist_ok=True)
    for c in sorted(codes):
        shutil.copytree(project / "blind" / c, destination / "blind" / c)
    name = f"Practice set from {cfg['project_name']}"
    pkg = {"project_name": name if len(name) <= 80 else name[:79] + "…"}
    pkg |= {k: raw[k] for k in PACKAGE_SECTIONS if k in raw}
    # how answers are combined into outcomes (names and answers only, never genotypes), so the
    # agreement per outcome can be shown in the set itself
    un = cfg["unblinding"]
    pkg["unblinding"] = {k: un[k] for k in ("question_outcomes", "rank_outcomes", "secondary_defect_calls") if un.get(k)}
    if cfg.get("reliability", {}).get("call_weights"):
        pkg["reliability"] = {"call_weights": cfg["reliability"]["call_weights"]}
    pkg["scorer_package"] = {"id": pid, "scorer": "", "made": made, "order": order, "form": form_hash(cfg),
                             "practice": True, "key_sha256": key_doc["sha256"],
                             "blind_sha256": {c: folder_hashes(project / "blind" / c) for c in sorted(codes)}}
    (destination / "config.json").write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    (destination / KEY_FILE).write_text(json.dumps(key_doc, indent=1, ensure_ascii=False), encoding="utf-8")
    from .scoreform import definitions, image_file
    for group in definitions(cfg).values():
        for d in group.values():
            f = image_file(project, d.get("image"))
            if f is not None and f.is_file():
                (destination / "criteria").mkdir(exist_ok=True)
                shutil.copyfile(f, destination / d["image"])
    (destination / "README.txt").write_text(
        f"wormscore practice set: {len(codes)} images from \"{cfg['project_name']}\".\n\n"
        "Open this folder in wormscore (Open project) and score every image, on your own.\n"
        "When every image is scored, wormscore shows how your answers compare with the answer key.\n"
        "The key is in practice_key.json: do not open it before you have finished.\n"
        "To record your practice in your own project, use Add a practice result there.\n", encoding="utf-8")
    rec = project / RESULTS_DIR / "made"
    rec.mkdir(parents=True, exist_ok=True)
    (rec / f"{pid}.json").write_text(json.dumps({"id": pid, "made": made, "folder": str(destination.resolve()),
                                                 "codes": sorted(codes), "key_sha256": key_doc["sha256"]}, indent=1),
                                     encoding="utf-8")
    append_log(project, f"practice set made ({pid})", [
        f"{len(codes)} scored images" + (" marked sure" if sure_only else "") + ", taken in turn from each genotype "
        "at random, with this project's saved answers as the answer key. The set has no genotypes or file names.",
        f"Answer key SHA-256: {key_doc['sha256']}",
        f"Folder: {destination.resolve()}"])
    log(f"Practice set of {len(codes)} images in {destination}")
    return destination


# ------------------------------------------------------------------ scoring a set

def _load_key(folder):
    folder = Path(folder)
    info = package_info(folder)
    if not (info and info.get("practice")):
        raise SystemExit(f"{folder} is not a wormscore practice set.")
    try:
        key = json.loads((folder / KEY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit("The practice set's answer key (practice_key.json) is missing or cannot be read.")
    if not isinstance(key, dict) or key.get("format") != KEY_FORMAT or not isinstance(key.get("answers"), dict):
        raise SystemExit("The practice set's answer key is not one wormscore made.")
    if key.get("id") != info.get("id") or _checksum(key) != info.get("key_sha256") or key.get("sha256") != info.get("key_sha256"):
        raise SystemExit("The practice set's answer key was changed, so it cannot be used.")
    # A set comes from someone else: its id names a file in the project (practice/<id>.json) and
    # its other text goes into log.md and the Methods draft, so both are checked before use.
    if not isinstance(key["id"], str) or not SET_ID.fullmatch(key["id"]):
        raise SystemExit("The practice set's id is not one wormscore made, so it cannot be used.")
    key["source"], key["made"] = _plain(key.get("source"), 120), _plain(key.get("made"), 16)
    return info, key


def _plain(v, limit):
    """Text from a practice set, as one short line: no line breaks or control characters."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", v if isinstance(v, str) else "").strip()[:limit]


def _label(field, cfg):
    sc = cfg["scoring"]
    if field == "usable":
        return "Usable image?"
    if field == "unusable_reason":
        return "Why not usable"
    if field == "cells_seen":
        return sc["count_question"]["label"]
    qs = {q["id"]: q["label"] for q in sc["neuron_questions"] + (sc.get("animal_questions") or [])}
    if field.startswith("a_"):
        return qs.get(field[2:], field)
    head, _, rest = field.partition("_")
    i = int(head[1:]) if head[1:].isdigit() else 0
    neuron = sc["neurons"][i - 1] if 0 < i <= len(sc["neurons"]) else head
    return f"{neuron}: " + {"call": "call", "side": "side"}.get(rest, qs.get(rest, rest))


def result(folder, verify=True):
    """How a practice set's scores compare with its key: {"done", "scored", "total", "measures",
    "differences", ...}. Agreement is only given once every image is scored. verify: also check
    every image against its checksum (done when a result is recorded; the home page, which asks
    every few seconds, skips it)."""
    from . import stats
    folder = Path(folder)
    info, key = _load_key(folder)
    cfg = C.load(folder)
    if form_hash(cfg) != key["form"]:
        raise SystemExit("The practice set's scoring criteria were changed, so it cannot be compared with its key.")
    mine = _scores(folder)
    codes = sorted(key["answers"])
    out = {"id": key["id"], "source": key["source"], "made": key["made"], "total": len(codes),
           "scored": sum(1 for c in codes if c in mine), "key_sha256": key["sha256"], "form": key["form"]}
    out["done"] = out["scored"] == out["total"]
    if not out["done"]:
        return out
    for c in codes if verify else []:
        if folder_hashes(folder / "blind" / c) != info["blind_sha256"].get(c):
            raise SystemExit(f"The images of {c} in the practice set were changed, so it cannot be compared.")
    pre = lambda d: {f"score_{k}": str(v) for k, v in d.items()} | {"score_saved": "x"}
    pairs = [(pre(key["answers"][c]), pre(mine[c])) for c in codes]
    out["measures"] = [m for m in stats.reliability(pairs, cfg) if m["pairs"]]
    order = {c: i for i, c in enumerate(info.get("order") or codes)}
    differences = []
    for c in sorted(codes, key=lambda c: order.get(c, 0)):
        k, m = key["answers"][c], mine[c]
        fields = ["usable"] if "no" in (k.get("usable"), m.get("usable")) else [f for f in k if f != "unusable_reason"]
        for f in fields:
            if str(k.get(f, "")) != str(m.get(f, "")):
                differences.append({"code": c, "answer": _label(f, cfg), "yours": str(m.get(f, "")) or "-",
                                    "key": str(k.get(f, "")) or "-"})
    out["differences"] = differences
    out["practised"] = _plain(max((str(mine[c].get("saved", "")) for c in codes), default=""), 10)
    return out


# ------------------------------------------------------------------ recording a result

def _headline(measures):
    """The measure a sentence quotes: neuron calls if there are any, else the first one."""
    return next((m for m in measures if m["measure"] == "neuron call"), measures[0] if measures else None)


def add_result(project, folder, log=print):
    """Record a finished practice set's agreement in this project (practice/<id>.json and log.md)."""
    from .editconfig import scoring_started
    project = Path(project)
    refuse_in_package(project, "Adding a practice result")
    r = result(folder)
    if not r["done"]:
        raise SystemExit(f"The practice set is not finished: {r['scored']} of {r['total']} images are scored.")
    cfg = C.load(project)
    same = form_hash(cfg) == r["form"]
    rec = {k: r[k] for k in ("id", "source", "made", "total", "practised", "key_sha256", "measures")}
    # answer and outcome names come from the set's own config.json: one plain line each in log.md
    rec["measures"] = [dict(m, measure=_plain(m["measure"], 80)) for m in rec["measures"]]
    r = dict(r, measures=rec["measures"])
    rec |= {"same_criteria": same, "added": f"{dt.datetime.now():%Y-%m-%d %H:%M}",
            "before_scoring": not scoring_started(project), "differences": len(r["differences"])}
    d = project / RESULTS_DIR
    d.mkdir(exist_ok=True)
    out = d / f"{r['id']}.json"
    if out.resolve().parent != d.resolve():     # the id is checked in _load_key; this guards the write too
        raise SystemExit("The practice set's id is not one wormscore made, so it cannot be used.")
    out.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    h = _headline(r["measures"])
    append_log(project, f"practice result added ({r['id']})", [
        f"Practice set of {r['total']} images from \"{r['source']}\" (made {r['made']}), scored {r['practised']}"
        + ("" if rec["before_scoring"] else ", added after scoring had begun") + ".",
        f"Answer key SHA-256: {r['key_sha256']}",
        ("Agreement with the key: " + "; ".join(f"{m['measure']} {m['same_answer']}/{m['pairs']}"
                                                 + (f", kappa {m['kappa']}" if m["kappa"] != "" else "")
                                                 for m in r["measures"])) if h else "No answers to compare.",
        "Same scoring criteria as this project." if same else
        "The practice set's scoring criteria differ from this project's.",
        "Computed from the practice set's own files, not typed in."])
    log(f"Practice result recorded: {r['total']} images" + (f", {h['measure']} {h['percent_agreement']}% agreement" if h else ""))
    return rec


def results(project):
    """Practice results recorded in this project, oldest first."""
    d = Path(project) / RESULTS_DIR
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda r: r.get("added", ""))


def methods_sentence(project):
    """One sentence per recorded practice result, or "" when there is none (Methods read as before)."""
    out = []
    try:
        who = (C.load(project).get("blinding") or {}).get("scored_by") or "the scorer"
    except (Exception, SystemExit):
        who = "the scorer"
    for r in results(project):
        h = _headline(r.get("measures") or [])
        when = "Before scoring" if r.get("before_scoring") else "After scoring had begun"
        agree = (f", agreeing with the key on {h['same_answer']} of {h['pairs']} {h['measure']}s ({h['percent_agreement']}%"
                 + (f"; Cohen's kappa {h['kappa']}" if h["kappa"] != "" else "") + ")") if h else ""
        crit = "" if r.get("same_criteria") else " with scoring criteria that differ from this project's"
        out.append(f"{when}, {who} practised on a set of {r['total']} coded images from an earlier project "
                   f"(\"{r['source']}\"){crit}, scored against an answer key of an experienced scorer's saved answers{agree}; "
                   f"answer key SHA-256 {str(r['key_sha256'])[:16]}, full value in log.md. ")
    return "".join(out)


def summary(project):
    """For the home page: practice results recorded here, and practice sets made here (counts only)."""
    p = Path(project)
    made = sorted((p / RESULTS_DIR / "made").glob("*.json")) if (p / RESULTS_DIR / "made").is_dir() else []
    rows = []
    for r in results(p):
        h = _headline(r.get("measures") or [])
        rows.append({"id": r["id"], "source": r.get("source", ""), "total": r.get("total", 0),
                     "practised": r.get("practised", ""), "same_criteria": bool(r.get("same_criteria")),
                     "measure": h["measure"] if h else "", "percent": h["percent_agreement"] if h else "",
                     "kappa": h["kappa"] if h else ""})
    return {"results": rows, "sets_made": len(made)}
