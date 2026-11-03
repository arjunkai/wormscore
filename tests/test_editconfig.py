"""Image folders and genotype rules set from the home page instead of in config.json."""
import json

import pytest

from wormscore import editconfig as E
from wormscore.pipeline import inventory, read_csv
from test_home import call, desk, state, wait_for_job  # noqa: F401  (desk is a fixture)


def body_of(project, change=None):
    cur = E.current(project)
    body = {"project_name": cur["project_name"], "sources": [dict(s, **{"from": i}) for i, s in enumerate(cur["sources"])]}
    if change:
        change(body)
    return body


def test_preview_sorts_scanned_images_with_unsaved_rules(fresh_project):
    p = E.preview(fresh_project, body_of(fresh_project))
    (src,) = p["sources"]
    assert p["scanned"] and src["images"] == 6 and src["genotypes"] == {"control": 3, "mutant": 3}

    def typo(b):
        b["sources"][0]["genotypes"][0]["match"] = "*ctrl*"
        b["sources"][0]["exclude"] = [{"match": "*mutant*", "reason": "test"}]
    (src,) = E.preview(fresh_project, body_of(fresh_project, typo))["sources"]
    assert src["genotypes"] == {"UNKNOWN": 3} and src["left_out"] == 3
    assert json.loads((fresh_project / "config.json").read_text())["sources"][0]["genotypes"][0]["match"] != "*ctrl*"


def test_save_changes_only_the_edited_fields(fresh_project):
    path = fresh_project / "config.json"
    raw = json.loads(path.read_text())
    raw["sources"][0]["note"] = "collected by others"
    raw["display"] = {"something": 1} if "display" not in raw else raw["display"]
    path.write_text(json.dumps(raw))

    def edit(b):
        b["project_name"] = "Renamed"
        b["sources"][0]["genotypes"].append({"match": "*x*", "genotype": "other"})
    E.save(fresh_project, body_of(fresh_project, edit))
    new = json.loads(path.read_text())
    assert new["project_name"] == "Renamed"
    assert new["sources"][0]["note"] == "collected by others"            # kept, not shown in the form
    assert new["sources"][0]["genotypes"][-1] == {"match": "*x*", "genotype": "other"}
    assert {k: v for k, v in new.items() if k not in ("project_name", "sources")} == \
        {k: v for k, v in raw.items() if k not in ("project_name", "sources")}


def test_saved_rules_are_used_by_the_next_scan(fresh_project):
    E.save(fresh_project, body_of(fresh_project, lambda b: b["sources"][0]["genotypes"][1].update(genotype="mutant A")))
    inventory(fresh_project, log=lambda *a: None)
    assert {r["genotype"] for r in read_csv(fresh_project / "outputs" / "inventory.csv")} == {"control", "mutant A"}


@pytest.mark.parametrize("change, message", [
    (lambda b: b.update(project_name=""), "project name is empty"),
    (lambda b: b["sources"][0].update(name=""), "the name is empty"),
    (lambda b: b["sources"].append(dict(b["sources"][0], **{"from": None})), "Two image folders"),
    (lambda b: b["sources"][0]["genotypes"][0].update(genotype=""), "genotype is empty"),
    (lambda b: b["sources"][0]["genotypes"][0].update(match="a\nb"), "line break"),
    (lambda b: b["sources"][0].update(exclude=[{"match": "*x*", "reason": ""}]), "reason is empty"),
    (lambda b: b.update(sources="x"), "No image folders"),
])
def test_bad_answers_are_refused_and_nothing_is_written(fresh_project, change, message):
    before = (fresh_project / "config.json").read_bytes()
    with pytest.raises(ValueError, match=message):
        E.save(fresh_project, body_of(fresh_project, change))
    assert (fresh_project / "config.json").read_bytes() == before


def test_rules_that_built_the_blind_set_lock_once_scoring_starts(fresh_project, tmp_path):
    (fresh_project / "scoring").mkdir(exist_ok=True)
    (fresh_project / "scoring" / "scores.json").write_text("{}")
    assert E.current(fresh_project)["locked"] == 1
    with pytest.raises(ValueError, match="cannot be changed or removed"):
        E.save(fresh_project, body_of(fresh_project, lambda b: b["sources"][0]["genotypes"][0].update(genotype="wt")))
    with pytest.raises(ValueError, match="cannot be changed or removed"):
        E.save(fresh_project, body_of(fresh_project, lambda b: b.update(sources=[])))
    new = tmp_path / "later"
    new.mkdir()
    E.save(fresh_project, body_of(fresh_project, lambda b: b["sources"].append(
        {"name": "later batch", "path": str(new), "genotypes": [{"match": "*", "genotype": "mutant"}], "from": None})))
    assert [s["name"] for s in json.loads((fresh_project / "config.json").read_text())["sources"]][-1] == "later batch"


def test_a_package_has_no_project_settings(fresh_project, tmp_path):
    from wormscore.scorers import export_package
    pkg = tmp_path / "pkg"
    export_package(fresh_project, "Sam", pkg, log=lambda *a: None)
    with pytest.raises(ValueError, match="second scorer's package"):
        E.current(pkg)


# ------------------------------------------------------------- home routes

def test_home_routes_for_settings_and_second_scorers(desk, tmp_path):
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    s, cur = call(port, "POST", "/api/project-settings", {"action": "load"})
    assert s == 200 and cur["sources"][0]["genotypes"]
    s, r = call(port, "POST", "/api/project-settings", {"action": "save", "project_name": "", "sources": []})
    assert s == 400 and "project name" in r["error"]
    assert state(port)["project"]["second_scorers"] == []

    assert call(port, "POST", "/api/second-scorer", {"action": "export", "scorer": "bad/name", "percent": 30})[0] == 400
    assert call(port, "POST", "/api/second-scorer", {"action": "export", "scorer": "Sam", "percent": 0})[0] == 400
    desk["picks"].append(str(tmp_path / "pkg"))
    assert call(port, "POST", "/api/second-scorer", {"action": "export", "scorer": "Sam", "percent": 50})[0] == 200
    assert wait_for_job(port)["outcome"] == "done"
    (pk,) = state(port)["project"]["second_scorers"]
    assert (pk["scorer"], pk["images"], pk["imported"], pk["folder_found"]) == ("Sam", 4, False, True)
    assert call(port, "POST", "/api/open", {"what": "package", "id": pk["id"]})[0] == 200
    assert desk["opened"][-1].name == "pkg"
    assert call(port, "POST", "/api/open", {"what": "package", "id": "../../x"})[0] == 400

    call(port, "POST", "/api/select", {"path": str(tmp_path / "pkg")})
    p = state(port)["project"]
    assert p["package"] == {"scorer": "Sam"} and p["coded"] == 4 and p["second_scorers"] == []
    assert call(port, "POST", "/api/project-settings", {"action": "load"})[0] == 400
    assert call(port, "POST", "/api/second-scorer", {"action": "import"})[0] == 400
    text = json.dumps(state(port))
    for leak in ("control", "mutant", "genotype"):
        assert leak not in text


def test_new_project_opens_settings_not_a_text_editor(desk, tmp_path):
    new = tmp_path / "brand_new"
    new.mkdir()
    desk["picks"].append(str(new))
    s, r = call(desk["port"], "POST", "/api/new-project", {})
    assert s == 200 and r["settings"] is True and desk["opened"] == []
    p = state(desk["port"])["project"]
    assert p["folders_found"] == 0          # the template's placeholder folder: the page asks for folders first
