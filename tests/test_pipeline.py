import csv
import json

import pytest

from wormscore.pipeline import prepare, read_csv


def test_inventory_identifies_channels_and_roles(demo_project):
    inv = read_csv(demo_project / "outputs" / "inventory.csv")
    assert len(inv) == 6
    assert {r["channel_roles"] for r in inv} == {"MARKER/BF"}
    roles = {r["role"] for r in read_csv(demo_project / "outputs" / "manifest.csv")}
    assert "raw image" in roles and "reference (not an image)" in roles


def test_key_traces_every_code_to_its_file(demo_project):
    key = read_csv(demo_project / "key" / "key.csv")
    assert len(key) == 6
    for r in key:
        assert r["original_path"] and r["local_path"] and r["location_in_file"]
        assert len(r["file_sha256"]) == 64
        assert r["genotype"] in ("control", "mutant")


def test_blind_folder_reveals_no_genotype_or_file_name(demo_project):
    for meta_path in (demo_project / "blind").glob("*/meta.json"):
        text = meta_path.read_text()
        for leak in ("control", "mutant", "demo_", ".ome.tif", "genotype", "path"):
            assert leak not in text, f"{leak!r} found in {meta_path}"
    names = {p.name for p in (demo_project / "blind").rglob("*")}
    assert not any("demo_" in n for n in names)


def test_codes_do_not_change_on_rerun(fresh_project):
    before = {r["local_path"]: r["code"] for r in read_csv(fresh_project / "key" / "key.csv")}
    prepare(fresh_project, log=lambda *a: None)
    after = {r["local_path"]: r["code"] for r in read_csv(fresh_project / "key" / "key.csv")}
    assert before == after


def test_reassigning_codes_is_refused_after_scoring(fresh_project):
    (fresh_project / "scoring").mkdir()
    (fresh_project / "scoring" / "scores.json").write_text("{}")
    with pytest.raises(SystemExit):
        prepare(fresh_project, reassign=True, log=lambda *a: None)


def test_inclusion_rules_exclude_with_reason(fresh_project):
    cfg = json.loads((fresh_project / "config.json").read_text())
    cfg["inclusion"]["min_z_extent_um"] = 1000
    (fresh_project / "config.json").write_text(json.dumps(cfg))
    with pytest.raises(SystemExit):
        prepare(fresh_project, reassign=True, log=lambda *a: None)
    rows = read_csv(fresh_project / "outputs" / "excluded.csv")
    assert rows and all("z-stack covers" in r["reason"] for r in rows)


def test_log_records_parameters(demo_project):
    log = (demo_project / "log.md").read_text(encoding="utf-8")
    assert "median_filter_px" in log and "Software:" in log
    assert (demo_project / "key" / "seed.txt").read_text().strip() not in log
