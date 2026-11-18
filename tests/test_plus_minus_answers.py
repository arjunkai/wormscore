"""1.26.4: calls and answers that start with + - = or @ count at unblinding as they were chosen."""
import json

from wormscore import server
from wormscore.pipeline import prepare, read_csv
from wormscore.scorers import export_package, import_scores
from wormscore.unblind import unblind

quiet = lambda *a: None


def plus_minus(project):
    path = project / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["scoring"].update(calls=["+", "+/-", "-"], defect_calls=["+/-", "-"], neuron_questions=[])
    path.write_text(json.dumps(cfg), encoding="utf-8")
    prepare(project, log=quiet)


def score_all(project, folder=None):
    key = {r["code"]: r for r in read_csv(project / "key" / "key.csv")}
    p = server.Project(folder or project)
    for code in sorted(d.name for d in p.blind.iterdir()):
        p.save_score(code, {"usable": "yes", "count_this_image": "yes", "cells_seen": "2", "notes": "-typed",
                            "n1_call": "-" if key[code]["genotype"] == "mutant" else "+", "n2_call": "+",
                            "flag_answers": {}})


def test_calls_named_plus_and_minus_count_at_unblinding(fresh_project):
    """scores.csv stores "-" as "'-" (so a spreadsheet does not run it). Read back with the mark, it
    matched no call: every mutant scored "-" was reported as unaffected."""
    plus_minus(fresh_project)
    score_all(fresh_project)
    stored = read_csv(fresh_project / "scoring" / "scores.csv")[0]
    assert stored["n2_call"] == "'+" and stored["notes"] == "'-typed"      # the file itself is as before
    out = unblind(fresh_project, finished=True, log=quiet)
    pen = {r["genotype"]: r for r in read_csv(out / "penetrance.csv")}
    assert pen["mutant"]["animals_any_affected"] == "3" and pen["mutant"]["penetrance_pct"] == "100.0"
    assert pen["control"]["animals_any_affected"] == "0"
    merged = read_csv(out / "merged.csv")
    assert {r["score_n1_call"] for r in merged} == {"+", "-"}
    assert {r["score_notes"] for r in merged} == {"'-typed"}               # typed text keeps its mark
    assert {r["call"] for r in read_csv(out / "per_neuron.csv")} == {"+", "-"}


def test_a_second_scorer_package_with_such_calls_comes_back(fresh_project, tmp_path):
    """Import refused it: "an answer the scoring criteria do not allow"."""
    plus_minus(fresh_project)
    score_all(fresh_project)
    pkg = export_package(fresh_project, "Sam", tmp_path / "pkg", fraction=1.0, log=quiet)
    score_all(fresh_project, pkg)
    import_scores(fresh_project, pkg, log=quiet)
    out = unblind(fresh_project, finished=True, log=quiet)
    calls = next(r for r in read_csv(out / "between_scorers.csv") if r["measure"] == "neuron call")
    assert calls["pairs"] == "12" and calls["percent_agreement"] == "100.0"
