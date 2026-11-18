"""1.26.4: unblinding says when a result is not what it seems: scores saved after the genotypes were
revealed, genotype rules changed after Scan data, a misspelled genotype in the analysis settings, and a
project with fewer images than the planned minimum."""
import csv
import json

import pytest

from wormscore import config as C
from wormscore import editconfig
from wormscore.pipeline import inventory, prepare, read_csv
from wormscore.server import score_fields
from wormscore.unblind import unblind

quiet = lambda *a: None


def edit_config(project, change):
    path = project / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    change(cfg)
    path.write_text(json.dumps(cfg), encoding="utf-8")


def write_scores(project, rows):
    """rows: {code: saved time}; every image usable, counted, mutants dim."""
    key = {r["code"]: r for r in read_csv(project / "key" / "key.csv")}
    fields = score_fields(C.load(project))
    (project / "scoring").mkdir(exist_ok=True)
    for name in ("scores.csv", "score_history.csv"):
        with open(project / "scoring" / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows({"code": c, "saved": saved, "usable": "yes", "count_this_image": "yes",
                         "n1_call": "dim" if key[c]["genotype"] == "mutant" else "normal", "n2_call": "normal"}
                        for c, saved in rows.items())


def codes(project):
    return [r["code"] for r in read_csv(project / "key" / "key.csv")]


def test_scores_saved_after_the_first_unblinding_are_marked(fresh_project):
    """After Unblind the scorer knows every genotype. A score saved or changed afterwards was counted
    like any other, with nothing in the summary, the tables or the Methods draft."""
    all_codes = codes(fresh_project)
    write_scores(fresh_project, {c: "2001-01-01 10:00:00" for c in all_codes})
    out = unblind(fresh_project, finished=True, log=quiet)
    assert "score_saved_after_unblinding" not in read_csv(out / "merged.csv")[0]
    assert "after this project was first unblinded" not in (out / "summary.txt").read_text(encoding="utf-8")
    assert "first revealed" not in (out / "methods_text.md").read_text(encoding="utf-8")

    # the first unblinding is on record at 2050; two scores are saved after it
    log = fresh_project / "log.md"
    log.write_text(log.read_text(encoding="utf-8") + "\n## 2050-01-01 00:00 unblinded\n- earlier\n", encoding="utf-8")
    late = all_codes[:2]
    write_scores(fresh_project, {c: "2099-01-01 12:00:00" if c in late else "2001-01-01 10:00:00" for c in all_codes})
    out = unblind(fresh_project, finished=True, log=quiet)
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "WARNING: 2 images were scored, or had their score changed, after this project was first unblinded" in summary
    assert all(c in summary.split("first unblinded")[1].split("\n")[0] for c in late)
    merged = {r["code"]: r for r in read_csv(out / "merged.csv")}
    assert {c for c, r in merged.items() if r["score_saved_after_unblinding"] == "yes"} == set(late)
    methods = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "[2 images were scored, or had their score changed, after the genotypes were first revealed on" in methods
    assert "not blind" in (fresh_project / "log.md").read_text(encoding="utf-8").split(chr(10) + "## ")[-1]
    # the tests still run: warn, do not block
    assert read_csv(out / "fisher_tests.csv")[0]["genotype_a"]


def test_a_project_smaller_than_the_planned_minimum_unblinds_when_every_image_is_scored(fresh_project):
    edit_config(fresh_project, lambda c: c["unblinding"].update(min_scored_worms=30))
    all_codes = codes(fresh_project)
    write_scores(fresh_project, {c: "2001-01-01 10:00:00" for c in all_codes[:-1]})
    with pytest.raises(SystemExit, match="at least 30"):
        unblind(fresh_project, finished=True, log=quiet)
    write_scores(fresh_project, {c: "2001-01-01 10:00:00" for c in all_codes})
    unblind(fresh_project, finished=True, log=quiet)
    last = (fresh_project / "log.md").read_text(encoding="utf-8").split(chr(10) + "## ")[-1]
    assert "every coded image; fewer than the planned minimum of 30" in last and "override used" not in last


def test_genotype_rules_changed_after_scan_are_pointed_out(fresh_project):
    """A rule corrected in config.json after Scan data does not reach the key until Scan data and a
    rebuild; the results used the old genotypes without a word."""
    write_scores(fresh_project, {c: "2001-01-01 10:00:00" for c in codes(fresh_project)})
    out = unblind(fresh_project, finished=True, log=quiet)
    assert "another genotype" not in (out / "summary.txt").read_text(encoding="utf-8")

    edit_config(fresh_project, lambda c: c["sources"][0]["genotypes"].insert(0, {"match": "*mutant_01*", "genotype": "het"}))
    out = unblind(fresh_project, finished=True, log=quiet)
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "WARNING: 1 coded image has another genotype under the genotype rules now in config.json" in summary
    edit_config(fresh_project, lambda c: c["sources"][0].update(exclude=[{"match": "*control_02*", "reason": "test"}]))
    out = unblind(fresh_project, finished=True, log=quiet)
    assert "WARNING: 1 coded image matches an exclusion rule now in config.json" in (out / "summary.txt").read_text(encoding="utf-8")


def test_a_misspelled_genotype_in_the_analysis_settings_is_named(fresh_project):
    said = []
    edit_config(fresh_project, lambda c: c["unblinding"].update(reference_genotype="contrl"))
    prepare(fresh_project, log=said.append)
    assert any(s.startswith("Warning: the analysis settings name the genotype 'contrl'") for s in said)
    assert "name the genotype 'contrl'" in (fresh_project / "log.md").read_text(encoding="utf-8")
    write_scores(fresh_project, {c: "2001-01-01 10:00:00" for c in codes(fresh_project)})
    out = unblind(fresh_project, finished=True, log=quiet)
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "WARNING: no counted image has the genotype 'contrl', named as the genotype to compare with" in summary

    said.clear()
    edit_config(fresh_project, lambda c: c["unblinding"].update(reference_genotype="control"))
    prepare(fresh_project, log=said.append)
    assert not any(s.startswith("Warning: the analysis settings") for s in said)


def test_project_settings_ask_for_scan_and_rebuild_after_a_rule_change(fresh_project):
    """Saving new folders or rules showed a message for a few seconds; nothing then said that the blind
    set still used the old ones."""
    assert editconfig.stale(fresh_project) is None
    cur = editconfig.current(fresh_project)
    src = dict(cur["sources"][0], **{"from": 0})
    src["genotypes"] = [{"match": "*mutant_01*", "genotype": "het"}] + src["genotypes"]
    editconfig.save(fresh_project, {"project_name": cur["project_name"], "sources": [src]})
    assert editconfig.stale(fresh_project) == {"scan": True, "build": True}
    inventory(fresh_project, log=quiet)
    assert editconfig.stale(fresh_project) == {"scan": False, "build": True}
    prepare(fresh_project, log=quiet)
    assert editconfig.stale(fresh_project) is None
    assert {r["genotype"] for r in read_csv(fresh_project / "key" / "key.csv")} == {"control", "mutant", "het"}
    # a save that changes no folder or rule asks for nothing
    editconfig.save(fresh_project, {"project_name": "Another name", "sources": [src]})
    assert editconfig.stale(fresh_project) is None
