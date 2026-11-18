"""1.26.16: what an independent review of 1.26.13 to 1.26.15 found on the app side. Each test failed before."""
import json
import os
import shutil
import sys

import numpy as np
import pytest

from test_add_images import add_new_worms
from test_build_failure import SCORE, consistent, snapshot
from wormscore import flatfield as FF
from wormscore import pipeline, server
from wormscore import update as U
from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare, read_csv

quiet = lambda *a: None


def built(tmp_path):
    project = make_demo(tmp_path / "p", n_per_group=2, log=quiet)
    inventory(project, log=quiet)
    prepare(project, log=quiet)
    return project


def test_add_new_images_never_takes_a_code_that_has_a_saved_score(tmp_path):
    """An Add new images that failed in 1.26.12 or earlier could leave a coded image in blind/ with no key
    row, and it could be scored. 1.26.15 deleted such a folder and gave its code to another image, so the
    saved score sat on an image the scorer never saw."""
    project = built(tmp_path)
    shutil.copytree(project / "blind" / "W0001", project / "blind" / "W0005")
    meta = json.loads((project / "blind" / "W0005" / "meta.json").read_text())
    (project / "blind" / "W0005" / "meta.json").write_text(json.dumps({**meta, "code": "W0005"}))
    server.Project(project).save_score("W0005", dict(SCORE, n1_call="missing"))
    left = (project / "blind" / "W0005" / "mip.png").read_bytes()
    add_new_worms(project, tmp_path, 1)
    inventory(project, log=quiet)
    prepare(project, add=True, log=quiet)
    assert (project / "blind" / "W0005" / "mip.png").read_bytes() == left          # not replaced
    codes = [r["code"] for r in read_csv(project / "key" / "key.csv")]
    assert "W0005" not in codes and sorted(codes)[4] == "W0006"                    # new images have codes of their own
    assert json.loads((project / "scoring" / "scores.json").read_text())["W0005"]["n1_call"] == "missing"


def test_new_codes_whose_images_cannot_be_put_in_place_leave_the_key_and_drafts(tmp_path, monkeypatch):
    """The old key, seed and drafts were set aside before the exchange and stayed aside when it failed."""
    project = built(tmp_path)
    (project / "scoring_drafts").mkdir()
    (project / "scoring_drafts" / "drafts.json").write_text('{"W0001": {"values": {"n1_call": "dim"}, "at": "x"}}')
    before, rename = snapshot(project), pipeline._rename

    def held(src, dst, tries=10):
        if src.name == pipeline.BUILDING:
            raise PermissionError(13, "Access is denied", str(src))
        return rename(src, dst, tries)

    for kind in ({"reassign": True}, {}):
        with monkeypatch.context() as m:
            m.setattr(pipeline, "_rename", held)
            with pytest.raises(SystemExit, match="could not be put in place .*as they were. Close other programs"):
                prepare(project, log=quiet, **kind)
        assert snapshot(project) == before and consistent(project)
        assert sorted(x.name for x in (project / "key").iterdir()) == ["key.csv", "seed.txt", "settings_groups.csv"]
        assert not (project / pipeline.BUILDING).exists() and not (project / pipeline.PREVIOUS).exists()
    prepare(project, log=quiet)


def test_add_new_images_cut_off_after_the_key_says_what_to_do(tmp_path):
    project = built(tmp_path)
    shutil.rmtree(project / "blind" / "W0004")           # as left by a shutdown between the key and the folders
    with pytest.raises(SystemExit, match="1 code in the key has no images in the blind folder.*Rebuild, keeping"):
        prepare(project, add=True, log=quiet)
    prepare(project, log=quiet)
    assert consistent(project)


def test_the_scoring_page_puts_back_a_blind_set_caught_in_the_exchange(tmp_path):
    project = built(tmp_path)
    (project / "blind").rename(project / pipeline.PREVIOUS)
    assert server.Project(project).blind.is_dir() and not (project / pipeline.PREVIOUS).exists()


# ------------------------------------------------------------------ flat-field: which session, not only its words

def session(i, settings, day, **kw):
    return {"index": i, "system": "M", "settings": settings, "day": day, "images": 2, "width": 8, "height": 8,
            "brightness": 1000.0, "dark": False, "dark_level": 0.0, "factor": 1.0, "group_brightness": 1000.0, **kw}


@pytest.mark.parametrize("recorded", [False, True])
def test_a_scored_image_keeps_its_session_when_a_later_one_fits_the_same_words(tmp_path, recorded):
    """The image was corrected with the only slide images of its day (another objective). Slide images of its
    own objective, same day, were added after scoring: key.csv's words ("nearest day, 2026-03-05 (other
    settings)") fit both, and 1.26.15 took the later ones with no warning."""
    corr = FF.Correction.__new__(FF.Correction)
    corr.ff, corr.project, corr.used_now = {"across_field": True, "between_sessions": True}, tmp_path, {}
    corr.arrays = {"flat_0": np.ones((8, 8), np.float32), "dark_0": np.zeros((8, 8), np.float32),
                   "flat_1": np.full((8, 8), 0.5, np.float32), "dark_1": np.zeros((8, 8), np.float32)}
    img = {"local_path": "a.tif", "location_in_file": "", "system": "M", "settings": "laser 5", "objective": "63x",
           "zoom": "", "acquired": "2026-03-05T10:00:00", "width": 8, "height": 8}
    corr.sessions = [session(0, "laser 2 | 10x", "2026-03-05")]
    row = {"code": "W0001", "local_path": "a.tif", "location_in_file": "",
           **{k: str(v) for k, v in corr.key_columns(img, code="W0001").items()}}
    raw = np.full((2, 8, 8), 1000, np.uint16)
    scored_on = corr.apply(raw, img)
    if recorded:        # a build of 1.26.16 or later wrote which session each code used
        (tmp_path / "key").mkdir()
        (tmp_path / "key" / FF.USED).write_text(json.dumps(corr.used_now))
    corr.sessions = corr.sessions + [session(1, "laser 9 | 63x", "2026-03-05", added="2026-04-01 10:00",
                                             added_after_scoring_began=True)]
    assert corr.resolve(img)["index"] == 1                       # what a build without the keep would use
    assert corr.keep_coded([img], [row]) == (1, 0)
    assert corr.resolve(img)["index"] == 0 and np.array_equal(corr.apply(raw, img), scored_on)


def test_a_build_records_the_session_of_each_code_in_the_key_folder(tmp_path):
    (tmp_path / "key").mkdir()
    (tmp_path / "outputs" / FF.FOLDER).mkdir(parents=True)
    rows = [{"code": "W0001", "flat_field": FF.SAME}, {"code": "W0002", "flat_field": FF.SAME}]
    FF.record_build(tmp_path, rows, {"W0001": 0, "W0002": "avg:0+1"})
    FF.record_build(tmp_path, rows + [{"code": "W0003", "flat_field": FF.SAME}], {"W0003": 2})      # Add new images
    assert FF.used_record(tmp_path) == {"W0001": 0, "W0002": "avg:0+1", "W0003": 2}
    assert not list((tmp_path / "outputs").rglob(FF.USED))       # it tells imaging days apart: only in key/


# ------------------------------------------------------------------ Mac updater, app on another volume

def test_of_two_kept_copies_the_one_kept_last_is_the_previous_version(tmp_path, monkeypatch):
    """A Mac with the app on another volume: Go back in this version keeps a copy next to the app; the
    version gone back to (before 1.26.15) then updates and keeps its own copy in the data folder. 1.26.15
    deleted that one, the real previous version, and offered the stale copy under its name."""
    apps, data = tmp_path / "ext" / "Applications", tmp_path / "home" / "data"
    cur = apps / "wormscore.app"

    def app(folder, version):
        (folder / "Contents" / "MacOS").mkdir(parents=True)
        (folder / "Contents" / "MacOS" / "wormscore").write_text(version)

    monkeypatch.setattr(U, "data_dir", lambda: data)
    monkeypatch.setattr(U, "same_volume", lambda a, b: False)
    monkeypatch.setattr(sys, "executable", str(cur / "Contents" / "MacOS" / "wormscore"))
    paths = U.app_paths("mac")
    monkeypatch.setattr(U, "app_paths", lambda kind=None: paths)
    app(cur, "1.26.16")
    app(paths["previous"], "1.26.15")                    # stale: left next to the app by an earlier Go back
    app(paths["previous_before"], "1.26.14")             # what the update that just ran kept
    os.utime(paths["previous"].parent, (1_000_000, 1_000_000))
    os.utime(paths["previous_before"].parent, (2_000_000, 2_000_000))
    (data / "previous.json").write_text('{"version": "1.26.14"}')
    (data / "update-result.txt").write_text("ok\n\n")
    assert U.last_result()["ok"]
    kept = U.kept_copy(paths)
    assert (kept / "Contents" / "MacOS" / "wormscore").read_text() == "1.26.14" and U.previous_version() == "1.26.14"
    assert not paths["previous"].exists()                # the stale copy is the one removed


def test_go_back_says_plainly_when_the_folder_of_the_app_cannot_be_written(tmp_path, monkeypatch):
    cur, kept = tmp_path / "apps" / "wormscore.app", tmp_path / "data" / "previous" / "wormscore.app"
    for folder in (cur, kept):
        (folder / "Contents" / "MacOS").mkdir(parents=True)
        (folder / "Contents" / "MacOS" / "wormscore").write_text("x")
    paths = {"current": cur, "previous": cur.parent / U.NEAR_PREVIOUS / cur.name, "stage": cur.parent / U.NEAR_STAGE,
             "app": "wormscore.app", "check": "Contents/MacOS/wormscore", "previous_before": kept}
    monkeypatch.setattr(U, "install_kind", lambda: "mac")
    monkeypatch.setattr(U, "app_paths", lambda kind=None: paths)
    monkeypatch.setattr(U, "previous_version", lambda: "1.26.14")
    real = type(cur).mkdir

    def read_only(self, *a, **kw):
        if self.name == U.NEAR_STAGE:
            raise PermissionError(30, "Read-only file system", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(type(cur), "mkdir", read_only)
    with pytest.raises(U.UpdateError, match="cannot be written to, so nothing was changed"):
        U.rollback(relaunch=False)
    assert (kept / "Contents" / "MacOS" / "wormscore").exists()
