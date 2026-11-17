"""1.21.2: steps that used to be refused are allowed with a warning, recorded in log.md and the Methods."""
import json
import random
import shutil

import pytest

from wormscore import analysis as A
from wormscore import editconfig as E
from wormscore import methods  # noqa: F401  (loaded for unblind)
from wormscore.pipeline import read_csv, spaced_order
from wormscore.unblind import unblind

from test_analysis_settings import body_of, log, score_all

QUIET = {"log": lambda *a: None}


def first_score_now(project):
    """A first score saved now, as the scoring page records it; settings changed in the next step must
    count as later, so their log.md sections are dated after it."""
    import datetime as dt
    (project / "scoring" / "score_history.csv").write_text(
        f"code,saved\nA01,{dt.datetime.now():%Y-%m-%d %H:%M:%S}\n", encoding="utf-8")


def later(project, title):
    text = (project / "log.md").read_text(encoding="utf-8")
    i = text.rindex(f" {title}\n")
    j = text.rindex("\n## ", 0, i) + 4
    (project / "log.md").write_text(text[:j] + "2099-01-01 00:00" + text[j + 16:], encoding="utf-8")


def test_the_analysis_plan_can_change_after_scoring_with_a_reason(fresh_project):
    score_all(fresh_project)
    first_score_now(fresh_project)
    with pytest.raises(ValueError, match="Change anyway"):
        E.save(fresh_project, body_of(fresh_project, ref="control"))
    with pytest.raises(ValueError, match="hidden repeats"):
        E.save(fresh_project, body_of(fresh_project, frac=0.1, unlock_reason="more repeats"))
    E.save(fresh_project, body_of(fresh_project, ref="control", unlock_reason="the reviewer asked for a control"))
    assert A.CHANGE_TITLE in log(fresh_project) and "Reason given: the reviewer asked" in log(fresh_project)
    later(fresh_project, A.CHANGE_TITLE)
    out = unblind(fresh_project, finished=True, anyway=True, **QUIET)
    tests = read_csv(out / "fisher_tests.csv")
    assert tests and all(t["set_before_scoring"] == A.CHANGED_STATUS for t in tests)
    assert "changed after scoring began" in (out / "summary.txt").read_text(encoding="utf-8")
    text = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "reported as exploratory" in text and "the reviewer asked for a control" in text


def test_a_later_hand_edit_still_runs_no_planned_test(fresh_project):
    score_all(fresh_project)
    first_score_now(fresh_project)
    E.save(fresh_project, body_of(fresh_project, ref="control", unlock_reason="checked with the PI"))
    later(fresh_project, A.CHANGE_TITLE)
    raw = json.loads((fresh_project / "config.json").read_text(encoding="utf-8"))
    raw["unblinding"]["reference_genotype"] = "mutant"
    (fresh_project / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    out = unblind(fresh_project, finished=True, anyway=True, **QUIET)
    assert read_csv(out / "fisher_tests.csv")[0]["note"].startswith("NOT RUN")


def test_a_moved_folder_and_a_genotype_fix_follow_the_coded_images(fresh_project, tmp_path):
    score_all(fresh_project)
    src = json.loads((fresh_project / "config.json").read_text(encoding="utf-8"))["sources"][0]
    moved = tmp_path / "new drive" / "images"
    shutil.copytree(src["path"], moved)

    def change(b):
        b["sources"][0]["path"] = str(moved)
        b["sources"][0]["genotypes"][0]["genotype"] = "wild type"     # the control rule was misnamed
        b["folders_reason"] = "images moved to a new drive; control renamed"
    with pytest.raises(ValueError, match="Change anyway"):
        E.save(fresh_project, {k: v for k, v in body_of(fresh_project).items()} | {"sources": [
            dict(s, path=str(moved)) for s in body_of(fresh_project)["sources"]]})
    body = body_of(fresh_project)
    change(body)
    E.save(fresh_project, body)
    key = read_csv(fresh_project / "key" / "key.csv")
    assert all(r["local_path"].startswith(str(moved)) for r in key)
    changes = read_csv(fresh_project / "key" / "changes_after_scoring.csv")
    assert changes and all(c["after"] == "wild type" for c in changes)
    assert list((fresh_project / "key" / "backups").glob("key-*.csv"))
    text = log(fresh_project)
    assert E.FOLDERS_TITLE in text and "moved from" in text and "wild type" not in text.split(E.FOLDERS_TITLE)[-1]


def test_an_exclusion_added_after_scoring_is_applied_at_unblinding(fresh_project):
    score_all(fresh_project)
    body = body_of(fresh_project)
    body["sources"][0]["exclude"] = [{"match": "*mutant_01*", "reason": "the animal was damaged"}]
    body["folders_reason"] = "one animal was found damaged on re-reading the notes"
    E.save(fresh_project, body)
    out = unblind(fresh_project, finished=True, anyway=True, **QUIET)
    assert "left out by exclusion rules changed after scoring began" in (out / "summary.txt").read_text(encoding="utf-8")
    assert "left out because the animal was damaged" in (out / "methods_text.md").read_text(encoding="utf-8")


def test_hidden_repeats_that_do_not_fit_are_placed_as_far_apart_as_they_can_be():
    a = {"code": "a"}
    rep = {"code": "r", "_orig": a}
    order, gap = spaced_order([a], [rep], random.Random(0))
    assert gap == 1 and len(order) == 2
