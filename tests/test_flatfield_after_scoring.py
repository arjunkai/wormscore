"""Slide images added after scoring began, then a rebuild that keeps the codes: every coded image keeps
the planes it was scored on. Such a rebuild used to correct every image again with all sessions, so
images already scored changed without a word in the Build log or log.md."""
import json
import shutil

import numpy as np
import tifffile

from wormscore import flatfield as FF
from wormscore import pipeline, server
from wormscore.demo import make_demo
from wormscore.files import read_csv

from test_flatfield import slides

QUIET = {"log": lambda *a: None}
SCORE = {"usable": "yes", "count_this_image": "yes", "n1_call": "normal", "n2_call": "normal", "flag_answers": {}}


def stamp(path, day):
    """Give an OME-TIFF an acquisition day (the demo writes none)."""
    text = tifffile.tiffcomment(str(path))
    i = text.index(">", text.index("<Image ")) + 1
    tifffile.tiffcomment(str(path), text[:i] + f"<AcquisitionDate>{day}T10:00:00</AcquisitionDate>" + text[i:])


def dated_slides(folder, day, **kw):
    for f in slides(folder, **kw).glob("*.ome.tif"):
        stamp(f, day)
    return folder


def dated_project(tmp_path, days):
    p = make_demo(tmp_path / "p", n_per_group=1, **QUIET)
    for f, day in zip(sorted((p / "data").glob("*.ome.tif")), days):
        if day:
            stamp(f, day)
    pipeline.inventory(p, **QUIET)
    return p


def blind_files(p):
    return {f.relative_to(p).as_posix(): f.read_bytes() for f in sorted((p / "blind").rglob("*")) if f.is_file()}


def score_all(p):
    pr = server.Project(p)
    for r in read_csv(p / "key" / "key.csv"):
        pr.save_score(r["code"], dict(SCORE))


def last_section(p):
    log = (p / "log.md").read_text(encoding="utf-8")
    return log[log.rindex("\n## "):]


def test_a_rebuild_after_scoring_keeps_images_on_the_slide_images_they_were_scored_with(tmp_path):
    p = dated_project(tmp_path, ["2026-03-01", "2026-03-05"])
    FF.measure(p, dated_slides(tmp_path / "ref1", "2026-03-01"), **QUIET)
    pipeline.prepare(p, **QUIET)
    key = (p / "key" / "key.csv").read_bytes()
    assert sorted(r["flat_field"] for r in read_csv(p / "key" / "key.csv")) == ["nearest day, 2026-03-01", "same session"]
    before = blind_files(p)
    score_all(p)
    # the Build warning's own advice: add the slide images of the day that had none
    FF.add_sessions(p, dated_slides(tmp_path / "ref2", "2026-03-05", level=1000, seed=1), **QUIET)
    said = []
    pipeline.prepare(p, log=said.append)
    assert blind_files(p) == before
    assert (p / "key" / "key.csv").read_bytes() == key
    line = "1 coded image keeps the flat-field correction it was scored with."
    assert any(s.startswith(line) for s in said) and line in last_section(p)
    assert any("Coded images stay as they were scored." in s for s in said)
    assert not any("Add that session's slide images" in s for s in said)

    # an image added after the slide images uses them, and the next rebuild keeps both kinds as they are
    new = p / "data" / "demo_control_09.ome.tif"
    shutil.copy(p / "data" / "demo_mutant_01.ome.tif", new)          # the 2026-03-05 image, as another animal
    with tifffile.TiffFile(new) as t:
        data, text = t.asarray(), t.ome_metadata
    data[0, 0, 0, 0] += 1
    tifffile.imwrite(new, data, photometric="minisblack", description=text, metadata=None)
    pipeline.inventory(p, **QUIET)
    pipeline.prepare(p, add=True, **QUIET)
    rows = read_csv(p / "key" / "key.csv")
    added = next(r for r in rows if r["added_on"])
    assert added["flat_field"] == "same session" and added["imaging_session"].endswith("2026-03-05")
    assert {k: v for k, v in blind_files(p).items() if not k.startswith(f"blind/{added['code']}/")} == before
    before = blind_files(p)
    pipeline.prepare(p, **QUIET)
    assert blind_files(p) == before
    same = lambda rs: [{k: v for k, v in r.items() if k != "same_field_group"} for r in rs]   # a running number
    assert same(read_csv(p / "key" / "key.csv")) == same(rows)


def test_images_without_a_date_keep_their_only_session_when_another_is_added_after_scoring(tmp_path):
    p = dated_project(tmp_path, [None, None])
    FF.measure(p, slides(tmp_path / "ref1"), **QUIET)
    pipeline.prepare(p, **QUIET)
    before = blind_files(p)
    score_all(p)
    FF.add_sessions(p, dated_slides(tmp_path / "ref2", "2026-03-05", level=1000, seed=1), **QUIET)
    pipeline.prepare(p, **QUIET)          # used to become "average of 2 sessions", without the brightness factor
    assert blind_files(p) == before
    assert {r["flat_field"] for r in read_csv(p / "key" / "key.csv")} == {"same session"}


def test_before_scoring_a_rebuild_uses_the_added_slide_images(tmp_path):
    p = dated_project(tmp_path, ["2026-03-01", "2026-03-05"])
    FF.measure(p, dated_slides(tmp_path / "ref1", "2026-03-01"), **QUIET)
    pipeline.prepare(p, **QUIET)
    FF.add_sessions(p, dated_slides(tmp_path / "ref2", "2026-03-05", level=1000, seed=1), **QUIET)
    said = []
    pipeline.prepare(p, log=said.append)
    assert {r["flat_field"] for r in read_csv(p / "key" / "key.csv")} == {"same session"}
    assert not any("flat-field correction" in s for s in said)


def test_a_correction_changed_by_hand_after_scoring_is_built_with_a_recorded_warning(tmp_path):
    p = dated_project(tmp_path, [None, None])
    FF.measure(p, slides(tmp_path / "ref1"), **QUIET)
    pipeline.prepare(p, **QUIET)
    score_all(p)
    cfg = json.loads((p / "config.json").read_text(encoding="utf-8"))
    del cfg[FF.KEY]                      # Turn off is refused once scoring began; config.json can still be edited
    (p / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    said = []
    pipeline.prepare(p, log=said.append)
    line = "Warning: this build changes the flat-field correction of 2 coded images"
    assert any(s.startswith(line) for s in said) and line in last_section(p)


def test_a_project_without_the_correction_rebuilds_after_scoring_without_a_flat_field_line(tmp_path):
    p = dated_project(tmp_path, [None, None])
    pipeline.prepare(p, **QUIET)
    before = blind_files(p)
    score_all(p)
    said = []
    pipeline.prepare(p, log=said.append)
    assert blind_files(p) == before
    assert not any("flat-field" in s for s in said) and "flat-field" not in last_section(p)


def test_an_average_is_named_by_its_sessions():
    """Two images held to different session lists must not share one stored average."""
    corr = FF.Correction.__new__(FF.Correction)
    corr.ff = {"across_field": True}
    corr.sessions = [{"index": i, "system": "A", "settings": "", "day": d, "factor": 1.0, "dark_level": 0.0,
                      "height": 2, "width": 2, **({"added": "2026-04-01 10:00"} if i == 2 else {})}
                     for i, d in enumerate(["2026-03-01", "2026-03-10", "2026-03-20"])]
    corr.arrays = {f"flat_{i}": np.full((2, 2), v, np.float32) for i, v in enumerate([0.5, 1.0, 2.0])}
    corr.arrays.update({f"dark_{i}": np.zeros((2, 2), np.float32) for i in range(3)})
    img = {"system": "A", "settings": "", "acquired": "", "height": 2, "width": 2, "local_path": "a", "location_in_file": ""}
    raw = np.full((1, 2, 2), 700, np.uint16)
    assert corr.resolve(img)["how"] == "average of 3 sessions"
    assert (corr.apply(raw, img) == 600).all()                       # 700 / mean(0.5, 1, 2)
    row = {"local_path": "a", "location_in_file": "", **{k: str(v) for k, v in corr.key_columns(img, corr.sessions[:2]).items()}}
    assert corr.keep_coded([img], [row]) == (1, 0)
    assert corr.resolve(img)["how"] == "average of 2 sessions"
    assert (corr.apply(raw, img) == 933).all()                       # 700 / mean(0.5, 1)
