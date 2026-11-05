"""Ordered scale and count answers (wormscore 1.4.0): one value per animal, then a Mann-Whitney test
with a permutation p-value.

A "rank outcome" (config.json unblinding.rank_outcomes) names one scale or count question:
  {"name": "dendrite score", "question": "dendrite", "rollup": "worst", "cutoff": "4",
   "unclear_answers": ["can't tell"], "unclear": "exclude_neuron"}
Scale answers are levels in order, best first and worst last (the level's position is its value).
Count answers are whole numbers; a last answer such as "10+" means "10 or more" and is taken as 10.

The animal is the unit. A question asked for each neuron is reduced to one value per animal:
  scale: "worst" (default; the highest level of any neuron) or "mean" (mean level of assessed neurons)
  count: "sum" (default; total over neurons) or "mean_per_neuron" (mean of assessed neurons)
"worst" and "sum" need every neuron assessed, otherwise the animal is left out (an animal whose
assessed neuron already has the worst level counts, since no other answer can change its value).
A "can't tell" answer is never a level: that neuron is unclear. Neurons whose call skips the question
are "not assessed". Animals left out are counted per reason, never dropped silently.

Two genotypes are compared with the Mann-Whitney (Wilcoxon rank-sum) statistic on the animal values.
The p-value comes from permuting the pooled midranks: exact (every rearrangement) when there are at
most EXACT_LIMIT rearrangements, otherwise RESAMPLES random ones with a fixed seed. scipy's own
mannwhitneyu is not used, because with ties it falls back to a normal approximation that can give
p-values about half the correct size on short scales. Effect size: A = U / (n_a n_b), the chance
that a random genotype_a animal scores worse than a random genotype_b animal (ties count half),
and Cliff's delta = 2A - 1.
"""
import inspect
import math
import re
from collections import defaultdict


SCALE, COUNT = "scale", "count"
RANK_TYPES = (SCALE, COUNT)
ROLLUPS = {SCALE: ("worst", "mean"), COUNT: ("sum", "mean_per_neuron")}
DEFAULT_ROLLUP = {SCALE: "worst", COUNT: "sum"}
EXACT_LIMIT = 100_000
RESAMPLES = 100_000
SEED = 1404
REASONS = ("not_assessed", "unclear", "incomplete")


# ------------------------------------------------------------------ questions and levels

def questions(cfg):
    """Every question by id: (question, "neuron" or "animal")."""
    sc = cfg["scoring"]
    out = {q["id"]: (q, "neuron") for q in sc["neuron_questions"]}
    out.update({q["id"]: (q, "animal") for q in sc.get("animal_questions") or []})
    return out


def count_value(text):
    """The number of a count answer ("3" -> 3, "10+" -> 10), or None if it is not one."""
    m = re.fullmatch(r"(\d{1,4})(\+?)", str(text).strip())
    return int(m.group(1)) if m else None


def unclear_of(q, ro):
    """The "can't tell" answers: the question's own (set by the builder) or the outcome's."""
    return list(ro.get("unclear_answers") or ([q["cant_tell"]] if q.get("cant_tell") else []))


def levels(q, ro):
    """The answers that are levels, in order (the question's options minus the unclear answers)."""
    unclear = set(unclear_of(q, ro))
    return [o for o in q["options"] if o not in unclear]


def count_levels_ok(opts):
    """Count answers: whole numbers going up by one, the last may end in "+"."""
    vals = [count_value(o) for o in opts]
    if len(opts) < 2 or None in vals or any(o.endswith("+") for o in opts[:-1]):
        return False
    return all(b == a + 1 for a, b in zip(vals, vals[1:]))


def value_of(q, ro, answer):
    lv = levels(q, ro)
    if answer not in lv:
        return None
    return count_value(answer) if q.get("type") == COUNT else lv.index(answer)


def top_bin(q, ro):
    lv = levels(q, ro)
    return lv[-1] if q.get("type") == COUNT and lv and lv[-1].endswith("+") else ""


def rollup(q, ro, where):
    return "" if where == "animal" else ro.get("rollup") or DEFAULT_ROLLUP[q["type"]]


def cutoff_value(q, ro):
    """The cutoff as a value on the animal scale, or None if no cutoff is set."""
    c = ro.get("cutoff")
    if c in (None, ""):
        return None
    if q.get("type") == COUNT:
        return int(c)
    return levels(q, ro).index(c)


def cutoff_text(q, ro):
    c = ro.get("cutoff")
    if c in (None, ""):
        return ""
    return f"{c} or more" if q.get("type") == COUNT else f"{c} or worse"


# ------------------------------------------------------------------ one value per animal

def neuron_answer_state(row, i, q, ro):
    """(value, state) for neuron i: state is "value", "unclear", "not_assessed" or "unanswered"."""
    ans = row.get(f"score_n{i}_{q['id']}", "")
    if ans in unclear_of(q, ro):
        return None, "unclear"
    v = value_of(q, ro, ans)
    if v is not None:
        return v, "value"
    call = row.get(f"score_n{i}_call", "")
    if call in (q.get("skip_when_calls") or []):
        return None, "not_assessed"
    return None, "unanswered"


def animal_value(row, cfg, ro):
    """(value, reason): the animal's value, or None and why it is left out ("not_assessed",
    "unclear" or "incomplete")."""
    q, where = questions(cfg)[ro["question"]]
    if where == "animal":
        ans = row.get(f"score_a_{q['id']}", "")
        if ans in unclear_of(q, ro):
            return None, "unclear"
        v = value_of(q, ro, ans)
        return (v, "") if v is not None else (None, "incomplete")
    n = len(cfg["scoring"]["neurons"])
    got = [neuron_answer_state(row, i, q, ro) for i in range(1, n + 1)]
    vals = [v for v, s in got if s == "value"]
    states = {s for _, s in got}
    if "unanswered" in states:
        return None, "incomplete"
    why = "unclear" if "unclear" in states else "not_assessed"
    how = rollup(q, ro, where)
    if how in ("worst", "sum"):
        if len(vals) == n:
            return (max(vals) if how == "worst" else sum(vals)), ""
        top = len(levels(q, ro)) - 1
        if how == "worst" and top in vals:
            return top, ""
        return None, why
    if "unclear" in states and ro.get("unclear", "exclude_neuron") == "exclude_animal":
        return None, "unclear"
    if not vals:
        return None, why
    return sum(vals) / len(vals), ""


def _quantile(sorted_vals, p):
    """Linear interpolation between order statistics (as Excel QUARTILE.INC and R type 7)."""
    if not sorted_vals:
        return ""
    h = (len(sorted_vals) - 1) * p
    lo = math.floor(h)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (h - lo) * (sorted_vals[hi] - sorted_vals[lo])


def _num(x):
    if x == "":
        return ""
    return int(x) if float(x).is_integer() else round(x, 3)


def outcome_table(rows, cfg, ro):
    """Per microscope and genotype: animals with a value, animals left out by reason, median and
    quartiles, % at each level (scales whose value is a level) or the mean (counts), and the share
    at or above the cutoff. Returns (table, values by (system, genotype))."""
    q, where = questions(cfg)[ro["question"]]
    how = rollup(q, ro, where)
    lv = levels(q, ro)
    cut = cutoff_value(q, ro)
    groups = defaultdict(list)
    for r in rows:
        groups[(r["system"], r["genotype"])].append(animal_value(r, cfg, ro))
    table, values = [], {}
    for (system, geno), got in sorted(groups.items()):
        vals = sorted(v for v, _ in got if v is not None)
        values[(system, geno)] = vals
        n = len(vals)
        row = {"outcome": ro["name"], "system": system, "genotype": geno,
               "question_type": q["type"], "asked": "once per animal" if where == "animal" else "for each neuron",
               "rollup": how or "none (one answer per animal)", "animals": n,
               **{f"left_out_{x}": sum(1 for v, why in got if v is None and why == x) for x in REASONS},
               "median": _num(_quantile(vals, 0.5)), "q1": _num(_quantile(vals, 0.25)),
               "q3": _num(_quantile(vals, 0.75))}
        if q["type"] == COUNT:
            row["mean"] = round(sum(vals) / n, 3) if n else ""
        elif how != "mean":
            for j, name in enumerate(lv):
                row[f"pct_level_{j}_{name}"] = round(100 * vals.count(j) / n, 1) if n else ""
        if cut is not None:
            k = sum(v >= cut for v in vals)
            row["cutoff"] = cutoff_text(q, ro)
            row["animals_at_or_above_cutoff"] = k
            row["pct_at_or_above_cutoff"] = round(100 * k / n, 1) if n else ""
        table.append(row)
    return table, values


def level_key(q, ro):
    """'0 = full; 1 = partial; 2 = short' for scales, so the numbers in the tables can be read."""
    if q["type"] != SCALE:
        return ""
    return "; ".join(f"{j} = {name}" for j, name in enumerate(levels(q, ro)))


# ------------------------------------------------------------------ Mann-Whitney, permutation p

def _rng_kwargs(func, seed):
    import numpy as np
    params = inspect.signature(func).parameters
    gen = np.random.default_rng(seed)
    return {"rng": gen} if "rng" in params else {"random_state": gen}


def mann_whitney(a, b, alternative="two-sided"):
    """Mann-Whitney U of a vs b (larger = worse) with a permutation p-value on the pooled midranks.
    alternative "greater": a scores worse than b. Returns a dict with u, a (probability of
    superiority), cliffs_delta, p, method, rearrangements and seed (Monte Carlo only)."""
    import numpy as np
    from scipy.stats import permutation_test, rankdata
    na, nb = len(a), len(b)
    pooled = np.asarray(list(a) + list(b), dtype=float)
    r2 = np.rint(2 * rankdata(pooled)).astype(np.int64)      # doubled midranks: whole numbers, no rounding
    sa2 = int(r2[:na].sum())
    u = sa2 / 2 - na * (na + 1) / 2
    prob = u / (na * nb)
    n_max = math.comb(na + nb, na)
    out = {"u": _num(u), "a": round(prob, 3), "cliffs_delta": round(2 * prob - 1, 3)}
    if np.all(pooled == pooled[0]):
        return out | {"p": 1.0, "method": "all values equal", "rearrangements": n_max, "seed": "",
                      "note": "every animal has the same value, so p = 1"}
    centre = na * (na + nb + 1)                                # 2 x the expected rank sum of a
    if alternative == "two-sided":
        stat, alt = (lambda x, y, axis: np.abs(np.sum(x, axis=axis) - centre)), "greater"
    else:
        stat, alt = (lambda x, y, axis: np.sum(x, axis=axis)), alternative
    exact = n_max <= EXACT_LIMIT
    res = permutation_test((r2[:na], r2[na:]), stat, permutation_type="independent", vectorized=True,
                           n_resamples=n_max if exact else RESAMPLES, alternative=alt, batch=20_000,
                           **_rng_kwargs(permutation_test, SEED))
    p = float(min(1.0, res.pvalue))
    return out | {"p": p, "method": "exact" if exact else "Monte Carlo",
                  "rearrangements": n_max if exact else RESAMPLES, "seed": "" if exact else SEED, "note": ""}


def tests(table, values, ro, reference="", comparisons=(), alternative="two-sided"):
    """Mann-Whitney tests between the same pairs as Fisher's exact tests (within each microscope),
    Bonferroni over the pairs of this outcome."""
    from . import stats   # scipy: loaded only when tests run, not when a config is checked
    pairs = stats.planned_pairs(table, reference, comparisons)
    out = []
    for system, ra, rb in pairs:
        va, vb = values[(system, ra["genotype"])], values[(system, rb["genotype"])]
        mw = mann_whitney(va, vb, alternative)
        out.append({
            "outcome": ro["name"], "test": "Mann-Whitney, permutation p", "system": system,
            "genotype_a": ra["genotype"], "genotype_b": rb["genotype"],
            "n_a": len(va), "n_b": len(vb), "median_a": ra["median"], "median_b": rb["median"],
            "u": mw["u"], "prob_a_worse": mw["a"], "cliffs_delta": mw["cliffs_delta"],
            stats.p_column("mann_whitney", alternative): float(f"{mw['p']:.4g}"),
            "p_bonferroni": float(f"{min(1.0, mw['p'] * len(pairs)):.4g}"), "n_tests": len(pairs),
            "p_method": mw["method"], "rearrangements": mw["rearrangements"], "seed": mw["seed"],
            **({"note": mw["note"]} if mw["note"] else {})})
    return out


def cutoff_tests(table, ro, q, reference="", comparisons=(), alternative="two-sided"):
    """Fisher's exact test on animals at or above the cutoff vs below it."""
    from . import stats   # scipy: loaded only when tests run, not when a config is checked
    pen = [{"system": r["system"], "genotype": r["genotype"], "animals": r["animals"],
            "animals_any_affected": r["animals_at_or_above_cutoff"]} for r in table]
    return [{"outcome": ro["name"], "test": "Fisher's exact, at or above the cutoff",
             "definition": f"at or above the cutoff ({cutoff_text(q, ro)})", **t}
            for t in stats.fisher_tests(pen, reference, comparisons, alternative)]


def describe(ro, cfg):
    """One line for the summary: what the value is."""
    q, where = questions(cfg)[ro["question"]]
    how = rollup(q, ro, where)
    what = {"worst": "worst level of any neuron", "mean": "mean level of the assessed neurons",
            "sum": "total over the neurons", "mean_per_neuron": "mean per assessed neuron",
            "": "the answer for the animal"}[how]
    return f"\"{q.get('label', q['id'])}\" ({q['type']}; value per animal = {what})"


# ------------------------------------------------------------------ Methods text

def methods_sentences(cfg, rank_tests):
    out = []
    for ro in cfg["unblinding"].get("rank_outcomes") or []:
        q, where = questions(cfg)[ro["question"]]
        how = rollup(q, ro, where)
        unclear = unclear_of(q, ro)
        n = sum(1 for t in rank_tests if t["outcome"] == ro["name"] and t["test"].startswith("Mann"))
        lv = levels(q, ro)
        if q["type"] == SCALE:
            scale = (f"an ordered scale from {lv[0]} (best) to {lv[-1]} (worst), scored 0 to {len(lv) - 1}")
        else:
            scale = f"a count from {lv[0]} to {lv[-1]}" + (f" ({lv[-1]} counted as {count_value(lv[-1])})"
                                                          if top_bin(q, ro) else "")
        if where == "animal":
            unit = "Each animal was answered once"
        else:
            unit = {"worst": "The animal's value was the worst level of its neurons; animals with a neuron not "
                             "assessed were left out unless an assessed neuron already had the worst level",
                    "mean": "The animal's value was the mean level of its assessed neurons",
                    "sum": "The animal's value was the total over its neurons; animals with a neuron not "
                           "assessed were left out",
                    "mean_per_neuron": "The animal's value was the mean count per assessed neuron"}[how]
        skip = q.get("skip_when_calls") or []
        drops = "neuron" if how in ("mean", "mean_per_neuron") and \
            ro.get("unclear", "exclude_neuron") == "exclude_neuron" else "animal"
        sentence = (f"For {ro['name']}, \"{q.get('label', q['id'])}\" was answered on {scale}. {unit}"
                    + (f"; neurons called {stats_or(skip)} were not assessed" if where == "neuron" and skip else "")
                    + (f"; an answer of {stats_or(unclear)} was treated as unclear, never as a level, and left "
                       f"out the {drops}" if unclear else "")
                    + ". Animals left out are counted per genotype and reason in rank_outcomes.csv. ")
        mw = [t for t in rank_tests if t["outcome"] == ro["name"] and t["test"].startswith("Mann")]
        methods_used = sorted({t["p_method"] for t in mw})
        how_p = ("exact (every rearrangement of the pooled midranks)" if methods_used == ["exact"]
                 else f"from {RESAMPLES:,} random rearrangements of the pooled midranks (seed {SEED})"
                 if methods_used == ["Monte Carlo"] else
                 f"exact when there were at most {EXACT_LIMIT:,} rearrangements of the pooled midranks, otherwise "
                 f"from {RESAMPLES:,} random rearrangements (seed {SEED})")
        sentence += (f"Genotypes were compared with the Mann-Whitney test on one value per animal, with the "
                     f"p-value {how_p}, Bonferroni-corrected for {n} test{'s' if n != 1 else ''}; because a rank "
                     "test was used, normality was not tested. The effect size is the probability that an animal "
                     "of the first genotype scores worse than one of the second (ties counted half), with "
                     "Cliff's delta = 2 x that probability - 1.")
        if ro.get("cutoff") not in (None, ""):
            sentence += (f" The share of animals at {cutoff_text(q, ro)}, a cutoff set before scoring, was "
                         "compared with Fisher's exact test.")
        out.append(sentence)
    return out


def stats_or(items):
    items = list(items)
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


# ------------------------------------------------------------------ config check

def check(cfg, outcome_names):
    """Stop with a plain message if animal_questions or rank_outcomes do not fit. Returns the rank
    outcome names."""
    sc, un = cfg["scoring"], cfg["unblinding"]
    aq = sc.get("animal_questions") or []
    if not isinstance(aq, list):
        raise SystemExit("scoring.animal_questions must be a list.")
    for q in aq:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not q["id"] \
                or not isinstance(q.get("options"), list) or len(q["options"]) < 2:
            raise SystemExit("Each animal question needs an 'id' and at least two 'options'.")
    if not aq and not un.get("rank_outcomes") and not any("type" in q for q in sc["neuron_questions"]):
        return []          # nothing from 1.4.0 is used: forms from earlier versions are checked as before
    ids = [q.get("id") for q in sc["neuron_questions"]] + [q.get("id") for q in aq]
    for q in sc["neuron_questions"] + aq:
        if ids.count(q["id"]) > 1:
            raise SystemExit(f"question id {q['id']!r} is used twice (ids must differ across neuron and animal questions).")
        if q.get("type") not in (None, SCALE, COUNT):
            raise SystemExit(f"question {q['id']!r}: type must be \"scale\" or \"count\" (or left out).")
        if q.get("cant_tell") and q["cant_tell"] not in q["options"]:
            raise SystemExit(f"question {q['id']!r}: cant_tell must be one of its options.")
        if q.get("type") == COUNT and not count_levels_ok([o for o in q["options"] if o != q.get("cant_tell")]):
            raise SystemExit(f"question {q['id']!r}: count answers must be whole numbers going up by one, "
                             "for example 0, 1, 2, 3+ (only the last may end in +).")
    for q in aq:
        if "skip_when_calls" in q:
            raise SystemExit(f"animal question {q['id']!r}: skip_when_calls only applies to neuron questions.")
        if not isinstance(q.get("required", False), bool):
            raise SystemExit(f"animal question {q['id']!r}: required must be true or false.")
    ros = un.get("rank_outcomes") or []
    if not isinstance(ros, list):
        raise SystemExit("rank_outcomes must be a list.")
    qs = questions(cfg)
    names, used = [], set()
    for ro in ros:
        name = ro.get("name", "") if isinstance(ro, dict) else ""
        if not name or name in names or name in outcome_names or name == "calls":
            raise SystemExit(f"rank_outcomes: each entry needs a unique 'name' other than 'calls' and the "
                             f"question_outcomes names (got {name!r}).")
        names.append(name)
        qid = ro.get("question")
        if qid not in qs:
            raise SystemExit(f"rank_outcomes '{name}': no question with id {qid!r}.")
        if qid in used:
            raise SystemExit(f"rank_outcomes '{name}': question {qid!r} already has a rank outcome.")
        used.add(qid)
        q, where = qs[qid]
        if q.get("type") not in RANK_TYPES:
            raise SystemExit(f"rank_outcomes '{name}': question {qid!r} must have type \"scale\" or \"count\".")
        if q.get("cant_tell") and q["cant_tell"] not in q["options"]:
            raise SystemExit(f"question {qid!r}: cant_tell must be one of its options.")
        unclear = unclear_of(q, ro)
        if set(unclear) - set(q["options"]):
            raise SystemExit(f"rank_outcomes '{name}': unclear_answers {sorted(set(unclear) - set(q['options']))} "
                             f"are not options of question {qid!r}.")
        lv = levels(q, ro)
        if len(lv) < 2:
            raise SystemExit(f"rank_outcomes '{name}': question {qid!r} needs at least two levels.")
        if q["type"] == COUNT and not count_levels_ok(lv):
            raise SystemExit(f"rank_outcomes '{name}': count answers must be whole numbers going up by one, "
                             "for example 0, 1, 2, 3+ (only the last may end in +).")
        how = ro.get("rollup", "")
        if where == "animal":
            if how:
                raise SystemExit(f"rank_outcomes '{name}': question {qid!r} is asked once per animal, so it has no rollup.")
        elif how and how not in ROLLUPS[q["type"]]:
            raise SystemExit(f"rank_outcomes '{name}': rollup for a {q['type']} must be "
                             f"{' or '.join(ROLLUPS[q['type']])}.")
        if ro.get("unclear", "exclude_neuron") not in ("exclude_neuron", "exclude_animal"):
            raise SystemExit(f"rank_outcomes '{name}': 'unclear' must be exclude_neuron or exclude_animal.")
        c = ro.get("cutoff")
        if c not in (None, ""):
            if q["type"] == SCALE and (c not in lv or c == lv[0]):
                raise SystemExit(f"rank_outcomes '{name}': cutoff must be one of the levels after the first "
                                 f"({', '.join(lv[1:])}).")
            if q["type"] == COUNT and (isinstance(c, bool) or not isinstance(c, int) or c < 1):
                raise SystemExit(f"rank_outcomes '{name}': cutoff for a count must be a whole number of 1 or more.")
        unknown = set(ro) - {"name", "question", "rollup", "cutoff", "unclear_answers", "unclear"}
        if unknown:
            raise SystemExit(f"rank_outcomes '{name}': unknown setting(s) {sorted(unknown)}.")
    return names
