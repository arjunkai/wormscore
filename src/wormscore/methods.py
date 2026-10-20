"""A Methods paragraph written at unblinding, built from what the project files record.

Covers what reporting checklists ask about blinding (ARRIVE 2.0 item 5: who knew the group at
each stage; MDAR and journal forms: whether blinding was used) plus software, parameters and
statistics. Facts wormscore cannot know (names, whether imaging was blind) come from the
"blinding" section of config.json, or are left as [brackets] for the author to fill in.
"""
import datetime as dt

from . import __version__
from . import config as C


def _date(path):
    return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d") if path.exists() else "[date]"


FORMULAS = {"product": "1 - (1 - P1)(1 - P2)", "additive": "P1 + P2 (at most 1)", "min": "the larger of P1 and P2"}


def _or(items):
    items = list(items)
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


def outcome_sentences(cfg, outcome_tests):
    labels = {q["id"]: q["label"] for q in cfg["scoring"]["neuron_questions"]}
    out = []
    for oc in cfg["unblinding"]["question_outcomes"]:
        qs = " / ".join(f'"{labels[q]}"' for q in oc["questions"])
        unclear = oc.get("unclear_answers", [])
        n = sum(1 for t in outcome_tests if t["outcome"] == oc["name"])
        out.append(
            f"For {oc['name']}, a neuron was defective if the answer to {qs} was {_or(oc['defect_answers'])}; "
            f"neurons called {_or(oc.get('not_visible_calls', ['missing']))} were not assessed"
            + (f", and an answer of {_or(unclear)} left out the "
               f"{'animal' if oc.get('unclear', 'exclude_neuron') == 'exclude_animal' else 'neuron'}" if unclear else "")
            + ". An animal was defective if any assessed neuron was; animals with no assessed neuron were left "
            f"out. Groups were compared by the same Fisher's exact tests (Bonferroni for {n} test{'s' if n != 1 else ''}).")
    return out


def interaction_sentence(cfg, interaction_tests):
    ia = C.interaction(cfg)
    if not ia:
        return []
    what = "share of affected animals" if ia["outcome"] == "calls" else f"share of animals with {ia['outcome']}"
    pre = ("chosen before scoring and recorded in the project log" if interaction_tests
           and all(t["set_before_scoring"] == "yes" for t in interaction_tests) else "[state when the model was chosen]")
    return [f"Genetic interaction was tested with the {ia['model']} model (Mani et al. 2008, PNAS 105:3461), "
            f"{pre}. The {what} expected in {ia['double']} was {FORMULAS[ia['model']]}, where P1 and P2 are the "
            f"observed shares in {ia['single_a']} and {ia['single_b']}. The observed share was compared with this "
            "value by a two-sided one-sample binomial test, only between animals imaged on the same microscope. "
            "The expected value treats P1 and P2 as exact and ignores their sampling error."]


def statement(project, cfg, n_included, n_excluded, history, n_scored, tests, outcome_tests=(),
              interaction_tests=()):
    b = cfg["blinding"]
    who = lambda k, default: b.get(k) or default
    mc, sc = cfg["measurement"], cfg["scoring"]
    saved = sorted(r["saved"][:10] for r in history if r.get("saved"))
    span = ("[dates]" if not saved else f"on {saved[0]}" if saved[0] == saved[-1]
            else f"between {saved[0]} and {saved[-1]}")
    calls = sc["calls"][:-1] and f"{', '.join(sc['calls'][:-1])} or {sc['calls'][-1]}" or sc["calls"][0]
    imaging = {"yes": "The person who imaged the animals knew their genotype.",
               "no": "The person who imaged the animals did not know their genotype."}.get(
        str(b.get("imager_knew_genotype", "")).lower(),
        "[State whether the person who imaged the animals knew their genotype.]")
    ref = cfg["unblinding"]["reference_genotype"]
    lines = [
        "# Methods text (draft; check every statement before use)",
        "",
        "**Blinded scoring.** "
        f"{imaging} Images were then coded and scored blind with wormscore {__version__} "
        "(https://github.com/arjunkai/wormscore). Inclusion rules were set before scoring and applied "
        f"without regard to genotype: {n_included} of {n_included + n_excluded} images were included"
        + (" (the reason for each exclusion is listed in excluded.csv)" if n_excluded else "")
        + f". On {_date(project / 'key' / 'seed.txt')}, "
        f"{who('coded_by', '[name]')} assigned every included image a random code; the key linking codes to "
        "files and genotypes was kept in a separate folder that the scoring software does not read. "
        f"{who('scored_by', '[name]')} scored the coded images {span}, seeing no file names, genotypes or "
        "brightness measurements, with every image from the same microscope shown at the same fixed "
        f"brightness range. Each neuron was called {calls}; "
        f"{' or '.join(sc['defect_calls'])} counted as affected. Codes were linked "
        f"back to genotypes on {dt.date.today():%Y-%m-%d}, after {n_scored} image{'s' if n_scored != 1 else ''} "
        f"had been scored.",
        "",
        "**Measurement.** "
        f"Each plane was median-filtered ({mc['median_filter_px']} x {mc['median_filter_px']} pixels). For each "
        "scored region, background was the median of a ring starting "
        f"{mc['background_ring_gap_um']} um outside the region and {mc['background_ring_width_um']} um wide, "
        "excluding other regions. Reported values are the background-subtracted mean in the sharpest plane "
        "and the background-subtracted sum over all planes; saturated pixels were counted. Brightness was "
        "compared only between images taken on the same microscope with the same settings.",
        "",
        "**Statistics.** "
        "Each animal was one image and the unit of analysis. Penetrance was the share of animals with at "
        "least one affected neuron. "
        + (f"Each genotype was compared with {ref}" if ref else "Genotypes were compared pairwise")
        + " by two-sided Fisher's exact test, only between animals imaged on the same microscope, with "
        f"Bonferroni correction for {len(tests)} test{'s' if len(tests) != 1 else ''}."
        + "".join(" " + x for x in outcome_sentences(cfg, outcome_tests) + interaction_sentence(cfg, interaction_tests)),
        "",
        "## Who knew the genotype at each stage (ARRIVE 2.0 item 5)",
        "",
        "| Stage | Blinded? |",
        "|---|---|",
        f"| Imaging | {imaging} |",
        f"| Coding | {who('coded_by', '[name]')} ran the coding step; codes were random (seed kept in the key) |",
        f"| Scoring (outcome assessment) | Blind: {who('scored_by', '[name]')} saw coded images only |",
        "| Analysis | Measurements were computed automatically and joined to genotypes only at unblinding |",
    ]
    return "\n".join(lines) + "\n"
