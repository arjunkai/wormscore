"""Interim totals (1.17.0): pooled totals of the answers saved so far, recorded as an interim look.
Nothing by genotype and nothing about hidden repeats may appear, even as a count."""
import datetime as dt
import json

from wormscore import config as C
from wormscore import interim as I
from wormscore import methods
from wormscore.pipeline import prepare, read_csv

from test_results_options import edit_config


def save_scores(project, calls, start="2026-11-16 10:00:00", minutes=2):
    """Scores as the scoring page saves them, one image every `minutes`, in code order (made-up codes
    past the end of the key: the report reads only the saved answers)."""
    codes = [r["code"] for r in read_csv(project / "key" / "key.csv")]
    codes += [f"X{k:04d}" for k in range(len(calls) - len(codes))]
    t0 = dt.datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    scores = {}
    for k, (code, c) in enumerate(zip(codes, calls)):
        s = {"saved": f"{t0 + dt.timedelta(minutes=minutes * k):%Y-%m-%d %H:%M:%S}", "confidence": "sure"}
        if c is None:
            s |= {"usable": "no", "unusable_reason": "out of focus or moving"}
        else:
            s |= {"usable": "yes", "n1_call": c[0], "n2_call": c[1]}
        scores[code] = s
    (project / "scoring").mkdir(exist_ok=True)
    (project / "scoring" / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    return codes


def test_totals_are_pooled_and_never_name_a_genotype(fresh_project):
    edit_config(fresh_project, lambda c: c.setdefault("reliability", {}).update(repeat_fraction=0.2))
    prepare(fresh_project, reassign=True, log=lambda *a: None)
    save_scores(fresh_project, [("normal", "dim"), ("normal", "normal"), None, ("missing", "normal")])
    rep = I.report(fresh_project)
    assert rep["scored"] == 4 and rep["usable"] == 3 and rep["unusable"] == 1
    assert rep["calls"] == {"normal": 4, "dim": 1, "missing": 1}
    assert rep["affected"] == 2 and rep["judged"] == 3 and rep["affected_pct"] == 67
    assert rep["unusable_reasons"] == {"out of focus or moving": 1}
    text = json.dumps(rep) + " ".join(I.lines(rep))
    genotypes = {r["genotype"] for r in read_csv(fresh_project / "key" / "key.csv")}
    assert not any(g in text for g in genotypes)
    assert "repeat_of" not in text and rep["coded"] == len(read_csv(fresh_project / "key" / "key.csv"))


def test_pace_ignores_breaks_and_estimates_the_time_left(fresh_project):
    save_scores(fresh_project, [("normal", "normal")] * 5, minutes=3)
    rep = I.report(fresh_project)
    assert rep["minutes_per_image"] == 3.0 and rep["minutes_left"] == 3 * rep["left"]
    scores = json.loads((fresh_project / "scoring" / "scores.json").read_text())
    last = max(scores, key=lambda c: scores[c]["saved"])
    scores[last]["saved"] = "2026-11-17 09:00:00"          # overnight: a break, not time on the image
    (fresh_project / "scoring" / "scores.json").write_text(json.dumps(scores))
    assert I.report(fresh_project)["minutes_per_image"] == 3.0


def test_drift_between_the_halves_is_pointed_out(fresh_project):
    save_scores(fresh_project, [("normal", "normal")] * 10 + [("dim", "normal")] * 10)
    d = I.report(fresh_project)["drift"]
    assert d == {"first": 0, "second": 100, "n_first": 10, "n_second": 10, "large": True}
    save_scores(fresh_project, [("normal", "normal")] * 12)
    assert I.report(fresh_project)["drift"] is None         # under 10 in a half: no comparison


def test_each_look_is_logged_and_counted_in_the_methods(fresh_project):
    save_scores(fresh_project, [("normal", "dim")] * 3)
    assert I.methods_sentence(fresh_project) == ""
    for _ in range(2):
        I.record(fresh_project, I.report(fresh_project))
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert log.count(I.LOG_TITLE) == 2 and "No result by genotype" in log
    assert I.looks(fresh_project) == 2
    assert "looked 2 times at interim totals" in methods.interim_sentence(fresh_project)
    assert C.load(fresh_project)      # config untouched
