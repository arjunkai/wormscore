import pytest

from wormscore import config as C
from wormscore import stats

CFG = C.DEFAULTS     # neurons "Neuron 1", "Neuron 2"; defect calls dim, missing


def animals(system, genotype, calls):
    return [{"code": f"{genotype}{i}", "system": system, "genotype": genotype, "settings_group": "A",
             "acquired": "2026-01-02 10:00:00", "n_z": "20", "z_step_um": "0.5",
             "score_n1_call": c1, "score_n2_call": c2} for i, (c1, c2) in enumerate(calls)]


def test_penetrance_counts_animals_by_affected_neurons():
    rows = animals("scope", "mut", [("missing", "missing"), ("dim", "normal"), ("normal", "normal"),
                                    ("normal", "")])
    (p,) = stats.penetrance(rows, CFG)
    assert (p["animals"], p["animals_incomplete"]) == (3, 1)
    assert (p["animals_0_affected"], p["animals_1_affected"], p["animals_2_affected"]) == (1, 1, 1)
    assert p["animals_any_affected"] == 2 and p["penetrance_pct"] == 66.7
    assert p["Neuron 1_affected_pct"] == 66.7 and p["Neuron 2_affected_pct"] == 33.3


def test_fisher_within_microscope_with_bonferroni():
    rows = (animals("A", "ctrl", [("normal", "normal")] * 10)
            + animals("A", "mut", [("missing", "normal")] * 5 + [("normal", "normal")] * 5)
            + animals("B", "ctrl", [("normal", "normal")] * 10)
            + animals("B", "mut", [("missing", "normal")] * 5 + [("normal", "normal")] * 5))
    pen = stats.penetrance(rows, CFG)
    tests = stats.fisher_tests(pen, reference="ctrl")
    assert [(t["system"], t["genotype_b"]) for t in tests] == [("A", "mut"), ("B", "mut")]
    assert tests[0]["p_fisher_two_sided"] == pytest.approx(0.03251, abs=1e-4)
    assert tests[0]["p_bonferroni"] == pytest.approx(2 * 0.03251, abs=2e-4)
    # Genotypes on different microscopes are never compared with each other.
    only_cross = animals("A", "ctrl", [("normal", "normal")] * 5) + animals("B", "mut", [("dim", "dim")] * 5)
    assert stats.fisher_tests(stats.penetrance(only_cross, CFG)) == []


def test_per_neuron_rows_are_one_per_animal_and_neuron():
    rows = animals("A", "mut", [("dim", "normal")])
    long = stats.per_neuron_rows(rows, CFG)
    assert [(r["neuron"], r["call"], r["affected"]) for r in long] == [("Neuron 1", "dim", 1), ("Neuron 2", "normal", 0)]
    assert long[0]["imaging_date"] == "2026-01-02"
