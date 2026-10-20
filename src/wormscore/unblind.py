"""Unblind, only after scoring is finished.

Joins scores, measurements and the key into outputs/unblinded/merged.csv (every row
carries the original path, local copy, location in file and checksum of its image)
and writes a summary, results tables and Fisher's exact tests (see stats.py). It never
classifies a cell; every call in these tables is the scorer's.
"""
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from . import config as C
from . import methods, stats
from .pipeline import INTERACTION_LOG, append_log, read_csv, write_csv


def stack_shapes(rows):
    """Count of each (planes x z-step) per settings group. A sum over planes depends on both."""
    shapes = defaultdict(Counter)
    for r in rows:
        shapes[r["settings_group"]][f"{r['n_z']} x {r['z_step_um']}"] += 1
    return shapes


def recorded_interaction(project, first_score):
    """The interaction settings last written to log.md before the first score was saved
    (any time, if nothing is scored), or None if never recorded."""
    log_path = project / "log.md"
    rec, ts = None, ""
    for line in (log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []):
        if line.startswith("## "):
            ts = line[3:19]
        elif line.startswith("- " + INTERACTION_LOG) and (not first_score or ts <= first_score[:16]):
            rec = line[2 + len(INTERACTION_LOG):]
    return rec


def unblind(project, finished=False, anyway=False, log=print):
    project = Path(project)
    if not finished:
        raise SystemExit("Unblinding reveals genotypes. Confirm you have finished scoring "
                         "(--i-have-finished-scoring, or the checkbox in the launcher).")
    cfg = C.load(project)
    C.check(cfg)
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
                "integrated_best_minus_bg", "stack_sum_minus_bg", "best_z_at_stack_edge", "saturated_px_mip")
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
    for grp, shapes in sorted(stack_shapes(counted).items()):
        if len(shapes) > 1:
            lines.append(f"  WARNING: group {grp} mixes stack shapes {dict(shapes)} (planes x z-step um); "
                         "stack_sum_minus_bg is comparable only between stacks of the same shape.")

    pen = stats.penetrance(counted, cfg)
    tests = stats.fisher_tests(pen, cfg["unblinding"]["reference_genotype"])
    lines.append("")
    lines.append("Penetrance (animals with at least one affected neuron; outputs/unblinded/penetrance.csv):")
    for p in pen:
        lines.append(f"  {p['system']} | {p['genotype']}: {p['animals_any_affected']}/{p['animals']} "
                     f"({p['penetrance_pct']}%)" + (f", {p['animals_incomplete']} with a neuron uncalled"
                                                    if p["animals_incomplete"] else ""))
    lines.append("Fisher's exact tests within each microscope, Bonferroni-corrected "
                 "(outputs/unblinded/fisher_tests.csv):")
    for t in tests:
        lines.append(f"  {t['system']} | {t['genotype_a']} {t['a_affected']} vs {t['genotype_b']} "
                     f"{t['b_affected']}: p = {t['p_fisher_two_sided']}, corrected p = {t['p_bonferroni']}")
    if not tests:
        lines.append("  none (need two genotypes with scored animals on the same microscope)")

    ref = cfg["unblinding"]["reference_genotype"]
    outcome_rows, outcome_tests, by_outcome = [], [], {"calls": pen}
    for oc in cfg["unblinding"]["question_outcomes"]:
        t = stats.outcome_table(counted, cfg, oc)
        by_outcome[oc["name"]] = t
        outcome_rows += t
        outcome_tests += [{"outcome": oc["name"], **x} for x in stats.fisher_tests(t, ref)]
        lines.append("")
        lines.append(f"{oc['name']} (from the per-neuron questions; outputs/unblinded/question_outcomes.csv):")
        for p in t:
            lines.append(f"  {p['system']} | {p['genotype']}: {p['animals_any_affected']}/{p['animals']} "
                         f"({p['penetrance_pct']}%); left out: {p['left_out_no_visible_neuron']} no visible "
                         f"neuron, {p['left_out_unclear']} unclear, {p['animals_incomplete']} incomplete")
        for x in outcome_tests:
            if x["outcome"] == oc["name"]:
                lines.append(f"  Fisher: {x['system']} | {x['genotype_a']} {x['a_affected']} vs {x['genotype_b']} "
                             f"{x['b_affected']}: p = {x['p_fisher_two_sided']}, corrected p = {x['p_bonferroni']}")

    history = opt(project / "scoring" / "score_history.csv")
    ia, itests, inote = C.interaction(cfg), [], ""
    if ia:
        first = min((r["saved"] for r in history if r.get("saved")), default="")
        rec = recorded_interaction(project, first)
        lines.append("")
        lines.append(f"Genetic interaction ({ia['model']} model, outcome: {ia['outcome']}; "
                     "outputs/unblinded/interaction_test.csv):")
        if rec is not None and rec != json.dumps(ia, sort_keys=True):
            inote = ("NOT RUN: the interaction settings differ from those recorded in log.md before scoring "
                     f"began ({rec}). Restore them to run the planned test.")
            lines.append("  " + inote)
        else:
            itests = [{**t, "set_before_scoring": "yes" if rec else "NO (not in log.md before scoring)"}
                      for t in stats.interaction_tests(by_outcome[ia["outcome"]], ia)]
            for t in itests:
                lines.append(f"  {t['system']}: {t['single_a']} {t['single_a_affected']}, {t['single_b']} "
                             f"{t['single_b_affected']}; expected double {t['expected_double_pct']}%, observed "
                             f"{t['double_affected']} ({t['observed_double_pct']}%), {t['observed_vs_expected']}; "
                             f"binomial p = {t['p_binomial_two_sided']}, corrected p = {t['p_bonferroni']}")
            if not itests:
                inote = "none (need all three genotypes with assessed animals on the same microscope)"
                lines.append("  " + inote)
            if rec is None:
                lines.append("  WARNING: these settings were not recorded in log.md before scoring began.")

    write_csv(out / "per_neuron.csv", stats.per_neuron_rows(counted, cfg) or [{"note": "no usable counted images"}])
    write_csv(out / "penetrance.csv", pen or [{"note": "no usable counted images"}])
    write_csv(out / "fisher_tests.csv", tests or [{"note": "no comparison possible"}])
    write_csv(out / "summary_by_genotype.csv", summary or [{"note": "no usable counted images"}])
    if cfg["unblinding"]["question_outcomes"]:
        write_csv(out / "question_outcomes.csv", outcome_rows or [{"note": "no usable counted images"}])
        write_csv(out / "question_outcome_tests.csv", outcome_tests or [{"note": "no comparison possible"}])
    if ia:
        write_csv(out / "interaction_test.csv", itests or [{"note": inote}])
    excluded = [r for r in opt(project / "outputs" / "excluded.csv") if r.get("local_path")]
    (out / "methods_text.md").write_text(methods.statement(
        project, cfg, len(key), len(excluded), history, len(scores), tests,
        outcome_tests=outcome_tests, interaction_tests=itests), encoding="utf-8")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_log(project, "unblinded", [
        f"{len(scores)} images scored at unblinding" + (" (below the planned minimum; override used)" if len(scores) < need else ""),
        "Merged table: outputs/unblinded/merged.csv; summary: outputs/unblinded/summary.txt; "
        "results tables: per_neuron.csv, penetrance.csv, fisher_tests.csv; draft Methods text: methods_text.md",
    ])
    for line in lines:
        log(line)
    return out
