"""1.26.14: Olympus/Evident .oir files (FV3000, FV4000, FVMPE-RS).

Real images are never stored in this repository. tests/oir_metadata/ holds the metadata XML of four real
files, shortened to the elements wormscore reads (elements left out, nothing changed):

- chloroplasts: "Chloroplasts 3D.oir", Zenodo record 16966021, CC BY 4.0. FV3000 (FV31S 2.6.1.243), 34
  planes, two phases: EGFP, mCherry and a transmitted detector (TD) in the first, "Red" in the second.
- venus: "venus stack.oir", OME sample images, Olympus-OIR/etienne, CC BY 4.0 (Etienne Labrie-Dion).
  FVMPE-RS multiphoton (FluoView 1.3.1.83), 67 planes, brightness corrected along z. Planes 1, 34 and 67.
- sequence: "1202-interval_30sec_sequence_frame.oir", Zenodo record 14417737 (OME sample images,
  Olympus-OIR/imagesc-105684), CC BY 4.0 (Chin Hung). FV4000 (FV40S 3.1.1.67), FITC and TRITC.
- hek293: "110818_hek293_splitSCN1AGFP1to10and11 2D posttrans 20x_hoechst_phal.oir", Zenodo record
  1490851, CC BY 4.0. FV3000 (FV31S 2.2.1.114), a neutral density filter in the laser path.

The tile list is from matl.omp2info of Zenodo record 13680725 (CC BY 4.0, Nicolas Chiaruttini and Lucie
Dixsaut; FV4000), cut to 3 of its 25 areas.

Pixels are tested with a small file written here in the layout oirfile reads (header, blocks, index),
holding the chloroplasts metadata and synthetic planes. The reading of real pixels was compared with
Bio-Formats outside the test suite (tools/README.md).
"""
import copy
import re
import struct
from pathlib import Path

import numpy as np
import pytest

from wormscore import config as C
from wormscore import readers as R

DATA = Path(__file__).parent / "oir_metadata"
GFP = C.merge(C.DEFAULTS, {})
EGFP, MCHERRY, RED, TD = ("b5c33198-045a-46f1-9657-e8dd563af400", "a3bd8257-ff10-48ff-9c19-2bd96c48fb5b",
                          "9afb29ce-8e2d-4859-b848-59bc6a33b72b", "f0c60e81-cf26-4e94-9482-dccae8a45ac7")


def xml(name):
    return (DATA / f"{name}.xml").read_text(encoding="ascii")


def marker(**settings):
    cfg = copy.deepcopy(GFP)
    cfg["marker"] = {**C.PRESETS[settings.pop("preset", "GFP")], **settings}
    return cfg


# ------------------------------------------------------------------ metadata of real files

def test_four_channels_with_a_transmitted_detector_and_two_phases():
    meta = R._oir_meta(xml("chloroplasts.image"), [xml("chloroplasts.plane0")], GFP)
    # the file stores the channels as 1, 4, 2, 3; "order" puts them in the order of the pixels
    assert meta["channel_ids"] == [EGFP, MCHERRY, RED, TD]
    assert meta["channel_names"] == ["CH1 EGFP", "CH2 mCherry", "CH3 Red", "CH4 TD"]
    assert meta["channel_roles"] == ["MARKER", "OTHER", "OTHER", "BF"]
    assert meta["settings"] == "HV 500 V; offset 3; 488nm@1%"
    assert (meta["system"], meta["objective"]) == ("Olympus FV3000", "60x/1.2 (UPLSAPO 60XW)")
    assert (meta["pixel_um"], meta["z_step_um"], meta["bits"], meta["zoom"]) == (0.1057, 0.55, 12, "1.96")
    assert meta["acquired"] == "2025-02-21 14:38:26"            # local time as the file writes it, like Zeiss
    assert meta["planned_z"] == 34 and meta["flags"] == []


def test_the_reporter_gives_its_own_detector_and_its_own_phase_of_the_laser():
    """mCherry (phase 1) and "Red" (phase 2) are both excited at 561 nm, at 0.5% and at 2%. The channel
    that names the configured dye is the reporter, and the settings are its own."""
    meta = R._oir_meta(xml("chloroplasts.image"), [xml("chloroplasts.plane0")], marker(preset="mCherry"))
    assert meta["channel_roles"] == ["OTHER", "MARKER", "OTHER", "BF"]
    assert meta["settings"] == "HV 475 V; offset 3; 561nm@0.5%"


def test_transmitted_light_is_read_from_the_detector_not_from_the_position():
    image = xml("chloroplasts.image")
    assert R._oir_meta(image, [], GFP)["channel_roles"][3] == "BF"
    # the same channel on a fluorescence detector is not brightfield, although it is still the last one
    other = image.replace("<commonphase:deviceName>TD</commonphase:deviceName>", "<commonphase:deviceName>HSD4</commonphase:deviceName>")
    other = other.replace("__FV31-LETD_D_1__", "__FV30-HSD_D_4__")
    assert other != image
    meta = R._oir_meta(other, [], GFP)
    assert meta["channel_roles"] == ["MARKER", "OTHER", "OTHER", "OTHER"] and meta["channel_names"][3] == "CH4"


def _with(image, voltage=None, share=None):
    """The chloroplasts metadata with another detector voltage or 488 nm laser share for the EGFP channel."""
    if voltage:
        image, n = re.subn(rf'(<lsmparam:pmt channelId="{EGFP}"[^>]*>\s*<lsmparam:voltage>)500<', rf"\g<1>{voltage}<", image)
        assert n == 1
    if share:
        image, n = re.subn(r'(<lsmparam:mainLaser laserDataId="LD488_65794Imaging_main_phase_1">\s*<commonparam:power>100.0'
                           r'</commonparam:power>\s*<commonparam:transmissivity>)1.0<', rf"\g<1>{share}<", image)
        assert n == 1
    return image


def test_images_at_another_voltage_or_laser_power_never_share_a_settings_text():
    image = xml("chloroplasts.image")
    read = lambda text: R._oir_meta(text, [], GFP)["settings"]
    assert read(image) == "HV 500 V; offset 3; 488nm@1%"
    assert read(_with(image, voltage=520)) == "HV 520 V; offset 3; 488nm@1%"
    assert read(_with(image, share=2.5)) == "HV 500 V; offset 3; 488nm@2.5%"
    # another channel's laser does not change the reporter's settings
    assert read(image.replace("<commonparam:transmissivity>0.5<", "<commonparam:transmissivity>0.7<")) == read(image)


def test_a_reporter_that_names_no_laser_lists_every_laser_that_is_on():
    """Not guessed: the settings then split on any laser of the channel's phase."""
    image = xml("chloroplasts.image")
    own = "<lsmimage:laserDataId>LD488_65794Imaging_main_phase_1</lsmimage:laserDataId>"
    assert image.count(own) == 1
    cut = image.replace(own, "")
    meta = R._oir_meta(cut, [], GFP)
    assert meta["channel_roles"] == ["MARKER", "OTHER", "OTHER", "BF"]         # by the dye name alone
    assert meta["settings"] == "HV 500 V; offset 3; 488nm@1%;561nm@0.5%"


def test_brightness_correction_along_z_gives_the_range_and_a_flag():
    """The multiphoton file raises detector voltage, offset and laser power with depth. Its 940 nm line is
    not a GFP setting, so the role is not guessed; a project that lists it gets the range over the planes."""
    planes = [xml(f"venus.plane{n}") for n in (0, 33, 66)]
    assert R._oir_meta(xml("venus.image"), planes, GFP)["channel_roles"] == ["OTHER"]
    meta = R._oir_meta(xml("venus.image"), planes, marker(preset="YFP", excitation_nm=[514, 940]))
    assert meta["channel_roles"] == ["MARKER"] and meta["channel_names"] == ["CH3"]
    assert meta["settings"] == "HV 727-743 V; offset 0-19; 940nm@4.9-30.1%; line integration 4"
    (flag,) = meta["flags"]
    assert flag.startswith("brightness is corrected along z: detector voltage, offset, 940 nm laser")
    assert (meta["system"], meta["bits"], meta["pixel_um"], meta["z_step_um"]) == ("Olympus FluoView Software", 12, 0.9944, 1.49)


def test_two_records_of_the_laser_power_are_both_written_and_flagged():
    """In 10 of 23 real files the first plane's record of a laser's share is not the image's (here 1%
    against 0.1%). The image's value is the one Bio-Formats reports; which one the scan used cannot be
    told from the file, so both are in the settings text and the image is flagged."""
    meta = R._oir_meta(xml("sequence.image"), [xml("sequence.plane0")], GFP)
    # only the two channels that were scanned are in the file: CH1 (DAPI) and CH4 (Cy5) were switched off
    assert meta["channel_names"] == ["CH2 FITC", "CH3 TRITC"] and meta["channel_roles"] == ["MARKER", "OTHER"]
    assert meta["settings"] == "488nm@0.1% (first plane: 1%)"           # FV4000 detectors: no voltage, gain 1
    assert meta["flags"] == ["the file records two values: 488 nm laser 0.1% in the image's settings, 1% in the "
                             "first plane's record (check in the Olympus software)"]
    assert (meta["system"], meta["bits"]) == ("Olympus FV4000", 16)
    alone = R._oir_meta(xml("sequence.image"), [], GFP)
    assert alone["settings"] == "488nm@0.1%" and alone["flags"] == []


def test_a_neutral_density_filter_and_line_integration_are_in_the_settings():
    meta = R._oir_meta(xml("hek293.image"), [xml("hek293.plane0")], GFP)
    assert meta["channel_names"] == ["CH1 Hoechst33258", "CH2 EGFP", "CH3 Rhodamine Phalloidin"]
    assert meta["channel_roles"] == ["OTHER", "MARKER", "OTHER"]
    assert meta["settings"] == "HV 465 V; 488nm@1.2% (ND filter 10% in); line integration 2"


def test_an_image_changed_in_the_olympus_software_is_flagged():
    """Projections saved by the Olympus software have the system name "FV3000 (Modified)" (seen in Zenodo
    record 20758452, CC BY 4.0). Here the name is put into the chloroplasts metadata."""
    image = xml("chloroplasts.image").replace("<base:systemName>FV3000<", "<base:systemName>FV3000 (Modified)<")
    meta = R._oir_meta(image, [], GFP)
    assert meta["system"] == "Olympus FV3000"
    assert meta["flags"] == ["made or changed in the Olympus software after the scan (the file's system is 'FV3000 (Modified)')"]


def test_channels_without_a_stated_order_stay_unresolved():
    image = xml("chloroplasts.image").replace(f'id="{TD}" order="4"', f'id="{TD}"')
    meta = R._oir_meta(image, [], GFP)
    assert meta["channel_roles"] == ["UNRESOLVED"] * 4 and meta["settings"] == ""
    assert any("order of its channels" in f for f in meta["flags"])


TILES = """<?xml version="1.0" encoding="ASCII"?>
<matl:properties xmlns:matl="http://www.olympus.co.jp/hpf/protocol/matl/model/matl">
  <matl:map><matl:image>Map_A01.oir</matl:image></matl:map>
  <matl:group objectId="5782ea9a-e8e1-476d-945d-dc9797b7aab1">
    <matl:stitching>true</matl:stitching>
    <matl:areaInfo><matl:numOfXAreas>7</matl:numOfXAreas><matl:numOfYAreas>5</matl:numOfYAreas></matl:areaInfo>
    <matl:area id="e3f87655-9ecd-428c-84ee-d92fc9f604ed">
      <matl:image>tiling-sample-brain-section_A01_G001_0001.oir</matl:image><matl:xIndex>1</matl:xIndex><matl:yIndex>0</matl:yIndex>
    </matl:area>
    <matl:area id="bdbb0077-fd9c-429c-ad39-1c1fa4a44747">
      <matl:image>tiling-sample-brain-section_A01_G001_0002.oir</matl:image><matl:xIndex>2</matl:xIndex><matl:yIndex>0</matl:yIndex>
    </matl:area>
    <matl:area id="3afdf695-6ae2-47fd-9cf3-788ab7360a67">
      <matl:image>tiling-sample-brain-section_A01_G001_0003.oir</matl:image><matl:xIndex>4</matl:xIndex><matl:yIndex>1</matl:yIndex>
    </matl:area>
  </matl:group>
</matl:properties>"""


def test_a_tile_is_known_from_the_list_next_to_it():
    assert R._oir_tiles(TILES, "tiling-sample-brain-section_A01_G001_0002.oir") == 3
    assert R._oir_tiles(TILES, "Map_A01.oir") is None and R._oir_tiles(TILES, "other.oir") is None
    assert R._oir_tiles("not xml", "a.oir") is None


# ------------------------------------------------------------------ a small file in the layout oirfile reads

NX, NY = 8, 6


def write_oir(path, planes, n_z=3, order=(TD, EGFP, RED, MCHERRY), image=None):
    """planes: {(z, channel id): uint16 (NY, NX) array, or bytes for a plane that is cut short}. The blocks
    are written plane by plane in the given channel order, which is not the order of the channels."""
    image = image or xml("chloroplasts.image")
    for old, new in (("<commonimage:width>524<", f"<commonimage:width>{NX}<"), ("<commonimage:height>516<", f"<commonimage:height>{NY}<"),
                     ("<commonparam:maxSize>34<", f"<commonparam:maxSize>{n_z}<")):
        assert old in image
        image = image.replace(old, new)
    plane = xml("chloroplasts.plane0").replace("<base:width>524<", f"<base:width>{NX}<").replace("<base:height>516<", f"<base:height>{NY}<")
    blocks = [struct.pack("<II", len(image), 0) + image.encode("ascii")]              # metadata
    for z in range(n_z):
        if any(k[0] == z for k in planes):
            blocks.append(struct.pack("<II", len(plane), 1) + plane.encode("ascii"))  # the plane's own record
        for c in order:
            if (z, c) not in planes:
                continue
            data = planes[z, c] if isinstance(planes[z, c], bytes) else planes[z, c].astype("<u2").tobytes()
            uid = f"z{z + 1:03d}_0_1_{c}_0".encode("ascii")
            blocks.append(struct.pack("<IIIII", 12 + len(uid), 3, 0, len(data), len(uid)) + uid)
            blocks.append(struct.pack("<II", len(data), 4) + data)
    offsets, at = [], 96
    for b in blocks:
        offsets.append(at)
        at += len(b)
    index = struct.pack("<I", 0xFFFFFFFF) + b"".join(struct.pack("<Q", o) for o in offsets)
    head = b"OLYMPUSRAWFORMAT" + struct.pack("<IIIIQQ", 12, 0, 1, 2, at + len(index), at) + bytes(24) + b"FLUOVIEW" + bytes(16)
    assert len(head) == 96
    path.write_bytes(head + b"".join(blocks) + index)
    return path


def stack(n_z=3):
    rng = np.random.default_rng(1)
    return {(z, c): rng.integers(0, 4096, (NY, NX)).astype(np.uint16) for z in range(n_z) for c in (EGFP, MCHERRY, RED, TD)}


def test_oir_files_are_raw_images_and_their_continuation_files_are_parts(tmp_path):
    assert R.file_role(Path("a/scan.oir")) == "raw image" and R.file_role(Path("a/SCAN.OIR")) == "raw image"
    # a large scan continues in files without an extension, named like the .oir
    (tmp_path / "scan.oir").write_bytes(b"")
    assert R.file_role(tmp_path / "scan_00001") == R.OIR_PART
    assert R.file_role(tmp_path / "other_00001") == "reference (not an image)"


def test_a_file_is_listed_and_each_channel_reads_its_own_planes(tmp_path):
    planes = stack()
    path = write_oir(tmp_path / "worm.oir", planes)
    (img,) = R.images_in(path, GFP)
    assert img["format"] == "Olympus OIR" and img["location_in_file"] == "whole file" and img["_reader"][0] == "oir"
    assert (img["n_z"], img["n_t"], img["height"], img["width"], img["bits"]) == (3, 1, NY, NX, 12)
    assert img["channel_roles"] == ["MARKER", "OTHER", "OTHER", "BF"]
    assert img["settings"] == "HV 500 V; offset 3; 488nm@1%" and img["flags"] == []
    assert (img["pixel_um"], img["z_step_um"], img["stage_x"]) == (0.1057, 0.55, None)
    for k, c in enumerate((EGFP, MCHERRY, RED, TD)):
        got = R.read(img, k)
        assert got.dtype == np.uint16 and got.shape == (3, NY, NX)
        assert np.array_equal(got, np.stack([planes[z, c] for z in range(3)]))
    assert len(R.pixel_sha256(img)) == 64


def test_a_scan_stopped_inside_its_last_plane_is_read_without_that_plane(tmp_path):
    """oirfile fills the missing part of the plane with zeros (a real case: Zenodo record 20703055, CC BY
    4.0, whose sixth plane is 42% scanned). The plane is left out, as Bio-Formats does, and flagged."""
    planes = stack()
    for c in (EGFP, MCHERRY, RED, TD):
        planes[2, c] = planes[2, c].astype("<u2").tobytes()[: NX * NY]          # half of the last plane
    path = write_oir(tmp_path / "stopped.oir", planes)
    (img,) = R.images_in(path, GFP)
    assert img["n_z"] == 2
    assert img["flags"] == ["the scan stopped during plane 3: that plane is left out",
                            "the z-stack was set to 3 planes, the file holds 2 whole planes (stopped early, or a continuation file is missing)"]
    got = R.read(img, 0)
    assert got.shape == (2, NY, NX) and np.array_equal(got[1], planes[1, EGFP])


def test_a_file_with_a_plane_missing_in_the_middle_is_flagged_and_not_read(tmp_path):
    planes = stack()
    del planes[1, MCHERRY]
    path = write_oir(tmp_path / "gap.oir", planes)
    (img,) = R.images_in(path, GFP)
    assert any(f.startswith(R.INCOMPLETE) for f in img["flags"])
    with pytest.raises(ValueError, match="incomplete"):                    # never read as a black plane
        R.read(img, 1)


def test_a_file_cut_short_is_refused_with_the_reason(tmp_path):
    path = write_oir(tmp_path / "copy.oir", stack())
    path.write_bytes(path.read_bytes()[:900])
    with pytest.raises(ValueError, match="the file is incomplete: it holds"):
        R.images_in(path, GFP)
    (tmp_path / "text.oir").write_text("not an image")
    with pytest.raises(ValueError, match="not an Olympus .oir file"):
        R.images_in(tmp_path / "text.oir", GFP)


def test_scan_data_lists_oir_images_and_names_what_it_cannot_use(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_oir(data / "worm_A01_G001_0001.oir", stack())
    write_oir(data / "Map_A01.oir", {})                               # the overview map of a multi-area scan: no pixels
    (data / "matl.omp2info").write_text(TILES.replace("tiling-sample-brain-section", "worm"), encoding="ascii")
    cfg = C.merge(C.DEFAULTS, {"sources": [{"name": "s", "path": str(data)}]})
    manifest, images = R.scan(cfg, log=lambda *a: None)
    roles = {m["relative_path"]: (m["role"], m["note"]) for m in manifest}
    assert roles["worm_A01_G001_0001.oir"] == ("raw image", "")
    assert roles["Map_A01.oir"] == ("unreadable image", "the file holds no image (for example the overview map of a multi-area scan)")
    assert roles["matl.omp2info"][0] == "reference (not an image)"
    (img,) = images
    assert img["file"] == "worm_A01_G001_0001.oir" and img["channel_roles"] == ["MARKER", "OTHER", "OTHER", "BF"]
    assert img["flags"] == ["one tile of a tile scan (3 tiles, listed in matl.omp2info): the tiles are not joined"]
