"""Lifetime images LAS X computed (1.26.21), beyond the STELLARIS MeanArrivalTime channels of 1.26.20.
LAS X FLIM keeps what it computed from a lifetime scan as series inside the scan's "FLIM Compressed"
folder: 29 kinds in Zenodo 13752242's FLIM250523.lif (STELLARIS 8): Intensity, Fast Flim, Standard
Deviation, Phasor Real/Imaginary/Intensity/Mask/Plot, FlimDecayTime, FlimAmplitude, FretEfficiencyPercentage,
FlimMeanDecayTime, ChiSquare and others. They were left unresolved as "0 detectors for 1 channels"; they
now say what they are. The scan's own series listed the channels of the series nested in it (ten names for
one channel). A channel type other than the ones known for measured light (PhotonCounting,
PhotonIntegration) or computed (MeanArrivalTime) is flagged. All of it only outside readers.as_1_26_20()
and readers.as_before(), so an existing project reads as before until it scans again."""
import xml.etree.ElementTree as ET

import numpy as np

import test_readers
from test_readers import write_lif
from wormscore import checkreading as K
from wormscore import readers


def stack():
    rng = np.random.default_rng(0)
    return rng.integers(0, 4096, (2, 6, 8)), rng.integers(0, 4096, (2, 6, 8))


def test_flim_results_are_named_and_never_scored(tmp_path):
    path = tmp_path / "flim.lif"
    write_lif(path, [("scan", stack()), ("FLIM Compressed/Fast Flim", stack())])
    cfg = K.default_settings()
    scan, fast = readers.leica_images(path, cfg)
    assert fast["location_in_file"] == "series 1 (FLIM Compressed/Fast Flim)"
    assert fast["channel_roles"] == ["OTHER", "OTHER"]
    assert fast["flags"] == ["lifetime result computed by LAS X (FLIM), not scored"]
    assert scan["channel_roles"] == ["MARKER", "BF"] and scan["flags"] == []
    with readers.as_before():
        old = readers.leica_images(path, cfg)[1]
    # as before: this test series carries scan settings, so it read as a scan (a real result has none and
    # read as "0 detectors for 1 channels")
    assert old["channel_roles"] == ["MARKER", "BF"] and old["flags"] == []


def test_a_series_reads_only_its_own_channels():
    scan = ET.fromstring(
        '<Element Name="scan"><Data><Image><ImageDescription><Channels><ChannelDescription LUTName="Green"/>'
        '</Channels></ImageDescription></Image></Data><Children><Element Name="FLIM Compressed"><Children>'
        '<Element Name="Fast Flim"><Data><Image><ImageDescription><Channels><ChannelDescription LUTName="Rainbow"/>'
        '</Channels></ImageDescription><Attachment><ATLConfocalSettingDefinition/></Attachment></Image></Data>'
        '</Element></Children></Element></Children></Element>')
    assert [c.get("LUTName") for c in scan.iter("ChannelDescription")] == ["Green", "Rainbow"]
    own = readers._own(scan)
    assert [c.get("LUTName") for c in own.iter("ChannelDescription")] == ["Green"]
    assert own.find(".//ATLConfocalSettingDefinition") is None and scan.find(".//ATLConfocalSettingDefinition") is not None


def test_a_channel_of_an_unknown_type_is_flagged(tmp_path, monkeypatch):
    """Found by review: turning such a channel's role to OTHER could, after a new scan, drop an image already
    scored. Each channel here has its own detector (computed channels add channels beyond the detectors), so
    the roles stay and the flag names the type."""
    real = test_readers._series_xml

    def typed(*a, **k):
        xml = real(*a, **k)
        for kind in ("TauGating", "PhotonIntegration"):    # GFP channel of an unknown type, brightfield measured
            xml = xml.replace('BytesInc="', 'Mark="', 1).replace(
                '/>', f'><ChannelProperties><ChannelProperty><Key>ChannelType</Key><Value>{kind}</Value>'
                      '</ChannelProperty></ChannelProperties></ChannelDescription>', 1).replace('Mark="', 'BytesInc="', 1)
        return xml
    monkeypatch.setattr(test_readers, "_series_xml", typed)
    path = tmp_path / "tau.lif"
    write_lif(path, [("scan", stack())])
    cfg = K.default_settings()
    img = readers.leica_images(path, cfg)[0]
    assert img["channel_roles"] == ["MARKER", "BF"]
    assert img["flags"] == ["channel type TauGating not known to wormscore: check that the reporter channel holds "
                            "measured light"]
    for old in (readers.as_before, readers.as_1_26_20):
        with old():
            assert readers.leica_images(path, cfg)[0]["flags"] == []


def test_slide_images_are_read_as_the_projects_images_were(tmp_path):
    """Found by review: 1.26.20 is released, and its gate told only before and after 1.26.20. A project
    scanned by 1.26.20 reads its slide and control images as 1.26.20 did until it scans again."""
    from wormscore import flatfield as FF
    path = tmp_path / "flim.lif"
    write_lif(path, [("scan", stack()), ("FLIM Compressed/Fast Flim", stack())])
    p = tmp_path / "p"
    (p / "outputs").mkdir(parents=True)
    inv = p / "outputs" / "inventory.csv"

    def flags():
        with FF.reading(p):
            return readers.leica_images(path, K.default_settings())[1]["flags"]
    new = ["lifetime result computed by LAS X (FLIM), not scored"]
    assert flags() == new                                              # never scanned: this version
    inv.write_text("source,file\nA,a.lif\n", encoding="utf-8")       # before 1.26.20
    assert flags() == [] and readers._BEFORE.get() is False
    inv.write_text("source,file,scanned_with\nA,a.lif,1.26.20\n", encoding="utf-8")
    assert flags() == []
    inv.write_text("source,file,scanned_with\nA,a.lif,1.26.21\nA,b.lif,1.26.20\n", encoding="utf-8")
    assert flags() == []                                               # the oldest
    inv.write_text("source,file,scanned_with\nA,a.lif,1.26.21\n", encoding="utf-8")
    assert flags() == new
    inv.write_text("source,file,scanned_with\n", encoding="utf-8")   # scanned, nothing found
    assert flags() == new
    inv.write_text("source,file,scanned_with\nA,a.lif,edited\nA,b.lif,1.26.20\n", encoding="utf-8")
    assert flags() == []                                               # a value that is no version is passed over
    inv.write_text("source,file,scanned_with\nA,a.lif,edited\n", encoding="utf-8")
    assert flags() == new                                              # none left: as this version, as 1.26.20 did
    with readers.as_before():
        assert readers._before_1_26_21()
    assert readers.reading_as("1.27.0").__class__.__name__ == "nullcontext"
