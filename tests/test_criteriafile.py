"""Scoring criteria as one file, shared between projects and labs (1.11.0)."""
import json

import pytest

from wormscore import config as C
from wormscore import criteriafile as F
from wormscore import editconfig as E
from wormscore import methods
from wormscore import scoreform as S
from test_definitions import PNG, with_definitions
from test_home import call, desk  # noqa: F401  (desk is a fixture)
from test_scoreform import body_with_form


def other_project(tmp_path):
    """A second, new project to import into."""
    p = tmp_path / "other"
    p.mkdir()
    (p / "config.json").write_text(json.dumps(C.template("Other lab"), indent=2), encoding="utf-8")
    return p


def test_criteria_travel_to_another_project_with_their_images(fresh_project, tmp_path):
    m, path = with_definitions(fresh_project)
    out = F.write_export(fresh_project, tmp_path, m, "Dim calls, lab A", "1.2", "Doe et al. 2026")
    assert out.name == "dim-calls-lab-a-v1.2" + F.SUFFIX
    assert F.write_export(fresh_project, tmp_path, m, "Dim calls, lab A", "1.2").name.endswith("-2" + F.SUFFIX)  # never overwrites
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["format"] == F.FORMAT and doc["citation"] == "Doe et al. 2026" and set(doc["images"]) == {path}

    other = other_project(tmp_path)
    got = F.load(other, out.read_text(encoding="utf-8"))
    assert got["notes"] == [] and (other / path).read_bytes() == PNG
    assert got["source"] == {"name": "Dim calls, lab A", "version": "1.2", "citation": "Doe et al. 2026",
                             "sha256": doc["sha256"]}
    # the same criteria give the same checksum in both projects
    assert F.checksum(got["model"], other) == F.checksum(m, fresh_project) == doc["sha256"]

    r = E.save(other, body_with_form(other, got["model"]))
    assert r["form_changed"]
    cfg = C.load(other)
    assert cfg["scoring"]["criteria_source"]["name"] == "Dim calls, lab A"
    assert F.cfg_checksum(cfg, other) == doc["sha256"]                 # unchanged by saving
    log = (other / "log.md").read_text(encoding="utf-8")
    assert F.CHECKSUM_LOG + doc["sha256"] + ' (imported from "Dim calls, lab A" version 1.2, unchanged since)' in log
    assert "used as published" in methods.criteria_sentence(cfg, other)
    assert doc["sha256"][:16] in methods.criteria_sentence(cfg, other)


def test_changing_imported_criteria_is_reported_as_adapted(fresh_project, tmp_path):
    m, _ = with_definitions(fresh_project)
    text = F.export(fresh_project, m, "Lab A")[1]
    other = other_project(tmp_path)
    got = F.load(other, text)
    got["model"]["calls"][1]["def"] = "Our own wording."         # wording is enough to change the checksum
    E.save(other, body_with_form(other, got["model"]))
    cfg = C.load(other)
    sentence = methods.criteria_sentence(cfg, other)
    assert "adapted from \"Lab A\"" in sentence and "[describe what was changed]" in sentence
    assert "changed since)" in (other / "log.md").read_text(encoding="utf-8")
    # a source is wording: the recorded form is the one the file had
    assert S.form_record(cfg) == S.form_record(S._cfg_from_raw(S.apply(C.template("x"), F.load(other, text)["model"])))


def test_the_checksum_is_of_the_criteria_not_the_file(fresh_project):
    m = E.current(fresh_project)["form"]["model"]
    a = json.loads(F.export(fresh_project, m, "One name", "1")[1])
    b = json.loads(F.export(fresh_project, m, "Another name", "2", "cite")[1])
    assert a["sha256"] == b["sha256"]
    m["rules"] = m["rules"] + ["One more rule."]
    assert F.checksum(m, fresh_project) != a["sha256"]
    # projects whose criteria were never imported still get a checksum in log.md and Methods
    cfg = C.load(fresh_project)
    assert F.log_lines(cfg, fresh_project) == [F.CHECKSUM_LOG + F.cfg_checksum(cfg, fresh_project)]
    assert methods.criteria_sentence(cfg, fresh_project).startswith("The scoring criteria are identified by their checksum")


def test_a_changed_or_wrong_file_is_caught(fresh_project, tmp_path):
    m, path = with_definitions(fresh_project)
    doc = json.loads(F.export(fresh_project, m, "Lab A")[1])
    other = other_project(tmp_path)
    tampered = dict(doc, criteria=dict(doc["criteria"], rules=["Changed by hand."]))
    got = F.load(other, json.dumps(tampered))
    assert "changed after it was exported" in got["notes"][0]
    assert got["source"]["sha256"] == F.checksum(got["model"], other) != doc["sha256"]
    for bad, msg in [("not json", "not JSON"), (json.dumps({"format": "x"}), "not a wormscore criteria file"),
                     (json.dumps(dict(doc, format_version=99)), "newer wormscore"),
                     (json.dumps(dict(doc, criteria=dict(doc["criteria"], calls=[]))), "could not be used")]:
        with pytest.raises(ValueError, match=msg):
            F.load(other, bad)
    empty = tmp_path / "empty"                 # a project that does not have the image yet
    empty.mkdir()
    with pytest.raises(ValueError, match="missing 1 reference image"):
        F.load(empty, json.dumps(dict(doc, images={})))
    with pytest.raises(ValueError, match="name"):
        F.export(fresh_project, m, "  ")


def test_the_home_page_exports_and_imports_criteria(desk, tmp_path):
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    m = call(port, "POST", "/api/project-settings", {"action": "load"})[1]["form"]["model"]
    desk["picks"].append(str(tmp_path))
    st, r = call(port, "POST", "/api/project-settings", {"action": "export-criteria", "form": m, "name": "Ours", "version": "1"})
    assert st == 200 and r["path"].endswith("ours-v1" + F.SUFFIX)
    text = open(r["path"], encoding="utf-8").read()
    st, r = call(port, "POST", "/api/project-settings", {"action": "import-criteria", "text": text})
    assert st == 200 and r["source"]["name"] == "Ours" and r["model"]["source"]["name"] == "Ours"
    st, r = call(port, "POST", "/api/project-settings", {"action": "import-criteria", "text": "{}"})
    assert st == 400 and "not a wormscore criteria file" in r["error"]
    st, r = call(port, "POST", "/api/project-settings", {"action": "export-criteria", "form": m, "name": ""})
    assert st == 400 and "name" in r["error"]
