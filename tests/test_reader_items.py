"""1.26.8: Leica sequential scans from current LAS X, and reader items from the independent audit:
dimensions that are read only in part, pixel sizes in other units, older OME schemas and channel names.

The sequential-scan fixtures follow the layout of a Stellaris file (LAS X 4.4, steps list their detectors
with gain) and of an SP8 file (LAS X 3.5, steps list their detectors without gain), as read from public
sample files; in both, the detection bands and the averaging are only in the master setting."""
import numpy as np
import pytest
import tifffile

from test_acquisition_settings import write_ome
from test_readers import write_lif
from wormscore import config as C
from wormscore import readers
from wormscore.demo import make_demo
from wormscore.files import write_csv
from wormscore.pipeline import inventory, prepare, read_csv, scan_changes

quiet = lambda *a: None
MASTER = """<ATLConfocalSettingDefinition ObjectiveName="HC PL APO CS2 63x/1.40 OIL" Zoom="2" LineAverage="2" FrameAverage="1">
   <Spectro><MultiBand Channel="1" LeftWorld="502" RightWorld="566"/><MultiBand Channel="2" LeftWorld="566" RightWorld="643"/></Spectro>
   <AotfList><Aotf><LaserLineSetting LaserLine="405" IntensityDev="5"/><LaserLineSetting LaserLine="488" IntensityDev="0"/></Aotf></AotfList>
   <DetectorList>
    <Detector Name="HyD S 1" Channel="1" Gain="13.4" IsActive="1"/>
    <Detector Name="HyD S 2" Channel="2" Gain="2.5" IsActive="0"/>
    <Detector Name="Trans PMT" Channel="100" Gain="250" IsActive="0"/>
   </DetectorList>
  </ATLConfocalSettingDefinition>"""


def step(laser, active, gains):
    dets = "".join(f'<Detector Name="{name}" Channel="{ch}" {gains.get(name, "")} IsActive="{int(name == active)}"/>'
                   for name, ch in (("HyD S 1", 1), ("HyD S 2", 2), ("Trans PMT", 100)))
    return (f'<ATLConfocalSettingDefinition ScanMode="xyz"><AotfList><Aotf><LaserLineSetting LaserLine="488" IntensityDev="{laser}"/>'
            f'<LaserLineSetting LaserLine="561" IntensityDev="0"/></Aotf></AotfList><DetectorList>{dets}</DetectorList>'
            "</ATLConfocalSettingDefinition>")


def sequential(gains):
    """Two steps: the reporter on HyD S 1 with the 488 nm line, then the transmitted-light detector."""
    return (f'<Attachment Name="HardwareSetting" SystemTypeName="STELLARIS 5">{MASTER}<LDM_Block_Sequential>'
            f"<LDM_Block_Sequential_Master>{MASTER}</LDM_Block_Sequential_Master><LDM_Block_Sequential_List>"
            f'{step(1, "HyD S 1", gains[0])}{step(1, "Trans PMT", gains[1])}'
            "</LDM_Block_Sequential_List></LDM_Block_Sequential></Attachment>")


@pytest.mark.parametrize("gains, want", [
    # Stellaris: each step gives the gain of its detectors
    (({"HyD S 1": 'Gain="18.8"'}, {"Trans PMT": 'Gain="250"'}), "HyD gain 18.8; 488nm@1.0%; line avg 2"),
    # SP8: no gain in the steps; the master's is used
    (({}, {}), "HyD gain 13.4; 488nm@1.0%; line avg 2"),
])
def test_leica_sequential_scan_takes_band_gain_and_averaging_from_the_master(tmp_path, gains, want):
    """Each step had no detection band, so the reporter was never found and such a lab got no images."""
    rng = np.random.default_rng(0)
    stacks = [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))]
    path = tmp_path / "seq.lif"
    write_lif(path, stacks, hardware=sequential(gains))
    (im,) = readers.leica_images(path, C.merge(C.DEFAULTS, {}))
    assert im["channel_roles"] == ["MARKER", "BF"]
    assert im["settings"] == want                 # the step's 488 nm line, not the master's 405 nm
    assert np.array_equal(readers.read(im, 0), stacks[0][1][0])


def test_dimensions_read_only_in_part_are_flagged():
    assert readers._extra_axes_flags("CZYX", (2, 10, 64, 64)) == []
    assert readers._extra_axes_flags("TCZYX", (3, 2, 10, 64, 64)) == []          # time series have their own rule
    assert readers._extra_axes_flags("MZCYX", (4, 3, 2, 64, 64)) == ["tile scan (4 tiles): only the first is read"]
    assert readers._extra_axes_flags("MZCYX", (1, 3, 2, 64, 64)) == []
    assert readers._extra_axes_flags("HCZYX", (16, 2, 3, 8, 8)) == ["phases (16): only the first is read"]
    assert readers._extra_axes_flags("LZYX", (32, 3, 8, 8)) == ["lambda scan (32 wavelengths): only the first is read"]


@pytest.mark.parametrize("flag, words", [
    ("tile scan (4 tiles): only the first is read", "only its first entry is in the blind set"),
    ("pixel size is not calibrated in the file (1 um per pixel is a placeholder)", "read as 1 um per pixel"),
])
def test_build_warns_about_images_read_only_in_part_or_without_calibration(tmp_path, flag, words):
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    inventory(project, log=quiet)
    inv = project / "outputs" / "inventory.csv"
    rows = read_csv(inv)
    rows[0]["flags"] = flag
    write_csv(inv, rows, list(rows[0]))
    lines = []
    prepare(project, log=lines.append)
    (warning,) = [line for line in lines if line.startswith("Warning:")]
    assert warning.startswith("Warning: 1 image ") and words in warning
    assert words in (project / "log.md").read_text(encoding="utf-8")
    assert len(read_csv(project / "key" / "key.csv")) == 2          # still coded: warned, not blocked


def rewrite(path, change):
    with tifffile.TiffFile(path) as f:
        xml, planes = f.ome_metadata, f.asarray()
    tifffile.imwrite(path, planes.reshape(-1, *planes.shape[-2:]), description=change(xml), metadata=None,
                     photometric="minisblack")
    return path


def test_ome_tiff_pixel_size_in_other_units(tmp_path):
    cfg = C.merge(C.DEFAULTS, {})
    path = write_ome(tmp_path / "a.ome.tif", bits=16)
    assert readers.ome_images(path, cfg)[0]["pixel_um"] == 0.2
    nm = lambda xml: xml.replace('PhysicalSizeX="0.2"', 'PhysicalSizeX="200" PhysicalSizeXUnit="nm"') \
        .replace('PhysicalSizeZ="1.0"', 'PhysicalSizeZ="0.001" PhysicalSizeZUnit="mm"')
    img = readers.ome_images(rewrite(path, nm), cfg)[0]
    assert img["pixel_um"] == 0.2 and img["z_step_um"] == 1.0 and img["flags"] == []     # was 200 um per pixel
    pixel = lambda xml: xml.replace('PhysicalSizeXUnit="nm"', 'PhysicalSizeXUnit="pixel"')
    img = readers.ome_images(rewrite(path, pixel), cfg)[0]
    assert img["pixel_um"] == 0.0 and img["flags"] == ["pixel size is given in 'pixel', not a length"]


def test_older_ome_schemas_are_read(tmp_path):
    cfg = C.merge(C.DEFAULTS, {})
    path = write_ome(tmp_path / "a.ome.tif", gain=2, bits=12)
    new = readers.ome_images(path, cfg)[0]
    for year in ("2010-06", "2013-06", "2015-01"):
        old = readers.ome_images(rewrite(path, lambda xml: xml.replace("OME/2016-06", f"OME/{year}")), cfg)
        assert len(old) == 1, year                                   # was listed as "no microscope metadata"
        assert {k: v for k, v in old[0].items() if k != "_reader"} == {k: v for k, v in new.items() if k != "_reader"}
        rewrite(path, lambda xml: xml.replace(f"OME/{year}", "OME/2016-06"))


def test_channel_names_that_only_begin_like_brightfield():
    cfg = C.merge(C.DEFAULTS, {})
    role = lambda name: readers.marker_from_named(name, None, None, False, cfg)
    for name in ("BFP", "TLR-4", "BFP-T1", "TDP-43", "DICER"):
        assert role(name) == "OTHER", name
    for name in ("BF", "BF 2", "BF-T1", "Brightfield", "DIC", "TL DIC", "TLDIC", "TL", "Trans", "Transmitted",
                 "T PMT-T1", "T-PMT", "TD", "TD1", "TPMT", "TPMT-T2"):
        assert role(name) == "BF", name
    assert role("GFP") == "MARKER"


def test_a_pixel_size_that_a_new_scan_reads_differently_is_named(tmp_path, monkeypatch):
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    nm = lambda xml: xml.replace('PhysicalSizeX="0.2"', 'PhysicalSizeX="200" PhysicalSizeXUnit="nm"')
    for k, name in enumerate(("demo_control_01.ome.tif", "demo_mutant_01.ome.tif")):
        rewrite(write_ome(project / "data" / name, bits=16, seed=k), nm)
    with monkeypatch.context() as m:           # as before 1.26.8: the unit was not read
        m.setattr(readers, "_ome_um", lambda px, axis, flags: float(px.get(f"PhysicalSize{axis}")))
        inventory(project, log=quiet)
    key = project / "key" / "key.csv"
    write_csv(key, [{"code": f"W000{k + 1}", "local_path": r["local_path"], "location_in_file": r["location_in_file"],
                     "system": r["system"], "settings": r["settings"], "settings_group": "A", "pixel_um": r["pixel_um"]}
                    for k, r in enumerate(read_csv(project / "outputs" / "inventory.csv"))])
    assert scan_changes(project)["lines"] == []
    inventory(project, log=quiet)
    assert scan_changes(project)["lines"] == ["the pixel size of 2 coded images changes from 200 to 0.2 um"]


def test_scan_progress_covers_the_pixel_checksums_too(tmp_path):
    """Progress reached 100% after the file checksums and stood there while every image was read."""
    project = make_demo(tmp_path / "p", n_per_group=2, log=quiet)
    seen = []
    inventory(project, log=quiet, progress=lambda done, total: seen.append(done / total))
    assert seen == sorted(seen) and seen[-1] == 1.0
    assert sum(0.5 < x < 1.0 for x in seen) >= 3 and sum(x <= 0.5 for x in seen) >= 1
    assert sum(x == 1.0 for x in seen) == 1


def test_metadata_named_in_another_folder_is_not_opened(tmp_path):
    """The companion file's name comes from the image file itself: only a plain name next to it is followed."""
    cfg = C.merge(C.DEFAULTS, {})
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "set").mkdir()
    full = write_ome(tmp_path / "elsewhere" / "meta.ome.tif", bits=16)
    with tifffile.TiffFile(full) as f:
        planes = f.asarray()
    for named, found in (("meta.ome.tif", 1), ("../elsewhere/meta.ome.tif", 0), (str(full), 0)):
        if found:
            write_ome(tmp_path / "set" / "meta.ome.tif", bits=16)
        xml = (f'<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06"><BinaryOnly MetadataFile="{named}" '
               'UUID="urn:uuid:0"/></OME>')
        part = tmp_path / "set" / "part.ome.tif"
        tifffile.imwrite(part, planes.reshape(-1, 32, 32), description=xml, metadata=None, photometric="minisblack")
        assert len(readers.ome_images(part, cfg)) == found, named
        (tmp_path / "set" / "meta.ome.tif").unlink(missing_ok=True)
