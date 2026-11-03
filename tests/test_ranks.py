"""Ordered scale and count answers (1.4.0): one value per animal, Mann-Whitney with a permutation p,
cutoff with Fisher, questions asked once per animal, builder round trip and presets."""
import itertools
import json

import pytest
from scipy.stats import mannwhitneyu, rankdata

from wormscore import config as C
from wormscore import ranks, scoreform, scorers, stats
from wormscore.pipeline import prepare, read_csv
from wormscore.server import score_fields
from wormscore.unblind import unblind

from test_results_options import edit_config, write_scores

LEVELS = ["full", "partial", "short"]
SCALE_Q = {"id": "dl", "label": "Dendrite length", "type": "scale", "options": LEVELS + ["can't tell"],
           "cant_tell": "can't tell", "skip_when_calls": ["missing"]}
COUNT_Q = {"id": "puncta", "label": "Puncta", "type": "count", "options": ["0", "1", "2", "3+"]}


def cfg_with(rank_outcomes, animal=(COUNT_Q,)):
    return C.merge(C.DEFAULTS, {"scoring": {"neuron_questions": [SCALE_Q], "animal_questions": list(animal)},
                                "unblinding": {"rank_outcomes": rank_outcomes}})


def row(genotype, n1, n2, puncta="", system="A"):
    """n1, n2: (call, dendrite length answer)."""
    r = {"code": "x", "system": system, "genotype": genotype, "score_a_puncta": puncta}
    for i, (call, a) in enumerate((n1, n2), start=1):
        r |= {f"score_n{i}_call": call, f"score_n{i}_dl": a}
    return r


def brute_force_p(a, b, alternative="two-sided"):
    """Every way to split the pooled midranks into groups of len(a) and len(b)."""
    ranks_ = list(rankdata(list(a) + list(b)))
    n, na = len(ranks_), len(a)
    obs, centre = sum(ranks_[:na]), na * (n + 1) / 2
    sums = [sum(ranks_[i] for i in idx) for idx in itertools.combinations(range(n), na)]
    if alternative == "two-sided":
        hit = [abs(s - centre) >= abs(obs - centre) - 1e-9 for s in sums]
    else:
        hit = [s >= obs - 1e-9 for s in sums]
    return sum(hit) / len(sums)


# ------------------------------------------------------------------ the test itself

def test_mann_whitney_hand_checked_example():
    # Pooled 1, 2, 0, 0: midranks 3, 4, 1.5, 1.5. Rank sum of a = 7, U = 7 - 3 = 4, A = 4 / (2 x 2) = 1.
    # The 6 splits give rank sums 3, 4.5, 4.5, 5.5, 5.5, 7 around 5: two are 2 away, so p = 2/6.
    mw = ranks.mann_whitney([1, 2], [0, 0])
    assert mw["u"] == 4 and mw["a"] == 1 and mw["cliffs_delta"] == 1
    assert mw["p"] == pytest.approx(1 / 3) and mw["method"] == "exact" and mw["rearrangements"] == 6
    assert ranks.mann_whitney([1, 2], [0, 0], "greater")["p"] == pytest.approx(1 / 6)
    assert ranks.mann_whitney([1, 2], [0, 0], "less")["p"] == pytest.approx(1.0)


def test_mann_whitney_matches_every_rearrangement_with_ties():
    a, b = [0, 1, 1, 2, 3, 3], [0, 0, 0, 1, 1, 2]
    mw = ranks.mann_whitney(a, b)
    assert mw["p"] == pytest.approx(brute_force_p(a, b))
    assert ranks.mann_whitney(a, b, "greater")["p"] == pytest.approx(brute_force_p(a, b, "greater"))
    u = mannwhitneyu(a, b).statistic
    assert mw["u"] == u and mw["a"] == round(u / 36, 3)


def test_mann_whitney_controls_all_zero():
    """The case in the research notes: the normal approximation gives about half the permutation p."""
    ctrl, mut = [0] * 12, [0, 0, 0, 0, 0, 0, 1, 2, 3, 3, 4, 6]
    mw = ranks.mann_whitney(mut, ctrl)
    assert mw["method"] == "Monte Carlo" and mw["seed"] == ranks.SEED and mw["rearrangements"] == ranks.RESAMPLES
    assert mw["p"] == pytest.approx(0.013, abs=0.003)
    assert mw["p"] > mannwhitneyu(mut, ctrl, method="asymptotic").pvalue
    assert ranks.mann_whitney(mut, ctrl) == mw          # fixed seed: the same p every run


def test_mann_whitney_all_values_equal():
    mw = ranks.mann_whitney([2, 2, 2], [2, 2])
    assert mw["p"] == 1.0 and mw["a"] == 0.5 and mw["cliffs_delta"] == 0 and "same value" in mw["note"]


# ------------------------------------------------------------------ one value per animal

def test_worst_needs_every_neuron_unless_already_worst():
    ro = {"name": "dendrite length", "question": "dl"}
    cfg = cfg_with([ro])
    v = lambda *n: ranks.animal_value(row("wt", *n), cfg, ro)
    assert v(("normal", "full"), ("normal", "partial")) == (1, "")
    assert v(("normal", "full"), ("normal", "can't tell")) == (None, "unclear")
    assert v(("normal", "full"), ("missing", "")) == (None, "not_assessed")
    assert v(("normal", "short"), ("missing", "")) == (2, "")             # already the worst level
    assert v(("normal", "short"), ("normal", "can't tell")) == (2, "")
    assert v(("normal", "full"), ("normal", "")) == (None, "incomplete")
    assert v(("normal", "short"), ("normal", "")) == (None, "incomplete")  # a missing answer is never filled in


def test_mean_sum_and_animal_questions():
    mean = {"name": "m", "question": "dl", "rollup": "mean"}
    cfg = cfg_with([mean])
    assert ranks.animal_value(row("wt", ("normal", "full"), ("normal", "short")), cfg, mean) == (1, "")
    assert ranks.animal_value(row("wt", ("normal", "short"), ("normal", "can't tell")), cfg, mean) == (2, "")
    strict = {**mean, "unclear": "exclude_animal"}
    assert ranks.animal_value(row("wt", ("normal", "short"), ("normal", "can't tell")), cfg, strict) == (None, "unclear")
    assert ranks.animal_value(row("wt", ("missing", ""), ("missing", "")), cfg, mean) == (None, "not_assessed")
    per_neuron_count = dict(COUNT_Q, id="branches", options=["0", "1", "2", "3", "4"], skip_when_calls=["missing"])
    cfg2 = C.merge(C.DEFAULTS, {"scoring": {"neuron_questions": [per_neuron_count]}})
    r = {"score_n1_call": "normal", "score_n1_branches": "3", "score_n2_call": "normal", "score_n2_branches": "2"}
    assert ranks.animal_value(r, cfg2, {"name": "b", "question": "branches"}) == (5, "")
    assert ranks.animal_value(r, cfg2, {"name": "b", "question": "branches", "rollup": "mean_per_neuron"}) == (2.5, "")
    animal = {"name": "puncta", "question": "puncta"}
    assert ranks.animal_value(row("wt", ("normal", "full"), ("normal", "full"), "3+"), cfg, animal) == (3, "")
    assert ranks.animal_value(row("wt", ("normal", "full"), ("normal", "full"), ""), cfg, animal) == (None, "incomplete")


def test_outcome_table_levels_cutoff_and_reasons():
    ro = {"name": "dendrite length", "question": "dl", "cutoff": "short"}
    cfg = cfg_with([ro])
    rows = [row("wt", ("normal", "full"), ("normal", "full")), row("wt", ("normal", "full"), ("normal", "partial")),
            row("mut", ("normal", "short"), ("normal", "full")), row("mut", ("normal", "short"), ("missing", "")),
            row("mut", ("normal", "full"), ("normal", "can't tell"))]
    table, values = ranks.outcome_table(rows, cfg, ro)
    mut, wt = table
    assert values[("A", "mut")] == [2, 2] and values[("A", "wt")] == [0, 1]
    assert mut["animals"] == 2 and mut["left_out_unclear"] == 1 and mut["pct_level_2_short"] == 100.0
    assert wt["median"] == 0.5 and wt["animals_at_or_above_cutoff"] == 0 and mut["animals_at_or_above_cutoff"] == 2
    assert mut["cutoff"] == "short or worse"
    (t,) = ranks.tests(table, values, ro, reference="wt")
    assert t["genotype_a"] == "wt" and t["prob_a_worse"] == 0 and t["p_method"] == "exact"
    (c,) = ranks.cutoff_tests(table, ro, SCALE_Q, reference="wt")
    assert c["a_affected"] == "0/2" and c["b_affected"] == "2/2"


def test_count_table_has_mean_and_top_bin():
    ro = {"name": "puncta", "question": "puncta", "cutoff": 1}
    cfg = cfg_with([ro])
    rows = [row("wt", ("normal", "full"), ("normal", "full"), p) for p in ("0", "0", "1")] + \
           [row("mut", ("normal", "full"), ("normal", "full"), p) for p in ("2", "3+", "3+")]
    table, _ = ranks.outcome_table(rows, cfg, ro)
    assert [r["mean"] for r in table] == [2.667, 0.333]
    assert "pct_level_0_0" not in table[0] and table[0]["pct_at_or_above_cutoff"] == 100.0
    assert ranks.top_bin(COUNT_Q, ro) == "3+"


# ------------------------------------------------------------------ config checks and the plan

@pytest.mark.parametrize("change, message", [
    (lambda c: c["unblinding"]["rank_outcomes"][0].update(rollup="sum"), "rollup for a scale"),
    (lambda c: c["unblinding"]["rank_outcomes"][0].update(cutoff="full"), "levels after the first"),
    (lambda c: c["unblinding"]["rank_outcomes"].append({"name": "puncta", "question": "puncta", "rollup": "sum"}),
     "no rollup"),
    (lambda c: c["unblinding"]["rank_outcomes"].append({"name": "x", "question": "dl"}), "already has a rank outcome"),
    (lambda c: c["unblinding"].update(interaction={"single_a": "a", "single_b": "b", "double": "d",
                                                   "outcome": "dendrite length"}), "scale or count outcome"),
    (lambda c: c["unblinding"].update(allow_cross_microscope=["dendrite length"]), "only within one microscope"),
    (lambda c: c["scoring"]["animal_questions"][0].update(options=["0", "2", "3+"]), "going up by one"),
    (lambda c: c["scoring"]["animal_questions"][0].update(id="dl"), "used twice"),
    (lambda c: c["unblinding"].update(question_outcomes=[{"name": "dendrite length", "questions": ["puncta"],
                                                         "defect_answers": ["3+"]}]), "unique 'name'"),
    (lambda c: c["unblinding"].update(question_outcomes=[
        {"name": "mixed", "questions": ["dl", "puncta"], "defect_answers": ["short"]}]), "not both"),
])
def test_config_check_refuses(change, message):
    raw = {"scoring": {"neuron_questions": [dict(SCALE_Q)], "animal_questions": [dict(COUNT_Q)]},
           "unblinding": {"rank_outcomes": [{"name": "dendrite length", "question": "dl"},
                                            {"name": "puncta", "question": "puncta"}]}}
    C.check(C.merge(C.DEFAULTS, raw))
    raw["unblinding"]["rank_outcomes"] = raw["unblinding"]["rank_outcomes"][:1]
    change(raw)
    with pytest.raises(SystemExit, match=message):
        C.check(C.merge(C.DEFAULTS, raw))


def test_plan_and_form_record_unchanged_without_new_settings():
    cfg = C.merge(C.DEFAULTS, {"scoring": {"neuron_questions": [{"id": "q", "label": "q?", "options": ["yes", "no"]}]}})
    assert "rank_outcomes" not in C.analysis_plan(cfg) and C.analysis_plan(cfg) == {}
    rec = scoreform.form_record(cfg)
    assert "animal_questions" not in rec and "rank_outcomes" not in rec and "type" not in rec["neuron_questions"][0]
    cfg = cfg_with([{"name": "dendrite length", "question": "dl"}])
    assert C.analysis_plan(cfg)["rank_outcomes"] == [{"name": "dendrite length", "question": "dl"}]
    rec = scoreform.form_record(cfg)
    assert rec["neuron_questions"][0]["type"] == "scale" and rec["animal_questions"][0]["id"] == "puncta"


def test_score_fields_and_package_answers_include_animal_questions():
    cfg = cfg_with([])
    assert score_fields(cfg)[-5] == "a_puncta"
    assert scorers.form_answers(cfg)["a_puncta"] == {"0", "1", "2", "3+", ""}
    assert "a_puncta" not in score_fields(C.merge(C.DEFAULTS, {}))


# ------------------------------------------------------------------ builder

def test_builder_round_trip_scale_count_and_animal_questions():
    model = scoreform.preset_model("dendrite")
    model["questions"].append({"id": None, "label": "Puncta in the nerve ring", "type": "count", "ask": "animal",
                               "count": {"min": 0, "max": 5, "top": True}, "cant_tell": "can't tell",
                               "required": True, "outcome": "puncta", "cutoff": "1"})
    model["questions"].append({"id": None, "label": "Animal looks healthy?", "type": "yesno", "ask": "animal",
                               "cant_tell": "", "defect": ["no"], "required": False, "outcome": "unhealthy"})
    raw = scoreform.apply({}, model)
    sc, un = raw["scoring"], raw["unblinding"]
    assert sc["neuron_questions"][0] == {"id": "dendrite_length", "label": "Dendrite length", "type": "scale",
                                         "options": ["full", "partial", "short", "can't tell"],
                                         "cant_tell": "can't tell", "required": True, "skip_when_calls": ["not seen"]}
    assert sc["animal_questions"][0]["options"] == ["0", "1", "2", "3", "4", "5+", "can't tell"]
    assert un["rank_outcomes"] == [{"name": "dendrite length", "question": "dendrite_length", "rollup": "worst"},
                                   {"name": "puncta", "question": "puncta_in_the_nerve_ring", "cutoff": 1}]
    assert un["question_outcomes"] == [{"name": "unhealthy", "questions": ["animal_looks_healthy"],
                                        "defect_answers": ["no"], "not_visible_calls": []}]
    cfg = scoreform._cfg_from_raw(raw)
    C.check(cfg)
    assert scoreform.unsupported(cfg) == ""
    back = scoreform.to_model_cfg(cfg)
    assert scoreform.apply({}, back) == raw
    q = back["questions"][1]
    assert q["ask"] == "animal" and q["count"] == {"min": 0, "max": 5, "top": True} and q["cutoff"] == "1"


@pytest.mark.parametrize("bad, message", [
    ({"type": "count", "count": {"min": 3, "max": 3}}, "above the lowest"),
    ({"type": "count", "count": {"min": 0, "max": 40}}, "at most"),
    ({"type": "scale", "options": ["a", "b"], "outcome": "s", "cutoff": "a"}, "after the first"),
    ({"type": "scale", "options": ["a", "b"], "outcome": "s", "rollup": "sum"}, "one value per animal"),
])
def test_builder_refuses(bad, message):
    model = scoreform.preset_model("blank")
    model["questions"] = [{"label": "Q", "ask": "neuron", "cant_tell": "", "required": False, **bad}]
    with pytest.raises(ValueError, match=message):
        scoreform.clean(model)


def test_two_questions_cannot_share_a_scale_outcome():
    model = scoreform.preset_model("degeneration")
    model["questions"].append(dict(model["questions"][0], id=None, label="Axon score"))
    with pytest.raises(ValueError, match="tested on its own"):
        scoreform.clean(model)


@pytest.mark.parametrize("name", list(scoreform.PRESETS))
def test_every_preset_is_a_valid_form(name):
    raw = scoreform.apply({}, scoreform.preset_model(name))
    cfg = scoreform._cfg_from_raw(raw)
    C.check(cfg)
    assert scoreform.unsupported(cfg) == ""


# ------------------------------------------------------------------ end to end

def test_unblind_writes_rank_tables_tests_and_methods(fresh_project):
    def change(c):
        c["scoring"]["neuron_questions"] = [dict(SCALE_Q)]
        c["scoring"]["animal_questions"] = [dict(COUNT_Q)]
        c["unblinding"]["rank_outcomes"] = [{"name": "dendrite length", "question": "dl", "cutoff": "short"},
                                            {"name": "puncta", "question": "puncta"}]
        c["unblinding"]["primary_outcome"] = "dendrite length"
    edit_config(fresh_project, change)
    prepare(fresh_project, log=lambda *a: None)
    key = read_csv(fresh_project / "key" / "key.csv")
    rows = []
    for r in key:
        mut = r["genotype"] == "mutant"
        rows.append({"code": r["code"], "usable": "yes", "count_this_image": "yes", "n1_call": "normal",
                     "n2_call": "normal", "n1_dl": "short" if mut else "full", "n2_dl": "partial" if mut else "full",
                     "a_puncta": "3+" if mut else "0"})
    write_scores(fresh_project, rows)
    out = unblind(fresh_project, finished=True, anyway=True, log=lambda *a: None)
    table = read_csv(out / "rank_outcomes.csv")
    assert {r["outcome"] for r in table} == {"dendrite length", "puncta"}
    dl = {r["genotype"]: r for r in table if r["outcome"] == "dendrite length"}
    assert dl["mutant"]["median"] == "2" and dl["control"]["median"] == "0"
    assert dl["mutant"]["levels"] == "0 = full; 1 = partial; 2 = short"
    tests = read_csv(out / "rank_outcome_tests.csv")
    mw = [t for t in tests if t["test"].startswith("Mann")]
    assert len(mw) == 2 and all(t["set_before_scoring"] == "yes" for t in tests)
    roles = {(t["outcome"], t["test"][:4]): t["outcome_role"] for t in tests}
    assert roles[("dendrite length", "Mann")] == "primary" and roles[("dendrite length", "Fish")] == "secondary"
    assert roles[("puncta", "Mann")] == "secondary"
    first = next(t for t in mw if t["outcome"] == "dendrite length")
    assert float(first["prob_a_worse"]) in (0.0, 1.0) and first["p_method"] == "exact"
    methods = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "Mann-Whitney test on one value per animal" in methods and "normality was not tested" in methods
    per_neuron = read_csv(out / "per_neuron.csv")
    assert {"dendrite length", "dendrite length (animal value)", "puncta"} <= set(per_neuron[0])
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "counted as 3 (a lower bound)" in summary

    # Changing the roll-up after scoring began stops every test.
    edit_config(fresh_project, lambda c: c["unblinding"]["rank_outcomes"][0].update(rollup="mean"))
    out = unblind(fresh_project, finished=True, anyway=True, log=lambda *a: None)
    assert read_csv(out / "rank_outcome_tests.csv")[0]["note"].startswith("NOT RUN")


def test_hidden_repeat_agreement_includes_scale_outcomes():
    ro = {"name": "dendrite length", "question": "dl"}
    cfg = cfg_with([ro])
    a = row("wt", ("normal", "full"), ("normal", "short")) | {"score_usable": "yes"}
    b = row("wt", ("normal", "partial"), ("normal", "short")) | {"score_usable": "yes"}
    rel = {r["measure"]: r for r in stats.reliability([(a, b)], cfg)}
    assert rel["dendrite length"]["same_answer"] == 1 and rel["dendrite length"]["kappa_type"] == "linear-weighted"
