"""Primary and secondary definitions, cross-microscope pooling, test direction, planned
comparisons, source notes and the analysis-plan lock."""
import json

import pytest
from scipy.stats import binomtest, fisher_exact

from wormscore import config as C
from wormscore import stats
from wormscore.pipeline import prepare, read_csv
from wormscore.unblind import unblind
from test_results_options import write_scores

quiet = lambda *a: None


def cfg_with(**unblinding):
    return C.merge(C.DEFAULTS, {"scoring": {"defect_calls": ["missing"]}, "unblinding": unblinding})


def animals(system, genotype, calls):
    return [{"code": f"{system}{genotype}{i}", "system": system, "genotype": genotype, "source": system,
             "score_n1_call": a, "score_n2_call": b} for i, (a, b) in enumerate(calls)]


def edit_config(project, change):
    path = project / "config.json"
    cfg = json.loads(path.read_text())
    change(cfg)
    path.write_text(json.dumps(cfg))


# ------------------------------------------------------------- definitions and intervals

def test_primary_and_secondary_definitions_count_differently():
    rows = animals("A", "m", [("dim", "normal")] * 3 + [("missing", "normal")] + [("normal", "normal")] * 6)
    cfg = cfg_with(secondary_defect_calls=["dim", "missing"])
    (prim,) = stats.penetrance(rows, cfg)
    (sec,) = stats.with_ci(stats.penetrance(rows, cfg, defect=["dim", "missing"]))
    assert (prim["animals_any_affected"], sec["animals_any_affected"]) == (1, 4)
    assert (sec["ci95_low_pct"], sec["ci95_high_pct"]) == (16.8, 68.7)    # Wilson, 4 of 10


def test_config_check_refuses_bad_plans():
    with pytest.raises(SystemExit, match="only within one microscope"):
        C.check(cfg_with(secondary_defect_calls=["dim"], allow_cross_microscope=["secondary"]))
    with pytest.raises(SystemExit, match="needs 'comparisons'"):
        C.check(cfg_with(alternative="greater"))
    with pytest.raises(SystemExit, match="not in scoring.calls"):
        C.check(cfg_with(secondary_defect_calls=["faint"]))
    with pytest.raises(SystemExit, match="not 'calls'"):
        C.check(cfg_with(allow_cross_microscope=["brightness"]))
    C.check(cfg_with(secondary_defect_calls=["dim", "missing"], allow_cross_microscope=["calls"],
                     alternative="greater", comparisons=[["ab", "a"]]))


# ------------------------------------------------------------- cross-microscope pooling

def test_pooled_tests_compare_across_microscopes_and_say_so():
    rows = (animals("scope1", "a", [("missing", "normal")] * 2 + [("normal", "normal")] * 8)
            + animals("scope2", "a", [("normal", "normal")] * 5)
            + animals("scope3", "ab", [("missing", "normal")] * 9 + [("normal", "normal")]))
    cfg = cfg_with(allow_cross_microscope=["calls"])
    assert stats.fisher_tests(stats.penetrance(rows, cfg)) == []        # never across microscopes by default
    pooled = stats.penetrance(rows, cfg, pool=True)
    a = next(r for r in pooled if r["genotype"] == "a")
    assert (a["system"], a["animals"], a["microscopes"]) == (stats.POOLED, 15, "scope1: 10; scope2: 5")
    (t,) = stats.fisher_tests(pooled, comparisons=[("ab", "a")])
    assert t["warning"] == stats.CROSS_WARNING and t["microscopes_b"] == "scope1: 10; scope2: 5"
    assert t["p_fisher_two_sided"] == pytest.approx(fisher_exact([[9, 1], [2, 13]])[1], rel=1e-3)


def test_secondary_is_never_pooled_in_the_results(fresh_project):
    edit_config(fresh_project, lambda c: c["unblinding"].update(
        secondary_defect_calls=["dim", "missing"], allow_cross_microscope=["calls"]))
    prepare(fresh_project, log=quiet)
    key = read_csv(fresh_project / "key" / "key.csv")
    write_scores(fresh_project, [{"code": r["code"], "usable": "yes", "count_this_image": "yes",
                                  "n1_call": "dim", "n2_call": "normal"} for r in key])
    out = unblind(fresh_project, finished=True, log=quiet)
    pen = read_csv(out / "penetrance.csv")
    sec = [r for r in pen if r["definition"].startswith("secondary")]
    assert sec and all(r["system"] != stats.POOLED for r in sec)
    assert any(r["system"] == stats.POOLED for r in pen if r["definition"].startswith("primary"))
    assert all(r["definition"] != "secondary" for r in read_csv(out / "fisher_tests.csv"))


# ------------------------------------------------------------- direction and comparisons

def test_one_sided_tests_and_planned_comparisons():
    rows = (animals("A", "a", [("missing", "normal")] * 3 + [("normal", "normal")] * 7)
            + animals("A", "b", [("missing", "normal")] * 2 + [("normal", "normal")] * 8)
            + animals("A", "ab", [("missing", "normal")] * 8 + [("normal", "normal")] * 2))
    pen = stats.penetrance(rows, cfg_with())
    assert len(stats.fisher_tests(pen)) == 3                              # every pair
    tests = stats.fisher_tests(pen, comparisons=[("ab", "a"), ("ab", "b")], alternative="greater")
    assert [t["n_tests"] for t in tests] == [2, 2]
    assert tests[0]["p_fisher_greater"] == pytest.approx(fisher_exact([[8, 2], [3, 7]], alternative="greater")[1],
                                                         rel=1e-3)
    ia = {"single_a": "a", "single_b": "b", "double": "ab", "model": "product", "outcome": "calls"}
    (t,) = stats.interaction_tests(pen, ia, alternative="greater")
    assert t["expected_double_pct"] == 44.0                               # 1 - 0.7 * 0.8
    assert t["p_binomial_greater"] == pytest.approx(binomtest(8, 10, 0.44, alternative="greater").pvalue, rel=1e-3)


# ------------------------------------------------------------- plan lock, notes, verdict

def test_analysis_plan_is_locked_once_scoring_starts(fresh_project):
    edit_config(fresh_project, lambda c: (
        c["sources"][0].update(note="collected by others"),
        c["unblinding"].update(
            comparisons=[["mutant", "control"]], alternative="two-sided",
            interaction={"single_a": "control", "single_b": "mutant", "double": "mutant",
                         "expected_direction": "higher"})))
    prepare(fresh_project, log=quiet)
    assert "Analysis plan set before scoring" in (fresh_project / "log.md").read_text(encoding="utf-8")
    key = read_csv(fresh_project / "key" / "key.csv")
    write_scores(fresh_project, [{"code": r["code"], "usable": "yes", "count_this_image": "yes",
                                  "n1_call": "missing" if r["genotype"] == "mutant" else "normal",
                                  "n2_call": "normal"} for r in key])
    out = unblind(fresh_project, finished=True, log=quiet)
    (t,) = read_csv(out / "fisher_tests.csv")
    assert t["set_before_scoring"] == "yes" and t["sources_a"] == "simulated*"
    (i,) = read_csv(out / "interaction_test.csv")
    assert i["hypothesis"] in ("supported", "not supported (not significant)",
                               "not supported: significant in the other direction")
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "* simulated: collected by others" in summary

    edit_config(fresh_project, lambda c: c["unblinding"].update(alternative="greater"))
    out = unblind(fresh_project, finished=True, log=quiet)
    assert read_csv(out / "fisher_tests.csv")[0]["note"].startswith("NOT RUN")
    assert read_csv(out / "interaction_test.csv")[0]["note"].startswith("NOT RUN")


def test_methods_text_states_plan_images_and_notes(fresh_project):
    edit_config(fresh_project, lambda c: (
        c["sources"][0].update(note="collected by others"),
        c["unblinding"].update(secondary_defect_calls=["dim", "missing"], allow_cross_microscope=["calls"],
                               cross_microscope_reason="the planned comparison groups were imaged on different systems",
                               comparisons=[["mutant", "control"]],
                               methods_notes=["The less sensitive system makes a faint cell more likely to be called missing."])))
    edit_config(fresh_project, lambda c: c["scoring"].update(defect_calls=["missing"]))
    prepare(fresh_project, log=quiet)
    key = read_csv(fresh_project / "key" / "key.csv")
    write_scores(fresh_project, [{"code": r["code"], "usable": "yes", "count_this_image": "yes",
                                  "n1_call": "missing", "n2_call": "normal"} for r in key])
    text = (unblind(fresh_project, finished=True, log=quiet) / "methods_text.md").read_text(encoding="utf-8")
    for phrase in ("called missing (primary definition)", "secondary definition", "two-sided Fisher's exact test (mutant vs control)",
                   "because the planned comparison groups", "recorded in the project log before scoring began",
                   "**Limitations.** The less sensitive system", "| control | OME-TIFF", "simulated* | 3 | 0 |",
                   "*simulated: collected by others"):
        assert phrase in text, phrase
    assert "—" not in text
