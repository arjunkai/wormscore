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
from .pipeline import INTERACTION_LOG, PLAN_LOG, append_log, read_csv, write_csv


def stack_shapes(rows):
    """Count of each (planes x z-step) per settings group. A sum over planes depends on both."""
    shapes = defaultdict(Counter)
    for r in rows:
        shapes[r["settings_group"]][f"{r['n_z']} x {r['z_step_um']}"] += 1
    return shapes


def recorded(project, first_score, prefix):
    """The settings last written to log.md under this prefix before the first score was saved
    (any time, if nothing is scored), or None if never recorded."""
    log_path = project / "log.md"
    rec, ts = None, ""
    for line in (log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []):
        if line.startswith("## "):
            ts = line[3:19]
        elif line.startswith("- " + prefix) and (not first_score or ts <= first_score[:16]):
            rec = line[2 + len(prefix):]
    return rec


def plan_status(project, cfg, history):
    """'yes' if the analysis plan matches the one recorded before scoring began, 'changed' if it
    differs (then no test runs), 'NO (...)' if it was never recorded, '' if no plan is set."""
    plan = C.analysis_plan(cfg)
    if not plan:
        return ""
    first = min((r["saved"] for r in history if r.get("saved")), default="")
    rec = recorded(project, first, PLAN_LOG)
    if rec is not None:
        return "yes" if rec == json.dumps(plan, sort_keys=True) else "changed"
    old = recorded(project, first, INTERACTION_LOG)        # projects prepared with wormscore 0.3 to 0.4
    ia = {k: v for k, v in C.interaction(cfg).items() if not (k == "expected_direction" and not v)}
    if old is not None and old != json.dumps(ia, sort_keys=True):
        return "changed"
    return "NO (not in log.md before scoring)"


def verdict(t, ia):
    """Whether the result supports the direction stated in advance (interaction.expected_direction)."""
    if not ia.get("expected_direction"):
        return ""
    if t["p_bonferroni"] >= 0.05:
        return "not supported (not significant)"
    return "supported" if t["observed_vs_expected"] == ia["expected_direction"] else \
        "not supported: significant in the other direction"


def fmt_p(t):
    key = next(k for k in t if k.startswith("p_") and k != "p_bonferroni")
    return f"p ({key[2:].replace('_', ' ')}) = {t[key]}, corrected p = {t['p_bonferroni']}"


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

    un = cfg["unblinding"]
    ref, alt, cross = un["reference_genotype"], un["alternative"], set(un["allow_cross_microscope"])
    comps = [tuple(c) for c in un["comparisons"]]
    history = opt(project / "scoring" / "score_history.csv")
    status = plan_status(project, cfg, history)
    run = status != "changed"
    stop_note = ("NOT RUN: the analysis plan in config.json differs from the one recorded in log.md before "
                 "scoring began. Restore it to run the planned tests.")
    notes = {src.get("name"): src["note"] for src in cfg["sources"] if src.get("note")}

    def sources(genotype, system):
        names = sorted({r["source"] for r in counted if r["genotype"] == genotype
                        and system in (stats.POOLED, r["system"])})
        return "; ".join(n + ("*" if n in notes else "") for n in names)

    def mark(rows, a="genotype_a", b="genotype_b"):
        for t in rows:
            if notes:
                t["sources_a"], t["sources_b"] = sources(t[a], t["system"]), sources(t[b], t["system"])
            if status:
                t["set_before_scoring"] = status
        return rows

    pen = stats.penetrance(counted, cfg)
    extra = bool(un["secondary_defect_calls"] or cross)
    if extra:
        pen = [{"definition": "primary", **r} for r in stats.with_ci(pen)]
    pen_test = pen
    if "calls" in cross:
        pen_test = [{"definition": "primary, pooled over microscopes", **r}
                    for r in stats.with_ci(stats.penetrance(counted, cfg, pool=True))]
    tests = mark(stats.fisher_tests(pen_test, ref, comps, alt)) if run else []
    sec, sec_tests = [], []
    if un["secondary_defect_calls"]:
        sec = [{"definition": "secondary (within one microscope)", **r}
               for r in stats.with_ci(stats.penetrance(counted, cfg, defect=un["secondary_defect_calls"]))]
        if un["test_secondary"] and run:
            sec_tests = mark([{"definition": "secondary", **t} for t in stats.fisher_tests(sec, ref, comps, alt)])
        tests = [{"definition": "primary", **t} for t in tests] + sec_tests
    pen_rows = pen + (pen_test if pen_test is not pen else []) + sec
    if notes:
        for r in pen_rows:
            r["sources"] = sources(r["genotype"], r["system"])

    lines.append("")
    lines.append("Penetrance (animals with at least one affected neuron; outputs/unblinded/penetrance.csv):")
    for p in pen_rows:
        label = f"[{p['definition']}] " if extra else ""
        ci = f", 95% CI {p['ci95_low_pct']} to {p['ci95_high_pct']}" if "ci95_low_pct" in p else ""
        lines.append(f"  {label}{p['system']} | {p['genotype']}: {p['animals_any_affected']}/{p['animals']} "
                     f"({p['penetrance_pct']}%{ci})" + (f", {p['animals_incomplete']} with a neuron uncalled"
                                                       if p["animals_incomplete"] else "")
                     + (f"; microscopes {p['microscopes']}" if "microscopes" in p else ""))
    if un["secondary_defect_calls"] and not un["test_secondary"]:
        lines.append("  The secondary definition is descriptive only (no test) and is never pooled over microscopes.")
    lines.append(f"Fisher's exact tests ({alt}), Bonferroni-corrected (outputs/unblinded/fisher_tests.csv)"
                 + (": genotypes POOLED over microscopes (allowed for this measure in config); a test is marked "
                    "when its genotypes come from different microscopes:" if "calls" in cross
                    else " within each microscope:"))
    for t in tests:
        lines.append(f"  {t['system']} | {t['genotype_a']} {t['a_affected']} vs {t['genotype_b']} "
                     f"{t['b_affected']}: {fmt_p(t)}" + (f"  [{t['warning']}]" if t.get("warning") else ""))
    if not run:
        lines.append("  " + stop_note)
    elif not tests:
        lines.append("  none (need two genotypes with scored animals to compare)")

    outcome_rows, outcome_tests, by_outcome = [], [], {"calls": pen_test}
    for oc in un["question_outcomes"]:
        t = stats.outcome_table(counted, cfg, oc)
        t_test = stats.outcome_table(counted, cfg, oc, pool=True) if oc["name"] in cross else t
        by_outcome[oc["name"]] = t_test
        rows_oc = t + (t_test if t_test is not t else [])
        if notes:
            for r in rows_oc:
                r["sources"] = sources(r["genotype"], r["system"])
        outcome_rows += rows_oc
        oc_tests = mark([{"outcome": oc["name"], **x} for x in stats.fisher_tests(t_test, ref, comps, alt)]) \
            if run else []
        outcome_tests += oc_tests
        lines.append("")
        lines.append(f"{oc['name']} (from the per-neuron questions; outputs/unblinded/question_outcomes.csv)"
                     + (": tests POOL genotypes over microscopes (allowed in config); a test is marked when "
                        "its genotypes come from different microscopes:" if t_test is not t else ":"))
        for p in rows_oc:
            lines.append(f"  {p['system']} | {p['genotype']}: {p['animals_any_affected']}/{p['animals']} "
                         f"({p['penetrance_pct']}%); left out: {p['left_out_no_visible_neuron']} no visible "
                         f"neuron, {p['left_out_unclear']} unclear, {p['animals_incomplete']} incomplete"
                         + (f"; microscopes {p['microscopes']}" if "microscopes" in p else ""))
        for x in oc_tests:
            lines.append(f"  Fisher ({alt}): {x['system']} | {x['genotype_a']} {x['a_affected']} vs "
                         f"{x['genotype_b']} {x['b_affected']}: {fmt_p(x)}"
                         + (f"  [{x['warning']}]" if x.get("warning") else ""))
        if not run:
            lines.append("  " + stop_note)

    ia, itests, inote = C.interaction(cfg), [], ""
    if ia:
        pooled = ia["outcome"] in cross
        lines.append("")
        lines.append(f"Genetic interaction ({ia['model']} model, {alt} binomial test, outcome: {ia['outcome']}"
                     + (", genotypes pooled over microscopes" if pooled else ", within each microscope")
                     + "; outputs/unblinded/interaction_test.csv):")
        if not run:
            inote = stop_note
            lines.append("  " + inote)
        else:
            itests = mark(stats.interaction_tests(by_outcome[ia["outcome"]], ia, alt), "single_a", "single_b")
            for t in itests:
                if notes:
                    t["sources_double"] = sources(t["double"], t["system"])
                if ia["expected_direction"]:
                    t["hypothesis"] = verdict(t, ia)
                lines.append(f"  {t['system']}: {t['single_a']} {t['single_a_affected']}, {t['single_b']} "
                             f"{t['single_b_affected']}; expected double {t['expected_double_pct']}%, observed "
                             f"{t['double_affected']} ({t['observed_double_pct']}%), {t['observed_vs_expected']}; "
                             f"{fmt_p(t)}" + (f"  [{t['warning']}]" if t.get("warning") else ""))
                if ia["expected_direction"]:
                    lines.append(f"  The hypothesis was stated in advance as: observed double {ia['expected_direction']} "
                                 f"than expected. Result: {t['hypothesis']}.")
            if not itests:
                inote = ("none (need all three genotypes with assessed animals "
                         + ("in the data" if pooled else "on the same microscope") + ")")
                lines.append("  " + inote)
    if status.startswith("NO"):
        lines.append("")
        lines.append("WARNING: the analysis plan was not recorded in log.md before scoring began.")
    if notes:
        lines.append("")
        lines += [f"* {name}: {note}" for name, note in sorted(notes.items())]

    write_csv(out / "per_neuron.csv", stats.per_neuron_rows(counted, cfg) or [{"note": "no usable counted images"}])
    write_csv(out / "penetrance.csv", pen_rows or [{"note": "no usable counted images"}])
    write_csv(out / "fisher_tests.csv", tests or [{"note": stop_note if not run else "no comparison possible"}])
    write_csv(out / "summary_by_genotype.csv", summary or [{"note": "no usable counted images"}])
    if un["question_outcomes"]:
        write_csv(out / "question_outcomes.csv", outcome_rows or [{"note": "no usable counted images"}])
        write_csv(out / "question_outcome_tests.csv",
                  outcome_tests or [{"note": stop_note if not run else "no comparison possible"}])
    if ia:
        write_csv(out / "interaction_test.csv", itests or [{"note": inote}])
    excluded = [r for r in opt(project / "outputs" / "excluded.csv") if r.get("local_path")]
    (out / "methods_text.md").write_text(methods.statement(
        project, cfg, len(key), len(excluded), history, len(scores), tests,
        outcome_tests=outcome_tests, interaction_tests=itests,
        added=sorted(Counter(f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in (r.get("added_on") for r in key.values()) if d).items()),
        key_rows=list(key.values()), excluded_rows=excluded),
        encoding="utf-8")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_log(project, "unblinded", [
        f"{len(scores)} images scored at unblinding" + (" (below the planned minimum; override used)" if len(scores) < need else ""),
        "Merged table: outputs/unblinded/merged.csv; summary: outputs/unblinded/summary.txt; "
        "results tables: per_neuron.csv, penetrance.csv, fisher_tests.csv; draft Methods text: methods_text.md",
    ])
    for line in lines:
        log(line)
    return out
