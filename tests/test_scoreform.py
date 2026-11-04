"""The score form builder (Project settings), the form lock and the unblinding checks."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from wormscore import config as C
from wormscore import editconfig as E
from wormscore import scoreform as S
from wormscore.pipeline import prepare, read_csv
from wormscore.scorers import form_hash
from wormscore.unblind import unblind
from test_analysis_plan import edit_config
from test_home import call, desk  # noqa: F401  (desk is a fixture)
from test_results_options import write_scores

quiet = lambda *a: None
STATIC = Path(__file__).resolve().parents[1] / "src" / "wormscore" / "static"


def body_with_form(project, form):
    cur = E.current(project)
    return {"project_name": cur["project_name"], "form": form,
            "sources": [dict(s, **{"from": i}) for i, s in enumerate(cur["sources"])]}


def score_all(project, extra=lambda r: {}):
    key = read_csv(project / "key" / "key.csv")
    write_scores(project, [{"code": r["code"], "usable": "yes", "count_this_image": "yes",
                            "n1_call": "missing" if r["genotype"] == "mutant" else "normal", "n2_call": "normal",
                            **extra(r)} for r in key])


# ------------------------------------------------------------- nothing changes for existing forms

def test_defaults_keep_their_hash_and_plan():
    cfg = C.merge(C.DEFAULTS, {})
    assert form_hash(cfg) == "253567883375e6f0"            # the 1.2.0 value: its packages still import
    assert C.analysis_plan(cfg) == {}
    assert S.unsupported(cfg) == ""


def test_saving_an_unchanged_form_leaves_config_json_as_it_was(fresh_project):
    path = fresh_project / "config.json"
    path.write_text(json.dumps(C.template("Demo"), indent=2) + "\n", encoding="utf-8")
    before = path.read_bytes()
    cur = E.current(fresh_project)
    body = body_with_form(fresh_project, cur["form"]["model"])
    body["sources"] = [dict(s, **{"from": i}) for i, s in enumerate(cur["sources"])]
    assert S.apply(json.loads(before), cur["form"]["model"]) == json.loads(before)
    r = E.save(fresh_project, body)
    assert not r["changed"] and path.read_bytes() == before


def test_fate_preset_is_the_defaults():
    raw = C.template("x")
    assert S.apply(raw, S.preset_model("fate")) == raw


@pytest.mark.parametrize("name", list(S.PRESETS))
def test_each_preset_passes_the_config_check_and_round_trips(name):
    raw = S.apply(C.template("x"), S.preset_model(name))
    cfg = S._cfg_from_raw(raw)
    C.check(cfg)
    assert S.clean(S.to_model_cfg(cfg)) == S.clean(S.preset_model(name))


def test_hand_written_outcomes_round_trip_without_change():
    raw = C.template("x")
    raw["scoring"]["neuron_questions"] = [{"id": "dendrite", "label": "dendrite to nose?", "options": ["yes", "no", "can't tell"]},
                                          {"id": "axon", "label": "axon in ring?", "options": ["yes", "no", "can't tell"]}]
    raw["unblinding"] = {"question_outcomes": [{"name": "process defect", "questions": ["dendrite", "axon"],
                                                "defect_answers": ["no"], "unclear_answers": ["can't tell"]}]}
    cfg = S._cfg_from_raw(raw)
    m = S.to_model_cfg(cfg)
    assert [q["skip_when"] for q in m["questions"]] == [["missing"], ["missing"]]     # the default not_visible_calls
    new = S.apply(raw, m)
    assert new["unblinding"]["question_outcomes"] == raw["unblinding"]["question_outcomes"]   # kept byte for byte
    assert S.form_record(S._cfg_from_raw(new))["question_outcomes"] == S.form_record(cfg)["question_outcomes"]


def test_a_form_the_builder_cannot_show_is_read_only(fresh_project):
    edit_config(fresh_project, lambda c: c.setdefault("unblinding", {}).update(question_outcomes=[
        {"name": "a", "questions": ["dendrite"], "defect_answers": ["no"]},
        {"name": "b", "questions": ["dendrite"], "defect_answers": ["can't tell"]}]))
    form = E.current(fresh_project)["form"]
    assert "two outcomes" in form["readonly"]
    m = dict(form["model"], neurons=["A", "B"])
    with pytest.raises(ValueError, match="cannot show"):
        E.save(fresh_project, body_with_form(fresh_project, m))


# ------------------------------------------------------------- building

def test_builder_writes_questions_outcomes_and_logs_the_form(fresh_project):
    m = S.preset_model("axon")
    r = E.save(fresh_project, body_with_form(fresh_project, m))
    assert r["form_changed"]
    raw = json.loads((fresh_project / "config.json").read_text())
    assert raw["unblinding"]["primary_outcome"] == "axon defect"
    (oc,) = raw["unblinding"]["question_outcomes"]
    assert oc["questions"] == ["stops_early", "wrong_path", "extra_branch"] and oc["not_visible_calls"] == ["not seen"]
    assert all(q["required"] and q["skip_when_calls"] == ["not seen"] for q in raw["scoring"]["neuron_questions"])
    assert raw["sources"]                                                    # the rest is kept
    assert list((fresh_project / "config_backups").glob("config-*.json"))     # old file backed up
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert log.count(S.FORM_LOG) >= 2 and log.rstrip().endswith(S.record_text(C.load(fresh_project)))


@pytest.mark.parametrize("change, message", [
    (lambda m: m.update(calls=[{"text": "seen", "defect": False}, {"text": "not seen", "defect": False}]), "defect"),
    (lambda m: m.update(calls=[{"text": "only", "defect": True}]), "at least 2"),
    (lambda m: m.update(neurons=["A", "a"]), "twice"),
    (lambda m: m["questions"].append(dict(m["questions"][0], id=None, defect=["no"])), "separate outcomes"),
    (lambda m: m["questions"][0].update(skip_when=[]), "same calls"),
    (lambda m: m["questions"][0].update(outcome="calls"), "cannot be called"),
    (lambda m: m.update(primary="nothing"), "primary outcome"),
    (lambda m: m["questions"][0].update(type="choice", options=["a"]), "at least 2"),
    (lambda m: m["questions"][0].update(defect=[]), "tick the answers"),
])
def test_builder_refuses_forms_that_cannot_be_analysed(change, message):
    m = S.preset_model("axon")
    change(m)
    with pytest.raises(ValueError, match=message):
        S.clean(m)


def test_config_check_refuses_skips_the_outcome_does_not_leave_out():
    raw = S.apply(C.template("x"), S.preset_model("axon"))
    raw["unblinding"]["question_outcomes"][0]["not_visible_calls"] = []
    with pytest.raises(SystemExit, match="not_visible_calls"):
        C.check(S._cfg_from_raw(raw))


def test_new_question_ids_are_unique_and_kept(fresh_project):
    m = E.current(fresh_project)["form"]["model"]
    m["questions"] += [{"id": None, "label": "Dendrite visible?", "type": "yesno", "options": [], "cant_tell": "",
                        "defect": ["no"], "required": False, "skip_when": [], "outcome": "x"}]
    clean = S.clean(m)
    assert [q["id"] for q in clean["questions"]] == ["dendrite", "dendrite_visible"]


# ------------------------------------------------------------- the lock

def test_the_form_locks_at_the_first_score(fresh_project):
    assert E.current(fresh_project)["form"]["locked"] == ""
    score_all(fresh_project)
    assert "Scoring has started" in E.current(fresh_project)["form"]["locked"]
    m = dict(E.current(fresh_project)["form"]["model"], neurons=["A", "B"])
    with pytest.raises(ValueError, match="locked"):
        E.save(fresh_project, body_with_form(fresh_project, m))
    # an unchanged form sent with other settings is fine
    assert E.save(fresh_project, body_with_form(fresh_project, E.current(fresh_project)["form"]["model"]))["ok"]


def test_a_second_scorer_package_locks_the_form(fresh_project):
    (fresh_project / "scorers" / "B").mkdir(parents=True)
    assert "package" in S.locked(fresh_project)


def test_the_scoring_page_records_the_form_at_the_first_score(fresh_project):
    from wormscore.server import Project
    proj = Project(fresh_project)
    code = read_csv(fresh_project / "key" / "key.csv")[0]["code"]
    before = (fresh_project / "log.md").read_text(encoding="utf-8").count(S.FORM_LOG)
    proj.save_score(code, {"usable": "yes", "n1_call": "normal", "n2_call": "normal"})
    proj.save_score(code, {"usable": "yes", "n1_call": "dim", "n2_call": "normal"})
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert log.count(S.FORM_LOG) == before + 1 and "first score: score form locked" in log


# ------------------------------------------------------------- unblinding

def test_a_form_changed_after_scoring_stops_every_test(fresh_project):
    edit_config(fresh_project, lambda c: c.setdefault("unblinding", {}).update(
        question_outcomes=[{"name": "dendrite defect", "questions": ["dendrite"], "defect_answers": ["no"]}],
        reference_genotype="control"))
    prepare(fresh_project, log=quiet)
    score_all(fresh_project, lambda r: {"n1_dendrite": "no", "n2_dendrite": "yes"})
    out = unblind(fresh_project, finished=True, log=quiet)
    t = read_csv(out / "fisher_tests.csv")[0]
    assert "p_value" in t or any(k.startswith("p_") for k in t)
    assert "form_recorded_before_scoring" not in t

    edit_config(fresh_project, lambda c: c["scoring"].update(defect_calls=["missing"]))    # by hand, after scoring
    out = unblind(fresh_project, finished=True, log=quiet)
    for name in ("fisher_tests.csv", "question_outcome_tests.csv"):
        assert "score form" in read_csv(out / name)[0]["note"]
    assert (out / "penetrance.csv").exists() and "note" not in read_csv(out / "penetrance.csv")[0]   # descriptive kept
    assert "changed after scoring began" in (out / "summary.txt").read_text(encoding="utf-8")
    assert "no statistical test was run" in (out / "methods_text.md").read_text(encoding="utf-8")


def test_a_wording_fix_does_not_stop_the_tests(fresh_project):
    score_all(fresh_project)
    edit_config(fresh_project, lambda c: c["scoring"].update(rules=["Reworded rule."]))
    out = unblind(fresh_project, finished=True, log=quiet, anyway=True)
    assert "note" not in read_csv(out / "fisher_tests.csv")[0]


def test_old_projects_fall_back_to_the_settings_logged_at_build(fresh_project):
    log = fresh_project / "log.md"
    text = log.read_text(encoding="utf-8")
    log.write_text("\n".join(l for l in text.splitlines() if S.FORM_LOG not in l) + "\n", encoding="utf-8")
    score_all(fresh_project)
    out = unblind(fresh_project, finished=True, log=quiet, anyway=True)
    assert "form_recorded_before_scoring" not in read_csv(out / "fisher_tests.csv")[0]      # the logged settings match

    log.write_text("", encoding="utf-8")                                                     # no record at all
    out = unblind(fresh_project, finished=True, log=quiet, anyway=True)
    t = read_csv(out / "fisher_tests.csv")[0]
    assert t["form_recorded_before_scoring"] == "NO" and "note" not in t                     # tests still run
    assert "WARNING: the score form was not recorded" in (out / "summary.txt").read_text(encoding="utf-8")


def test_primary_outcome_labels_the_tests(fresh_project):
    edit_config(fresh_project, lambda c: c.setdefault("unblinding", {}).update(
        question_outcomes=[{"name": "dendrite defect", "questions": ["dendrite"], "defect_answers": ["no"]}],
        primary_outcome="dendrite defect"))
    prepare(fresh_project, log=quiet)
    assert '"primary_outcome": "dendrite defect"' in (fresh_project / "log.md").read_text(encoding="utf-8")
    score_all(fresh_project, lambda r: {"n1_dendrite": "no", "n2_dendrite": "yes"})
    out = unblind(fresh_project, finished=True, log=quiet)
    assert {t["outcome_role"] for t in read_csv(out / "fisher_tests.csv")} == {"secondary"}
    assert {t["outcome_role"] for t in read_csv(out / "question_outcome_tests.csv")} == {"primary"}
    assert 'The primary outcome, named before scoring' in (out / "methods_text.md").read_text(encoding="utf-8")


# ------------------------------------------------------------- the pages

def test_home_page_loads_previews_and_saves_the_form(desk):
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    st, cur = call(port, "POST", "/api/project-settings", {"action": "load"})
    assert st == 200 and cur["form"]["model"]["calls"][0]["text"] == "normal"
    st, pre = call(port, "POST", "/api/project-settings", {"action": "preset", "preset": "blank"})
    assert st == 200 and pre["model"]["neurons"] == ["Neuron 1"]
    body = {"action": "save", "project_name": cur["project_name"], "form": pre["model"],
            "sources": [dict(s, **{"from": i}) for i, s in enumerate(cur["sources"])]}
    st, r = call(port, "POST", "/api/project-settings", body)
    assert st == 200 and r["form_changed"]
    assert C.load(project)["scoring"]["neurons"] == ["Neuron 1"]
    assert call(port, "GET", "/static/form.js")[0] == 200


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_required_questions_skip_when_their_neuron_is_not_seen():
    sc = {"neurons": ["A", "B"], "neuron_questions": [
        {"id": "q", "required": True, "skip_when_calls": ["missing"]}, {"id": "r", "required": False}]}
    js = (STATIC / "form.js").read_text(encoding="utf-8") + f"""
const sc = {json.dumps(sc)};
console.log(JSON.stringify([
  wsForm.missingRequired({{n1_call: "normal", n2_call: "missing"}}, sc),
  wsForm.missingRequired({{n1_call: "normal", n1_q: "yes", n2_call: "missing"}}, sc),
  wsForm.missingRequired({{n1_call: "", n2_call: "dim"}}, sc)]));"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [[1], [], [1, 2]]


# ------------------------------------------------------------- the Score form step (1.5.0)

def rename_neurons(project):
    """A form change made in config.json by hand: not recorded in log.md."""
    edit_config(project, lambda c: c.setdefault("scoring", {}).update(neurons=["Left", "Right"]))


def test_build_records_the_form_and_a_hand_edit_is_not_recorded(fresh_project):
    from wormscore.home import project_status
    st = project_status(fresh_project)["form"]       # the demo project is built
    assert st["recorded"] is True and st["calls"] == 3 and st["locked"] == ""
    rename_neurons(fresh_project)
    assert project_status(fresh_project)["form"]["recorded"] is False


def test_checking_the_form_unchanged_records_it_once(fresh_project):
    from wormscore.home import project_status
    rename_neurons(fresh_project)
    m = E.current(fresh_project)["form"]["model"]
    # saved from another section (folders): the form was not looked at, so it is not "checked"
    assert not E.save(fresh_project, body_with_form(fresh_project, m)).get("form_changed")
    assert project_status(fresh_project)["form"]["recorded"] is False
    # opened from the Score form step and saved unchanged: recorded, config.json untouched
    before = (fresh_project / "config.json").read_bytes()
    r = E.save(fresh_project, dict(body_with_form(fresh_project, m), form_checked=True))
    assert r["form_changed"] and not r["changed"]
    assert (fresh_project / "config.json").read_bytes() == before
    assert "score form checked in Project settings" in (fresh_project / "log.md").read_text(encoding="utf-8")
    assert project_status(fresh_project)["form"]["recorded"] is True
    # checking again adds nothing
    E.save(fresh_project, dict(body_with_form(fresh_project, m), form_checked=True))
    assert (fresh_project / "log.md").read_text(encoding="utf-8").count("score form checked") == 1


# ------------------------------------------------------------- changing a locked form anyway (1.5.1)

def save_after_scoring(project, body, monkeypatch):
    """Save with log.md entries stamped after the test scores (saved 2099-01-01 12:00)."""
    from wormscore import pipeline
    monkeypatch.setattr(pipeline, "now", lambda: "2100-01-01 00:00:00")
    return E.save(project, body)


def test_a_locked_form_changes_only_with_a_reason_and_is_logged(fresh_project, monkeypatch):
    from wormscore.home import project_status
    score_all(fresh_project)
    m = E.current(fresh_project)["form"]["model"]
    assert E.current(fresh_project)["form"]["can_unlock"] is True
    changed = dict(m, neurons=["Left", "Right"])
    with pytest.raises(ValueError, match="locked"):
        E.save(fresh_project, body_with_form(fresh_project, changed))
    r = save_after_scoring(fresh_project, dict(body_with_form(fresh_project, changed), unlock_reason="  neuron names were wrong  "), monkeypatch)
    assert r["after_lock"] and r["form_changed"]
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert "score form changed after scoring began" in log and "Reason given: neuron names were wrong" in log
    assert "runs no planned test" in log
    st = project_status(fresh_project)["form"]
    assert st["locked"] and st["changed_after_lock"] is True
    out = unblind(fresh_project, finished=True, log=quiet)
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "changed after scoring began" in summary
    assert "beyond wording. Reason: neuron names were wrong" in summary          # disclosed with its reason
    assert "Reason recorded: neuron names were wrong" in (out / "methods_text.md").read_text(encoding="utf-8")


def test_a_wording_change_after_the_lock_keeps_the_tests(fresh_project, monkeypatch):
    from wormscore.home import project_status
    score_all(fresh_project)
    m = E.current(fresh_project)["form"]["model"]
    reworded = dict(m, rules=["Reworded rule."]) if "rules" in m else None
    if reworded is None:
        pytest.skip("the builder model has no rules text")
    r = save_after_scoring(fresh_project, dict(body_with_form(fresh_project, reworded), unlock_reason="typo"), monkeypatch)
    assert r["after_lock"]
    assert "Only wording changed" in (fresh_project / "log.md").read_text(encoding="utf-8")
    assert project_status(fresh_project)["form"]["changed_after_lock"] is False


def test_no_unlock_while_a_second_scorer_package_exists(fresh_project):
    (fresh_project / "scorers" / "Sam").mkdir(parents=True)
    m = E.current(fresh_project)["form"]["model"]
    assert E.current(fresh_project)["form"]["can_unlock"] is False
    with pytest.raises(ValueError, match="package"):
        E.save(fresh_project, dict(body_with_form(fresh_project, dict(m, neurons=["L", "R"])), unlock_reason="x"))
