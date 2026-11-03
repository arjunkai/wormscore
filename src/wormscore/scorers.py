"""Second scorers: a package of the blind set for another person, and their scores brought back.

A package is an ordinary wormscore project folder holding only the chosen coded images and a
config with the scoring form and display settings. It has no key, no sources, no paths, no
analysis settings and no list of checksums (repeats are byte copies of their originals, so a
checksum list would show which codes are repeats). The owner keeps the checksums and the
selection in scorers/<id>/package.json, next to the key, and checks them when the scores come
back. The scorer's answers are stored in scorers/<id>/scoring/ and are never mixed with the
owner's scoring/ folder, so the primary results never change.
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
from .pipeline import REPEAT_GAP, append_log, read_csv, write_csv

PACKAGE_FILE = "package.json"
# Raw config sections a scorer needs: the form, what the images look like and how drawn regions
# are measured. Nothing that names files, genotypes or tests.
PACKAGE_SECTIONS = ("marker", "identity", "scoring", "display", "measurement")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")


def package_info(project):
    """The scorer_package block of a package's config.json, or None for a normal project."""
    try:
        raw = json.loads((Path(project) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    info = raw.get("scorer_package")
    return info if isinstance(info, dict) else None


def refuse_in_package(project, what):
    if package_info(project):
        raise SystemExit(f"This folder is a second-scorer package: it can only be scored. {what} is done in "
                         "the original project, by the person who made the package.")


def form_answers(cfg):
    """Every answer field of the scoring form and the answers it allows."""
    sc = cfg["scoring"]
    allowed = {"usable": {"yes", "no", ""}, "count_this_image": {"yes", "no", ""},
               "cells_seen": set(sc["count_question"]["options"]) | {""}}
    for i in range(1, len(sc["neurons"]) + 1):
        allowed[f"n{i}_call"] = set(sc["calls"]) | {""}
        for q in sc["neuron_questions"]:
            allowed[f"n{i}_{q['id']}"] = set(q["options"]) | {""}
    for q in sc.get("animal_questions") or []:
        allowed[f"a_{q['id']}"] = set(q["options"]) | {""}
    return allowed


def form_hash(cfg):
    """Fingerprint of the scoring form (its fields, allowed answers and neuron names), so scores come
    back for the same questions. Only what the answers depend on is included, so a later version
    that adds a display or help setting under "scoring" still accepts packages made earlier."""
    from .server import score_fields
    form = {"neurons": cfg["scoring"]["neurons"], "fields": sorted(score_fields(cfg)),
            "answers": {k: sorted(v) for k, v in sorted(form_answers(cfg).items())}}
    return hashlib.sha256(json.dumps(form, sort_keys=True).encode()).hexdigest()[:16]


def folder_hashes(folder):
    """SHA-256 of every file under folder. A link (symlink or junction) anywhere is refused, so a
    returned package can never make wormscore read or copy files from outside it."""
    out, folder = {}, Path(folder)
    root = folder.resolve()
    for p in sorted(folder.rglob("*")):
        junction = getattr(p, "is_junction", lambda: False)()
        if p.is_symlink() or junction or p.resolve() != root / p.relative_to(folder):
            raise SystemExit(f"The package contains a link ({p.name}); it cannot be used.")
        if p.is_file():
            out[p.relative_to(folder).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# Files of a scorer's scoring/ folder that are brought back; nothing else is copied.
RETURNED = ("scores.csv", "score_history.csv", "scores.json", "rois.json", "roi_measurements.json",
            "roi_measurements.csv")


def check_answers(rows, cfg):
    """Every column and answer in a returned scores.csv must come from the scoring form, so a
    changed file cannot slip other text (or spreadsheet formulas) into the results."""
    from .server import score_fields
    fields = set(score_fields(cfg))
    allowed = form_answers(cfg)
    for r in rows:
        unknown = set(r) - fields
        if unknown:
            raise SystemExit(f"The package's scores.csv has columns the scoring form does not: {sorted(unknown)[:3]}.")
        for k, ok in allowed.items():
            if r.get(k, "") not in ok:
                raise SystemExit(f"The package's scores.csv has an answer that is not on the form ({k} of {r.get('code')}).")


def scorer_ids(project):
    d = Path(project) / "scorers"
    return sorted(x.name for x in d.iterdir() if (x / PACKAGE_FILE).is_file()) if d.is_dir() else []


def packages(project):
    """The packages made from this project, for the home page: name, size and whether scores came
    back. Counts and dates only, nothing about which images or genotypes."""
    out = []
    for sid in scorer_ids(project):
        own = Path(project) / "scorers" / sid
        try:
            rec = json.loads((own / PACKAGE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        scores = own / "scoring" / "scores.csv"
        out.append({"id": sid, "scorer": rec.get("scorer", ""), "made": rec.get("made", ""),
                    "images": len(rec.get("order", [])), "imported": scores.is_file(),
                    "folder_found": bool(rec.get("folder")) and Path(rec["folder"]).is_dir(),
                    "scored": len(read_csv(scores)) if scores.is_file() else 0})
    return out


def choose(key, fraction, rng):
    """Originals to include: the given share of each genotype's images (at least one per genotype),
    so a rare group is not left out by chance; plus the hidden repeats of the chosen originals."""
    originals = [r for r in key if not r.get("repeat_of")]
    by_geno = {}
    for r in originals:
        by_geno.setdefault(r["genotype"], []).append(r["code"])
    picked = []
    for geno in sorted(by_geno):
        codes = sorted(by_geno[geno])
        picked += rng.sample(codes, max(1, min(len(codes), round(fraction * len(codes)))))
    picked = set(picked)
    repeats = {r["code"]: r["repeat_of"] for r in key if r.get("repeat_of") in picked}
    return sorted(picked), repeats


def own_order(codes, repeats, rng):
    """A new random order for the second scorer, with each repeat kept away from its original."""
    order = [c for c in codes]
    rng.shuffle(order)
    gap = max(2, round(REPEAT_GAP * (len(codes) + len(repeats))))
    for rep, orig in sorted(repeats.items()):
        for _ in range(2000):
            at = rng.randrange(len(order) + 1)
            trial = order[:at] + [rep] + order[at:]
            pos = {c: i for i, c in enumerate(trial)}
            if all(abs(pos[r] - pos[o]) >= gap for r, o in repeats.items() if r in pos):
                order = trial
                break
        else:
            order.insert(rng.randrange(len(order) + 1), rep)    # tiny sets: place it anyway
    return order


def export_package(project, scorer, destination, fraction=0.3, log=print):
    """Make a package of the blind set for a second scorer. fraction: share of each genotype's
    images (0 to 1, at least one per genotype). Returns the package folder."""
    project, destination = Path(project), Path(destination)
    refuse_in_package(project, "Making a package")
    if not NAME.fullmatch(scorer or ""):
        raise SystemExit("Give the second scorer a short name (letters, digits, spaces, . _ -; up to 40 characters).")
    if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not 0 < fraction <= 1:
        raise SystemExit("The share of images must be more than 0 and at most 1 (for example 0.3 for 30%).")
    key_path = project / "key" / "key.csv"
    if not key_path.is_file() or not (project / "blind").is_dir():
        raise SystemExit("Build the blind set first.")
    cfg = C.load(project)
    C.check(cfg)
    raw = json.loads((project / "config.json").read_text(encoding="utf-8"))
    pid = secrets.token_hex(6)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", scorer).strip("_") or "scorer"
    sid = f"{slug}_{dt.date.today():%Y%m%d}_{pid[:4]}"
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f"{destination} is not empty. Choose a new, empty folder for the package.")
    if project.resolve() in destination.resolve().parents or destination.resolve() == project.resolve():
        raise SystemExit("Make the package outside the project folder, so it can be handed over on its own.")

    rng = random.Random(secrets.randbits(32))
    key = read_csv(key_path)
    codes, repeats = choose(key, fraction, rng)
    order = own_order(codes, repeats, rng)
    missing = [c for c in order if not (project / "blind" / c / "meta.json").is_file()]
    if missing:
        raise SystemExit(f"The blind set is incomplete ({len(missing)} coded images missing). Rebuild it first.")

    destination.mkdir(parents=True, exist_ok=True)
    for c in sorted(order):
        shutil.copytree(project / "blind" / c, destination / "blind" / c)
    pkg_cfg = {"project_name": f"{cfg['project_name']} (second scorer: {scorer})"}
    pkg_cfg |= {k: raw[k] for k in PACKAGE_SECTIONS if k in raw}
    if C.dim_cutoff(cfg):     # drawn regions then also record background noise
        pkg_cfg["unblinding"] = {"dim_cutoff_sd": cfg["unblinding"]["dim_cutoff_sd"]}
    pkg_cfg["scorer_package"] = {"id": pid, "scorer": scorer, "made": f"{dt.datetime.now():%Y-%m-%d %H:%M}",
                                 "order": order, "form": form_hash(cfg)}
    (destination / "config.json").write_text(json.dumps(pkg_cfg, indent=2), encoding="utf-8")
    (destination / "README.txt").write_text(
        f"wormscore second-scorer package for {scorer}.\n\n"
        "Open this folder in wormscore (Open project) and score every image in the scoring window.\n"
        "Score on your own: do not ask how anyone else scored these images.\n"
        "When you have finished, send the whole folder back (zip it). Do not rename it or change its files.\n",
        encoding="utf-8")

    own = project / "scorers" / sid
    own.mkdir(parents=True)
    (own / PACKAGE_FILE).write_text(json.dumps({
        "id": pid, "scorer": scorer, "made": pkg_cfg["scorer_package"]["made"], "fraction": fraction,
        "folder": str(destination.resolve()),
        "originals": codes, "repeats": repeats, "order": order, "form": form_hash(cfg),
        "blind_sha256": {c: folder_hashes(project / "blind" / c) for c in order}}, indent=1), encoding="utf-8")
    n_orig = len([r for r in key if not r.get("repeat_of")])
    append_log(project, f"second-scorer package made ({sid})", [
        f"Scorer: {scorer}. {len(codes)} of {n_orig} included images ({round(100 * fraction)}% of each genotype, "
        f"at least one, chosen at random)" + (f", plus {len(repeats)} hidden repeat(s) of those images" if repeats else "")
        + f"; {len(order)} codes in the scorer's own random order.",
        "The package holds coded images and the scoring form only: no key, no file names, no analysis plan.",
        f"Selection and checksums kept in scorers/{sid}/package.json (not in the package).",
    ])
    log(f"Package for {scorer}: {len(order)} images in {destination}")
    return destination


def import_scores(project, package, log=print):
    """Bring a second scorer's scores back after checking the package is the one made here,
    unchanged. Copies them to scorers/<id>/scoring/. Returns the scorer folder."""
    project, package = Path(project), Path(package)
    info = package_info(package)
    if not info:
        raise SystemExit(f"{package} is not a second-scorer package (its config.json has no scorer_package).")
    match = [s for s in scorer_ids(project)
             if json.loads((project / "scorers" / s / PACKAGE_FILE).read_text(encoding="utf-8"))["id"] == info.get("id")]
    if not match:
        raise SystemExit("This package was not made from this project.")
    sid = match[0]
    own = project / "scorers" / sid
    rec = json.loads((own / PACKAGE_FILE).read_text(encoding="utf-8"))
    cfg = C.load(project)
    if rec["form"] != form_hash(cfg) or info.get("form") != rec["form"]:
        raise SystemExit("The scoring form changed since the package was made, so the answers cannot be compared.")
    present = sorted(d.name for d in (package / "blind").iterdir() if d.is_dir()) if (package / "blind").is_dir() else []
    if present != sorted(rec["order"]):
        raise SystemExit("The package's images are not the ones it was made with (codes added or removed).")
    for code in rec["order"]:
        if folder_hashes(package / "blind" / code) != rec["blind_sha256"][code]:
            raise SystemExit(f"The images of {code} in the package were changed, so its scores cannot be used.")
        if folder_hashes(project / "blind" / code) != rec["blind_sha256"][code]:
            raise SystemExit(f"The project's own images of {code} changed since the package was made.")
    src = package / "scoring"
    if not (src / "scores.csv").is_file():
        raise SystemExit("The package has no scores yet (scoring/scores.csv is missing).")
    if (src.is_symlink() or not src.is_dir() or (src / "scores.csv").is_symlink()
            or not (src / "scores.csv").is_file()):
        raise SystemExit("The package's scoring folder is not a plain folder with scores.csv in it.")
    rows = read_csv(src / "scores.csv")
    check_answers(rows, cfg)
    extra = sorted({r["code"] for r in rows} - set(rec["order"]))
    if extra:
        raise SystemExit(f"The package's scores include codes it never had: {', '.join(extra[:5])}.")
    dest = own / "scoring"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir()
    for name in RETURNED:
        f = src / name
        if f.is_symlink():
            raise SystemExit(f"The package's {name} is a link; it cannot be used.")
        if f.is_file():
            shutil.copyfile(f, dest / name)
    append_log(project, f"second-scorer scores imported ({sid})", [
        f"Scorer: {rec['scorer']}. {len(rows)} of {len(rec['order'])} package images scored. "
        "Checked: same package, scoring form and images. Stored in scorers/{sid}/scoring/; "
        "the primary scores in scoring/ are unchanged.".replace("{sid}", sid)])
    log(f"Imported {len(rows)} scores from {rec['scorer']}.")
    return own


def between_scorers(project, rows, cfg):
    """Agreement between the primary scorer and each imported second scorer, on originals both
    scored (repeats are left out so no image counts twice). rows: merged.csv rows (primary).
    Returns (agreement rows, disagreement rows, descriptions)."""
    from . import stats
    from .server import safe_cell
    project = Path(project)
    by_code = {r["code"]: r for r in rows if not r.get("repeat_of") and r.get("score_saved")}
    agree, differ, about = [], [], []
    log_text = (project / "log.md").read_text(encoding="utf-8") if (project / "log.md").is_file() else ""
    unblinded = sorted(re.findall(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) unblinded$", log_text, re.M))
    n = len(cfg["scoring"]["neurons"])
    for sid in scorer_ids(project):
        rec = json.loads((project / "scorers" / sid / PACKAGE_FILE).read_text(encoding="utf-8"))
        path = project / "scorers" / sid / "scoring" / "scores.csv"
        if not path.is_file():
            continue
        second = {r["code"]: {f"score_{k}": v for k, v in r.items() if k != "code"} | {"code": r["code"]}
                  for r in read_csv(path)}
        pairs = [(by_code[c], second[c]) for c in rec["originals"] if c in by_code and c in second]
        for x in stats.reliability(pairs, cfg):
            agree.append({"second_scorer": rec["scorer"], "package": sid, **x})
        for a, b in pairs:
            fields = [("usable", "score_usable"), ("cells seen", "score_cells_seen")] + \
                     [(f"neuron {i} call", f"score_n{i}_call") for i in range(1, n + 1)]
            for label, f in fields:
                if a.get(f, "") != b.get(f, ""):
                    differ.append({k: safe_cell(v) for k, v in {
                        "second_scorer": rec["scorer"], "code": a["code"], "answer": label,
                        "primary": a.get(f, ""), "second": b.get(f, "")}.items()})
        saved = sorted(v.get("score_saved", "") for v in second.values() if v.get("score_saved"))
        about.append({"scorer": rec["scorer"], "id": sid, "made": rec["made"], "fraction": rec["fraction"],
                      "n_package": len(rec["originals"]), "n_pairs": len(pairs),
                      "first": saved[0][:10] if saved else "", "last": saved[-1][:10] if saved else "",
                      "after_unblinding": bool(unblinded and saved and saved[0][:16] > unblinded[0])})
    return agree, differ, about


def package_folder(project, sid):
    """Where a package of this project was made (to zip and send it), or None."""
    if not isinstance(sid, str) or sid not in scorer_ids(project):
        return None
    rec = json.loads((Path(project) / "scorers" / sid / PACKAGE_FILE).read_text(encoding="utf-8"))
    folder = Path(rec["folder"]) if rec.get("folder") else None
    return folder if folder and folder.is_dir() and package_info(folder) else None
