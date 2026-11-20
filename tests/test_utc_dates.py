"""Acquisition times written in UTC (1.26.20). ZEN and the FV3000 software 2.x write the time with a "Z"
("2019-12-16T16:45:40.311Z"); OlyVIA shows that FV3000 file as 12/16/2019 11:45:40 AM on a computer in US
Eastern time. wormscore showed 16:45:40 as written, so an evening session in the Americas (or a morning one
in Asia) had the next or the previous day. Such a time is now given in the scanning computer's time zone,
as Leica start times are. A time with its own offset (older FluoView) or with no zone is given as before.
An existing project corrects, measures and reports as before until it runs Scan data again."""
import datetime as dt
from pathlib import Path

import pytest
import tifffile

from test_flatfield import slides
from test_flatfield_dates import QUIET, SCORE, blind_files, by_name
from wormscore import checkreading as K
from wormscore import flatfield as FF
from wormscore import pipeline, readers, rescan, server
from wormscore.demo import make_demo
from wormscore.files import read_csv

EST = dt.timezone(dt.timedelta(hours=-5))


@pytest.fixture
def est(monkeypatch):
    monkeypatch.setattr(readers, "LOCAL_ZONE", EST)


def test_a_utc_time_is_given_in_the_computers_time_zone(est):
    # the FV3000 bead file of figshare 11512431, as OlyVIA shows it on a computer in US Eastern time
    assert readers._iso_acquired("2019-12-16T16:45:40.311Z") == ("2019-12-16 11:45:40 -05:00", "2019-12-16 16:45:40")
    late = readers._iso_acquired("2026-03-06T01:00:00.1234567Z")[0]               # ZEN: seven decimals
    assert late == "2026-03-05 20:00:00 -05:00"
    assert FF.acquired_day(late) == FF.scanned_day(late) == FF.imaging_date(late) == "2026-03-05"
    # a time with its own offset is the lab's clock already; no zone, or no time: as before
    for text in ("2015-10-28T16:29:23.944-04:00", "2026-03-05T10:00:00", "2026-03-05", "", "Z", "garbageZ"):
        assert readers._iso_acquired(text) == (text[:19].replace("T", " "),) * 2, text


def test_the_time_zone_is_the_computers_own_by_default():
    utc = dt.datetime(2019, 12, 16, 16, 45, 40, tzinfo=dt.timezone.utc)
    assert readers._iso_acquired("2019-12-16T16:45:40Z")[0][:19] == utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def stamp(path, when):
    text = tifffile.tiffcomment(str(path))
    i = text.index(">", text.index("<Image ")) + 1
    tifffile.tiffcomment(str(path), text[:i] + f"<AcquisitionDate>{when}</AcquisitionDate>" + text[i:])
    return path


def test_scan_data_and_check_the_reading_give_the_local_time(tmp_path, est):
    p = make_demo(tmp_path / "p", n_per_group=1, **QUIET)
    f = stamp(sorted((p / "data").glob("*.ome.tif"))[0], "2026-03-06T01:00:00.5Z")
    img = readers.images_in(f, K.default_settings())[0]
    assert img["acquired"] == "2026-03-05 20:00:00 -05:00" and img["_acquired_before"] == "2026-03-06 01:00:00"
    line = next(ln for ln in K.check_reading(f).splitlines() if ln.strip().startswith("Acquired"))
    assert line.split(None, 1)[1] == "2026-03-05 20:00:00 -05:00"
    pipeline.inventory(p, **QUIET)
    row = next(r for r in read_csv(p / "outputs" / "inventory.csv") if Path(r["local_path"]).name == f.name)
    assert (row["acquired"], row["acquired_day"]) == ("2026-03-05 20:00:00 -05:00", "2026-03-05")
    assert "_acquired_before" not in row


# ------------------------------------------------------------------ existing projects (flat-field)

# In UTC. Read before this version, the second day's images and slide images are on 6 March; in US Eastern
# time they are on the evening of 5 March.
TIMES = {"data/demo_control_01.ome.tif": "2026-03-01T15:00:00Z", "data/demo_mutant_01.ome.tif": "2026-03-01T16:00:00Z",
         "data/demo_control_02.ome.tif": "2026-03-06T01:00:00Z", "data/demo_mutant_02.ome.tif": "2026-03-06T02:00:00Z",
         "a/slide_0.ome.tif": "2026-03-01T14:00:00Z", "a/slide_1.ome.tif": "2026-03-01T14:05:00Z",
         "b/slide_0.ome.tif": "2026-03-06T00:00:00Z", "b/slide_1.ome.tif": "2026-03-06T00:05:00Z"}


@pytest.fixture
def before(monkeypatch, est):
    """state["new"]: whether the reader gives UTC times in the computer's zone (this version) or not."""
    state = {"new": False}
    real = readers._iso_acquired
    monkeypatch.setattr(readers, "_iso_acquired", lambda t: real(t) if state["new"] else (real(t)[1],) * 2)
    return state


def existing(root):
    p = make_demo(root / "p", n_per_group=2, **QUIET)
    for name, when in TIMES.items():
        folder, file = name.split("/")
        target = p / "data" / file if folder == "data" else root / "ref" / folder / file
        if folder != "data" and not target.exists():
            slides(root / "ref" / "a")
            slides(root / "ref" / "b", level=1000, seed=1)
        stamp(target, when)
    pipeline.inventory(p, **QUIET)
    FF.measure(p, root / "ref", **QUIET)
    pipeline.prepare(p, **QUIET)
    return p


LINE = ("the flat-field imaging day of 2 coded images changes: the file records the time in UTC, now read in "
        "this computer's time zone; choose the slide images again so theirs are read the same way")


def test_an_existing_project_is_unchanged_until_it_scans_again(tmp_path, before):
    p = existing(tmp_path)
    assert sorted(s["day"] for s in FF.summary(p)["sessions"]) == ["2026-03-01", "2026-03-06"]
    key, blind = (p / "key" / "key.csv").read_bytes(), blind_files(p)
    sessions = (p / "outputs" / "flat_field" / "sessions.csv").read_bytes()
    before["new"] = True                                               # the update
    pipeline.prepare(p, **QUIET)
    assert (p / "key" / "key.csv").read_bytes() == key and blind_files(p) == blind
    FF.measure(p, tmp_path / "ref", **QUIET)                           # slide images chosen again: as before
    assert (p / "outputs" / "flat_field" / "sessions.csv").read_bytes() == sessions
    assert "utc_days" not in FF.summary(p)
    assert rescan.scan_changes(p)["lines"] == []


def test_a_new_scan_says_so_until_the_slide_images_are_chosen_again(tmp_path, before):
    p = existing(tmp_path)
    before["new"] = True
    pipeline.inventory(p, **QUIET)
    days = {Path(r["local_path"]).name: r["acquired_day"] for r in read_csv(p / "outputs" / "inventory.csv")}
    assert sorted(set(days.values())) == ["2026-03-01", "2026-03-05"]
    assert LINE in rescan.scan_changes(p)["lines"]
    FF.measure(p, tmp_path / "ref", **QUIET)                           # now read the same way
    assert FF.summary(p)["utc_days"] is True
    assert sorted(s["day"] for s in FF.summary(p)["sessions"]) == ["2026-03-01", "2026-03-05"]
    assert rescan.scan_changes(p)["lines"] == []
    said = []
    pipeline.prepare(p, log=said.append)
    assert {r["flat_field"] for r in by_name(p).values()} == {"same session"}
    assert {r["imaging_session"].split(", ")[1] for r in by_name(p).values()} == {"2026-03-01", "2026-03-05"}
    assert not [s for s in said if s.startswith("Warning")]
    pipeline.inventory(p, **QUIET)
    assert rescan.scan_changes(p)["lines"] == []


ZONE_LINE = ("the flat-field imaging day of 2 coded images changes: this computer is in another time zone than the "
             "one that ran Scan data before; scan there, or choose the slide images again")


def test_a_scan_in_another_time_zone_says_that_days_move(tmp_path, before, monkeypatch):
    before["new"] = True
    p = existing(tmp_path)
    assert rescan.scan_changes(p)["lines"] == []
    monkeypatch.setattr(readers, "LOCAL_ZONE", dt.timezone.utc)
    pipeline.inventory(p, **QUIET)
    assert rescan.scan_changes(p)["lines"] == [ZONE_LINE]


def test_a_new_scan_after_scoring_keeps_the_coded_images_as_scored(tmp_path, before):
    p = existing(tmp_path)
    blind = blind_files(p)
    pr = server.Project(p)
    for r in read_csv(p / "key" / "key.csv"):
        pr.save_score(r["code"], dict(SCORE))
    before["new"] = True
    pipeline.inventory(p, **QUIET)
    assert LINE in rescan.scan_changes(p)["lines"]
    pipeline.prepare(p, **QUIET)
    assert blind_files(p) == blind
    # and at every later build: key.csv keeps the date the image was scored with (found by review)
    assert {r["acquired"][-6:] for r in read_csv(p / "key" / "key.csv")} != {"-05:00"}
    said = []
    pipeline.prepare(p, log=said.append)
    assert blind_files(p) == blind
    assert not any("this build changes the flat-field correction" in s for s in said)


def test_the_line_stays_after_a_rebuild_until_the_slide_images_are_chosen_again(tmp_path, before):
    """Found by review: after one rebuild key.csv has the new date, and the line used to go although the
    slide images still had their UTC days."""
    p = existing(tmp_path)
    before["new"] = True
    pipeline.inventory(p, **QUIET)
    pipeline.prepare(p, **QUIET)
    pipeline.inventory(p, **QUIET)
    assert LINE in rescan.scan_changes(p)["lines"]
    FF.measure(p, tmp_path / "ref", **QUIET)
    assert rescan.scan_changes(p)["lines"] == []


# ------------------------------------------------------------------ the N-SIM's laser power (same release)

class _SimFile:
    def __init__(self, sample):
        self.sample = sample

    def unstructured_metadata(self, include=None):
        return {"ImageMetadataSeqLV|0": {"SLxPictureMetadata": {"PicturePlanes": {"SampleSetting": self.sample}}}}


def test_the_n_sim_laser_power_is_read():
    """As the N-SIM file of Zenodo 15046688 records it; its authors write "488nm of excitation with 30%
    laser power and 200ms of exposition"."""
    on = {"CameraSetting": {"PropertiesFast": {"NSimLaser405Power": 0.0, "NSimLaser488Power": 30.0,
                                               "NSimLaser561Power": 0.0, "NSimExLaserL1Power": 5.0}}}
    off = {"CameraSetting": {"PropertiesFast": {"NSimLaser561Power": 12.5}}}
    assert readers._nikon_sim_lasers(_SimFile([on, off]), 0) == ["488nm@30"]
    assert readers._nikon_sim_lasers(_SimFile([on, off]), 1) == ["561nm@12.5"]
    assert readers._nikon_sim_lasers(_SimFile({"0": on}), 0) == ["488nm@30"]
    assert readers._nikon_sim_lasers(_SimFile({}), 0) == []
    assert readers._nikon_sim_lasers(object(), 0) == []


class _Broken:
    def unstructured_metadata(self, include=None):
        raise OSError("a damaged chunk")


def test_the_n_sim_probe_never_fails_an_image():
    """Any error reading the camera settings leaves the image readable (found by review, 20 November 2026)."""
    assert readers._nikon_sim_lasers(_Broken(), 0) == []
