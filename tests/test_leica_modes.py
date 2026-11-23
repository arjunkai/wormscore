"""1.26.27: a STELLARIS detector's Operating Mode, as LAS X shows it.

Read off LAS X Office 1.4.11 for four public STELLARIS files (23 November 2026): OperatingType 1 is Analog
whatever AcquisitionModeName says (a HyD S saved as "PhotonCounting" is Analog in LAS X); OperatingType 2 is
Digital with AcquisitionMode 0 (PhotonIntegration) and Counting with AcquisitionMode 1 (PhotonCounting). An SP8
writes no OperatingType and LAS X shows no mode for it. A project scanned before reads as then."""
import xml.etree.ElementTree as ET

import numpy as np

from wormscore import checkreading as K
from wormscore import readers as R
from test_readers import HARDWARE, write_lif


def det(op, acq, name="HyD X 3"):
    attrs = f'Name="{name}" AcquisitionMode="{acq}"' + (f' OperatingType="{op}"' if op is not None else "")
    return ET.fromstring(f"<Detector {attrs}/>")


def test_the_modes_las_x_showed():
    assert R._leica_mode(det("1", "1", "HyD S 1")) == "Analog mode"         # saved as PhotonCounting
    assert R._leica_mode(det("1", "-1", "Trans PMT")) == "Analog mode"
    assert R._leica_mode(det("2", "0")) == "Digital mode"
    assert R._leica_mode(det("2", "1")) == "Counting mode"
    assert R._leica_mode(det(None, "1", "HyD SMD 2")) == ""                 # an SP8: LAS X shows none
    assert R._leica_mode(det("1", "0")) == "" and R._leica_mode(det("3", "1")) == ""     # not seen in LAS X


def stellaris(tmp_path, op="2", acq="1"):
    hw = HARDWARE.replace('<Detector Name="HyD 1" Channel="1" Gain="100" IsActive="1"/>',
                          f'<Detector Name="HyD 1" Channel="1" Gain="100" IsActive="1" OperatingType="{op}" '
                          f'AcquisitionMode="{acq}"/>')
    assert hw != HARDWARE
    rng = np.random.default_rng(0)
    path = tmp_path / f"stellaris_{op}_{acq}.lif"
    write_lif(path, [("Series001", (rng.integers(0, 4096, (2, 6, 8)), rng.integers(0, 4096, (2, 6, 8))))], hardware=hw)
    return path


def test_the_reporters_mode_is_in_its_scan_settings_from_1_26_27(tmp_path):
    im = R.leica_images(stellaris(tmp_path), K.default_settings())[0]
    assert im["scan_settings"].endswith("Counting mode")
    assert "mode" not in im["settings"]                       # the settings group stays as it was
    assert R.leica_images(stellaris(tmp_path, "2", "0"), K.default_settings())[0]["scan_settings"].endswith("Digital mode")
    for gate in (R.as_1_26_26, R.as_1_26_24):
        with gate():
            assert "mode" not in R.leica_images(stellaris(tmp_path), K.default_settings())[0].get("scan_settings", "")
    assert R.reading_as("1.26.26").__class__ is not type(R.reading_as("1.26.27"))


def test_an_image_in_another_mode_is_flagged(tmp_path, monkeypatch):
    # each mode is one setting: "Counting mode" and "Analog mode" were two kinds and never compared
    from test_lasaf import flags_for
    flags = flags_for(tmp_path, monkeypatch, ["400 Hz; Counting mode", "400 Hz; Counting mode", "400 Hz; Analog mode"])
    flagged = [f for f in flags if "scan settings differ" in f]
    assert flagged and all("(Analog mode here; most: Counting mode)" in f for f in flagged)
