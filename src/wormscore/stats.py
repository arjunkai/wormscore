"""Results tables written at unblinding: one row per neuron, penetrance, Fisher's exact tests.

The animal is the unit: each counted, usable image is one animal. A neuron is "affected" when
its call is one of the configured defect calls (default: dim or missing). Tests compare the
share of animals with at least one affected neuron, only between genotypes imaged on the
same microscope, because calls made on different microscopes are not interchangeable.
"""
from collections import defaultdict
from itertools import combinations

from scipy.stats import fisher_exact

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
