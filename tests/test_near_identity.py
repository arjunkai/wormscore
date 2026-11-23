"""1.26.21's near laser lines find the reporter only, never the identity channel: an identity channel found
that way would be drawn on a coded image built without one after a rescan. Checked on an OME-TIFF whose
channels record only their excitation and emission wavelengths (no name a preset matches), 3 nm from the
mCherry preset's 587 nm line (22 November 2026; the public sample files all name their identity channel)."""
import numpy as np
import tifffile

from wormscore import config as C
from wormscore import readers as R

NS = "http://www.openmicroscopy.org/Schemas/OME/2016-06"


def ome(path, excitation):
    channels = [f'ExcitationWavelength="488" EmissionWavelength="510"',
                f'ExcitationWavelength="{excitation}" EmissionWavelength="610"',
                'IlluminationType="Transmitted" ContrastMethod="Brightfield"']
    ch = "".join(f'<Channel ID="Channel:0:{k}" SamplesPerPixel="1" {c}/>' for k, c in enumerate(channels))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><OME xmlns="{NS}"><Instrument ID="Instrument:0">'
           f'<Objective ID="Objective:0" NominalMagnification="63" LensNA="1.4" Immersion="Oil"/></Instrument>'
           f'<Image ID="Image:0" Name="img0"><Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="uint16" SizeX="16" '
           f'SizeY="16" SizeZ="4" SizeC="3" SizeT="1" PhysicalSizeX="0.25" PhysicalSizeY="0.25" PhysicalSizeZ="1.0">'
           f'{ch}<TiffData IFD="0" PlaneCount="12"/></Pixels></Image></OME>')
    data = np.random.default_rng(0).integers(100, 3000, (12, 16, 16)).astype(np.uint16)
    tifffile.imwrite(path, data, photometric="minisblack", description=xml, metadata=None)
    return path


def roles(path, marker, identity):
    cfg = C.apply_identity(C.merge(C.DEFAULTS, {}), {"preset": identity})
    cfg["marker"] = C.resolve_marker({"preset": marker})
    im = R.images_in(path, cfg)[0]
    return im["channel_roles"], [f for f in im["flags"] if "found by its emission" in f]


def test_an_identity_channel_is_never_found_by_a_near_line(tmp_path):
    near = ome(tmp_path / "near.ome.tif", 590)
    assert roles(near, "GFP", "mCherry") == (["MARKER", "OTHER", "BF"], [])
    assert roles(ome(tmp_path / "exact.ome.tif", 587), "GFP", "mCherry")[0] == ["MARKER", "IDENTITY", "BF"]


def test_the_reporter_is_found_by_the_same_near_line_and_flagged(tmp_path):
    found, flags = roles(ome(tmp_path / "near.ome.tif", 590), "mCherry", "GFP")
    assert found == ["IDENTITY", "MARKER", "BF"]
    assert flags == ["mCherry channel found by its emission and an excitation of 590 nm (3 nm from the "
                     "preset's 587 nm line)"]
