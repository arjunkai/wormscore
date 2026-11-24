"""A Build, rebuild or Add new images that fails midway leaves the blind set, the key and the scores as
they were, says so, and can be run again. A rebuild used to delete blind/ first and leave part of the
coded images; a failed Add new images left folders that stopped every later Add new images."""
import errno
import json

import pytest
from PIL import Image

from wormscore import pipeline, server
from wormscore import readers as R
from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare, read_csv

from test_add_images import add_new_worms

quiet = lambda *a: None
SCORE = {"usable": "yes", "count_this_image": "yes", "n1_call": "normal", "n2_call": "normal", "flag_answers": {}}


def snapshot(project):
    """Every file the scorer or unblinding reads, by content."""
    return {f.relative_to(project).as_posix(): f.read_bytes()
            for d in ("blind", "key", "scoring", "scoring_drafts") for f in sorted((project / d).rglob("*")) if f.is_file()}


def break_at(monkeypatch, how, k):
    """The k-th image read cannot be read, or the disk is full when its planes are written."""
    seen, read, save = set(), R.read, Image.Image.save

    def bad_read(img, ci):
        seen.add((img["local_path"], img["location_in_file"]))
        if how == "unreadable" and len(seen) >= k:
            raise OSError(errno.EIO, "Input/output error", img["local_path"])
        return read(img, ci)

    def bad_save(im, fp, *a, **kw):
        if how == "disk full" and len(seen) >= k and "planes" in str(fp):
            raise OSError(errno.ENOSPC, "No space left on device", str(fp))
        return save(im, fp, *a, **kw)
    monkeypatch.setattr(R, "read", bad_read)
    monkeypatch.setattr(Image.Image, "save", bad_save)


def consistent(project):
    codes = sorted(r["code"] for r in read_csv(project / "key" / "key.csv"))
    whole = sorted(d.name for d in (project / "blind").iterdir() if (d / "meta.json").is_file())
    return codes == whole == sorted(d.name for d in (project / "blind").iterdir())


HOW = ("unreadable", "disk full")


@pytest.mark.parametrize("how", HOW)
def test_a_first_build_that_fails_leaves_no_blind_set_and_can_be_run_again(tmp_path, monkeypatch, how):
    project = make_demo(tmp_path / "p", n_per_group=2, log=quiet)
    inventory(project, log=quiet)
    with monkeypatch.context() as m:
        break_at(m, how, 3)
        with pytest.raises(SystemExit, match="No blind set was made"):
            prepare(project, log=quiet)
    assert not (project / "blind").exists() and not (project / "key" / "key.csv").exists()
    assert not (project / pipeline.BUILDING).exists()
    prepare(project, log=quiet)
    assert consistent(project) and len(read_csv(project / "key" / "key.csv")) == 4


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("reassign", (False, True))
def test_a_rebuild_that_fails_leaves_the_blind_set_key_and_scores_as_they_were(fresh_project, monkeypatch, how, reassign):
    if not reassign:           # new codes are only possible before scoring
        server.Project(fresh_project).save_score("W0002", dict(SCORE))
        server.Project(fresh_project).set_draft("W0003", {"n1_call": "dim"})
    before = snapshot(fresh_project)
    said = []
    with monkeypatch.context() as m:
        break_at(m, how, 3)
        with pytest.raises(SystemExit, match="The blind set, key and scores are as they were") as stop:
            prepare(fresh_project, reassign=reassign, log=said.append)
    assert snapshot(fresh_project) == before
    assert not (fresh_project / pipeline.BUILDING).exists() and not (fresh_project / pipeline.PREVIOUS).exists()
    assert not list((fresh_project / "key").glob("superseded_*"))
    # the message names no file: after the progress lines it would show which image a code is
    assert "demo_" not in str(stop.value) and ("free space" if how == "disk full" else "Scan data again") in str(stop.value)
    if not reassign:           # the scores still open on their images
        srv = server.Project(fresh_project)
        assert srv.load("scores.json", {})["W0002"]["n1_call"] == "normal" and srv.meta("W0002")["code"] == "W0002"
    prepare(fresh_project, reassign=reassign, log=quiet)
    assert consistent(fresh_project)
    if not reassign:
        assert {k: v for k, v in snapshot(fresh_project).items() if k.startswith(("blind/", "scoring"))} \
            == {k: v for k, v in before.items() if k.startswith(("blind/", "scoring"))}


@pytest.mark.parametrize("how", HOW)
def test_adding_images_that_fails_adds_nothing_and_can_be_run_again(fresh_project, tmp_path, monkeypatch, how):
    server.Project(fresh_project).save_score("W0002", dict(SCORE))
    add_new_worms(fresh_project, tmp_path)
    before = snapshot(fresh_project)
    with monkeypatch.context() as m:
        break_at(m, how, 2)
        with pytest.raises(SystemExit, match="No image was added"):
            prepare(fresh_project, add=True, log=quiet)
    assert snapshot(fresh_project) == before and consistent(fresh_project)
    prepare(fresh_project, add=True, log=quiet)          # used to stop with FileExistsError
    assert consistent(fresh_project) and len(read_csv(fresh_project / "key" / "key.csv")) == 10
    after = snapshot(fresh_project)
    assert {k: v for k, v in after.items() if k.startswith("blind/") and k[6:11] <= "W0006"} \
        == {k: v for k, v in before.items() if k.startswith("blind/")}


def test_folders_left_by_a_failed_add_of_an_earlier_version_do_not_stop_adding(fresh_project, tmp_path):
    add_new_worms(fresh_project, tmp_path)
    (fresh_project / "blind" / "W0007" / "planes").mkdir(parents=True)       # as 1.26.12 left them
    (fresh_project / "blind" / "W0007" / "planes" / "z00.png").write_bytes(b"part")
    prepare(fresh_project, add=True, log=quiet)
    assert consistent(fresh_project)
    assert json.loads((fresh_project / "blind" / "W0007" / "meta.json").read_text())["code"] == "W0007"


def test_a_build_that_was_cut_off_is_tidied_by_the_next_one(fresh_project):
    """The process ended midway (the computer shut down): blind_building/ is left, and, in the moment of
    the exchange, the old blind set under blind_previous/."""
    before = snapshot(fresh_project)
    (fresh_project / pipeline.BUILDING / "W0001").mkdir(parents=True)
    (fresh_project / "blind").rename(fresh_project / pipeline.PREVIOUS)
    said = []
    pipeline.tidy_unfinished_build(fresh_project, said.append)
    assert snapshot(fresh_project) == before and len(said) == 2
    assert not (fresh_project / pipeline.BUILDING).exists() and not (fresh_project / pipeline.PREVIOUS).exists()
    (fresh_project / pipeline.BUILDING / "W0001").mkdir(parents=True)
    prepare(fresh_project, log=quiet)
    assert consistent(fresh_project) and {k: v for k, v in snapshot(fresh_project).items() if k.startswith("blind/")} \
        == {k: v for k, v in before.items() if k.startswith("blind/")}


def test_a_blind_folder_that_cannot_be_replaced_stays_whole(fresh_project, monkeypatch):
    """A file of the blind set open in another program: the rebuild used to delete the files it could."""
    before = snapshot(fresh_project)
    real = pipeline._rename

    def held(src, dst, tries=10):
        if src.name == "blind":
            raise PermissionError(errno.EACCES, "Access is denied", str(src))
        return real(src, dst, tries)
    monkeypatch.setattr(pipeline, "_rename", held)
    with pytest.raises(SystemExit, match="open in another program.*as they were"):
        prepare(fresh_project, log=quiet)
    assert snapshot(fresh_project) == before and not (fresh_project / pipeline.BUILDING).exists()


def test_a_rebuild_without_room_for_its_images_says_so_at_once(fresh_project, monkeypatch):
    # 1.26.28: a full drive stopped the build only when it ran out, possibly after half an hour. It now warns
    # at once and goes on (an estimate can be off); a drive that cannot say how much is free changes nothing
    import collections
    Usage = collections.namedtuple("Usage", "total used free")
    said = []
    monkeypatch.setattr(pipeline.shutil, "disk_usage", lambda p: Usage(10**9, 10**9 - 1000, 1000))
    prepare(fresh_project, log=said.append)
    warned = [x for x in said if "may need about" in str(x)]
    assert len(warned) == 1 and "the project's drive has 0 MB free" in warned[0]
    assert "leaves the blind set, key and scores as they were" in warned[0]
    said.clear()
    monkeypatch.setattr(pipeline.shutil, "disk_usage", lambda p: Usage(10**12, 0, 10**12))
    prepare(fresh_project, log=said.append)
    assert not any("may need about" in str(x) for x in said)

    def broken(p):
        raise OSError("the share cannot say")
    monkeypatch.setattr(pipeline.shutil, "disk_usage", broken)
    prepare(fresh_project, log=said.append)                 # builds as before
