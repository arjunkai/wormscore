"""1.26.11: laser power and detector voltage of a Nikon A1 point-scanning confocal.

The text is the description of "ER_a-flag_100x_param1_Z-stack002.nd2" (Zenodo 10814759, CC BY 4.0, a Nikon
A1 LFOV with a DU4 detector), shortened to the lines that matter. Each channel's part lists every detector
channel; before, none of it was read and every such image had empty settings."""
from wormscore import readers

CONFOCAL = """  {{Scanner Selection}}: Galvano
  {{Detector Selection}}: DU4
  \t\t{{GaAsP}}: CH2/3;
  CH1\t{{Laser Wavelength}}: 405.0\t{{Laser Power}}: 3.4
  \t{{PMT HV}}: 138\t{{PMT Offset}}: 0
  CH2\t{{Laser Wavelength}}: 488.0\t{{Laser Power}}: {power}
  \t{{PMT HV}}: {hv}\t{{PMT Offset}}: -15
  CH3\t{{Laser Wavelength}}: 561.0\t{{Laser Power}}: 3.8
  \t{{PMT HV}}: 140\t{{PMT Offset}}: -10
  {{Pinhole Size(um)}}: 56.19
  {{Line Average Mode}}: Average
  {{Line Average/Integrate Count}}: 2
"""


def description(power="1.5", hv="114"):
    block = CONFOCAL.format(power=power, hv=hv)
    planes = "".join(f"Plane #{k}:\r\n Name: {name}\r\n Component Count: 1\r\n Modality: Widefield Fluorescence, "
                     f"Laser Scan Confocal\r\n Camera Settings:                 \r\n{block}\r\n Microscope Settings:   "
                     "Microscope: Ti2 Microscope\r\n  Zoom: 1.00x\r\n  pE-300, MultiLaser(pE-300):\r\n"
                     "     Line:1; ExW:406; Power: 54.0; Off\r\n"
                     for k, name in enumerate(("DAPI", "a-FLAG A488", "ER"), start=1))
    return "Metadata:\r\nDimensions: Z(56)\r\nCamera Name: Nikon A1 LFOV\r\nNumber of Picture Planes: 3\r\n" + planes


def test_the_reporter_channel_gives_its_own_laser_power_and_detector_voltage():
    read = lambda d, name, ex: readers._nikon_settings(d, "Nikon A1 LFOV\n", name, 3, ex)
    assert read(description(), "a-FLAG A488", 488.0) == "488nm@1.5 HV 114; line average 2"
    assert read(description(), "DAPI", 405.0) == "405nm@3.4 HV 138; line average 2"
    # images that differ in laser power or detector voltage are in different settings groups
    assert read(description(power="6.0"), "a-FLAG A488", 488.0) == "488nm@6 HV 114; line average 2"
    assert read(description(hv="90"), "a-FLAG A488", 488.0) == "488nm@1.5 HV 90; line average 2"
    assert len({read(description(p, h), "a-FLAG A488", 488.0) for p, h in (("1.5", "114"), ("6.0", "114"), ("1.5", "90"))}) == 3


def test_without_a_matching_excitation_every_detector_channel_is_kept():
    """Not guessed: the settings then split on any channel's laser or voltage."""
    read = lambda ex: readers._nikon_settings(description(), "", "a-FLAG A488", 3, ex)
    whole = "405nm@3.4 HV 138; 488nm@1.5 HV 114; 561nm@3.8 HV 140; line average 2"
    assert read(None) == whole and read(514.0) == whole


def test_camera_files_are_read_as_before():
    old = "exposure 100 ms; multiplier 100; gain 5.1x"
    capturing = "Andor\nExposure: 100 ms\nMultiplier: 100\nConversion Gain: 5.1x\n"
    assert readers._nikon_settings("", capturing, "GFP", 2, 488.0) == old
