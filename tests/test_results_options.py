"""Optional results settings: config exclude rules, question outcome tables, interaction test."""
import csv
import json
from math import comb

import pytest

from wormscore import config as C
from wormscore import stats
from wormscore.pipeline import prepare, read_csv
from wormscore.server import score_fields
from wormscore.unblind import unblind

OC = {"name": "process defect", "questions": ["dendrite", "axon"], "defect_answers": ["no"],
      "unclear_answers": ["can't tell"]}


def cfg_with(**unblinding):
    q = lambda i: {"id": i, "label": f"{i}?", "options": ["yes", "no", "can't tell"]}
    return C.merge(C.DEFAULTS, {"scoring": {"neuron_questions": [q("dendrite"), q("axon")]},
                                "unblinding": unblinding})


def animal(genotype, n1, n2, system="A"):
    """n1, n2: (call, dendrite answer, axon answer) for each neuron."""
    row = {"code": "x", "system": system, "genotype": genotype}
    for i, (call, d, a) in enumerate((n1, n2), start=1):
        row |= {f"score_n{i}_call": call, f"score_n{i}_dendrite": d, f"score_n{i}_axon": a}
    return row


def edit_config(project, change):
    path = project / "config.json"
    cfg = json.loads(path.read_text())
    change(cfg)
    path.write_text(json.dumps(cfg))


# ------------------------------------------------------------- exclude rule

def test_config_exclude_rule_leaves_images_out_with_reason(fresh_project):
    edit_config(fresh_project, lambda c: c["sources"][0].__setitem__(
        "exclude", [{"match": "*control_01*", "reason": "wrong marker"}]))
    prepare(fresh_project, reassign=True, log=lambda *a: None)
    excluded = read_csv(fresh_project / "outputs" / "excluded.csv")
    assert len(excluded) == 1 and "wrong marker" in excluded[0]["reason"]
    key = read_csv(fresh_project / "key" / "key.csv")
    assert len(key) == 5 and not any("control_01" in r["file"] for r in key)


def test_config_check_names_the_problem():
    with pytest.raises(SystemExit, match="no neuron question"):
        C.check(cfg_with(question_outcomes=[{**OC, "questions": ["nose"]}]))
    with pytest.raises(SystemExit, match="not options"):
        C.check(cfg_with(question_outcomes=[{**OC, "defect_answers": ["broken"]}]))
    with pytest.raises(SystemExit, match="model must be"):
        C.check(cfg_with(interaction={"single_a": "a", "single_b": "b", "double": "ab", "model": "log"}))
    C.check(cfg_with(question_outcomes=[OC], interaction={"single_a": "a", "single_b": "b", "double": "ab",
                                                          "outcome": "process defect"}))


# ------------------------------------------------------------- question outcomes

def test_outcome_table_counts_and_leaves_out_animals():
    ok, bad, unsure, gone = ("normal", "yes", "yes"), ("dim", "yes", "no"), ("normal", "can't tell", "yes"), \
        ("missing", "", "")
    rows = [animal("m", ok, ok),            # normal
            animal("m", bad, ok),           # defective (either neuron)
            animal("m", gone, bad),         # defective; the missing neuron is not assessed
            animal("m", gone, ok),          # normal, judged on the visible neuron
            animal("m", gone, gone),        # left out: no visible neuron
            animal("m", unsure, ok),        # exclude_neuron: judged on neuron 2, normal
            animal("m", unsure, unsure),    # left out: unclear
            animal("m", ("normal", "", "yes"), ok)]   # incomplete
    (t,) = stats.outcome_table(rows, cfg_with(), OC)
    assert (t["animals"], t["animals_any_affected"]) == (5, 2)
    assert (t["left_out_no_visible_neuron"], t["left_out_unclear"], t["animals_incomplete"]) == (1, 1, 1)
    (t,) = stats.outcome_table(rows, cfg_with(), {**OC, "unclear": "exclude_animal"})
    assert (t["animals"], t["left_out_unclear"]) == (4, 2)


def test_per_neuron_table_gets_outcome_column_only_when_configured():
    rows = [animal("m", ("dim", "no", "yes"), ("normal", "yes", "yes")) | {
        "settings_group": "A", "n_z": "1", "z_step_um": "1"}]
    plain = stats.per_neuron_rows(rows, cfg_with())
    assert "process defect" not in plain[0]
    long = stats.per_neuron_rows(rows, cfg_with(question_outcomes=[OC]))
    assert [r["process defect"] for r in long] == ["defect", "normal"]


# ------------------------------------------------------------- interaction

def two_sided_binomial(k, n, p):
    """Exact two-sided p: sum of the probabilities no larger than that of the observed count."""
    pmf = [comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    return sum(x for x in pmf if x <= pmf[k] * (1 + 1e-7))


def pen_row(genotype, affected, n, system="A"):
    return {"system": system, "genotype": genotype, "animals": n, "animals_any_affected": affected}


@pytest.mark.parametrize("model, expected", [("product", 0.44), ("additive", 0.5), ("min", 0.3)])
def test_interaction_expected_value_and_binomial_test(model, expected):
    # Single A 6/20 = 0.3, single B 4/20 = 0.2. Product: 1 - 0.7 * 0.8 = 0.44; additive: 0.3 + 0.2;
    # min (of the unaffected shares 0.7 and 0.8): 1 - 0.7 = 0.3.
    pen = [pen_row("a", 6, 20), pen_row("b", 4, 20), pen_row("ab", 18, 20), pen_row("ab", 1, 3, system="B")]
    (t,) = stats.interaction_tests(pen, {"single_a": "a", "single_b": "b", "double": "ab", "model": model,
                                         "outcome": "calls"})
    assert t["expected_double_pct"] == pytest.approx(100 * expected)
    assert t["double_affected"] == "18/20" and t["observed_vs_expected"] == "higher"
    assert t["p_binomial_two_sided"] == pytest.approx(two_sided_binomial(18, 20, expected), rel=1e-3)
    assert t["system"] == "A" and t["n_tests"] == 1      # system B lacks the singles


def write_scores(project, rows, saved="2099-01-01 12:00:00"):
    fields = score_fields(C.load(project))
    for name in ("scores.csv", "score_history.csv"):
        (project / "scoring").mkdir(exist_ok=True)
        with open(project / "scoring" / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows({**r, "saved": saved} for r in rows)


def test_unblind_runs_only_the_interaction_set_before_scoring(fresh_project):
    ia = {"single_a": "control", "single_b": "mutant", "double": "mutant"}
    edit_config(fresh_project, lambda c: c["unblinding"].update(
        interaction=ia, question_outcomes=[{"name": "dendrite defect", "questions": ["dendrite"],
                                            "defect_answers": ["no"]}]))
    prepare(fresh_project, log=lambda *a: None)
    assert '"model": "product"' in (fresh_project / "log.md").read_text(encoding="utf-8")
    key = read_csv(fresh_project / "key" / "key.csv")
    write_scores(fresh_project, [{"code": r["code"], "usable": "yes", "count_this_image": "yes",
                                  "n1_call": "normal", "n2_call": "dim" if r["genotype"] == "mutant" else "normal",
                                  "n1_dendrite": "yes", "n2_dendrite": "no"} for r in key])
    out = unblind(fresh_project, finished=True, log=lambda *a: None)
    (t,) = read_csv(out / "interaction_test.csv")
    assert t["model"] == "product" and t["set_before_scoring"] == "yes"
    assert {r["outcome"] for r in read_csv(out / "question_outcomes.csv")} == {"dendrite defect"}
    assert "product model" in (out / "methods_text.md").read_text(encoding="utf-8")

    # Switching models after scoring began is refused: the planned test is not replaced.
    edit_config(fresh_project, lambda c: c["unblinding"]["interaction"].update(model="additive"))
    out = unblind(fresh_project, finished=True, log=lambda *a: None)
    (t,) = read_csv(out / "interaction_test.csv")
    assert t["note"].startswith("NOT RUN")


def test_results_files_unchanged_without_options(fresh_project):
    write_scores(fresh_project, [{"code": "W0001", "usable": "yes", "count_this_image": "yes",
                                  "n1_call": "normal", "n2_call": "normal"}])
    out = unblind(fresh_project, finished=True, anyway=True, log=lambda *a: None)
    assert not (out / "question_outcomes.csv").exists() and not (out / "interaction_test.csv").exists()
    assert "Interaction test" not in (fresh_project / "log.md").read_text(encoding="utf-8")
