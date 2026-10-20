"""Unblind, only after scoring is finished.

Joins scores, measurements and the key into outputs/unblinded/merged.csv (every row
carries the original path, local copy, location in file and checksum of its image)
and writes a descriptive summary. It classifies nothing and runs no statistical tests.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from . import config as C
from .pipeline import append_log, read_csv, write_csv


def unblind(project, finished=False, anyway=False, log=print):
    project = Path(project)
    if not finished:
        raise SystemExit("Unblinding reveals genotypes. Confirm you have finished scoring "
                         "(--i-have-finished-scoring, or the checkbox in the launcher).")
    cfg = C.load(project)
    sc = cfg["scoring"]
    opt = lambda p: read_csv(p) if p.exists() else []
    scores = {r["code"]: r for r in opt(project / "scoring" / "scores.csv")}
    need = cfg["unblinding"]["min_scored_worms"]
    if len(scores) < need and not anyway:
        raise SystemExit(f"Only {len(scores)} images scored; the project plan says at least {need} before "
                         "comparing calls to numbers. Use --anyway to override (recorded in the log).")
    key = {r["code"]: r for r in read_csv(project / "key" / "key.csv")}
    rois = defaultdict(dict)
    for r in opt(project / "scoring" / "roi_measurements.csv"):
        rois[r["code"]][r["region"]] = r
    auto = defaultdict(dict)
    for r in opt(project / "outputs" / "auto_measurements.csv"):
        auto[r["code"]][r.get("candidate", "")] = r

    roi_cols = ("source", "area_um2", "peak_filtered", "mean_mip_minus_bg", "mean_best_minus_bg",
                "integrated_best_minus_bg", "best_z_at_stack_edge", "saturated_px_mip")
    rows = []
    for code, k in sorted(key.items()):
        s = scores.get(code, {})
        row = {**k, **{f"score_{x}": v for x, v in s.items() if x != "code"}}
        for i, name in enumerate(sc["neurons"], start=1):
            for f in roi_cols:
                row[f"n{i}_roi_{f}"] = rois[code].get(name, {}).get(f, "")
        for f in ("prominence", "peak_filtered", "mean_mip_minus_bg", "area_um2"):
            row[f"auto1_{f}"] = auto[code].get("1", {}).get(f, "")
        rows.append(row)
    out = project / "outputs" / "unblinded"
    write_csv(out / "merged.csv", rows)

    counted = [r for r in rows if r.get("score_count_this_image") == "yes" and r.get("score_usable") == "yes"]
    lines = [f"Scored {len(scores)} of {len(rows)} images."]
    unscored = [r["code"] for r in rows if not r.get("score_saved")]
    if unscored:
        lines.append(f"Unscored: {', '.join(unscored)}")
    per_field = Counter((r["local_path"], r["same_field_group"]) for r in counted)
    doubles = sum(1 for n in per_field.values() if n > 1)
    if doubles:
        lines.append(f"WARNING: {doubles} animals have more than one image counted (same stage position).")

    summary = []
    groups = defaultdict(list)
    for r in counted:
        groups[(r["system"], r["genotype"])].append(r)
    lines.append("")
    lines.append(f"Calls by microscope and genotype (usable, counted images; defect = any neuron "
                 f"{' or '.join(sc['defect_calls'])}):")
    for (system, geno), rs in sorted(groups.items()):
        calls = [r.get(f"score_n{i}_call", "") for r in rs for i in range(1, len(sc["neurons"]) + 1)]
        defect = sum(1 for r in rs if any(r.get(f"score_n{i}_call") in sc["defect_calls"]
                                          for i in range(1, len(sc["neurons"]) + 1)))
        lines.append(f"  {system} | {geno}: {len(rs)} animals, {defect} with a defect; neuron calls {dict(Counter(calls))}")
        summary.append({"system": system, "genotype": geno, "animals": len(rs), "animals_with_defect": defect,
                        **{f"neuron_calls_{c}": n for c, n in Counter(calls).items()}})

    lines.append("")
    lines.append("Neuron 1 drawn-region mean above background (sharpest plane), by settings group and call;")
    lines.append("compare only within a settings group (same microscope and settings). median [min-max] (n)")
    tab = defaultdict(list)
    for r in counted:
        v = r.get("n1_roi_mean_best_minus_bg")
        if v not in ("", None):
            tab[(r["settings_group"], r.get("score_n1_call", ""))].append(float(v))
    for (grp, call), vals in sorted(tab.items()):
        lines.append(f"  group {grp} {call:10s} {median(vals):10.1f} [{min(vals):.1f}-{max(vals):.1f}] (n={len(vals)})")

    write_csv(out / "summary_by_genotype.csv", summary or [{"note": "no usable counted images"}])
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_log(project, "unblinded", [
        f"{len(scores)} images scored at unblinding" + (" (below the planned minimum; override used)" if len(scores) < need else ""),
        "Merged table: outputs/unblinded/merged.csv; summary: outputs/unblinded/summary.txt",
    ])
    for line in lines:
        log(line)
    return out
