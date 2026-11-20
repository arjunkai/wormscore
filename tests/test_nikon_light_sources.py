"""Two Nikon readings fixed by holding wormscore against what a dataset's authors wrote (1.26.20). The text
follows a Ti2 widefield file with three light sources (Zenodo 18609221: "100 percent LED intensity, pE 4000",
exposures 10, 200, 100 and 200 ms for DAPI, EGFP, mCherry and Alexa-647), shortened."""
from wormscore import readers


def plane(n, name, exposure, pe_on, aotf_shutter="Closed"):
    lines = "\n".join(f"     Line:{i}; ExW:{w}; Power:100.0; {'On' if w == pe_on else 'Off'}"
                      for i, w in enumerate((385, 470, 550, 635), 1))
    return f"""Plane #{n}:
 Name: {name}
 Component Count: 1
 Camera Settings:   Exposure: {exposure} ms
  Binning: 1x1
 Microscope Settings:   Microscope: Ti2 Microscope
  pE-4000, Shutter(pE-4000): Active
  pE-4000, MultiLaser(pE-4000):
{lines}

  NIDAQ, Shutter(AOTF): {aotf_shutter}
  NIDAQ, MultiLaser(AOTF):
     Line:1; ExW:488; Power:100.0; On

  Spectra/AuraII, Shutter(Spectra): Closed
  Spectra/AuraII, MultiLaser(Spectra):
     Line:3; ExW:470; Power:100.0; On
     Line:5; ExW:555; Power: 73.0; Off
"""


DESCRIPTION = "Metadata:\nDimensions: λ(3)\n" + plane(1, "DAPI", 10, 385) + "\n" + plane(2, "EGFP", 200, 470) \
    + "\n" + plane(3, "mCherry", 100, 550)


def test_a_channel_named_with_a_space_has_its_own_settings():
    """NIS-Elements named the channel " mCherry" and its plane "mCherry"; the settings were the camera text
    of all four channels ("exposure 10 ms; exposure 200 ms; ...")."""
    assert readers._nikon_settings(DESCRIPTION, "", " mCherry", 3) == "exposure 100 ms; 550nm@100"


def test_a_light_source_behind_a_closed_shutter_is_not_in_use():
    assert readers._nikon_settings(DESCRIPTION, "", "EGFP", 3) == "exposure 200 ms; 470nm@100"
    # its shutter open, the AOTF's 488 nm line is in use
    opened = DESCRIPTION.replace(plane(2, "EGFP", 200, 470), plane(2, "EGFP", 200, 470, aotf_shutter="Active"))
    assert readers._nikon_settings(opened, "", "EGFP", 3) == "exposure 200 ms; 470nm@100;488nm@100"


def test_a_blank_line_inside_a_closed_sources_list_does_not_end_it():
    """Found by review: the lines after a blank line were kept as in use."""
    gap = DESCRIPTION.replace("     Line:3; ExW:470; Power:100.0; On\n", "\n     Line:3; ExW:470; Power:100.0; On\n")
    assert readers._nikon_settings(gap, "", "mCherry", 3) == "exposure 100 ms; 550nm@100"
