"""The scan zoom of a Zeiss laser scanning microscope (1.26.20). ZEN writes it as LaserScanInfo/ZoomX; the
first element named Zoom, read before, is a detector's and is 1 whatever the scan zoom. Found by holding
wormscore against what datasets' authors wrote (Zenodo 7994589: "scan magnification: 0.6"; Zenodo
4662053's 0.04 um pixels at 63x need zoom 3). Zoom is used only by the flat-field correction, so an
existing project corrects as before until it runs Scan data again, and images already scored keep the
correction they were scored with."""
import csv
import re
from pathlib import Path

import pytest

from test_acquisition_settings import zeiss_confocal
from test_flatfield import slides
from test_flatfield_dates import QUIET, SCORE, blind_files, by_name
from wormscore import config as C
from wormscore import flatfield as FF
from wormscore import pipeline, readers, rescan, server
from wormscore.demo import make_demo
from wormscore.files import read_csv


def test_the_scan_zoom_is_read_not_the_detectors():
    import xml.etree.ElementTree as ET
    cfg = C.merge(C.DEFAULTS, {})
    root = zeiss_confocal(800)
    for info in root.iter("LaserScanInfo"):
        info.append(ET.fromstring("<ZoomX>0.59999999999999998</ZoomX>"))
    root.find("Metadata").append(ET.fromstring(
        "<Information><Instrument><Detectors><Detector><Zoom>1</Zoom></Detector></Detectors></Instrument></Information>"))
    meta = readers._zeiss_meta(root, cfg)
    assert (meta["zoom"], meta["_zoom_before"]) == ("0.6", "1")
    plain = readers._zeiss_meta(zeiss_confocal(800), cfg)                # a file without a scan zoom: as before
    assert plain["zoom"] == "" and "_zoom_before" not in plain


# ------------------------------------------------------------------ existing projects (flat-field)

@pytest.fixture
def zeiss(monkeypatch):
    """The demo's files read as Zeiss files taken at scan zoom 0.6: state["new"] is this version's reading
    (0.6), otherwise the detector's 1 that versions before read."""
    state = {"new": False}
    real = readers.images_in

    def images_in(path, cfg, source=None):
        found = real(path, cfg, source)
        for img in found:
            img["format"] = "Zeiss CZI"
            img["zoom"] = "0.6" if state["new"] else "1"
            if state["new"]:
                img["_zoom_before"] = "1"
        return found
    monkeypatch.setattr(readers, "images_in", images_in)
    return state


def old_inventory(p):
    """inventory.csv as a version before 1.26.20 wrote it: no scanned_with."""
    path = p / "outputs" / "inventory.csv"
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    fields = [k for k in rows[0] if k != "scanned_with"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def existing(root):
    p = make_demo(root / "p", n_per_group=2, **QUIET)
    pipeline.inventory(p, **QUIET)
    old_inventory(p)
    slides(root / "ref" / "a")
    FF.measure(p, root / "ref", **QUIET)
    pipeline.prepare(p, **QUIET)
    return p


LINE = ("the flat-field zoom of 4 coded Zeiss images is now the scan zoom; choose the slide images again so "
        "theirs are read the same way")


def test_an_existing_project_is_unchanged_until_it_scans_again(tmp_path, zeiss):
    p = existing(tmp_path)
    key, blind = (p / "key" / "key.csv").read_bytes(), blind_files(p)
    sessions = (p / "outputs" / "flat_field" / "sessions.csv").read_bytes()
    zeiss["new"] = True                                                # the update
    pipeline.prepare(p, **QUIET)
    assert (p / "key" / "key.csv").read_bytes() == key and blind_files(p) == blind
    FF.measure(p, tmp_path / "ref", **QUIET)                           # slide images chosen again: as before
    assert (p / "outputs" / "flat_field" / "sessions.csv").read_bytes() == sessions
    assert "scan_zoom" not in FF.summary(p)
    assert rescan.scan_changes(p)["lines"] == []


def test_a_new_scan_says_so_until_the_slide_images_are_chosen_again(tmp_path, zeiss):
    p = existing(tmp_path)
    zeiss["new"] = True
    pipeline.inventory(p, **QUIET)
    assert {r["zoom"] for r in read_csv(p / "outputs" / "inventory.csv")} == {"0.6"}
    assert LINE in rescan.scan_changes(p)["lines"]
    pipeline.prepare(p, **QUIET)
    pipeline.inventory(p, **QUIET)
    assert LINE in rescan.scan_changes(p)["lines"]                     # also after a build
    FF.measure(p, tmp_path / "ref", **QUIET)
    assert FF.summary(p)["scan_zoom"] is True
    assert rescan.scan_changes(p)["lines"] == []
    said = []
    pipeline.prepare(p, log=said.append)
    assert {r["flat_field"] for r in by_name(p).values()} == {"same session"}
    assert all(re.search(r"(settings | \| )0\.6$", r["imaging_session"]) for r in by_name(p).values())
    assert not [s for s in said if s.startswith("Warning")]


def test_images_already_scored_keep_their_correction_through_every_build(tmp_path, zeiss):
    p = existing(tmp_path)
    blind = blind_files(p)
    pr = server.Project(p)
    for r in read_csv(p / "key" / "key.csv"):
        pr.save_score(r["code"], dict(SCORE))
    zeiss["new"] = True
    pipeline.inventory(p, **QUIET)
    assert LINE in rescan.scan_changes(p)["lines"]
    for _ in range(2):
        said = []
        pipeline.prepare(p, log=said.append)
        assert blind_files(p) == blind
        assert not any("this build changes the flat-field correction" in s for s in said)
        assert all(re.search(r"(settings | \| )1$", r["imaging_session"]) for r in by_name(p).values())


def test_a_new_project_that_measures_slide_images_before_its_first_scan_reads_the_scan_zoom(tmp_path, zeiss):
    """Found by review: without an inventory the slide images got the old zoom, and no image matched them."""
    zeiss["new"] = True
    p = make_demo(tmp_path / "p", n_per_group=2, **QUIET)
    slides(tmp_path / "ref" / "a")
    FF.measure(p, tmp_path / "ref", **QUIET)
    assert FF.summary(p)["scan_zoom"] is True
    pipeline.inventory(p, **QUIET)
    pipeline.prepare(p, **QUIET)
    assert {r["flat_field"] for r in by_name(p).values()} == {"same session"}
    assert rescan.scan_changes(p)["lines"] == []


def test_a_zoom_that_did_not_change_is_not_reported(tmp_path, monkeypatch):
    """Found by review: a Zeiss file without a scan zoom whose Zoom is 2 (a zoom microscope) reads 2 before
    and after; the line counted it."""
    real = readers.images_in

    def images_in(path, cfg, source=None):
        found = real(path, cfg, source)
        for img in found:
            img["format"], img["zoom"] = "Zeiss CZI", "2"
        return found
    monkeypatch.setattr(readers, "images_in", images_in)
    p = existing(tmp_path)
    pipeline.inventory(p, **QUIET)
    assert rescan.scan_changes(p)["lines"] == []
