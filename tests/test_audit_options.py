"""Options added after the measurement and statistics audit (all off by default)."""
import math
import random

import pytest

from wormscore import config as C
from wormscore import methods
from wormscore import stats
from test_results_options import pen_row

IA = {"single_a": "a", "single_b": "b", "double": "ab", "model": "product", "outcome": "calls"}


def test_default_interaction_test_is_still_the_binomial_test():
    pen = [pen_row("a", 6, 20), pen_row("b", 4, 20), pen_row("ab", 18, 20)]
    (t,) = stats.interaction_tests(pen, IA)
    assert "p_binomial_two_sided" in t and "likelihood_ratio_g" not in t
    assert C.interaction(C.merge(C.DEFAULTS, {"unblinding": {"interaction": IA}})).get("test") is None  # plan unchanged


def test_likelihood_ratio_matches_a_hand_computed_case():
    # Single B has no affected animals, so under the product model the double's share equals single A's.
    # The fit under the model pools A and the double: (2 + 29) / (50 + 61).
    ka, na, kd, nd = 2, 50, 29, 61
    p0 = (ka + kd) / (na + nd)
    ll = lambda k, n, p: k * math.log(p) + (n - k) * math.log(1 - p)
    g = 2 * (ll(ka, na, ka / na) + ll(kd, nd, kd / nd) - ll(ka, na, p0) - ll(kd, nd, p0))
    got, p = stats.likelihood_ratio_interaction(ka, na, 0, 49, kd, nd, "product")
    assert got == pytest.approx(g, rel=1e-6)
    _, p_up = stats.likelihood_ratio_interaction(ka, na, 0, 49, kd, nd, "product", "greater")
    _, p_down = stats.likelihood_ratio_interaction(ka, na, 0, 49, kd, nd, "product", "less")
    assert p_up == pytest.approx(p / 2) and p_down == pytest.approx(1 - p / 2)


@pytest.mark.parametrize("model", ["product", "additive", "min"])
def test_likelihood_ratio_is_near_zero_when_the_double_fits_the_model(model):
    pa, pb = 0.3, 0.2
    pd = stats.EXPECTED[model](pa, pb)
    g, p = stats.likelihood_ratio_interaction(30, 100, 20, 100, round(pd * 100), 100, model)
    assert g < 0.05 and p > 0.8


def test_likelihood_ratio_holds_its_false_positive_rate_better_than_the_binomial_test():
    rng = random.Random(1)
    draw = lambda n, p: sum(rng.random() < p for _ in range(n))
    n, pa, pb, runs = 30, 0.2, 0.3, 300
    lr = binom = 0
    for _ in range(runs):
        ka, kb, kd = draw(n, pa), draw(n, pb), draw(n, 1 - (1 - pa) * (1 - pb))
        pen = [pen_row("a", ka, n), pen_row("b", kb, n), pen_row("ab", kd, n)]
        (t,) = stats.interaction_tests(pen, IA)
        (u,) = stats.interaction_tests(pen, IA | {"test": "likelihood_ratio"})
        binom += t["p_binomial_two_sided"] < 0.05
        lr += u["p_likelihood_ratio_two_sided"] < 0.05
    assert lr / runs < 0.09 and lr < binom


def test_config_checks_the_new_settings():
    base = {"unblinding": {"interaction": IA | {"test": "t-test"}}}
    with pytest.raises(SystemExit, match="test must be one of"):
        C.check(C.merge(C.DEFAULTS, base))
    for rel, msg in (({"call_weights": "cubic"}, "call_weights"), ({"extra_statistics": "yes"}, "extra_statistics"),
                     ({"repeats": 1}, "unknown setting")):
        with pytest.raises(SystemExit, match=msg):
            C.check(C.merge(C.DEFAULTS, {"reliability": rel}))


def test_kappa_weights_and_extra_statistics():
    cats = ["normal", "dim", "missing"]
    pairs = [("normal", "normal")] * 6 + [("normal", "dim"), ("dim", "missing"), ("missing", "missing"), ("dim", "dim")]
    lin, quad, none = (stats.kappa(pairs, cats, w) for w in ("linear", "quadratic", "none"))
    assert lin == stats.kappa(pairs, cats, True) and none == stats.kappa(pairs, cats, False)
    assert quad > lin > none
    row = stats.agreement_row("neuron call", pairs, cats, weighted="quadratic", extra=True)
    assert row["kappa_type"] == "quadratic-weighted"
    assert row["pabak"] == pytest.approx(round((3 * 0.8 - 1) / 2, 3))
    assert row["kappa_ci95_low"] <= row["kappa"] <= row["kappa_ci95_high"]
    assert row == stats.agreement_row("neuron call", pairs, cats, weighted="quadratic", extra=True)  # fixed seed
    plain = stats.agreement_row("neuron call", pairs, cats, weighted=True)
    assert set(plain) == {"measure", "pairs", "same_answer", "percent_agreement", "kappa", "kappa_type"}


def test_high_agreement_low_kappa_case_shows_in_ac1():
    # 18 of 20 agree, but nearly every answer is "no": kappa is low, AC1 and PABAK are not.
    pairs = [("no", "no")] * 18 + [("no", "yes"), ("yes", "no")]
    row = stats.agreement_row("animal affected", pairs, ["no", "yes"], extra=True)
    assert row["kappa"] < 0 and row["gwet_ac1"] > 0.85 and row["pabak"] == pytest.approx(0.8)


def test_dim_check_counts_regions_with_no_measurable_noise():
    cfg = C.merge(C.DEFAULTS, {"unblinding": {"dim_cutoff_sd": 3}})
    rows = [{"settings_group": "A", "system": "s", "score_n1_call": "normal", "score_n2_call": "dim",
             "n1_roi_mean_best_minus_bg": "40", "n1_roi_background_sd_best_plane": "0",
             "n2_roi_mean_best_minus_bg": "40", "n2_roi_background_sd_best_plane": "5"}]
    assert sum(r["neurons_measured"] for r in stats.dim_check(rows, cfg)) == 1
    assert stats.dim_check_skipped(rows, cfg) == 1
    assert stats.dim_check_skipped([{"n1_roi_mean_best_minus_bg": ""}], cfg) == 0


def test_methods_says_when_the_dim_check_could_not_be_made():
    cfg = C.merge(C.DEFAULTS, {"unblinding": {"dim_cutoff_sd": 3}})
    (s,) = methods.dim_sentence(cfg, [])
    assert "could not be made" in s and "0 of 0" not in s
    rows = [{"call": "normal", "below_cutoff": 1, "neurons_measured": 4}]
    (s,) = methods.dim_sentence(cfg, rows, 2)
    assert "1 of 4" in s and "2 outlined regions with no measurable background noise" in s
    assert methods.dim_sentence(cfg, rows) == [methods.dim_sentence(cfg, rows, 0)[0]]
