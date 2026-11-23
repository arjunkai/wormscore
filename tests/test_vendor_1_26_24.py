"""1.26.24: what NIS-Elements Viewer and OlyVIA showed for the sample files and wormscore did not read.

- An old Nikon file's excitation and emission in its channel's filter record (Exception_2.nd2: PSM_GFP,
  488/520 in NIS-Elements Viewer; Exception61.nd2: "488 nm", 488.0/520.0). The nd2 library renames the
  XML element that holds them, so they were lost and the reporter never found.
- The Nikon model's name, camera binning, an A1's pinhole and scan speed, the Olympus pinhole.
- A time with its own offset shown with it (an FV4000's +08:00, which OlyVIA shows in this computer's zone).
- Check the reading: "not calibrated in the file", NIS-Elements' own names for the camera values.
- The Methods draft's table of each settings group's imaging parameters.
Each new reading rule has its level (readers.as_1_26_23), so a project scanned before reads as then."""
import csv
import re
from pathlib import Path

import pytest

from wormscore import checkreading as K
from wormscore import config as C
from wormscore import flatfield as FF
from wormscore import methods
from wormscore import readers as R

DATA = Path(__file__).parent / "oir_metadata"
GFP = C.merge(C.DEFAULTS, {})


# ------------------------------------------------------------------ Nikon filter record

def plane(name="PSM_GFP", ex=(4, 488), em=(4, 520), filters=1, comp=1):
    """A picture-plane record as nd2 0.12 gives it for a file saved by NIS-Elements 2.30 (Exception_2.nd2):
    each Wavelength element renamed to i0000000001."""
    def spectrum(points):
        return {"Count": len(points), "Point": {f"Point{k}": {"Type": t, "i0000000001": w, "TValue": 0.0}
                                                for k, (t, w) in enumerate(points)}}
    one = {"Name": name, "Placement": 3, "ExcitationSpectrum": spectrum([ex] if isinstance(ex[0], int) else ex),
           "EmissionSpectrum": spectrum([em] if isinstance(em[0], int) else em)}
    return {"CompCount": comp, "Description": name,
            "FilterPath": {"Count": filters, "Filter": {f"i{k:010}": one for k in range(filters)}}}


class FakeND2:
    def __init__(self, planes):
        self.planes = planes

    def unstructured_metadata(self, include=None):
        if self.planes is None:
            raise ValueError("no such block")
        return {"ImageMetadataSeq|0": {"PicturePlanes": {"Count": len(self.planes),
                                                          "Plane": {str(i): p for i, p in enumerate(self.planes)}}}}


def test_an_old_nikon_files_filter_peak_gives_the_channels_wavelengths():
    assert R._nikon_filter_peaks(FakeND2([plane()]), 1) == {0: (488.0, 520.0)}
    two = R._nikon_filter_peaks(FakeND2([plane(), plane("PSM_Rhod", (4, 568), (4, 630))]), 2)
    assert two == {0: (488.0, 520.0), 1: (568.0, 630.0)}


def test_a_filter_band_or_an_unclear_record_is_never_read_as_a_line():
    assert R._nikon_filter_peaks(FakeND2([plane(ex=[(2, 465), (3, 495)], em=[(2, 515), (3, 555)])]), 1) == {}   # a cube's band
    assert R._nikon_filter_peaks(FakeND2([plane(ex=(4, 0), em=(4, 0))]), 1) == {}           # nothing recorded
    assert R._nikon_filter_peaks(FakeND2([plane(ex=[(4, 488), (4, 561)])]), 1) == {0: (None, 520.0)}   # two peaks
    assert R._nikon_filter_peaks(FakeND2([plane(filters=2)]), 1) == {}                      # two filters
    assert R._nikon_filter_peaks(FakeND2([plane(comp=3)]), 1) == {}                         # a colour camera
    assert R._nikon_filter_peaks(FakeND2([plane()]), 2) == {}                               # another channel count
    assert R._nikon_filter_peaks(FakeND2(None), 1) == {}                                    # any error: as before
    assert R._nikon_filter_peaks(FakeND2([{"FilterPath": "not a record"}]), 1) == {}


def test_the_found_reporter_is_matched_as_any_other_channel():
    ex, em = R._nikon_filter_peaks(FakeND2([plane()]), 1)[0]
    assert R.marker_from_named("PSM_GFP", None, ex, False, GFP, em) == "MARKER"
    assert R.marker_from_named("PSM_GFP", None, None, False, GFP, None) == "OTHER"          # as 1.26.23 read it


# ------------------------------------------------------------------ Nikon scan settings and camera names

A1 = ("Nikon A1plus\n\n{Scanner Selection}: Galvano\n{Pinhole Size(um)}: 12.77\n{Scanner Zoom}: 8.000\n"
      "{Scan Speed}: 0.125\n{Line Average Mode}: None\nStim1\t{Type}: Nothing\n\t{Scan Speed}: 1\n")
CAMERA = ("Photometrics CoolSNAP HQ2\n\nCamera Type: CoolSnap HQ2\nBinning: 4x4\nExposure: Triggered\n"
          "Readout Speed: 10 MHz\nCalibrated Gain: 1 x\nVisual Gain: Off\nTemperature: -29.6C\n")


def test_binning_pinhole_and_scan_speed_are_read_as_nis_shows_them():
    assert R._nikon_scan("", A1, "DiO", 1) == "pinhole 12.77 um; scan speed 0.125"       # not the stimulation's speed
    assert R._nikon_scan("", CAMERA, "488 nm", 1) == "binning 4x4"
    assert R._nikon_scan("", "Andor Zyla\nExposure: 100 ms\n", "GFP", 1) == ""
    with R.as_1_26_23():
        assert R._nikon_scan("", A1, "DiO", 1) == ""


def test_the_camera_names_line_up_with_the_settings_text():
    settings = R._nikon_settings("", CAMERA, "488 nm", 1)
    assert settings == "exposure Triggered; gain 1 x; gain Off"
    fields = R._nikon_camera_fields("", CAMERA, "488 nm", 1)
    assert fields == "Exposure/Calibrated Gain/Visual Gain"
    parts = K.reporter_parts(settings, "Nikon ND2", "Nikon Roper", fields)
    assert [(what, value) for what, value, _ in parts] == [("Exposure mode", "Triggered"), ("Calibrated Gain", "1 x"),
                                                           ("Visual Gain", "Off")]
    # names that do not fit the pieces leave the labels as they were
    plain = K.settings_parts(settings)
    assert K.reporter_parts(settings, "Nikon ND2", "x", "Gain/Exposure/Gain") == plain
    assert K.reporter_parts(settings, "Nikon ND2", "x", "") == plain
    # the value is taken whole: NIS shows "Conversion Gain: Gain 3" (BF007.nd2)
    andor = K.reporter_parts("exposure 50 ms; multiplier 300; gain Gain 3; 488nm@20", "Nikon ND2", "x",
                             "Exposure/Multiplier/Conversion Gain")
    assert [(w, v) for w, v, _ in andor] == [("Exposure", "50 ms"), ("Multiplier", "300"), ("Conversion Gain", "Gain 3"),
                                             ("Laser 488 nm", "20")]


def test_a_camera_values_name_is_its_whole_field_and_never_other_text_on_its_line():
    andor = "Andor Zyla\nEM Gain Multiplier: 300\nReadout Speed: 10 MHz Gain: 1x\nExposure: 100 ms\n"
    settings = R._nikon_settings("", andor, "GFP", 1)
    assert settings == "multiplier 300; gain 1x; exposure 100 ms"
    assert R._nikon_camera_fields("", andor, "GFP", 1) == "EM Gain Multiplier/Gain/Exposure"   # not "MHz Gain"
    labels = [what for what, _, _ in K.reporter_parts(settings, "Nikon ND2", "Nikon Andor Zyla",
                                                      R._nikon_camera_fields("", andor, "GFP", 1))]
    assert labels == ["EM Gain Multiplier", "Gain", "Exposure"]


def test_an_odd_scan_value_is_left_out_never_fatal():
    odd = "Nikon A1plus\n{Pinhole Size(um)}: 1.2.3\n{Scan Speed}: 1/2\nBinning: 2x2\n"
    assert R._nikon_scan("", odd, "DiO", 1) == "binning 2x2"


def test_only_nikons_own_confocals_name_a_model():
    assert R.NIKON_CONFOCAL.match("Nikon A1plus") and R.NIKON_CONFOCAL.match("Nikon AX R")
    assert R.NIKON_CONFOCAL.match("Nikon C2plus") and R.NIKON_CONFOCAL.match("Nikon A1 LFOV")
    assert not R.NIKON_CONFOCAL.match("Nikon DS-Qi2") and not R.NIKON_CONFOCAL.match("Nikon DS-Fi3")
    assert not R.NIKON_CONFOCAL.match("Nikon A10")


def test_the_model_is_shown_and_the_microscope_name_stays():
    img = {"format": "Nikon ND2", "location_in_file": "whole file", "system": "Nikon Nikon A1plus",
           "model": "Nikon A1plus", "acquired": "", "objective": "", "zoom": "", "pixel_um": 1.0, "z_step_um": 0.0,
           "n_z": 1, "n_t": 1, "width": 8, "height": 8, "bits": 12, "channel_names": ["DiO"],
           "channel_roles": ["MARKER"], "settings": "", "flags": [f"pixel size {R.NOT_CALIBRATED} (1 um per pixel is a placeholder)"]}
    lines = K.describe(img, K.default_settings(), Path("x.nd2"))
    assert "  Microscope        Nikon A1plus" in lines
    assert "  Pixel size        not calibrated in the file" in lines
    assert any("pixel size is not calibrated" in x for x in lines)                         # the flag is kept


# ------------------------------------------------------------------ Olympus pinhole and a time's own offset

def with_pinhole(text, um="127.0"):
    head = '<commonphase:channel id="6928a7fe-c13b-454a-8adb-8e800003571d" order="2">'
    assert text.count(head) == 1
    return text.replace(head, head + f"<root:productData><fvLsmimage:pinholeDiameter>{um}</fvLsmimage:pinholeDiameter>"
                        "</root:productData>")


def test_the_olympus_reporters_pinhole_is_read_into_scan_settings():
    image = (DATA / "hek293.image.xml").read_text(encoding="ascii")
    assert R._oir_meta(with_pinhole(image), [], GFP)["scan_settings"] == "pinhole 127 um"   # OlyVIA: 127 um
    assert R._oir_meta(image, [], GFP)["scan_settings"] == ""
    with R.as_1_26_23():
        assert R._oir_meta(with_pinhole(image), [], GFP)["scan_settings"] == ""


def test_a_time_with_its_own_offset_keeps_its_clock_and_day(monkeypatch):
    shown, before = R._iso_acquired("2024-12-02T09:37:02.725+08:00")     # an FV4000 in Asia
    assert (shown, before) == ("2024-12-02 09:37:02 (UTC+08:00)", "2024-12-02 09:37:02")
    assert FF.acquired_day(shown) == FF.scanned_day(shown) == "2024-12-02"
    assert not re.search(r" [+-]\d\d:\d\d$", shown)          # never taken for a time given in this computer's zone
    where = K.VENDOR["Olympus OIR"][2]
    assert "this computer's time zone" in where["own_zone"]


def oir_image(acquired):
    return {"format": "Olympus OIR", "location_in_file": "whole file", "system": "Olympus FV4000", "acquired": acquired,
            "objective": "10x", "zoom": "", "pixel_um": 1.0, "z_step_um": 0.0, "n_z": 1, "n_t": 1, "width": 8,
            "height": 8, "bits": 16, "channel_names": ["CH2 FITC"], "channel_roles": ["MARKER"],
            "settings": "488nm@0.1%", "flags": []}


def test_check_reading_says_olyvia_converts_only_for_a_time_with_its_own_offset():
    note = K.VENDOR["Olympus OIR"][2]["own_zone"]
    lines = lambda acquired: K.describe(oir_image(acquired), K.default_settings(), Path("x.oir"))
    assert f"  {note}" in lines("2024-12-02 09:37:02 (UTC+08:00)")
    assert f"  {note}" not in lines("2019-12-16 11:45:40 -05:00")         # a UTC time given in this computer's zone
    assert f"  {note}" not in lines("2019-12-16 16:45:40")


# ------------------------------------------------------------------ the reading levels

def test_a_project_scanned_by_1_26_23_reads_its_slide_images_as_then(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    for version, current, lasaf in (("1.26.23", False, True), ("1.26.24", True, True), ("1.26.22", False, False)):
        (out / "inventory.csv").write_text(f"local_path,scanned_with\na.nd2,{version}\n", encoding="utf-8")
        with FF.reading(tmp_path):
            assert (not R._before_1_26_24()) == current, version
        assert FF.lasaf_from_scan(tmp_path) == lasaf, version
        assert methods.scanned_by_1_26_24(tmp_path) == current, version
    (out / "inventory.csv").write_text("local_path,scanned_with\na.nd2,edited\n", encoding="utf-8")
    assert not methods.scanned_by_1_26_24(tmp_path)                     # no version: not scanned by 1.26.24
    (out / "inventory.csv").unlink()
    assert not methods.scanned_by_1_26_24(tmp_path)


# ------------------------------------------------------------------ the Methods table

def write(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def project_with(tmp_path, version):
    (tmp_path / "outputs").mkdir(exist_ok=True)
    inv = [{"local_path": f"{n}.nd2", "location_in_file": "whole file", "scanned_with": version, "zoom": z,
            "scan_settings": s, "model": "Nikon A1plus", "format": "Nikon ND2", "flags": "",
            "camera_fields": f} for n, z, s, f in (("a", "8", "pinhole 12.77 um", ""),
                                                   ("b", "8", "pinhole 12.77 um", ""),
                                                   ("c", "4", "", "Exposure/Calibrated Gain/Visual Gain"))]
    write(tmp_path / "outputs" / "inventory.csv", inv)
    key = [{"local_path": f"{n}.nd2", "location_in_file": "whole file", "system": "Nikon Nikon A1plus",
            "settings_group": g, "settings": st, "objective": "Plan Apo 60x", "pixel_um": "0.0518",
            "z_step_um": z, "n_z": nz, "repeat_of": ""}
           for n, g, st, z, nz in (("a", "A", "488nm@1.5 HV 114; line average 2", "0.5", "11"),
                                   ("b", "A", "488nm@1.5 HV 114; line average 2", "0.5", "11"),
                                   ("c", "B", "exposure Triggered; gain 1 x; gain Off", "0", "1"))]
    key.append({**key[0], "repeat_of": "x1"})                 # a hidden repeat is not counted twice
    return key


def test_the_methods_table_gives_each_settings_groups_imaging_parameters(tmp_path):
    table = methods.imaging_table(tmp_path, project_with(tmp_path, "1.26.24"))
    assert table[0] == "## Imaging parameters per settings group"
    a = next(x for x in table if x.startswith("| A |"))
    b = next(x for x in table if x.startswith("| B |"))
    assert a == ("| A | Nikon A1plus | Plan Apo 60x | 8 | Laser 488 nm 1.5 | Detector voltage (HV) 114 | not recorded "
                 "| line average 2 | pinhole 12.77 um | 0.0518 | 0.5 | 2 |")
    # the camera values under NIS-Elements' names, as Check the reading shows them: Visual Gain is not a detector gain
    assert b == ("| B | Nikon A1plus | Plan Apo 60x | 4 | not recorded | Calibrated Gain 1 x; Visual Gain Off "
                 "| Exposure mode Triggered | not recorded | not recorded | 0.0518 | one plane | 1 |")
    assert all(cells(x) == cells(table[2]) for x in table if x.startswith("| "))   # no cell breaks the table


def cells(row):
    return len(re.split(r"(?<!\\)\|", row))


def test_the_table_says_what_the_file_does_not_settle(tmp_path):
    key = project_with(tmp_path, "1.26.24")
    rows = list(csv.DictReader(open(tmp_path / "outputs" / "inventory.csv", encoding="utf-8")))
    rows[2]["flags"] = f"pixel size {R.NOT_CALIBRATED} (1 um per pixel is a placeholder)"
    write(tmp_path / "outputs" / "inventory.csv", rows)
    key[2]["pixel_um"] = "1.0"
    key[2]["n_z"] = "x"                                                  # hand-edited: not fatal
    row = next(x for x in methods.imaging_table(tmp_path, key) if x.startswith("| B |"))
    assert "| not calibrated in the file | one plane |" in row
    key[1]["n_z"], key[1]["z_step_um"] = "1", "0"                         # a group with one plane and stacks
    row = next(x for x in methods.imaging_table(tmp_path, key) if x.startswith("| A |"))
    assert "| 0.5; one plane |" in row


def test_a_project_scanned_before_reports_as_before(tmp_path):
    assert methods.imaging_table(tmp_path, project_with(tmp_path, "1.26.23")) == []
    assert methods.imaging_table(tmp_path, []) == []


def test_a_bar_in_a_files_text_does_not_break_the_table(tmp_path):
    key = project_with(tmp_path, "1.26.24")
    key[2]["objective"] = "Plan | Apo"
    table = methods.imaging_table(tmp_path, key)
    row = next(x for x in table if x.startswith("| B |"))
    assert r"Plan \| Apo" in row and cells(row) == cells(table[2])
