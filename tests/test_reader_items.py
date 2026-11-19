"""1.26.8: Leica sequential scans from current LAS X, and reader items from the independent audit:
dimensions that are read only in part, pixel sizes in other units, older OME schemas and channel names.

The sequential-scan fixtures follow the layout of a Stellaris file (LAS X 4.4, steps list their detectors
with gain) and of an SP8 file (LAS X 3.5, steps list their detectors without gain), as read from public
sample files; in both, the detection bands and the averaging are only in the master setting."""
import hashlib
import re

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
    for name in ("BFP", "TLR-4", "BFP-T1", "TDP-43", "td-Tomato", "TD Tomato", "TD_561", "tdTomato-T2"):
        assert role(name) == "OTHER", name
    for name in ("BF", "BF 2", "BF-T1", "Brightfield", "DIC", "TL DIC", "TLDIC", "TL", "Trans", "Transmitted",
                 "T PMT-T1", "T-PMT", "TD", "TD1", "TPMT", "TPMT-T2"):
        assert role(name) == "BF", name
    assert role("GFP") == "MARKER"
    # a name the project lists itself is matched as before, whatever follows it
    own = C.merge(C.DEFAULTS, {"brightfield_channel_names": ["Ph", "DIC"]})
    assert readers.marker_from_named("Phase", None, None, False, own) == "BF"
    assert readers.marker_from_named("DICblue", None, None, False, own) == "BF"
    # a td-Tomato reporter found by its laser line keeps its role
    red = C.merge(C.DEFAULTS, {})
    red["marker"] = dict(red["marker"], excitation_nm=[561])
    assert readers.marker_from_named("td-Tomato", None, "561", False, red) == "MARKER"


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


MM_COMPANION = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?><OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">\
<Instrument ID="Instrument:0"><Objective ID="Objective:0" Immersion="Air" LensNA="0.95" NominalMagnification="40"/></Instrument>\
<Image ID="Image:0" Name="pos1"><AcquisitionDate>2022-04-12T22:06:05.047450Z</AcquisitionDate><InstrumentRef ID="Instrument:0"/>\
<Pixels BigEndian="false" DimensionOrder="XYZCT" ID="Pixels:0" PhysicalSizeX="0.1625" PhysicalSizeXUnit="µm" PhysicalSizeY="0.1625" \
PhysicalSizeYUnit="µm" PhysicalSizeZ="1.7" PhysicalSizeZUnit="µm" SizeC="1" SizeT="1" SizeX="40" SizeY="30" SizeZ="{nz}" Type="uint16">\
<Channel EmissionWavelength="525" ExcitationWavelength="466" ID="Channel:0:0" Name="GFP-LED" SamplesPerPixel="1"/>{tiffdata}\
<Plane ExposureTime="0.16" ExposureTimeUnit="s" TheC="0" TheT="0" TheZ="0"/></Pixels></Image></OME>"""


def write_mm_set(folder, nz=2, first_z=None, file_name=None):
    """A Micro-Manager style set: the planes in a TIFF whose own description is only "OME-TIFF", the
    metadata in <name>.companion.ome next to it (as in real files from a lab's Micro-Manager)."""
    folder.mkdir(parents=True, exist_ok=True)
    tif = folder / "worm_2022-04-12_230605_t0_p0.ome.tiff"
    planes = (np.arange(nz * 30 * 40, dtype=np.uint16) * 7).reshape(nz, 30, 40)
    with tifffile.TiffWriter(tif) as w:
        for k, p in enumerate(planes):
            w.write(p, description="OME-TIFF" if k == 0 else None, metadata=None, photometric="minisblack")
    first_z = list(range(nz)) if first_z is None else first_z
    td = "".join(f'<TiffData FirstC="0" FirstT="0" FirstZ="{z}" IFD="{k}" PlaneCount="1"><UUID FileName="{file_name or tif.name}">'
                 f'urn:uuid:0</UUID></TiffData>' for k, z in enumerate(first_z))
    (folder / "worm_2022-04-12_230605.companion.ome").write_text(MM_COMPANION.format(nz=nz, tiffdata=td), encoding="utf-8")
    return tif, planes


def test_a_micro_manager_z_stack_with_its_metadata_in_a_companion_file_is_read_whole(tmp_path):
    """The TIFF alone looks like a plain sequence of pages; the companion file says they are z-planes.
    Before, only the first plane was read and the image was flagged "illuminations (2)"."""
    cfg = C.merge(C.DEFAULTS, {})
    tif, planes = write_mm_set(tmp_path / "set")
    [img] = readers.ome_images(tif, cfg)
    flags = [f for f in img["flags"] if readers.NO_BITS not in f]      # the file does not record its bit depth
    assert (img["n_z"], img["n_t"], flags) == (2, 1, [])
    assert img["channel_names"] == ["GFP-LED"] and img["z_step_um"] == 1.7
    assert np.array_equal(readers.read(img, 0), planes)
    # the pixel checksum that finds the same image in two files is the same as before
    with tifffile.TiffFile(tif) as f:
        assert readers.pixel_sha256(img) == hashlib.sha256(np.ascontiguousarray(f.asarray()).tobytes()).hexdigest()


def test_pages_the_companion_file_does_not_place_are_flagged_plainly(tmp_path):
    """When the companion file does not say which page is which plane, the first page is read and the
    flag says what is known, not "illuminations"."""
    cfg = C.merge(C.DEFAULTS, {})
    for name, kwargs in (("swapped", {"first_z": [1, 0]}), ("other file", {"file_name": "another.ome.tiff"})):
        tif, planes = write_mm_set(tmp_path / name, **kwargs)
        [img] = readers.ome_images(tif, cfg)
        assert img["n_z"] == 1, name
        flags = [f for f in img["flags"] if readers.NO_BITS not in f]
        assert flags == ["2 images in one file that the metadata does not place: only the first is read"], name
        assert not any("illumination" in f for f in img["flags"])


def test_a_plane_count_that_a_new_scan_reads_differently_is_named(tmp_path):
    """A new scan that reads more planes for coded images (a Micro-Manager z-stack read whole, 1.26.18)
    would change what a rebuild keeping the codes shows; the Build card says so, as for pixel sizes."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    inventory(project, log=quiet)
    rows = read_csv(project / "outputs" / "inventory.csv")
    key = project / "key" / "key.csv"
    write_csv(key, [{"code": f"W000{k + 1}", "local_path": r["local_path"], "location_in_file": r["location_in_file"],
                     "system": r["system"], "settings": r["settings"], "settings_group": "A", "pixel_um": r["pixel_um"],
                     "n_z": r["n_z"]} for k, r in enumerate(rows)])
    assert scan_changes(project)["lines"] == []
    old = int(rows[0]["n_z"])
    write_csv(key, [{"code": f"W000{k + 1}", "local_path": r["local_path"], "location_in_file": r["location_in_file"],
                     "system": r["system"], "settings": r["settings"], "settings_group": "A", "pixel_um": r["pixel_um"],
                     "n_z": 1} for k, r in enumerate(rows)])
    assert scan_changes(project)["lines"] == [f"the number of planes of 2 coded images changes from 1 to {old}"]
# The header of a real file that Bio-Formats 5.2.0-DEV wrote from an Olympus .oir (three planes kept, names
# changed): the schema name of a development build, a channel with no name, a UUID in every TiffData.
DEV0_HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?><!-- Warning: this comment is an OME-XML metadata '
    'block, which contains crucial dimensional parameters and other important metadata. Please edit cautiously '
    '(if at all), and back up the original data before doing so. For more information, see the OME-TIFF web '
    'site: http://www.openmicroscopy.org/site/support/ome-model/ome-tiff/. -->'
    '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-DEV0" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" Creator="OME Bio-Formats 5.2.0-DEV" '
    'UUID="urn:uuid:00000000-0000-4000-8000-000000000001" '
    'xsi:schemaLocation="http://www.openmicroscopy.org/Schemas/OME/2016-DEV0 '
    'http://www.openmicroscopy.org/Schemas/OME/2016-DEV0/ome.xsd"><Image ID="Image:0">'
    '<Pixels BigEndian="false" DimensionOrder="XYCZT" ID="Pixels:0" PhysicalSizeX="0.194592741887198" '
    'PhysicalSizeXUnit="µm" PhysicalSizeY="0.194592741887198" PhysicalSizeYUnit="µm" PhysicalSizeZ="0.16" '
    'PhysicalSizeZUnit="µm" SizeC="1" SizeT="1" SizeX="24" SizeY="24" SizeZ="3" TimeIncrement="0.0" '
    'TimeIncrementUnit="s" Type="uint16"><Channel ID="Channel:0:0" SamplesPerPixel="1"><LightPath/></Channel>'
    + "".join(f'<TiffData FirstC="0" FirstT="0" FirstZ="{z}" IFD="{z}" PlaneCount="1"><UUID FileName="stack.ome.tif">'
              'urn:uuid:00000000-0000-4000-8000-000000000001</UUID></TiffData>' for z in range(3))
    + '</Pixels></Image></OME>')


def test_ome_tiff_from_a_development_build_of_bio_formats_is_read(tmp_path):
    """The schema name 2016-DEV0 (Bio-Formats 5.2.0-DEV, before 2016-06 was published) was not taken for an
    OME schema, so the file was listed as not used. Every state of that schema lies between 2015-01 and
    2016-06, which name everything read here alike."""
    cfg = C.merge(C.DEFAULTS, {})
    planes = np.random.default_rng(0).integers(0, 1000, (3, 24, 24), dtype=np.uint16)
    read = {}
    for name in ("2016-DEV0", "2016-06"):
        path = tmp_path / name / "stack.ome.tif"
        path.parent.mkdir()
        tifffile.imwrite(path, planes, description=DEV0_HEADER.replace("2016-DEV0", name).encode("utf-8"), metadata=None,
                         photometric="minisblack", compression="lzw")
        found = readers.images_in(path, cfg)
        assert len(found) == 1, name                                 # was no image at all for 2016-DEV0
        assert np.array_equal(readers._raw(found[0])[0].reshape(planes.shape), planes)
        read[name] = {k: v for k, v in found[0].items() if k != "_reader"}
    assert read["2016-DEV0"] == read["2016-06"]
    assert (read["2016-DEV0"]["n_z"], read["2016-DEV0"]["pixel_um"], read["2016-DEV0"]["z_step_um"]) == (3, 0.194592741887198, 0.16)
    # only this one development name: another is still not taken for a schema that is known
    other = tmp_path / "other.ome.tif"
    tifffile.imwrite(other, planes, description=DEV0_HEADER.replace("2016-DEV0", "2017-DEV1").encode("utf-8"),
                     metadata=None,
                     photometric="minisblack")
    assert readers.images_in(other, cfg) == []


def test_a_project_scanned_before_2016_dev0_was_read_gets_the_image_only_when_added(tmp_path, monkeypatch):
    """Such a file was "not used". Nothing changes until Scan data runs again; then it is a raw image, a plain
    rebuild refuses (the codes would change), and Add new images gives it a new code after the others."""
    project = make_demo(tmp_path / "p", n_per_group=2, log=quiet)
    donor = make_demo(tmp_path / "donor", n_per_group=1, seed=99, log=quiet)
    src = next((donor / "data").glob("demo_*.ome.tif"))
    dev = project / "data" / src.name.replace("demo_", "demo_dev0_")
    dev.write_bytes(src.read_bytes())
    rewrite(dev, lambda xml: xml.replace("OME/2016-06", "OME/2016-DEV0"))
    monkeypatch.setattr(readers, "OME_SCHEMA", re.compile(r"http://www\.openmicroscopy\.org/Schemas/OME/\d{4}-\d{2}"))
    inventory(project, log=quiet)                                    # as every version up to 1.26.17 scanned it
    role = lambda: {r["relative_path"]: r["role"] for r in read_csv(project / "outputs" / "manifest.csv")}[dev.name]
    assert role() == readers.NOT_USED
    prepare(project, log=quiet)
    key = (project / "key" / "key.csv").read_bytes()
    before = read_csv(project / "key" / "key.csv")
    assert len(before) == 4
    monkeypatch.undo()

    prepare(project, log=quiet)                                      # no new scan: a rebuild is as before
    assert (project / "key" / "key.csv").read_bytes() == key
    inventory(project, log=quiet)
    assert role() == "raw image"
    with pytest.raises(SystemExit, match="--add-new-images"):
        prepare(project, log=quiet)
    assert (project / "key" / "key.csv").read_bytes() == key
    prepare(project, add=True, log=quiet)
    rows = read_csv(project / "key" / "key.csv")
    assert [r["code"] for r in rows if r["added_on"]] == ["W0005"] and len(rows) == 5
    assert [r["file"] for r in rows if r["added_on"]] == [dev.name]
    assert {r["code"]: r["local_path"] for r in rows if not r["added_on"]} == \
        {r["code"]: r["local_path"] for r in before}


def test_a_tiffdata_without_ifd_or_plane_count_covers_every_page(tmp_path):
    """OME: a TiffData with neither IFD nor PlaneCount stands for every page of the file (independent review)."""
    cfg = C.merge(C.DEFAULTS, {})
    tif, planes = write_mm_set(tmp_path / "set", nz=3)
    comp = tmp_path / "set" / "worm_2022-04-12_230605.companion.ome"
    xml = comp.read_text(encoding="utf-8")
    import re as _re
    one = f'<TiffData><UUID FileName="{tif.name}">urn:uuid:0</UUID></TiffData>'
    comp.write_text(_re.sub(r"(<TiffData .*?</TiffData>)+", one, xml, count=1), encoding="utf-8")
    [img] = readers.ome_images(tif, cfg)
    assert img["n_z"] == 3 and np.array_equal(readers.read(img, 0), planes)
