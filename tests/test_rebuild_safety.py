"""1.26.3: a build never changes what saved scores belong to, and never leaves a project that is being
scored without its coded images."""
import hashlib
import json
import shutil

import numpy as np
import pytest
import tifffile

from wormscore import demo, server
from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare, read_csv
from wormscore.unblind import unblind

quiet = lambda *a: None
SCORE = {"usable": "yes", "count_this_image": "yes", "n1_call": "normal", "n2_call": "normal", "flag_answers": {}}


def picture(project, code):
    return hashlib.sha256((project / "blind" / code / "mip.png").read_bytes()).hexdigest()


def test_build_refuses_new_codes_when_the_key_is_away_and_scores_are_saved(fresh_project, tmp_path):
    """Some labs move key/ away while scoring. A build without it used to give every image a new code,
    so the saved scores then belonged to other images."""
    server.Project(fresh_project).save_score("W0001", dict(SCORE))
    before = {c: picture(fresh_project, c) for c in ("W0001", "W0002", "W0003")}
    shutil.move(str(fresh_project / "key"), str(tmp_path / "key_kept_elsewhere"))
    with pytest.raises(SystemExit, match="key is not in the key folder"):
        prepare(fresh_project, log=quiet)
    assert {c: picture(fresh_project, c) for c in before} == before
    assert not (fresh_project / "key" / "seed.txt").exists()
    # with the key back, the build keeps the codes as before
    shutil.rmtree(fresh_project / "key", ignore_errors=True)
    shutil.move(str(tmp_path / "key_kept_elsewhere"), str(fresh_project / "key"))
    prepare(fresh_project, log=quiet)
    assert {c: picture(fresh_project, c) for c in before} == before


def test_build_refuses_new_codes_when_the_key_is_away_and_a_package_exists(fresh_project, tmp_path):
    from wormscore.scorers import export_package
    export_package(fresh_project, "Sam", tmp_path / "pkg", fraction=0.5, log=quiet)
    shutil.move(str(fresh_project / "key"), str(tmp_path / "key_kept_elsewhere"))
    with pytest.raises(SystemExit, match="second-scorer package"):
        prepare(fresh_project, log=quiet)


def test_a_first_build_without_a_key_still_works(tmp_path):
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    inventory(project, log=quiet)
    prepare(project, log=quiet)
    assert len(read_csv(project / "key" / "key.csv")) == 2


def test_rebuild_with_the_image_files_away_leaves_the_blind_set(tmp_path):
    """A rebuild deletes blind/ before it reads the images. With the drive not connected it used to
    leave the project without coded images."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    inventory(project, log=quiet)
    prepare(project, log=quiet)
    before = {c: picture(project, c) for c in ("W0001", "W0002")}
    (project / "data").rename(project / "data_away")
    with pytest.raises(SystemExit, match="cannot be found"):
        prepare(project, log=quiet)
    assert {c: picture(project, c) for c in before} == before
    (project / "data_away").rename(project / "data")
    prepare(project, log=quiet)
    assert {c: picture(project, c) for c in before} == before


def add_brighter_batch(project, tmp_path, n=8):
    """A later batch on the same microscope, four times as bright, in a second source folder."""
    later = tmp_path / "later"
    later.mkdir()
    rng = np.random.default_rng(5)
    for i in range(n):
        marker, bf = demo._worm(rng, ["normal", "normal"])
        marker = np.clip(marker.astype(float) * 4, 0, 65535).astype(np.uint16)
        tifffile.imwrite(later / f"demo_later_control_{i}.ome.tif", np.stack([marker, bf]), photometric="minisblack",
                         ome=True, metadata={"axes": "CZYX", "PhysicalSizeX": 0.25, "PhysicalSizeY": 0.25,
                                             "PhysicalSizeZ": 1.0, "Channel": {"Name": ["GFP", "Brightfield"]}})
    cfg = json.loads((project / "config.json").read_text())
    cfg["sources"].append({**cfg["sources"][0], "name": "imaged later", "path": str(later), "original_path": str(later)})
    (project / "config.json").write_text(json.dumps(cfg))
    inventory(project, log=quiet)


def test_rebuild_after_adding_images_keeps_ranges_and_batches(fresh_project, tmp_path):
    """Add new images promises that coded images keep their display ranges. "Rebuild, keeping the current
    codes" afterwards used to work the ranges out again over all images and to drop added_on."""
    server.Project(fresh_project).save_score("W0001", dict(SCORE))
    add_brighter_batch(fresh_project, tmp_path)
    prepare(fresh_project, add=True, log=quiet)
    meta = json.loads((fresh_project / "blind" / "W0001" / "meta.json").read_text())
    ranges = read_csv(fresh_project / "outputs" / "display_ranges.csv")
    groups = read_csv(fresh_project / "key" / "settings_groups.csv")
    key = {r["code"]: r for r in read_csv(fresh_project / "key" / "key.csv")}
    shown = (fresh_project / "blind" / "W0001" / "marker_mip_enhanced.png").read_bytes()

    prepare(fresh_project, log=quiet)       # Rebuild, keeping the current codes

    after = json.loads((fresh_project / "blind" / "W0001" / "meta.json").read_text())
    assert after["enhanced_range"] == meta["enhanced_range"] and after["full_range"] == meta["full_range"]
    assert (fresh_project / "blind" / "W0001" / "marker_mip_enhanced.png").read_bytes() == shown
    assert read_csv(fresh_project / "outputs" / "display_ranges.csv") == ranges
    assert read_csv(fresh_project / "key" / "settings_groups.csv") == groups
    key_after = {r["code"]: r for r in read_csv(fresh_project / "key" / "key.csv")}
    assert {c: r["added_on"] for c, r in key_after.items()} == {c: r["added_on"] for c, r in key.items()}
    assert sum(1 for r in key_after.values() if r["added_on"]) == 8
    out = unblind(fresh_project, finished=True, anyway=True, log=quiet)
    assert "8 images added on" in (out / "methods_text.md").read_text(encoding="utf-8")


def test_rebuild_without_added_images_writes_the_key_as_before(fresh_project):
    """Projects that never added images: the key has no added_on column, as before."""
    before = (fresh_project / "key" / "key.csv").read_text(encoding="utf-8")
    prepare(fresh_project, log=quiet)
    assert (fresh_project / "key" / "key.csv").read_text(encoding="utf-8") == before


def test_scores_are_read_from_scores_csv_when_scores_json_is_gone(fresh_project):
    """scores.json lost (a sync conflict, a deleted file): the next save used to write scores.csv again
    with that one score only."""
    p = server.Project(fresh_project)
    p.save_score("W0001", dict(SCORE, notes="=first", flag_answers={"edge": "yes"}))
    p.save_score("W0002", dict(SCORE, n1_call="dim"))
    (fresh_project / "scoring" / "scores.json").unlink()

    p = server.Project(fresh_project)
    got = p.load("scores.json", {})
    assert set(got) == {"W0001", "W0002"} and got["W0001"]["notes"] == "=first"
    assert got["W0001"]["flag_answers"] == {"edge": "yes"} and got["W0002"]["n1_call"] == "dim"
    p.save_score("W0003", dict(SCORE))
    rows = {r["code"]: r for r in read_csv(fresh_project / "scoring" / "scores.csv")}
    assert set(rows) == {"W0001", "W0002", "W0003"} and rows["W0002"]["n1_call"] == "dim"
    assert rows["W0001"]["notes"] == "'=first"
    assert set(json.loads((fresh_project / "scoring" / "scores.json").read_text())) == set(rows)
