"""1.26.13: reader items found while checking real files.

- Leica settings text named every detector "HyD". A real SP8 file (LAS X 3.5, image.sc thread 30856) has its
  reporter on "PMT 3" (Detector Name="PMT 3" Type="PMT" Gain="624.97..."), and read "HyD gain 625.0".
- OME-TIFF files written by Bio-Formats give the objective as "40.0x/1.2000000000000002 Water".
- OME-TIFF files in the 2008-09 schema were read with no channels. The fixtures follow the layout of the OME
  sample files of that schema (multi-channel-z-series.ome.tiff and instrument.ome.tiff on
  downloads.openmicroscopy.org) and the attribute names of the published 2008-09 schema (ome.xsd).

A change of wording alone must not move a coded image to another settings group, or split a flat-field
session from its slide images."""
import numpy as np
import pytest
import tifffile

from test_acquisition_settings import write_ome
from test_flatfield import _corr
from test_reader_items import rewrite
from test_readers import HARDWARE, write_lif
from wormscore import config as C
from wormscore import flatfield as FF
from wormscore import home, readers
from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare, read_csv, scan_changes
from wormscore.rescan import same_reading, tidy_number

quiet = lambda *a: None
HYD = '<Detector Name="HyD 1" Channel="1" Gain="100" IsActive="1"/>'


def lif(path, detector, seed=0):
    rng = np.random.default_rng(seed)
    stack = lambda: rng.integers(90, 3000, (14, 32, 32))
    write_lif(path, [("Series001", (stack(), stack()))], hardware=HARDWARE.replace(HYD, detector))
    return path


@pytest.mark.parametrize("detector, want", [
    ('<Detector Name="PMT 3" Type="PMT" Channel="1" Gain="624.971389333944" IsActive="1"/>', "PMT gain 625.0"),
    ('<Detector Name="PMT 3" Channel="1" Gain="624.971389333944" IsActive="1"/>', "PMT gain 625.0"),
    ('<Detector Name="HyD S 2" Type="SiPM" Channel="1" Gain="18.82421875" IsActive="1"/>', "HyD gain 18.8"),     # Stellaris
    ('<Detector Name="HyD 1" Type="HyD" Channel="1" Gain="500" IsActive="1"/>', "HyD gain 500.0"),
    (HYD, "HyD gain 100.0"),                                                                                     # as before
])
def test_leica_settings_name_the_detector_the_file_gives(tmp_path, detector, want):
    (im,) = readers.leica_images(lif(tmp_path / "a.lif", detector), C.merge(C.DEFAULTS, {}))
    assert im["channel_roles"] == ["MARKER", "BF"]
    assert im["settings"] == f"{want}; 488nm@20.0%; line avg 3"


PMT = '<Detector Name="PMT 3" Type="PMT" Channel="1" Gain="{gain}" IsActive="1"/>'


def leica_project(tmp_path, monkeypatch, gains=(600, 600, 700)):
    """A Leica project with its reporter on a PMT, scanned and built as before 1.26.13 ("HyD gain ...")."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    for f in (project / "data").glob("*.tif"):
        f.unlink()
    for k, gain in enumerate(gains):
        lif(project / "data" / f"demo_{'control' if k % 2 else 'mutant'}_{k:02d}.lif", PMT.format(gain=gain), seed=k)
    with monkeypatch.context() as m:
        m.setattr(readers, "_leica_detector_kind", lambda d: "HyD")
        inventory(project, log=quiet)
    prepare(project, log=quiet)
    return project


def test_a_leica_detector_named_anew_moves_no_coded_image_and_warns_nothing(tmp_path, monkeypatch):
    project = leica_project(tmp_path, monkeypatch)
    key = lambda: {r["file"]: (r["code"], r["settings_group"]) for r in read_csv(project / "key" / "key.csv")}
    before = key()
    assert sorted(g for _, g in before.values()) == ["A", "A", "B"]
    assert {r["settings"][:8] for r in read_csv(project / "key" / "key.csv")} == {"HyD gain"}
    inventory(project, log=quiet)                                  # 1.26.13 reads "PMT gain ..."
    assert {r["settings"][:8] for r in read_csv(project / "outputs" / "inventory.csv")} == {"PMT gain"}
    ch = scan_changes(project)
    assert ch["lines"] == [] and ch["changed"] is False
    assert home.project_status(project)["scan_changes"] == []      # nothing on the Build card
    lines = []
    prepare(project, log=lines.append)                             # Rebuild, keeping the current codes
    assert not any(x.startswith("Warning") for x in lines)
    assert key() == before
    assert {r["settings"][:8] for r in read_csv(project / "key" / "key.csv")} == {"PMT gain"}


def test_images_added_after_a_detector_is_named_anew_join_their_settings_group(tmp_path, monkeypatch):
    """Before the fix the added image got a group of its own and a warning, though it was taken at the
    settings of coded images: only the wording of the settings differed."""
    project = leica_project(tmp_path, monkeypatch)
    before = {r["file"]: r["settings_group"] for r in read_csv(project / "key" / "key.csv")}
    at_700 = next(g for f, g in before.items() if f.endswith("_02.lif"))
    lif(project / "data" / "demo_control_09.lif", PMT.format(gain=700), seed=9)
    lif(project / "data" / "demo_control_10.lif", PMT.format(gain=800), seed=10)
    inventory(project, log=quiet)
    lines = []
    prepare(project, add=True, log=lines.append)
    assert not any("settings groups of their own" in x for x in lines)
    after = {r["file"]: r["settings_group"] for r in read_csv(project / "key" / "key.csv")}
    assert {f: after[f] for f in before} == before                  # coded images are left as they are
    assert after["demo_control_09.lif"] == at_700                   # the same settings, worded anew
    assert after["demo_control_10.lif"] not in before.values()      # a setting no coded image has: a new letter
    table = read_csv(project / "key" / "settings_groups.csv")
    by_letter = {}
    for r in table:
        by_letter.setdefault(r["settings_group"], set()).add(same_reading(r["system"], r["settings"]))
    assert all(len(v) == 1 for v in by_letter.values())             # one setting per letter, however worded


def test_same_reading_changes_only_the_wording():
    assert same_reading("Leica TCS SP8", "PMT gain 625.0; 488nm@2.0%; line avg 1") \
        == same_reading("Leica TCS SP8", "HyD gain 625.0; 488nm@2.0%; line avg 1")
    assert same_reading("Leica TCS SP8", "PMT gain 625.0; 488nm@2.0%; line avg 1") \
        != same_reading("Leica TCS SP8", "PMT gain 600.0; 488nm@2.0%; line avg 1")
    assert same_reading("Zeiss LSM 880", "gain 550; 488nm@2.00%") != same_reading("Zeiss LSM 880", "PMT 550 V; 488nm@2.00%")
    assert same_reading("Nikon A1", "exposure 100 ms; gain 5.1x") == "exposure 100 ms; gain 5.1x"     # not Leica: untouched
    assert same_reading("", "gain 550 | 40.0x/1.2000000000000002 Water") == same_reading("", "gain 550 | 40x/1.2 Water")
    assert (tidy_number("40.0"), tidy_number("1.2000000000000002"), tidy_number("1.40"), tidy_number("63"),
            tidy_number("1.45"), tidy_number("Plan")) == ("40", "1.2", "1.40", "63", "1.45", "Plan")


# ------------------------------------------------------------------ flat-field sessions measured before

def test_slide_images_measured_before_still_match_their_images():
    """Slide images carry the settings text of the version that measured them. An image scanned again with
    1.26.13 must find them as its own session, not as "other settings"."""
    old = "HyD gain 625.0; 488nm@2.0%; line avg 1 | HC PL APO 63x/1.40 OIL | 2"
    corr = _corr([{"index": 0, "system": "Leica TCS SP8", "settings": old, "day": "2026-03-01", "factor": 2.0},
                  {"index": 1, "system": "Leica TCS SP8", "settings": old.replace("625.0", "700.0"), "day": "2026-03-01"}])
    img = {"system": "Leica TCS SP8", "settings": "PMT gain 625.0; 488nm@2.0%; line avg 1", "zoom": "2",
           "objective": "HC PL APO 63x/1.40 OIL", "acquired": "2026-03-01 10:00:00", "height": 2, "width": 2}
    assert corr.session_for(img)["index"] == 0 and corr.resolve(img)["how"] == FF.SAME
    assert corr.resolve({**img, "settings": img["settings"].replace("625.0", "700.0")})["index"] == 1
    # OME-TIFF from Bio-Formats: the objective's numbers are written plainly now
    corr = _corr([{"index": 0, "system": "LSM", "settings": "gain 550 | 10.0x/0.30000000000000004 Air", "day": "2026-03-01"},
                  {"index": 1, "system": "LSM", "settings": "gain 550 | 40.0x/1.2000000000000002 Water", "day": "2026-03-01"},
                  {"index": 2, "system": "LSM", "settings": "40.0x/1.2000000000000002 Water", "day": "2026-03-02"}])
    img = {"system": "LSM", "settings": "gain 550", "objective": "40x/1.2 Water", "acquired": "2026-03-01 10:00:00",
           "height": 2, "width": 2}
    assert corr.resolve(img)["index"] == 1 and corr.resolve(img)["how"] == FF.SAME
    other = corr.resolve({**img, "settings": "gain 800", "acquired": "2026-03-02 10:00:00"})
    assert other["index"] == 2 and other["how"].endswith("(other settings)")     # its objective's, not the 10x's


# ------------------------------------------------------------------ OME-TIFF

def test_ome_tiff_objective_numbers_are_written_plainly(tmp_path):
    cfg = C.merge(C.DEFAULTS, {})
    path = write_ome(tmp_path / "a.ome.tif", gain=2, bits=16)
    lens = lambda mag, na: lambda xml: xml.replace(
        "<Image ", f'<Instrument ID="Instrument:0"><Objective ID="Objective:0" Immersion="Water" LensNA="{na}" '
                   f'NominalMagnification="{mag}"/></Instrument><Image ', 1)
    assert readers.ome_images(rewrite(path, lens("40.0", "1.2000000000000002")), cfg)[0]["objective"] == "40x/1.2 Water"
    path = write_ome(tmp_path / "b.ome.tif", gain=2, bits=16)
    assert readers.ome_images(rewrite(path, lens("63", "1.40")), cfg)[0]["objective"] == "63x/1.40 Water"     # as before


OME_2008 = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2008-09"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xsi:schemaLocation="http://www.openmicroscopy.org/Schemas/OME/2008-09 http://www.openmicroscopy.org/Schemas/OME/2008-09/ome.xsd">
 <Instrument ID="Instrument:0">
  <Microscope Type="Inverted" Manufacturer="OME Insturuments" Model="Lab Mk3"/>
  <LightSource ID="LightSource:0" Power="200"><Laser Type="SolidState" LaserMedium="Ruby" Wavelength="488"/></LightSource>
  <Detector ID="Detector:0" Type="CCD" Manufacturer="OME Detectors" Model="Standard CCD Mk2"/>
  <Objective ID="Objective:0" Manufacturer="OME Objectives" Model="60xUV-Air">
   <Correction>UV</Correction><Immersion>Air</Immersion><LensNA>1.2</LensNA>
   <NominalMagnification>60</NominalMagnification><CalibratedMagnification>60.12</CalibratedMagnification>
  </Objective>
 </Instrument>
 <Image AcquiredPixels="Pixels:0:0" DefaultPixels="Pixels:0:0" ID="Image:0" Name="18x24y5z1t2c8b-text">
  <CreationDate>2010-03-02T15:08:59</CreationDate>
  <InstrumentRef ID="Instrument:0"/>
  {channels}
  <Pixels BigEndian="false" DimensionOrder="XYCTZ" ID="Pixels:0:0" PixelType="uint8" SizeC="2" SizeT="1" SizeX="18"
   SizeY="24" SizeZ="3" PhysicalSizeX="0.2" PhysicalSizeY="0.2" PhysicalSizeZ="1.5">
   {planes}
  </Pixels>
 </Image>
</OME>"""
AS_THE_SAMPLE = ('<LogicalChannel ID="LogicalChannel:0" SamplesPerPixel="1"><ChannelComponent Index="0" Pixels="Pixels:0:0"/>'
                 '<ChannelComponent Index="1" Pixels="Pixels:0:0"/></LogicalChannel>')
NAMED = ('<LogicalChannel ID="LogicalChannel:0" Name="GFP" ExWave="488" EmWave="520" Fluor="GFP">'
         '<LightSourceRef ID="LightSource:0" Attenuation="0.5"/><DetectorRef ID="Detector:0" Gain="2.5"/>'
         '<ChannelComponent Index="0" Pixels="Pixels:0:0"/></LogicalChannel>'
         '<LogicalChannel ID="LogicalChannel:1" Name="TL" IlluminationType="Transmitted">'
         '<ChannelComponent Index="1" Pixels="Pixels:0:0"/></LogicalChannel>')
NO_COMPONENTS = '<LogicalChannel ID="LogicalChannel:0" Name="GFP" ExWave="488"/><LogicalChannel ID="LogicalChannel:1" Name="TL"/>'


def ome_2008(path, channels):
    rng = np.random.default_rng(0)
    data = rng.integers(0, 255, (3, 2, 24, 18), dtype=np.uint8)          # z, c, y, x: planes in the order XYCTZ
    planes = "".join(f'<TiffData FirstC="{c}" FirstT="0" FirstZ="{z}" IFD="{2 * z + c}" NumPlanes="1"/>'
                     for z in range(3) for c in range(2))
    tifffile.imwrite(path, data.reshape(-1, 24, 18), description=OME_2008.format(channels=channels, planes=planes),
                     metadata=None, photometric="minisblack")
    return path, data


def test_ome_tiff_2008_schema_channels_are_read_if_the_file_states_them(tmp_path):
    cfg = C.merge(C.DEFAULTS, {})
    # as the real sample: one logical channel covers both pixel channels and names neither
    path, data = ome_2008(tmp_path / "a.ome.tiff", AS_THE_SAMPLE)
    (im,) = readers.ome_images(path, cfg)
    assert im["channel_roles"] == ["OTHER", "OTHER"] and im["channel_names"] == ["", ""]     # was [] and []
    assert im["flags"] == [] and (im["n_z"], im["pixel_um"], im["z_step_um"]) == (3, 0.2, 1.5)
    assert im["objective"] == "60x/1.2 Air" and im["system"] == "Lab Mk3"
    # channels named as the schema allows: the reporter, its settings and the transmitted channel are found
    path, data = ome_2008(tmp_path / "b.ome.tiff", NAMED)
    (im,) = readers.ome_images(path, cfg)
    assert im["channel_roles"] == ["MARKER", "BF"] and im["channel_names"] == ["GFP", "TL"]
    assert im["settings"] == "gain 2.5; 488nm attenuation 0.5"
    assert np.array_equal(readers.read(im, 0), data[:, 0]) and np.array_equal(readers.read(im, 1), data[:, 1])
    # channels described but not tied to pixel channels: not guessed from their order
    path, data = ome_2008(tmp_path / "c.ome.tiff", NO_COMPONENTS)
    (im,) = readers.ome_images(path, cfg)
    assert im["channel_roles"] == ["UNRESOLVED", "UNRESOLVED"]
    assert im["flags"] == ["the file does not say which channel is which (an OME schema from before 2009)"]
    # a channel count no file can have is compared, not counted out (it would fill the memory)
    md = readers.ET.fromstring(OME_2008.format(channels=NAMED, planes="").replace('SizeC="2"', 'SizeC="99999999999"'))
    assert readers._ome_root(md).find(".//o:Pixels", readers.OME_NS).get("ChannelsNotStated") == "1"


def test_a_nikon_colour_camera_file_says_why_it_is_not_used(tmp_path, monkeypatch):
    """Three real colour-camera files (Nikon DS-Fi3 and DS-Ri2: histology and brightfield pictures, Zenodo
    10277961, 12734440, 8161776) were listed as "other image (no microscope metadata; not used)" with no
    reason. They are still not used: wormscore reads one grey value per pixel and channel."""
    class Colour:
        sizes, is_rgb, is_legacy = {"Y": 4, "X": 4, "S": 3}, True, False
        __init__ = lambda self, path: None
        __enter__ = lambda self: self
        __exit__ = lambda self, *a: None
    monkeypatch.setattr(readers, "_nd2", lambda: type("nd2", (), {"ND2File": Colour}))
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    (project / "data" / "HE_002.nd2").write_bytes(b"not read in this test")
    said = []
    inventory(project, log=said.append)
    (row,) = [r for r in read_csv(project / "outputs" / "manifest.csv") if r["relative_path"] == "HE_002.nd2"]
    assert row["role"] == readers.NOT_USED and "colour camera" in row["note"]
    assert any("HE_002.nd2" in x and "colour camera" in x for x in said)
