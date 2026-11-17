"""Analysis settings in Project settings (1.15.0): the reference genotype and the share of hidden
repeats, recorded in log.md and locked at the first score like the scoring criteria."""
import json

import pytest

from wormscore import analysis as A
from wormscore import config as C
from wormscore import editconfig as E
from wormscore import scoreform as S
from wormscore.pipeline import PLAN_LOG, prepare, read_csv
from wormscore.unblind import unblind

from test_results_options import edit_config, write_scores


def body_of(project, ref=None, frac=None, **extra):
    cur = E.current(project)
    an = cur["analysis"]
    return {"project_name": cur["project_name"], "sources": [dict(s, **{"from": i}) for i, s in enumerate(cur["sources"])],
            "analysis": {"reference_genotype": an["reference_genotype"] if ref is None else ref,
                         "repeat_fraction": an["repeat_fraction"] if frac is None else frac, **extra}}


def log(project):
    return (project / "log.md").read_text(encoding="utf-8")


def score_all(project):
    rows = [{"code": r["code"], "usable": "yes", "count_this_image": "yes", "n1_call": "normal", "n2_call": "normal"}
            for r in read_csv(project / "key" / "key.csv")]
    write_scores(project, rows)


def test_the_card_lists_the_genotypes_and_current_values(fresh_project):
    an = E.current(fresh_project)["analysis"]
    assert an["genotypes"] == ["control", "mutant"] and an["reference_genotype"] == ""
    assert an["repeat_fraction"] == 0 and an["repeats_in_key"] == 0 and an["locked"] == ""
    assert 0.1 in an["repeat_choices"]


def test_saving_unchanged_settings_leaves_config_and_log_alone(fresh_project):
    keys = lambda: {k: v for k, v in json.loads((fresh_project / "config.json").read_text()).items()
                    if k in ("unblinding", "reliability")}
    before, before_log = keys(), log(fresh_project)
    r = E.save(fresh_project, body_of(fresh_project))
    assert r["analysis_changed"] == [] and keys() == before
    assert log(fresh_project) == before_log


def test_a_new_reference_genotype_is_saved_and_recorded(fresh_project):
    r = E.save(fresh_project, body_of(fresh_project, ref="control"))
    assert r["analysis_changed"] == ["reference"]
    assert json.loads((fresh_project / "config.json").read_text())["unblinding"]["reference_genotype"] == "control"
    assert A.REF_LOG + '"control"' in log(fresh_project).splitlines()[-1]
    assert "analysis settings saved in Project settings" in log(fresh_project)


def test_build_records_the_reference_genotype(fresh_project):
    assert A.REF_LOG + '""' in log(fresh_project)      # the demo was built with every pair compared
    edit_config(fresh_project, lambda c: c.setdefault("unblinding", {}).update(reference_genotype="control"))
    prepare(fresh_project, log=lambda *a: None)
    assert log(fresh_project).rstrip().count(A.REF_LOG + '"control"') == 1


def test_first_score_records_the_settings(fresh_project):
    edit_config(fresh_project, lambda c: c.setdefault("unblinding", {}).update(
        reference_genotype="control", primary_outcome="calls"))
    S.record_first_score(fresh_project, C.load(fresh_project))
    tail = log(fresh_project).split("first score: score form locked")[-1]
    assert A.REF_LOG + '"control"' in tail and PLAN_LOG in tail


def test_settings_lock_at_the_first_score(fresh_project):
    (fresh_project / "scoring").mkdir(exist_ok=True)
    (fresh_project / "scoring" / "scores.json").write_text("{}")
    assert E.current(fresh_project)["analysis"]["locked"]
    with pytest.raises(ValueError, match="analysis settings are locked"):
        E.save(fresh_project, body_of(fresh_project, ref="control"))
    E.save(fresh_project, body_of(fresh_project))          # unchanged settings still save


def test_a_new_repeat_share_needs_a_rebuild_once_built(fresh_project):
    with pytest.raises(ValueError, match="rebuild with new codes"):
        E.save(fresh_project, body_of(fresh_project, frac=0.1))
    assert C.repeat_fraction(C.load(fresh_project)) == 0
    E.save(fresh_project, body_of(fresh_project, frac=0.1, rebuild=True))
    prepare(fresh_project, reassign=True, log=lambda *a: None)
    assert A.repeats_in_key(fresh_project) >= 2           # at least one for each genotype
    with pytest.raises(ValueError, match="between 0 and 50%"):
        E.save(fresh_project, body_of(fresh_project, frac=0.9, rebuild=True))


def test_a_reference_changed_by_hand_after_scoring_stops_the_tests(fresh_project):
    score_all(fresh_project)
    out = unblind(fresh_project, finished=True, anyway=True, log=lambda *a: None)
    assert "NOT RUN" not in (out / "fisher_tests.csv").read_text(encoding="utf-8")
    edit_config(fresh_project, lambda c: c.setdefault("unblinding", {}).update(reference_genotype="control"))
    out = unblind(fresh_project, finished=True, anyway=True, log=lambda *a: None)
    assert read_csv(out / "fisher_tests.csv")[0]["note"].startswith("NOT RUN")


def test_projects_without_a_recorded_reference_are_checked_as_before(fresh_project):
    # a project built before 1.15.0 has no reference line: changing it is not caught, as before
    text = "\n".join(line for line in log(fresh_project).splitlines() if A.REF_LOG not in line)
    (fresh_project / "log.md").write_text(text + "\n", encoding="utf-8")
    score_all(fresh_project)
    edit_config(fresh_project, lambda c: c.setdefault("unblinding", {}).update(reference_genotype="control"))
    out = unblind(fresh_project, finished=True, anyway=True, log=lambda *a: None)
    assert "NOT RUN" not in (out / "fisher_tests.csv").read_text(encoding="utf-8")
