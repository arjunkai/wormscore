"""Two readings of one acquisition date: imaging_date (the per_neuron.csv label, 1.26.13) and acquired_day
(the day of the flat-field imaging session, through session_key).

Nikon and Olympus write the date as the acquiring computer shows it. imaging_date marks a date that reads
both ways (11/3/2009) as not known; acquired_day reads it month first, as it always has, and is not changed:
it decides which slide images correct an image."""
from wormscore import flatfield as FF

AMBIGUOUS = "11/3/2009 12:20:54 PM"          # 3 November or 11 March
DAY_FIRST_PM = "17/7/2009 4:01:38 PM"         # only 17 July: there is no month 17


def test_a_date_that_reads_both_ways_is_marked_in_the_label_and_read_month_first_for_the_session():
    assert FF.imaging_date(AMBIGUOUS) == "11/3/2009 (day or month first: not known)"
    assert FF.acquired_day(AMBIGUOUS) == "2009-11-03"
    assert FF.session_key({"system": "A", "settings": "", "acquired": AMBIGUOUS})[2] == "2009-11-03"


def test_the_same_rule_reads_slide_images_and_images_so_same_day_matching_does_not_depend_on_it():
    """Slide images and images of one computer write the date the same way and go through the same
    session_key, so an image and the slide images of its day get the same day, whichever reading is right.
    Two different days never get the same day: a month-first reading of a day-first date has a day of the
    month of at most 12, and a date read the right way only because its day is above 12 has a day above 12."""
    corr = FF.Correction.__new__(FF.Correction)
    corr.sessions = [{"index": i, "system": "A", "settings": "s", "day": FF.acquired_day(t), "height": 2, "width": 2}
                     for i, t in enumerate(["11/3/2009 9:00:00 AM", "3/11/2009 9:00:00 AM"])]
    assert [s["day"] for s in corr.sessions] == ["2009-11-03", "2009-03-11"]        # two days stay two days
    img = lambda text: {"system": "A", "settings": "s", "acquired": text, "height": 2, "width": 2}
    assert corr.resolve(img("11/3/2009 4:00:00 PM"))["index"] == 0
    assert corr.resolve(img("3/11/2009 4:00:00 PM"))["index"] == 1
    assert corr.resolve(img("11/3/2009 4:00:00 PM"))["how"] == "same session"
    # every pair of day-first dates of one year: two different dates never give one day
    seen = {}
    for month in range(1, 13):
        for day in range(1, 29):
            got = FF.acquired_day(f"{day}/{month}/2009 10:00:00")       # 24-hour day-first: read today
            assert got, (day, month)
            assert seen.setdefault(got, (day, month)) == (day, month), (got, seen[got], (day, month))


def test_a_day_first_date_with_am_pm_gets_no_day_today():
    """Reproduces the gap: the day-first form is known with a 24-hour time only."""
    assert FF.imaging_date(DAY_FIRST_PM) == "2009-07-17"
    assert FF.acquired_day("17/7/2009 16:01:38") == "2009-07-17"
    assert FF.acquired_day(DAY_FIRST_PM) == ""


# ------------------------------------------------------------------ the day-first AM/PM form, from a new scan (1.26.18)

import hashlib                                                 # noqa: E402
import json                                                    # noqa: E402
from pathlib import Path                                       # noqa: E402

import numpy as np                                             # noqa: E402
import pytest                                                  # noqa: E402
from PIL import Image                                          # noqa: E402

from wormscore import pipeline, readers, rescan, server        # noqa: E402
from wormscore.demo import make_demo                           # noqa: E402
from wormscore.files import read_csv, write_csv                # noqa: E402

from test_flatfield import slides                              # noqa: E402

QUIET = {"log": lambda *a: None}
SCORE = {"usable": "yes", "count_this_image": "yes", "n1_call": "normal", "n2_call": "normal", "flag_answers": {}}
# One day-first computer with a 12-hour clock: 5 July reads (month first, as 7 May), 20 July did not read at all.
DATES = {"data/demo_control_01.ome.tif": "5/7/2009 10:00:00 AM", "data/demo_mutant_01.ome.tif": "5/7/2009 11:00:00 AM",
         "data/demo_control_02.ome.tif": "20/7/2009 4:01:38 PM", "data/demo_mutant_02.ome.tif": "20/7/2009 4:30:00 PM",
         "a/slide_0.ome.tif": "5/7/2009 9:00:00 AM", "a/slide_1.ome.tif": "5/7/2009 9:05:00 AM",
         "b/slide_0.ome.tif": "20/7/2009 3:00:00 PM", "b/slide_1.ome.tif": "20/7/2009 3:05:00 PM"}
LATE = ("demo_control_02.ome.tif", "demo_mutant_02.ome.tif")          # 20 July: no day in 1.26.17
EARLY = ("demo_control_01.ome.tif", "demo_mutant_01.ome.tif")         # 5 July, read as 7 May by every version
# This scenario built by wormscore 1.26.17 (commit beb4d4c), measured with fingerprint() below.
BUILT_BY_1_26_17 = {
    "blind/demo_control_01.ome.tif": "79970be18438fe2aaf0bcae7897cdb977fcecd5fe40fef10b82eccac5ee561bb",
    "blind/demo_control_02.ome.tif": "d8df44a76ec8bc8c05e7a5aea96bc3027845be18fa90deee8b2ef77131e001b9",
    "blind/demo_mutant_01.ome.tif": "bfeb4db3d393cb8dd85bf13f06544320947ba83b509830f2bf99d839e0fd8233",
    "blind/demo_mutant_02.ome.tif": "320d28a8a8a430abc810a2ed40866b2ed448f4e7693cd602a464958090578bad",
    "key.csv": "065521e2c7356321ebfb2ee72e8f1d849759b1c1b57300bdffc4c90a2fb2adeb",
    "sessions.csv": "e6e4d99371a968917c3bf57d8e51149e7f387d22bd16511c87366388d1a93d98",
    "flat_field_sessions.json": "6b809250f4777487ddc22e4224fd9f8e68b79fecf1e8de3d48dd0fc934f175b8",
}


@pytest.fixture
def dated(monkeypatch):
    """Each file read with the date its computer wrote (the demo and the slide images have none)."""
    real = readers.images_in

    def images_in(path, cfg, source=None):
        found = real(path, cfg, source)
        for img in found:
            img["acquired"] = DATES.get(f"{Path(path).parent.name}/{Path(path).name}", img["acquired"])
        return found
    monkeypatch.setattr(readers, "images_in", images_in)


def fingerprint(p, root):
    """SHA-256 of what a build made, without what differs from run to run: the codes (random), the folder the
    test runs in, and the files' own checksums (an OME-TIFF holds a new UUID each time it is written). Each
    PNG by its pixels, the other files by their bytes."""
    sha = lambda b: hashlib.sha256(b).hexdigest()
    key = read_csv(p / "key" / "key.csv")
    name = {r["code"]: r["file"] for r in key}
    per = {}
    for f in sorted((p / "blind").rglob("*")):
        if not f.is_file():
            continue
        code, rest = f.relative_to(p / "blind").parts[0], "/".join(f.relative_to(p / "blind").parts[1:])
        if f.suffix == ".png":
            a = np.asarray(Image.open(f))
            data = str((a.shape, a.dtype.str)).encode() + a.tobytes()
        else:
            data = f.read_bytes().replace(code.encode(), b"CODE")
        per.setdefault(name[code], []).append(rest + ":" + sha(data))
    out = {f"blind/{k}": sha("\n".join(v).encode()) for k, v in sorted(per.items())}
    rows = sorted(({k: v.replace(str(root), "ROOT").replace("\\", "/") for k, v in r.items()
                    if k not in ("code", "file_sha256")} for r in key), key=lambda r: r["file"])
    out["key.csv"] = sha(json.dumps(rows, sort_keys=True).encode())
    out["sessions.csv"] = sha((p / "outputs" / "flat_field" / "sessions.csv").read_bytes().replace(b"\r\n", b"\n"))
    used = json.loads((p / "key" / "flat_field_sessions.json").read_text(encoding="utf-8"))
    out["flat_field_sessions.json"] = sha(json.dumps({name[c]: v for c, v in used.items()}, sort_keys=True).encode())
    return out


def scanned_by_1_26_17(p):
    """inventory.csv as 1.26.17 wrote it: without the acquired_day column."""
    inv = p / "outputs" / "inventory.csv"
    with open(inv, encoding="utf-8") as fh:
        fields = [f for f in fh.readline().strip().split(",") if f != "acquired_day"]
    write_csv(inv, read_csv(inv), fields)


def existing_project(root):
    """A flat-field project of 1.26.17: scanned, slide images chosen, built."""
    p = make_demo(root / "p", n_per_group=2, **QUIET)
    pipeline.inventory(p, **QUIET)
    scanned_by_1_26_17(p)
    slides(root / "ref" / "a")
    slides(root / "ref" / "b", level=1000, seed=1)
    FF.measure(p, root / "ref", **QUIET)
    pipeline.prepare(p, **QUIET)
    return p


def blind_files(p):
    return {f.relative_to(p).as_posix(): f.read_bytes() for f in sorted((p / "blind").rglob("*")) if f.is_file()}


def by_name(p):
    return {r["file"]: r for r in read_csv(p / "key" / "key.csv")}


def test_an_existing_project_builds_as_1_26_17_did_until_it_scans_again(tmp_path, dated):
    """Without a new Scan data the project builds exactly as 1.26.17 built it, also after a rebuild that
    keeps the codes and one with new codes, and its slide images are read the old way as well."""
    p = existing_project(tmp_path)
    assert fingerprint(p, tmp_path) == BUILT_BY_1_26_17
    key, blind = (p / "key" / "key.csv").read_bytes(), blind_files(p)
    sessions = (p / "outputs" / "flat_field" / "sessions.csv").read_bytes()
    pipeline.prepare(p, **QUIET)                                     # keeping the codes
    assert (p / "key" / "key.csv").read_bytes() == key and blind_files(p) == blind
    assert (p / "outputs" / "flat_field" / "sessions.csv").read_bytes() == sessions
    pipeline.prepare(p, reassign=True, **QUIET)                      # new codes
    assert fingerprint(p, tmp_path) == BUILT_BY_1_26_17
    assert rescan.scan_changes(p)["lines"] == []
    FF.measure(p, tmp_path / "ref", **QUIET)                         # slide images chosen again: read the old way too
    assert (p / "outputs" / "flat_field" / "sessions.csv").read_bytes() == sessions


LINE = ("the flat-field imaging day of 2 coded images is now read from their date (written day first, with AM or PM); "
        "before scoring begins, this can change their flat-field correction; choose the slide images again so theirs "
        "are read the same way")


def test_a_new_scan_before_scoring_reads_the_day_and_says_so(tmp_path, dated):
    p = existing_project(tmp_path)
    before = by_name(p)
    pipeline.inventory(p, **QUIET)
    days = {Path(r["local_path"]).name: r["acquired_day"] for r in read_csv(p / "outputs" / "inventory.csv")}
    assert days == {"demo_control_01.ome.tif": "2009-05-07", "demo_mutant_01.ome.tif": "2009-05-07",
                    "demo_control_02.ome.tif": "2009-07-20", "demo_mutant_02.ome.tif": "2009-07-20"}
    assert LINE in rescan.scan_changes(p)["lines"]                   # on the Build card
    said = []
    pipeline.prepare(p, log=said.append)
    after = by_name(p)
    for f in EARLY:
        assert after[f] == before[f]
    for f in LATE:
        assert after[f]["flat_field"] == "nearest day, 2009-05-07"
        assert after[f]["imaging_session"].split(", ")[1] == "2009-07-20"
    assert any(LINE in s for s in said) and LINE in (p / "log.md").read_text(encoding="utf-8")
    # the slide images chosen again are read the same way: 20 July is a session of its own
    FF.measure(p, tmp_path / "ref", **QUIET)
    assert sorted(s["day"] for s in FF.summary(p)["sessions"]) == ["2009-05-07", "2009-07-20"]
    pipeline.prepare(p, **QUIET)
    assert {r["flat_field"] for r in by_name(p).values()} == {"same session"}


def test_a_new_scan_after_scoring_keeps_the_coded_images_as_they_were_scored(tmp_path, dated):
    p = existing_project(tmp_path)
    key, blind = (p / "key" / "key.csv").read_bytes(), blind_files(p)
    pr = server.Project(p)
    for r in read_csv(p / "key" / "key.csv"):
        pr.save_score(r["code"], dict(SCORE))
    pipeline.inventory(p, **QUIET)
    assert LINE in rescan.scan_changes(p)["lines"]
    said = []
    pipeline.prepare(p, log=said.append)
    assert (p / "key" / "key.csv").read_bytes() == key and blind_files(p) == blind
    assert any(s.startswith("2 coded images keep the flat-field correction they were scored with.") for s in said)
    assert not any("this build changes the flat-field correction" in s for s in said)


def test_the_day_from_a_scan_and_the_reading_of_1_26_17():
    assert FF.scanned_day(DAY_FIRST_PM) == "2009-07-17" and FF.acquired_day(DAY_FIRST_PM) == ""
    assert FF.scanned_day("20/7/2009") == "2009-07-20"
    assert FF.scanned_day(AMBIGUOUS) == FF.acquired_day(AMBIGUOUS) == "2009-11-03"    # unchanged: month first
    for text in ["9/24/2020 4:01:38 PM", "24.09.2020 16:01:38", "2020/09/24", "29/12/2010 12:13:08", "",
                 "2026-03-05 10:00:00", "06/03/2009 10:56:46 AM", "31/31/2009 4:01:38 PM", "sometime"]:
        assert FF.scanned_day(text) == FF.acquired_day(text), text
    img = {"system": "A", "settings": "", "acquired": DAY_FIRST_PM}
    assert FF.session_key(img)[2] == ""                                     # an inventory of 1.26.17
    assert FF.session_key({**img, "acquired_day": "2009-07-17"})[2] == "2009-07-17"
    assert FF.session_key({**img, "acquired_day": ""})[2] == ""


def test_a_project_built_from_a_new_scan_is_not_told_its_day_changed(tmp_path, dated):
    """The Build-card line about the day now read compared with the key's raw date text, so it showed
    for every such image, also in a project scanned and built by 1.26.18, and every rebuild logged a
    change warning (second review of 1.26.18). It counts only images whose recorded session lacks the day."""
    p = make_demo(tmp_path / "p", n_per_group=2, **QUIET)
    pipeline.inventory(p, **QUIET)                                   # scanned by 1.26.18
    slides(tmp_path / "ref" / "a")
    slides(tmp_path / "ref" / "b", level=1000, seed=1)
    FF.measure(p, tmp_path / "ref", **QUIET)
    pipeline.prepare(p, **QUIET)
    assert rescan.scan_changes(p)["lines"] == []
    said = []
    pipeline.prepare(p, log=said.append)
    assert not [s for s in said if s.startswith("Warning")]


def test_an_image_held_to_its_old_day_is_counted_once():
    """An image held to the day 1.26.17 read, in a key from before 1.26.16 whose newest session list has a
    session added after scoring, was counted twice in "N coded images keep ..." (second review)."""
    import tempfile
    import numpy as np
    c = FF.Correction.__new__(FF.Correction)
    c.project = Path(tempfile.mkdtemp())
    c.ff = {"across_field": True, "between_sessions": False}
    c.used_now = {}
    c.sessions = [
        {"index": 0, "system": "A", "settings": "s", "day": "2009-05-07", "height": 2, "width": 2, "factor": 1, "dark_level": 0},
        {"index": 1, "system": "A", "settings": "s", "day": "", "height": 2, "width": 2, "factor": 1, "dark_level": 0},
        {"index": 2, "system": "B", "settings": "t", "day": "2009-08-01", "height": 2, "width": 2, "factor": 1, "dark_level": 0,
         "added": "2009-08-02 10:00:00", "added_after_scoring_began": True},
    ]
    c.arrays = {f"flat_{i}": np.ones((2, 2)) for i in range(3)} | {f"dark_{i}": np.zeros((2, 2)) for i in range(3)}
    img = {"system": "A", "settings": "s", "acquired": "20/7/2009 4:01:38 PM", "acquired_day": "2009-07-20",
           "height": 2, "width": 2, "local_path": "x.tif", "location_in_file": "0"}
    was = {k: v for k, v in img.items() if k != "acquired_day"}
    cols = c.key_columns(was, c._states()[1])
    row = {"code": "W0001", "local_path": "x.tif", "location_in_file": "0", **{k: str(v) for k, v in cols.items()}}
    assert c.keep_coded([img], [row]) == (1, 0)
