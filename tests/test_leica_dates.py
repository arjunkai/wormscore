"""The acquisition date of Leica files (1.26.18). LAS X writes each frame's time in the series'
TimeStampList as a Windows file time (UTC); its Properties window shows the first as Start Time in the
computer's time zone, and Bio-Formats reads the same value. Scan data never recorded it, so Leica images
had no imaging_date and no flat-field day. It now records the start time in the scanning computer's time
zone, as LAS X shows it. An existing project corrects, measures and reports as before until it runs Scan
data again: slide images are read with their Leica dates only once the project's images have theirs."""
import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from test_flatfield import slides
from test_flatfield_dates import QUIET, SCORE, blind_files, by_name
from test_readers import HARDWARE, write_lif
from wormscore import checkreading as K
from wormscore import flatfield as FF
from wormscore import pipeline, readers, rescan, server
from wormscore.demo import make_demo
from wormscore.files import read_csv

EDT = dt.timezone(dt.timedelta(hours=-4))
NEW = '<TimeStampList NumberOfTimeStamps="2">1dd5ce2b05779b0 1dd5ce2b2a73610 </TimeStampList>'
OLD = ('<TimeStampList><TimeStamp HighInteger="30758066" LowInteger="1592197632"/>'
       '<TimeStamp HighInteger="30758066" LowInteger="1692197632"/></TimeStampList>')


@pytest.fixture
def edt(monkeypatch):
    monkeypatch.setattr(readers, "LOCAL_ZONE", EDT)


def one_lif(path, extra=""):
    rng = np.random.default_rng(0)
    write_lif(path, [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))],
              hardware=HARDWARE + extra)
    return path


def test_the_start_time_is_read_in_the_scanning_computers_time_zone(tmp_path, edt):
    """The owner's SP8 file: LAS X shows 10/15/2026 4:20:58 PM (US Eastern, summer time), Bio-Formats
    2026-10-15T20:20:58 (UTC)."""
    img = readers.leica_images(one_lif(tmp_path / "new.lif", NEW), K.default_settings())[0]
    assert img["acquired"] == "2026-10-15 16:20:58 -04:00"
    assert FF.acquired_day(img["acquired"]) == FF.scanned_day(img["acquired"]) == "2026-10-15"
    assert FF.imaging_date(img["acquired"]) == "2026-10-15"
    old = readers.leica_images(one_lif(tmp_path / "old.lif", OLD), K.default_settings())[0]
    assert old["acquired"] == "2019-08-17 00:15:15 -04:00"          # 04:15:15 UTC: the day before in New York
    for name, extra in (("none.lif", ""), ("empty.lif", '<TimeStampList NumberOfTimeStamps="0"></TimeStampList>'),
                        ("bad.lif", '<TimeStampList NumberOfTimeStamps="1">zz</TimeStampList>'),
                        ("zero.lif", '<TimeStampList NumberOfTimeStamps="1">0</TimeStampList>')):
        assert readers.leica_images(one_lif(tmp_path / name, extra), K.default_settings())[0]["acquired"] == "", name


def test_the_time_zone_is_the_computers_own_by_default(tmp_path):
    img = readers.leica_images(one_lif(tmp_path / "new.lif", NEW), K.default_settings())[0]
    utc = dt.datetime(2026, 10, 15, 20, 20, 58, tzinfo=dt.timezone.utc)
    assert img["acquired"][:19] == utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def test_check_the_reading_shows_the_date_scan_data_records(tmp_path, edt):
    text = K.check_reading(one_lif(tmp_path / "new.lif", NEW))
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("Acquired"))
    assert line.split(None, 1)[1] == "2026-10-15 16:20:58 -04:00"
    assert "Start Time (this computer's time zone)" in text
    undated = K.check_reading(one_lif(tmp_path / "none.lif"))
    assert "not recorded in the file" in undated and "Start Time" not in undated


# ------------------------------------------------------------------ existing projects (flat-field)

# The demo's files read as Leica files: first as every version before this one read them (no date), then
# with the date the new reader gives. a/ and b/ are slide images of the two days.
DATES = {"data/demo_control_01.ome.tif": "2026-03-01 10:00:00 -05:00", "data/demo_mutant_01.ome.tif": "2026-03-01 11:00:00 -05:00",
         "data/demo_control_02.ome.tif": "2026-03-05 15:00:00 -05:00", "data/demo_mutant_02.ome.tif": "2026-03-05 16:00:00 -05:00",
         "a/slide_0.ome.tif": "2026-03-01 09:00:00 -05:00", "a/slide_1.ome.tif": "2026-03-01 09:05:00 -05:00",
         "b/slide_0.ome.tif": "2026-03-05 14:00:00 -05:00", "b/slide_1.ome.tif": "2026-03-05 14:05:00 -05:00"}


@pytest.fixture
def leica(monkeypatch):
    """state["dated"]: whether the reader gives Leica files their date (this version) or not (before)."""
    state = {"dated": False}
    real = readers.images_in

    def images_in(path, cfg, source=None):
        found = real(path, cfg, source)
        for img in found:
            img["format"] = "Leica LIF"
            img["acquired"] = DATES.get(f"{Path(path).parent.name}/{Path(path).name}", "") if state["dated"] else ""
        return found
    monkeypatch.setattr(readers, "images_in", images_in)
    return state


def existing(root):
    p = make_demo(root / "p", n_per_group=2, **QUIET)
    pipeline.inventory(p, **QUIET)
    slides(root / "ref" / "a")
    slides(root / "ref" / "b", level=1000, seed=1)
    FF.measure(p, root / "ref", **QUIET)
    pipeline.prepare(p, **QUIET)
    return p


LINE = ("the flat-field imaging day of 4 coded Leica images is now read from the file; choose the slide images "
        "again so theirs are read the same way")


def test_an_existing_leica_project_is_unchanged_until_it_scans_again(tmp_path, leica):
    p = existing(tmp_path)
    assert {r["day"] for r in FF.summary(p)["sessions"]} == {""}
    key, blind = (p / "key" / "key.csv").read_bytes(), blind_files(p)
    sessions = (p / "outputs" / "flat_field" / "sessions.csv").read_bytes()
    leica["dated"] = True                                              # the update
    pipeline.prepare(p, **QUIET)
    assert (p / "key" / "key.csv").read_bytes() == key and blind_files(p) == blind
    FF.measure(p, tmp_path / "ref", **QUIET)                           # slide images chosen again: still undated
    assert (p / "outputs" / "flat_field" / "sessions.csv").read_bytes() == sessions
    assert rescan.scan_changes(p)["lines"] == []


def test_a_new_scan_reads_the_day_says_so_and_changes_nothing_until_the_slide_images_are_chosen_again(tmp_path, leica):
    p = existing(tmp_path)
    before, blind = by_name(p), blind_files(p)
    leica["dated"] = True
    pipeline.inventory(p, **QUIET)
    days = {Path(r["local_path"]).name: r["acquired_day"] for r in read_csv(p / "outputs" / "inventory.csv")}
    assert set(days.values()) == {"2026-03-01", "2026-03-05"}
    assert LINE in rescan.scan_changes(p)["lines"]
    said = []
    pipeline.prepare(p, log=said.append)
    after = by_name(p)
    assert blind_files(p) == blind                                     # the same correction: the one session
    assert {r["flat_field"] for r in after.values()} == {r["flat_field"] for r in before.values()}
    assert {r["imaging_session"].split(", ")[1] for r in after.values()} == {"no date"}   # the session used
    assert any(LINE in s for s in said)
    assert LINE in rescan.scan_changes(p)["lines"]                     # until the slide images are chosen again
    FF.measure(p, tmp_path / "ref", **QUIET)                           # now with their days
    assert sorted(s["day"] for s in FF.summary(p)["sessions"]) == ["2026-03-01", "2026-03-05"]
    assert rescan.scan_changes(p)["lines"] == []                       # done: the line goes at once
    said = []
    pipeline.prepare(p, log=said.append)
    assert {r["flat_field"] for r in by_name(p).values()} == {"same session"}
    assert {r["imaging_session"].split(", ")[1] for r in by_name(p).values()} == {"2026-03-01", "2026-03-05"}
    assert not [s for s in said if s.startswith("Warning")]
    assert rescan.scan_changes(p)["lines"] == []
    pipeline.inventory(p, **QUIET)
    assert rescan.scan_changes(p)["lines"] == []


ZONE_LINE = ("the flat-field imaging day of 2 coded images changes: this computer is in another time zone "
             "than the one that ran Scan data before; scan there, or choose the slide images again")


def test_a_scan_in_another_time_zone_says_that_days_move(tmp_path, leica, monkeypatch):
    """Leica dates are given in the scanning computer's time zone. A scan on a computer in another zone
    can move an image to the day before or after; the Build card says so (found by review)."""
    leica["dated"] = True
    p = existing(tmp_path)                                             # scanned, measured and built with dates
    assert rescan.scan_changes(p)["lines"] == []
    utc = {k: v.replace(" 10:00:00 -05:00", " 15:00:00 +00:00").replace(" 11:00:00 -05:00", " 16:00:00 +00:00")
                .replace(" 15:00:00 -05:00", " 20:00:00 +00:00").replace(" 16:00:00 -05:00", " 21:00:00 +00:00")
           for k, v in DATES.items()}
    utc["data/demo_control_02.ome.tif"] = "2026-03-06 01:00:00 +00:00"     # 20:00 on 5 March in New York
    utc["data/demo_mutant_02.ome.tif"] = "2026-03-06 02:00:00 +00:00"
    monkeypatch.setitem(globals(), "DATES", utc)
    pipeline.inventory(p, **QUIET)
    assert rescan.scan_changes(p)["lines"] == [ZONE_LINE]


def test_a_new_scan_after_scoring_keeps_the_coded_images_as_scored(tmp_path, leica):
    p = existing(tmp_path)
    blind = blind_files(p)
    pr = server.Project(p)
    for r in read_csv(p / "key" / "key.csv"):
        pr.save_score(r["code"], dict(SCORE))
    leica["dated"] = True
    pipeline.inventory(p, **QUIET)
    assert LINE in rescan.scan_changes(p)["lines"]
    said = []
    pipeline.prepare(p, log=said.append)
    assert blind_files(p) == blind
    assert not any("this build changes the flat-field correction" in s for s in said)
