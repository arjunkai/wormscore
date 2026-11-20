"""Readings fixed by holding wormscore against what datasets' authors wrote (1.26.20). The Nikon text
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


def test_line_averaging_that_is_off_is_left_out():
    """A C2+ file (S-BIAD2548): "{Line Average Mode}: None", count 0, read as "line none 0"."""
    block = ("CH2\t{Laser Wavelength}: 488.0\t{Laser Power}: 1.6\n\t{PMT HV}: 100\t{PMT Offset}: 0\n"
             "{Line Average Mode}: None\n{Line Average/Integrate Count}: 0\n")
    assert readers._nikon_confocal(block, 488) == ["488nm@1.6 HV 100"]
    assert readers._nikon_confocal(block.replace("None", "Average").replace("Count}: 0", "Count}: 4"), 488) == \
        ["488nm@1.6 HV 100", "line average 4"]


# ------------------------------------------------------------------ Olympus FluoView (.oib/.oif), same release

class _FakeOif:
    """The parts of an FV1000 .oib that olympus_images reads, as S-BIAD1944's F5-RC57_IL_60x_4.oib has them."""
    axes, shape = "CZYX", (2, 3, 8, 8)

    def __init__(self, path):
        def channel(name, dye, volt, gain, ex):
            return {"CH Name": name, "DyeName": dye, "AnalogPMTVoltage": volt, "AnalogPMTGain": gain,
                    "ExcitationWavelength": ex, "EmissionWavelength": ex + 30, "LightType": "Fluorescence"}
        self.mainfile = {
            "Acquisition Parameters Common": {"ZoomValue": 3.5, "LaserWavelength01": 488, "LaserTransmissivity01": 16.0,
                                              "Number of use Laser": 1, "IntegrationType": "None"},
            "Channel 1 Parameters": channel("CHS1", "Alexa Fluor 488", 714, 1000, 488),
            "Channel 2 Parameters": channel("CHS2", "Cy3", 770, 2000, 561),
            "Reference Image Parameter": {"WidthConvertValue": 0.207, "ValidBitCounts": 12},
            "Version Info": {"SystemName": "FLUOVIEW FV1000"},
        }

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fluoview_zoom_and_pmt_gain_are_read(monkeypatch):
    """S-BIAD1612's authors: "a 60x/1.3 NA oil immersion objective ... with a 3.5x zoom" and PMT "gain set at
    1000"; S-BIAD1944's Cy3 channel has gain 2000 (2x). Both were left out."""
    from wormscore import config as C
    monkeypatch.setattr(readers.oiffile, "OifFile", _FakeOif)
    monkeypatch.setattr(readers, "_olympus_plane_info", lambda f: {})
    for preset, settings in (("GFP", "PMT 714 V; 488nm@16%"), ("mCherry", "PMT 770 V; gain 2x; 488nm@16%")):
        cfg = C.merge(C.DEFAULTS, {})
        cfg["marker"] = C.resolve_marker({"preset": preset})
        img, = readers.olympus_images("x.oib", C.apply_identity(cfg, None))
        assert (img["zoom"], img["_zoom_before"], img["settings"]) == ("3.5", "", settings)


# ------------------------------------------------------------------ Leica SP5 / SP8X sequential steps, same release

def _step(laser, intensity, detectors, repeat=3):
    """One sequential step as a TCS SP5 (S-BIAD1480) or a 2015 SP8X (Zenodo 13752242) writes it: its laser
    line and active detectors listed three times, the laser's copies differing in display flags only."""
    flags = ['IsLineDeactivated="0" IsLineChecked="0"', 'IsChecked="0"', 'IsChecked="0"']
    lasers = "".join(f'<LaserLineSetting LaserLine="{laser}" IntensityDev="{intensity}" LineIndex="2" {flags[k]}/>'
                     for k in range(repeat))
    dets = "".join(f'<Detector Channel="{c}" Gain="{g}" IsActive="1"/>' for _ in range(repeat) for c, g in detectors)
    return (f'<ATLConfocalSettingDefinition LineAverage="1"><LaserArray>{lasers}</LaserArray>'
            f'<DetectorList>{dets}</DetectorList></ATLConfocalSettingDefinition>')


def test_a_detector_or_laser_line_listed_several_times_counts_once():
    """S-BIAD1480's authors: "BRD4 Laser -> 488 ... 15% / BRD4 PMT -> PMT 3 ... 753,1V"; "DAPI Laser -> 405 UV
    laser, 12% / DAPI PMT -> PMT1 ... 618,8". The copies made 5 detectors for 3 channels: none was resolved."""
    import xml.etree.ElementTree as ET
    elem = ET.fromstring("<Element><Data><Image/></Data><LDM_Block_Sequential_List>"
                         + _step(633, 44.998, [("4", 792.86)]) + _step(488, 14.997, [("2", 753.07)])
                         + _step(405, 12.0, [("1", 618.79)]) + "</LDM_Block_Sequential_List></Element>")
    chans = readers._leica_channels(elem)
    assert [(c["gain"], c["lasers"]) for c in chans] == [
        (792.86, [("633", 44.998)]), (753.07, [("488", 14.997)]), (618.79, [("405", 12.0)])]
    # two detectors of one step (eGFP and chlorophyll at 488 nm, Zenodo 13752242) stay two
    two = ET.fromstring("<Element><Data><Image/></Data><LDM_Block_Sequential_List>"
                        + _step(488, 15.0, [("2", 1172), ("4", 74)]) + "</LDM_Block_Sequential_List></Element>")
    assert [(c["gain"], c["lasers"]) for c in readers._leica_channels(two)] == [(1172, [("488", 15.0)]), (74, [("488", 15.0)])]


# ------------------------------------------------------------------ STELLARIS lifetime channels, same release

def _desc(lut, kind, detector):
    props = {"ChannelType": kind, "DetectorName": detector}
    return (f'<ChannelDescription LUTName="{lut}"><ChannelProperties>'
            + "".join(f"<ChannelProperty><Key>{k}</Key><Value>{v}</Value></ChannelProperty>" for k, v in props.items())
            + "</ChannelProperties></ChannelDescription>")


def test_lifetime_channels_computed_by_las_x_are_set_aside():
    """S-BIAD961 (STELLARIS 8, TauContrast): 7 channels from 5 detectors, two of them mean arrival times.
    Its authors: "515 nm laser at 40% power with a HyD-X detector (TauContrast mode, 595-640 nm)"."""
    import xml.etree.ElementTree as ET
    elem = ET.fromstring("<Element><Data><Image><ImageDescription><Channels>"
                         + _desc("Cyan", "PhotonCounting", "HyD X 2") + _desc("Rainbow", "MeanArrivalTime", "HyD X 2")
                         + _desc("Blue", "PhotonCounting", "HyD X 4") + _desc("Rainbow", "MeanArrivalTime", "HyD X 4")
                         + _desc("Red", "PhotonIntegration", "Trans PMT")
                         + "</Channels></ImageDescription></Image></Data></Element>")
    chans = [{"detector": d} for d in ("HyD X 2", "HyD X 4", "Trans PMT")]
    at = readers._leica_without_lifetime(elem, chans)
    assert [c and c["detector"] for c in at] == ["HyD X 2", None, "HyD X 4", None, "Trans PMT"]
    # the detectors in another order than the channels name them: not told, left unresolved
    assert readers._leica_without_lifetime(elem, chans[::-1]) is None
    # no computed channel: not this case
    plain = ET.fromstring("<Element>" + _desc("Cyan", "PhotonCounting", "HyD X 2") + "</Element>")
    assert readers._leica_without_lifetime(plain, chans[:1]) is None


def test_fluorophore_names_found_in_real_files_are_known():
    """idr0011's Zeiss file names its YFP "TaYFP" (channel TagYFP; its authors: "Dad4-YFP exposure times were
    40 msec"), S-BIAD961's STELLARIS "Leica/mCitrine"; worm labs use wrmScarlet and sfGFP."""
    from wormscore import config as C
    names = lambda p: C.PRESETS[p]["dye_names"]
    assert {"TaYFP", "TagYFP", "mCitrine", "Leica/mCitrine"} <= set(names("YFP"))
    assert "wrmScarlet" in names("mScarlet") and {"sfGFP", "mEGFP"} <= set(names("GFP"))
    assert C.DEFAULTS["marker"]["dye_names"] == names("GFP")


def test_read_as_before_forgets_only_the_names_the_presets_learned():
    """Found by review: a name the project wrote itself must stay known when files are read as 1.26.19."""
    from wormscore import config as C
    preset = C.resolve_marker({"preset": "YFP"})
    own = {**preset, "dye_names": ["sfGFP", "mCitrine"]}
    with readers.as_before():
        assert "mCitrine" not in readers._dyes(preset) and "Venus" in readers._dyes(preset)
        assert readers._dyes(own) == ["sfGFP", "mCitrine"]
        assert readers._named_match("C1", "mCitrine", None, None, own)
        assert not readers._named_match("C1", "mCitrine", None, None, preset)
    assert readers._named_match("C1", "mCitrine", None, None, preset)


def test_fluoview_zoom_and_gain_written_with_a_decimal_comma(monkeypatch):
    """FluoView writes numbers as '3,5.0' on a computer set to a decimal comma (see _olympus_number); the zoom
    was lost there (found by review, 20 November 2026)."""
    from wormscore import config as C

    class Comma(_FakeOif):
        def __init__(self, path):
            super().__init__(path)
            self.mainfile["Acquisition Parameters Common"]["ZoomValue"] = "3,5.0"
            self.mainfile["Channel 2 Parameters"]["AnalogPMTGain"] = "2000,0.0"
    monkeypatch.setattr(readers.oiffile, "OifFile", Comma)
    monkeypatch.setattr(readers, "_olympus_plane_info", lambda f: {})
    cfg = C.merge(C.DEFAULTS, {})
    cfg["marker"] = C.resolve_marker({"preset": "mCherry"})
    img, = readers.olympus_images("x.oib", C.apply_identity(cfg, None))
    assert (img["zoom"], img["settings"]) == ("3.5", "PMT 770 V; gain 2x; 488nm@16%")
