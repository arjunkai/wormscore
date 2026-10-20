"""Results tables written at unblinding: one row per neuron, penetrance, Fisher's exact tests.

The animal is the unit: each counted, usable image is one animal. A neuron is "affected" when
its call is one of the configured defect calls (default: dim or missing). Tests compare the
share of animals with at least one affected neuron, only between genotypes imaged on the
same microscope, because calls made on different microscopes are not interchangeable.
"""
from collections import defaultdict
from itertools import combinations

from scipy.stats import binomtest, fisher_exact

ROI_COLS = ("area_um2", "mean_best_minus_bg", "stack_sum_minus_bg", "saturated_px_mip")


def neuron_calls(row, cfg):
    return [row.get(f"score_n{i}_call", "") for i in range(1, len(cfg["scoring"]["neurons"]) + 1)]


def per_neuron_rows(rows, cfg):
    """Long table for plotting (for example SuperPlots): one row per animal and neuron."""
    out = []
    defect = cfg["scoring"]["defect_calls"]
    for r in rows:
        for i, (name, call) in enumerate(zip(cfg["scoring"]["neurons"], neuron_calls(r, cfg)), start=1):
            out.append({
                "code": r["code"], "genotype": r["genotype"], "system": r["system"],
                "settings_group": r["settings_group"], "imaging_date": (r.get("acquired") or "")[:10],
                "n_z": r["n_z"], "z_step_um": r["z_step_um"], "neuron": name, "call": call,
                "affected": "" if not call else int(call in defect),
                **{f"roi_{c}": r.get(f"n{i}_roi_{c}", "") for c in ROI_COLS},
                **{oc["name"]: neuron_state(r, i, oc) for oc in cfg["unblinding"]["question_outcomes"]},
            })
    return out


def penetrance(rows, cfg):
    """Per microscope and genotype: animals with 0, 1, 2, ... affected neurons, and the share
    with at least one. Animals with any neuron left uncalled are counted separately."""
    defect = cfg["scoring"]["defect_calls"]
    n_neurons = len(cfg["scoring"]["neurons"])
    groups = defaultdict(list)
    for r in rows:
        groups[(r["system"], r["genotype"])].append(neuron_calls(r, cfg))
    table = []
    for (system, geno), animals in sorted(groups.items()):
        complete = [a for a in animals if all(a)]
        k = [sum(c in defect for c in a) for a in complete]
        row = {"system": system, "genotype": geno, "animals": len(complete),
               "animals_incomplete": len(animals) - len(complete)}
        for j in range(n_neurons + 1):
            row[f"animals_{j}_affected"] = k.count(j)
        affected = sum(1 for x in k if x)
        row["animals_any_affected"] = affected
        row["penetrance_pct"] = round(100 * affected / len(complete), 1) if complete else ""
        for i, name in enumerate(cfg["scoring"]["neurons"]):
            row[f"{name}_affected_pct"] = (round(100 * sum(a[i] in defect for a in complete) / len(complete), 1)
                                           if complete else "")
        table.append(row)
    return table


def fisher_tests(pen, reference=""):
    """Two-sided Fisher's exact test on animals with vs without an affected neuron.
    Within each microscope: every genotype against the reference genotype if one is set,
    otherwise every pair. Bonferroni: each p is multiplied by the number of tests (max 1)."""
    by_system = defaultdict(list)
    for row in pen:
        if row["animals"]:
            by_system[row["system"]].append(row)
    pairs = []
    for system, rs in sorted(by_system.items()):
        ref = [r for r in rs if r["genotype"] == reference]
        if reference:
            pairs += [(system, ref[0], r) for r in rs if ref and r is not ref[0]]
        else:
            pairs += [(system, a, b) for a, b in combinations(rs, 2)]
    tests = []
    for system, a, b in pairs:
        ya, yb = a["animals_any_affected"], b["animals_any_affected"]
        _, p = fisher_exact([[ya, a["animals"] - ya], [yb, b["animals"] - yb]])
        tests.append({"system": system, "genotype_a": a["genotype"], "genotype_b": b["genotype"],
                      "a_affected": f"{ya}/{a['animals']}", "b_affected": f"{yb}/{b['animals']}",
                      "p_fisher_two_sided": float(f"{p:.4g}"),
                      "p_bonferroni": float(f"{min(1.0, p * len(pairs)):.4g}"), "n_tests": len(pairs)})
    return tests


# ------------------------------------------------------------- per-neuron question outcomes

def neuron_state(row, i, oc):
    """Neuron i under a question outcome: defect, normal, unclear, not_visible or unanswered."""
    call = row.get(f"score_n{i}_call", "")
    if call in oc.get("not_visible_calls", ["missing"]):
        return "not_visible"
    answers = [row.get(f"score_n{i}_{q}", "") for q in oc["questions"]]
    if any(a in oc["defect_answers"] for a in answers):
        return "defect"
    if not call or "" in answers:
        return "unanswered"
    if any(a in oc.get("unclear_answers", []) for a in answers):
        return "unclear"
    return "normal"


def animal_state(states, unclear_rule):
    """Defective if any neuron is. Unclear neurons are dropped (exclude_neuron) or drop the
    animal (exclude_animal). Animals with no assessed neuron are left out."""
    if "defect" in states:
        return "defect"
    if "unanswered" in states:
        return "incomplete"
    if "unclear" in states and unclear_rule == "exclude_animal":
        return "unclear"
    if "normal" in states:
        return "normal"
    return "unclear" if "unclear" in states else "not_visible"


def outcome_table(rows, cfg, oc):
    """Same layout as penetrance(): per microscope and genotype, animals assessed and defective,
    plus the animals left out and why."""
    n_neurons = len(cfg["scoring"]["neurons"])
    rule = oc.get("unclear", "exclude_neuron")
    groups = defaultdict(list)
    for r in rows:
        states = [neuron_state(r, i, oc) for i in range(1, n_neurons + 1)]
        groups[(r["system"], r["genotype"])].append(animal_state(states, rule))
    table = []
    for (system, geno), animals in sorted(groups.items()):
        n = animals.count("defect") + animals.count("normal")
        table.append({"outcome": oc["name"], "system": system, "genotype": geno, "animals": n,
                      "animals_any_affected": animals.count("defect"),
                      "penetrance_pct": round(100 * animals.count("defect") / n, 1) if n else "",
                      "left_out_no_visible_neuron": animals.count("not_visible"),
                      "left_out_unclear": animals.count("unclear"),
                      "animals_incomplete": animals.count("incomplete")})
    return table


# ------------------------------------------------------------- genetic interaction

# Expected share of affected double mutants from the two single-mutant shares. The models are
# those of Mani et al. 2008 (PNAS 105:3461) applied to the share of unaffected animals
# (W = 1 - penetrance): product W_ab = W_a * W_b; additive W_ab = W_a + W_b - 1; min W_ab = min(W_a, W_b).
EXPECTED = {
    "product": lambda a, b: 1 - (1 - a) * (1 - b),
    "additive": lambda a, b: min(1.0, a + b),
    "min": lambda a, b: max(a, b),
}


def interaction_tests(pen, ia):
    """One-sample two-sided binomial test of the observed double-mutant penetrance against the
    value the chosen model expects from the two singles, within each microscope that has all
    three genotypes. Bonferroni if more than one microscope qualifies."""
    by_system = defaultdict(dict)
    for row in pen:
        if row["animals"]:
            by_system[row["system"]][row["genotype"]] = row
    found = [(s, g) for s, g in sorted(by_system.items())
             if all(ia[k] in g for k in ("single_a", "single_b", "double"))]
    tests = []
    for system, g in found:
        a, b, d = (g[ia[k]] for k in ("single_a", "single_b", "double"))
        pa, pb = a["animals_any_affected"] / a["animals"], b["animals_any_affected"] / b["animals"]
        expected = EXPECTED[ia["model"]](pa, pb)
        k, n = d["animals_any_affected"], d["animals"]
        p = binomtest(k, n, expected).pvalue
        tests.append({
            "system": system, "outcome": ia["outcome"], "model": ia["model"],
            "single_a": ia["single_a"], "single_a_affected": f"{a['animals_any_affected']}/{a['animals']}",
            "single_b": ia["single_b"], "single_b_affected": f"{b['animals_any_affected']}/{b['animals']}",
            "double": ia["double"], "double_affected": f"{k}/{n}",
            "expected_double_pct": round(100 * expected, 1), "observed_double_pct": round(100 * k / n, 1),
            "observed_vs_expected": "higher" if k / n > expected else "lower" if k / n < expected else "equal",
            "p_binomial_two_sided": float(f"{p:.4g}"),
            "p_bonferroni": float(f"{min(1.0, p * len(found)):.4g}"), "n_tests": len(found)})
    return tests
