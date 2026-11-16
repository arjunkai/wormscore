"""Practice sets (1.13.0): images and an answer key from a finished project, scored by a new scorer
and recorded in their own project."""
import json
import shutil

import pytest

from wormscore import methods
from wormscore import practice as PR
from wormscore.files import read_csv
from wormscore.home import project_status
from wormscore.unblind import unblind
from test_results_options import write_scores


def finished(project):
    """Score every image (mutants' neuron 1 missing, all sure) and unblind."""
    key = read_csv(project / "key" / "key.csv")
    write_scores(project, [{"code": r["code"], "usable": "yes", "count_this_image": "yes", "cells_seen": "2",
                            "n1_call": "missing" if r["genotype"] == "mutant" else "normal", "n2_call": "normal",
                            "confidence": "sure"} for r in key])
    unblind(project, finished=True, log=lambda *a: None)
    return key


def score_set(folder, change=0, upto=None):
    """Score a practice set as the key does, with `change` neuron-1 calls changed."""
    key = json.loads((folder / PR.KEY_FILE).read_text(encoding="utf-8"))
    rows = []
    for i, (code, ans) in enumerate(sorted(key["answers"].items())[:upto]):
        r = {"code": code, "count_this_image": "yes", "confidence": "sure", **ans}
        if i < change:
            r["n1_call"] = "dim"
        rows.append(r)
    write_scores(folder, rows)
    return key


def test_a_practice_set_holds_coded_images_and_answers_only(fresh_project, tmp_path):
    with pytest.raises(SystemExit, match="unblind it first"):
        PR.make_practice_set(fresh_project, tmp_path / "early", n=6)
    key = finished(fresh_project)
    for bad in (4, 101, True):
        with pytest.raises(SystemExit, match="5 to 100"):
            PR.make_practice_set(fresh_project, tmp_path / "x", n=bad)
    with pytest.raises(SystemExit, match="outside the project"):
        PR.make_practice_set(fresh_project, fresh_project / "inside", n=6)
    folder = PR.make_practice_set(fresh_project, tmp_path / "set", n=6, log=lambda *a: None)
    codes = sorted(d.name for d in (folder / "blind").iterdir())
    assert len(codes) == 6
    genotype = {r["code"]: r["genotype"] for r in key}
    assert {genotype[c] for c in codes} == {"control", "mutant"}        # taken in turn from each genotype
    # nothing in the set names a genotype, a raw file or the key
    files = {r[k] for r in key for k in ("file", "file_sha256", "original_path", "local_path") if r.get(k)}
    assert files
    assert not (folder / "key").exists()
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".txt", ".csv"):
            text = f.read_text(encoding="utf-8")
            assert "mutant" not in text and "control" not in text, f.name
            assert not any(x and x in text for x in files), f.name
    cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    assert "sources" not in cfg and cfg["scorer_package"]["practice"] is True
    k = json.loads((folder / PR.KEY_FILE).read_text(encoding="utf-8"))
    assert sorted(k["answers"]) == codes
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert "practice set made" in log and k["sha256"] in log


def test_only_images_marked_sure_by_default(fresh_project, tmp_path):
    key = finished(fresh_project)
    rows = read_csv(fresh_project / "scoring" / "scores.csv")
    for r in rows[:-4]:
        r["confidence"] = "unsure"
    write_scores(fresh_project, rows)
    with pytest.raises(SystemExit, match="marked sure"):
        PR.make_practice_set(fresh_project, tmp_path / "set", n=6)
    assert len(list((PR.make_practice_set(fresh_project, tmp_path / "any", n=6, sure_only=False, log=lambda *a: None)
                     / "blind").iterdir())) == 6
    assert len(key) >= 6


def test_the_key_is_shown_only_once_every_image_is_scored(fresh_project, tmp_path):
    finished(fresh_project)
    folder = PR.make_practice_set(fresh_project, tmp_path / "set", n=6, log=lambda *a: None)
    st = project_status(folder)
    assert st["package"]["practice"] and st["practice_set"]["done"] is False
    score_set(folder, change=1, upto=3)
    st = project_status(folder)["practice_set"]
    assert st["scored"] == 3 and "measures" not in st and "differences" not in st
    score_set(folder, change=1)
    r = PR.result(folder)
    calls = next(m for m in r["measures"] if m["measure"] == "neuron call")
    assert r["done"] and calls["pairs"] == 12 and calls["same_answer"] == 11
    (d,) = r["differences"]
    assert d["answer"].endswith(": call") and d["yours"] == "dim" and d["key"] in ("normal", "missing")


def test_a_changed_key_or_image_is_refused(fresh_project, tmp_path):
    finished(fresh_project)
    folder = PR.make_practice_set(fresh_project, tmp_path / "set", n=6, log=lambda *a: None)
    score_set(folder)
    k = json.loads((folder / PR.KEY_FILE).read_text(encoding="utf-8"))
    code = sorted(k["answers"])[0]
    k["answers"][code]["n1_call"] = "dim"
    (folder / PR.KEY_FILE).write_text(json.dumps(k), encoding="utf-8")
    with pytest.raises(SystemExit, match="answer key was changed"):
        PR.result(folder)
    other = PR.make_practice_set(fresh_project, tmp_path / "set2", n=6, log=lambda *a: None)
    score_set(other)
    png = next((other / "blind").rglob("*.png"))
    png.write_bytes(png.read_bytes() + b"x")
    with pytest.raises(SystemExit, match="were changed"):
        PR.result(other)


def test_a_practice_result_is_recorded_and_named_in_the_methods(demo_project, fresh_project, tmp_path):
    finished(fresh_project)
    folder = PR.make_practice_set(fresh_project, tmp_path / "set", n=6, log=lambda *a: None)
    mine = tmp_path / "mine"                      # the new scorer's own project, before scoring
    shutil.copytree(demo_project, mine)
    assert methods.practice_sentence(mine) == ""  # nothing recorded: the Methods read as before
    score_set(folder, upto=2)
    with pytest.raises(SystemExit, match="not finished"):
        PR.add_result(mine, folder)
    key = score_set(folder, change=1)
    rec = PR.add_result(mine, folder, log=lambda *a: None)
    assert rec["before_scoring"] and rec["same_criteria"] and rec["differences"] == 1
    assert json.loads((mine / "practice" / f"{rec['id']}.json").read_text(encoding="utf-8"))["key_sha256"] == key["sha256"]
    log = (mine / "log.md").read_text(encoding="utf-8")
    assert "practice result added" in log and key["sha256"] in log and "not typed in" in log
    sentence = methods.practice_sentence(mine)
    assert sentence.startswith("Before scoring, the scorer practised on a set of 6 coded images")
    assert "11 of 12 neuron calls" in sentence and key["sha256"][:16] in sentence
    s = PR.summary(mine)["results"][0]
    assert s["total"] == 6 and s["measure"] == "neuron call"
    # the set's own project counts the sets it made
    assert PR.summary(fresh_project)["sets_made"] == 1


def test_a_practice_set_cannot_be_used_for_anything_but_scoring(fresh_project, tmp_path):
    finished(fresh_project)
    folder = PR.make_practice_set(fresh_project, tmp_path / "set", n=6, log=lambda *a: None)
    with pytest.raises(SystemExit, match="can only be scored"):
        PR.make_practice_set(folder, tmp_path / "again", n=5)
    with pytest.raises(SystemExit, match="can only be scored"):
        PR.add_result(folder, folder)


def test_the_methods_draft_at_unblinding_says_the_scorer_practised(demo_project, fresh_project, tmp_path):
    finished(fresh_project)
    folder = PR.make_practice_set(fresh_project, tmp_path / "set", n=5, log=lambda *a: None)
    score_set(folder)
    mine = tmp_path / "mine"
    shutil.copytree(demo_project, mine)
    PR.add_result(mine, folder, log=lambda *a: None)
    finished(mine)
    text = (mine / "outputs" / "unblinded" / "methods_text.md").read_text(encoding="utf-8")
    assert "practised on a set of 5 coded images" in text and "10 of 10 neuron calls (100.0%" in text
