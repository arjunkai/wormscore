"""1.26.12: flat-field slide images of the same objective stand in first, and Zeiss channels without a name."""
import numpy as np

from test_acquisition_settings import zeiss_camera, zeiss_confocal
from test_flatfield import _corr
from wormscore import config as C
from wormscore import readers


def test_slide_images_of_the_same_objective_stand_in_before_the_nearest_day():
    """Slide images measured before 1.26.7 carry the settings text of that time. After a new scan the
    images' text differs, so no session is their own; with slide images of two objectives taken the same
    day, the nearest day alone picked the first, whatever its objective."""
    corr = _corr([{"index": 0, "system": "Zeiss", "settings": "avg 2 | 10x/0.3 | 1", "day": "2026-03-01", "flat": 0.5},
                  {"index": 1, "system": "Zeiss", "settings": "avg 2 | 63x/1.4 | 1", "day": "2026-03-01", "flat": 2.0}])
    img = {"system": "Zeiss", "settings": "gain 550; 488nm@12.00%; avg 2", "objective": "63x/1.4", "zoom": "1",
           "acquired": "2026-03-01 10:00:00", "height": 2, "width": 2}
    ses = corr.resolve(img)
    assert ses["index"] == 1 and ses["how"] == "nearest day, 2026-03-01 (other settings)"
    assert (corr.apply(np.full((1, 2, 2), 500, np.uint16), img) == 250).all()        # the 63x slide images' flat
    assert "other settings" in corr.warnings([img])[0]                              # and Build says so
    # an objective with no slide images at all still gets the nearest day, as before
    assert corr.resolve({**img, "objective": "40x/1.3"})["index"] == 0
    # and an image whose settings match a session is its own, as before
    own = corr.resolve({**img, "settings": "avg 2"})
    assert own["index"] == 1 and own["how"] == "same session"


def test_zeiss_channels_without_a_name_are_listed_by_their_dye():
    cfg = C.merge(C.DEFAULTS, {})
    camera = readers._zeiss_meta(zeiss_camera(), cfg)
    assert camera["channel_names"] == ["EGFP", "TL DIC"] and camera["channel_roles"] == ["MARKER", "BF"]
    confocal = readers._zeiss_meta(zeiss_confocal(550), cfg)                         # named channels: as before
    assert confocal["channel_names"] == ["Ch1-T1", "T PMT-T1", "ChS1-T2"]


def test_ome_tiff_gain_on_the_instruments_detector(tmp_path):
    """As Bio-Formats writes it: the channel names its Detector by ID and the gain is on the Detector. Two
    real Zeiss files converted to OME-TIFF (gain 550 and 800) both read as empty settings."""
    import tifffile
    from test_acquisition_settings import write_ome
    cfg = C.merge(C.DEFAULTS, {})

    def converted(name, gain):
        path = write_ome(tmp_path / name, bits=16)
        with tifffile.TiffFile(path) as f:
            xml, planes = f.ome_metadata, f.asarray()
        xml = xml.replace("<Image ", '<Instrument ID="Instrument:0"><Microscope/><Laser ID="LightSource:0:0" Wavelength="488"/>'
                          f'<Detector AmplificationGain="1.0" Gain="{gain}" ID="Detector:1:0" Zoom="1.0"/>'
                          '<Detector AmplificationGain="1.0" Gain="130.0" ID="Detector:0:2"/></Instrument><Image ', 1)
        xml = xml.replace("<LightPath/>", '<LightSourceSettings ID="LightSource:0:0" Attenuation="0.88"/>'
                          '<DetectorSettings Binning="1x1" ID="Detector:1:0"/><LightPath/>', 1)
        tifffile.imwrite(path, planes.reshape(-1, 32, 32), description=xml, metadata=None, photometric="minisblack")
        return readers.ome_images(path, cfg)[0]["settings"]

    assert converted("a.ome.tif", "550.0") == "gain 550; 488nm attenuation 0.88"
    assert converted("b.ome.tif", "800.0") == "gain 800; 488nm attenuation 0.88"
