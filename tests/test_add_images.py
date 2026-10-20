"""Adding images to a project whose key already exists, without changing existing codes."""
import json
import shutil

import pytest

from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare, read_csv
from wormscore.unblind import unblind

quiet = lambda *a: None


def add_new_worms(project, tmp_path, n=2):
    """Simulated images (different pixels) in a second source folder, as if imaged later."""
    other = make_demo(tmp_path / "other", n_per_group=n, seed=99, log=quiet)
    later = tmp_path / "later"
    later.mkdir()
    for f in (other / "data").glob("demo_*.ome.tif"):
        shutil.copy(f, later / f.name.replace("demo_", "demo_later_"))
    cfg = json.loads((project / "config.json").read_text())
    cfg["sources"].append({**cfg["sources"][0], "name": "imaged later", "path": str(later),
                           "original_path": str(later)})
    (project / "config.json").write_text(json.dumps(cfg))
    inventory(project, log=quiet)


def test_added_images_get_new_codes_and_nothing_else_changes(fresh_project, tmp_path):
    key_before = {r["code"]: r for r in read_csv(fresh_project / "key" / "key.csv")}
    meta_before = {p.parent.name: p.read_bytes() for p in (fresh_project / "blind").glob("*/meta.json")}
    ranges_before = read_csv(fresh_project / "outputs" / "display_ranges.csv")
    groups_before = read_csv(fresh_project / "key" / "settings_groups.csv")
    (fresh_project / "scoring").mkdir()
    (fresh_project / "scoring" / "scores.csv").write_text("code\nW0001\n")

    add_new_worms(fresh_project, tmp_path)
    with pytest.raises(SystemExit, match="--add-new-images"):
        prepare(fresh_project, log=quiet)
    prepare(fresh_project, add=True, log=quiet)

    key = read_csv(fresh_project / "key" / "key.csv")
    assert len(key) == len(key_before) + 4
    for r in key:
        if r["code"] in key_before:
            assert r["local_path"] == key_before[r["code"]]["local_path"] and not r["added_on"]
    new = [r for r in key if r["code"] not in key_before]
    assert sorted(r["code"] for r in new) == ["W0007", "W0008", "W0009", "W0010"]
    assert all("demo_later_" in r["file"] and r["added_on"] for r in new)
    for code, text in meta_before.items():
        assert (fresh_project / "blind" / code / "meta.json").read_bytes() == text
    assert all((fresh_project / "blind" / r["code"] / "meta.json").exists() for r in new)
    assert read_csv(fresh_project / "outputs" / "display_ranges.csv") == ranges_before
    assert read_csv(fresh_project / "key" / "settings_groups.csv") == groups_before
    assert {r["code"] for r in read_csv(fresh_project / "outputs" / "auto_measurements.csv")} >= set(key_before)
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert "images added" in log and not any(r["code"] in log for r in new)

    out = unblind(fresh_project, finished=True, anyway=True, log=quiet)
    assert "4 images added on" in (out / "methods_text.md").read_text(encoding="utf-8")


def test_adding_refuses_to_drop_or_find_nothing(fresh_project, tmp_path):
    with pytest.raises(SystemExit, match="No new images"):
        prepare(fresh_project, add=True, log=quiet)
    with pytest.raises(SystemExit, match="not both"):
        prepare(fresh_project, add=True, reassign=True, log=quiet)
    add_new_worms(fresh_project, tmp_path, n=1)
    cfg = json.loads((fresh_project / "config.json").read_text())
    cfg["sources"][0]["exclude"] = [{"match": "demo_control_01*", "reason": "test"}]
    (fresh_project / "config.json").write_text(json.dumps(cfg))
    with pytest.raises(SystemExit, match="never removes"):
        prepare(fresh_project, add=True, log=quiet)
