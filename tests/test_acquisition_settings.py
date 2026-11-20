"""1.26.7: detector gain, laser power and bit depth are read for Zeiss, Nikon and OME-TIFF, and a project
that was already built is told what a new scan changes instead of changing silently.

The Zeiss confocal fixture is the reporter channel's block from "flp-17 L1 Head 1.czi" of the real-worm
demo (Zenodo 5602160, CC BY 4.0), shortened. The camera fixture follows the layout ZEN blue writes for a
widefield camera. The Nikon text follows the layout NIS-Elements writes for a spinning disk."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
import tifffile

from wormscore import config as C
from wormscore import home, readers
from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare, read_csv, scan_changes

quiet = lambda *a: None


def zeiss_confocal(gain):
    return ET.fromstring(f"""<ImageDocument><Metadata><Information><Image>
 <PixelType>Gray16</PixelType><ComponentBitCount>16</ComponentBitCount>
 <Dimensions><Channels>
  <Channel Id="1" Name="Ch1-T1">
   <Fluor>EBFP</Fluor><ExcitationWavelength>405.00000000000006</ExcitationWavelength>
   <LaserScanInfo><Averaging>2</Averaging></LaserScanInfo>
   <DetectorSettings><Binning>1x1</Binning><Gain>700</Gain><DigitalGain>1</DigitalGain><Offset>0</Offset></DetectorSettings>
   <LightSourcesSettings>
    <LightSourceSettings><Attenuation>0.90000000000000002</Attenuation><Wavelength>561</Wavelength></LightSourceSettings>
    <LightSourceSettings><Attenuation>0.99199999999999999</Attenuation><Wavelength>405.00000000000006</Wavelength></LightSourceSettings>
   </LightSourcesSettings>
  </Channel>
  <Channel Id="2" Name="T PMT-T1">
   <IlluminationType>Epifluorescence</IlluminationType><ContrastMethod>Other</ContrastMethod>
   <DetectorSettings><Gain>130</Gain><DigitalGain>1</DigitalGain></DetectorSettings>
  </Channel>
  <Channel Id="3" Name="ChS1-T2">
   <IlluminationType>Epifluorescence</IlluminationType><ContrastMethod>Fluorescence</ContrastMethod>
   <Fluor>EGFP</Fluor><ExcitationWavelength>488.00000000000006</ExcitationWavelength>
   <EmissionWavelength>503.50635050000022</EmissionWavelength>
   <AcquisitionMode>LaserScanningConfocalMicroscopy</AcquisitionMode>
   <LaserScanInfo><Averaging>2</Averaging></LaserScanInfo>
   <DetectorSettings><Binning>1x1</Binning><Gain>{gain}</Gain><DigitalGain>1</DigitalGain><Offset>0</Offset></DetectorSettings>
   <LightSourcesSettings>
    <LightSourceSettings><Attenuation>0.88</Attenuation><Wavelength>488.00000000000006</Wavelength></LightSourceSettings>
   </LightSourcesSettings>
  </Channel>
 </Channels></Dimensions>
</Image></Information>
<Scaling><Items><Distance Id="X"><Value>1.0e-07</Value></Distance><Distance Id="Z"><Value>5.0e-07</Value></Distance></Items></Scaling>
</Metadata></ImageDocument>""")


def zeiss_camera(bit_count="<ComponentBitCount>12</ComponentBitCount>"):
    return ET.fromstring(f"""<ImageDocument><Metadata><Information><Image>
 {bit_count}<PixelType>Gray16</PixelType>
 <Dimensions><Channels>
  <Channel Id="Channel:1">
   <ExcitationWavelength>488</ExcitationWavelength><EmissionWavelength>509</EmissionWavelength><Fluor>EGFP</Fluor>
   <AcquisitionMode>WideField</AcquisitionMode><ExposureTime>40000000</ExposureTime>
   <IlluminationType>Epifluorescence</IlluminationType><PixelType>Gray16</PixelType>{bit_count}
   <DetectorSettings><Binning>2,2</Binning></DetectorSettings>
   <LightSourcesSettings><LightSourceSettings><Intensity>40.00 %</Intensity></LightSourceSettings></LightSourcesSettings>
  </Channel>
  <Channel Id="Channel:2">
   <Fluor>TL DIC</Fluor><AcquisitionMode>WideField</AcquisitionMode><ExposureTime>25000000</ExposureTime>
   <IlluminationType>Transmitted</IlluminationType>{bit_count}
   <LightSourcesSettings><LightSourceSettings><Intensity>5.04 Volt</Intensity></LightSourceSettings></LightSourcesSettings>
  </Channel>
 </Channels></Dimensions>
</Image></Information></Metadata></ImageDocument>""")


def test_zeiss_gain_and_laser_come_from_the_reporter_channel():
    """The demo's real files were taken at gain 550 and 800 and all read as 'avg 2': one settings group."""
    cfg = C.merge(C.DEFAULTS, {})
    low, high = readers._zeiss_meta(zeiss_confocal(550), cfg), readers._zeiss_meta(zeiss_confocal(800), cfg)
    assert low["channel_roles"] == ["OTHER", "BF", "MARKER"]
    assert low["settings"] == "gain 550; 488nm@12.00%; avg 2"       # the reporter's laser, not the 405 and 561 nm of track 1
    assert high["settings"] == "gain 800; 488nm@12.00%; avg 2"
    assert low["bits"] == 16 and low["flags"] == []


def test_zeiss_camera_exposure_lamp_and_bit_depth():
    cfg = C.merge(C.DEFAULTS, {})
    meta = readers._zeiss_meta(zeiss_camera(), cfg)
    assert meta["channel_roles"] == ["MARKER", "BF"]
    assert meta["settings"] == "light 40.00 %; exposure 40 ms"
    assert meta["bits"] == 12 and meta["flags"] == []              # was 16: saturation at 65535, never reached


def test_zeiss_without_a_bit_depth_is_flagged_not_guessed():
    cfg = C.merge(C.DEFAULTS, {})
    meta = readers._zeiss_meta(zeiss_camera(bit_count=""), cfg)
    assert meta["bits"] == 16 and len(meta["flags"]) == 1 and "bit depth not recorded" in meta["flags"][0]
    assert "saturation_value in config.json" in meta["flags"][0]
    stated = C.merge(C.DEFAULTS, {"saturation_value": {"Zeiss": 4095}})
    assert readers._zeiss_meta(zeiss_camera(bit_count=""), stated)["flags"] == []


def write_ome(path, gain=None, bits=None, laser=None, seed=0):
    """A two-channel OME-TIFF (GFP, brightfield) with the detector and laser written as Bio-Formats does."""
    rng = np.random.default_rng(seed)
    data = rng.integers(90, 3000, (2, 12, 32, 32), dtype=np.uint16)
    tifffile.imwrite(path, data, ome=True, photometric="minisblack",
                     metadata={"axes": "CZYX", "PhysicalSizeX": 0.2, "PhysicalSizeY": 0.2, "PhysicalSizeZ": 1.0,
                               "Channel": {"Name": ["GFP", "Brightfield"]}})
    with tifffile.TiffFile(path) as f:
        xml = f.ome_metadata
    extra = (f'<LightSourceSettings ID="LightSource:0" Wavelength="488" Attenuation="{laser}"/>' if laser is not None else "") \
        + (f'<DetectorSettings ID="Detector:0" Gain="{gain}" Voltage="600"/>' if gain is not None else "")
    xml = xml.replace("<LightPath/>", extra + "<LightPath/>", 1)
    if bits:
        xml = xml.replace("<Pixels ", f'<Pixels SignificantBits="{bits}" ', 1)
    tifffile.imwrite(path, data.reshape(-1, 32, 32), description=xml, metadata=None, photometric="minisblack")
    return path


def test_ome_tiff_detector_and_laser_of_the_reporter_channel(tmp_path):
    cfg = C.merge(C.DEFAULTS, {})
    plain = readers.ome_images(write_ome(tmp_path / "a.ome.tif", bits=16), cfg)[0]
    assert plain["settings"] == "" and plain["flags"] == []                   # nothing recorded: as before
    img = readers.ome_images(write_ome(tmp_path / "b.ome.tif", gain=2.5, laser=0.5, bits=16), cfg)[0]
    assert img["channel_roles"] == ["MARKER", "BF"]
    assert img["settings"] == "gain 2.5; detector 600 V; 488nm attenuation 0.5"


def test_ome_tiff_reporter_named_for_another_fluorophore_is_flagged(tmp_path):
    """A channel called GFP whose Fluor is mCherry stays the reporter (by its name, as before) and is flagged."""
    cfg = C.merge(C.DEFAULTS, {})
    path = write_ome(tmp_path / "a.ome.tif", bits=16)
    with tifffile.TiffFile(path) as f:
        xml = f.ome_metadata
    for fluor, flagged in (("mCherry", True), ("EGFP", False), ("Alexa 488", False)):
        named = re.sub(r'(<Channel ID="Channel:0:0"[^>]*?)(/?>)', rf'\1 Fluor="{fluor}"\2', xml, count=1)
        assert named != xml
        with tifffile.TiffFile(path) as f:
            data = f.asarray()
        tifffile.imwrite(path, data.reshape(-1, 32, 32), description=named, metadata=None, photometric="minisblack")
        img = readers.ome_images(path, cfg)[0]
        assert img["channel_roles"] == ["MARKER", "BF"], fluor
        assert (img["flags"] == ["the GFP channel is named mCherry in the file (another fluorophore): check the channel roles"]) \
            if flagged else img["flags"] == [], (fluor, img["flags"])


def test_ome_tiff_without_significant_bits_is_flagged_not_guessed(tmp_path):
    cfg = C.merge(C.DEFAULTS, {})
    img = readers.ome_images(write_ome(tmp_path / "a.ome.tif"), cfg)[0]
    assert img["bits"] == 16 and "bit depth not recorded" in img["flags"][0]
    assert readers.ome_images(write_ome(tmp_path / "b.ome.tif", bits=12), cfg)[0]["bits"] == 12
    assert readers.ome_images(write_ome(tmp_path / "b.ome.tif", bits=12), cfg)[0]["flags"] == []
    stated = C.merge(C.DEFAULTS, {"saturation_value": {"OME-TIFF (instrument not named)": 4095}})
    assert readers.ome_images(tmp_path / "a.ome.tif", stated)[0]["flags"] == []


NIKON = """Metadata:
Dimensions: XY(1) x λ(2) x Z(21)
Camera Name: Andor DU-897
Number of Picture Planes: 2
Plane #1:
 Name: CSU far red
 Component Count: 1
 Modality: Widefield Fluorescence
 Camera Settings:
  Camera Type: Andor DU-897
  Binning: 1x1
  Exposure: 100 ms
  Multiplier: 100
  Conversion Gain: 5.1x
 Microscope Settings:   CSU, RPM: 10000
  ALC, MultiLaser(ALC):
     Line:1; ExW:405; Power:  0.0; Off
     Line:2; ExW:488; Power:  0.0; Off
     Line:4; ExW:640; Power: 77.0; On
  Zoom: 1.00x
Plane #2:
 Name: CSU green
 Component Count: 1
 Modality: Widefield Fluorescence, Spinning Disk Confocal
 Microscope Settings:   CSU, RPM: 10000
  ALC, MultiLaser(ALC):
     Line:1; ExW:405; Power:  0.0; Off
     Line:2; ExW:488; Power:{power}; On
     Line:4; ExW:640; Power:  0.0; Off
  Zoom: 1.00x
Z Stack Loop: 21
- Step: 5 µm
"""
CAPTURING = "Andor DU-897\nSample 1:\n  Camera Type: Andor DU-897\n  Binning: 1x1\n  Exposure: 100 ms\n  Multiplier: 100\n  Conversion Gain: 5.1x\n"


def test_nikon_laser_power_comes_from_the_reporter_channel():
    old = "exposure 100 ms; multiplier 100; gain 5.1x"       # all that was read before, whatever the laser power
    low = readers._nikon_settings(NIKON.format(power=" 20.0"), CAPTURING, "CSU green", 2)
    high = readers._nikon_settings(NIKON.format(power="100.0"), CAPTURING, "CSU green", 2)
    assert low == old + "; 488nm@20" and high == old + "; 488nm@100"
    assert readers._nikon_settings(NIKON.format(power="100.0"), CAPTURING, "CSU far red", 2) == old + "; 640nm@77"
    # no part of the description for this channel: the camera text, as before
    assert readers._nikon_settings("", CAPTURING, "CSU green", 2) == old
    assert readers._nikon_settings(NIKON.format(power="100.0"), CAPTURING, "another name", 2) == old


# ------------------------------------------------------------------ a project built before 1.26.7

@pytest.fixture
def built_before(tmp_path, monkeypatch):
    """A project of two OME-TIFF images taken at different gain, scanned and built as before 1.26.7:
    the gain was not read, so both are in settings group A."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    write_ome(project / "data" / "demo_control_01.ome.tif", gain=1, bits=16, seed=1)
    write_ome(project / "data" / "demo_mutant_01.ome.tif", gain=4, bits=16, seed=2)
    with monkeypatch.context() as m:
        m.setattr(readers, "_ome_settings", lambda chan, md=None: [])
        inventory(project, log=quiet)
    prepare(project, log=quiet)
    return project


def state(project):
    return {name: (project / name).read_bytes() for name in
            ("key/key.csv", "key/settings_groups.csv", "outputs/display_ranges.csv", "blind/W0001/meta.json",
             "blind/W0001/mip.png", "blind/W0002/marker_mip_full.png")}


def test_nothing_changes_for_a_built_project_until_it_scans_again(built_before):
    before = state(built_before)
    assert {r["settings_group"] for r in read_csv(built_before / "key" / "key.csv")} == {"A"}
    assert scan_changes(built_before)["lines"] == []
    assert home.project_status(built_before)["scan_changes"] == []
    lines = []
    prepare(built_before, log=lines.append)          # Rebuild, keeping the current codes
    assert state(built_before) == before
    assert not any(line.startswith("Warning") for line in lines)


def test_a_new_scan_that_changes_settings_groups_warns_and_records_it(built_before):
    codes = {r["file"]: r["code"] for r in read_csv(built_before / "key" / "key.csv")}
    inventory(built_before, log=quiet)
    assert {r["settings_group"] for r in read_csv(built_before / "key" / "key.csv")} == {"A"}   # the scan alone changes nothing
    want = "1 coded image moves from settings group A to B"
    assert scan_changes(built_before)["lines"] == [want]
    assert home.project_status(built_before)["scan_changes"] == [         # shown on the Build card, with its step
        "1 coded image moves from settings group A to B. Rebuild, keeping the current codes, to use it."]
    lines = []
    prepare(built_before, log=lines.append)
    (warning,) = [line for line in lines if line.startswith("Warning:")]
    assert want in warning and "Scores are unchanged" in warning
    assert want in (built_before / "log.md").read_text(encoding="utf-8")
    key = read_csv(built_before / "key" / "key.csv")
    assert {r["file"]: r["code"] for r in key} == codes
    assert sorted(r["settings_group"] for r in key) == ["A", "B"]
    assert {r["settings"] for r in key} == {"gain 1; detector 600 V", "gain 4; detector 600 V"}
    assert scan_changes(built_before)["lines"] == []


def test_settings_text_that_changes_for_every_image_alike_moves_no_image(tmp_path, monkeypatch):
    """Both images at the same gain: the text in the key changes, the group does not, so nothing is warned."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    for k, name in enumerate(("demo_control_01.ome.tif", "demo_mutant_01.ome.tif")):
        write_ome(project / "data" / name, gain=2, bits=16, seed=k)
    with monkeypatch.context() as m:
        m.setattr(readers, "_ome_settings", lambda chan, md=None: [])
        inventory(project, log=quiet)
    prepare(project, log=quiet)
    inventory(project, log=quiet)
    assert scan_changes(project) == {"lines": [], "kinds": [], "letters": {("OME-TIFF (instrument not named)", "gain 2; detector 600 V"): "A"},
                                     "changed": True, "reworded": None}
    lines = []
    prepare(project, log=lines.append)
    assert not any(line.startswith("Warning") for line in lines)
    assert {r["settings_group"] for r in read_csv(project / "key" / "key.csv")} == {"A"}


def test_a_new_scan_that_changes_the_bit_depth_warns_and_records_it(tmp_path):
    """A 12-bit camera whose file now states its bit depth: saturation moves from 65535 to 4095 at the next
    build, with a warning, and not before."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    names = ("demo_control_01.ome.tif", "demo_mutant_01.ome.tif")
    for k, name in enumerate(names):
        write_ome(project / "data" / name, seed=k)
    inventory(project, log=quiet)
    prepare(project, log=quiet)
    meta = lambda: json.loads((project / "blind" / "W0001" / "meta.json").read_text())
    assert meta()["saturation_value"] == 65535
    for k, name in enumerate(names):
        write_ome(project / "data" / name, bits=12, seed=k)
    inventory(project, log=quiet)
    want = "the saturation level of 2 coded images changes from 65535 to 4095"
    assert scan_changes(project)["lines"] == [want] and meta()["saturation_value"] == 65535
    lines = []
    prepare(project, log=lines.append)
    assert any(line.startswith("Warning:") and want in line for line in lines)
    assert want in (project / "log.md").read_text(encoding="utf-8")
    assert meta()["saturation_value"] == 4095 and meta()["full_range"] == [0, 4095]
    assert scan_changes(project)["lines"] == []


def test_adding_images_after_such_a_scan_says_the_groups_are_apart(built_before):
    write_ome(built_before / "data" / "demo_mutant_02.ome.tif", gain=4, bits=16, seed=3)
    inventory(built_before, log=quiet)
    lines = []
    prepare(built_before, add=True, log=lines.append)
    assert any(line.startswith("Warning:") and "settings groups of their own" in line for line in lines)
    key = {r["file"]: r["settings_group"] for r in read_csv(built_before / "key" / "key.csv")}
    assert key["demo_control_01.ome.tif"] == key["demo_mutant_01.ome.tif"] == "A"      # coded images are left as they are


def test_the_build_card_shows_the_warning():
    js = (Path(home.__file__).parent / "static" / "home.js").read_text(encoding="utf-8")
    assert re.search(r"sc\.length && built && !building \? scanNote\(sc\)", js)
    assert "and ${sc.length - 1} more" not in js            # every line, not the first (1.26.21)
