"""Flat-field correction from reference-slide images per imaging session (1.21.0)."""
import json

import numpy as np
import pytest
import tifffile
from PIL import Image

from wormscore import flatfield as FF
from wormscore import methods, pipeline
from wormscore.demo import NZ, PX_UM, SIZE, Z_UM, make_demo
from wormscore.files import read_csv

QUIET = {"log": lambda *a: None}


def shading():
    """Light across the field: 100% in the middle, 60% in the corners."""
    yy, xx = np.mgrid[:SIZE, :SIZE].astype(float)
    r2 = ((yy - SIZE / 2) ** 2 + (xx - SIZE / 2) ** 2) / (2 * (SIZE / 2) ** 2)
    return 1 - 0.4 * r2


def slides(folder, level=2000.0, offset=100.0, n=2, saturate=False, seed=0):
    """Reference-slide images at the demo's settings: offset + level x shading, with noise."""
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    for k in range(n):
        g = offset + level * shading()[None] + rng.normal(0, 5, (NZ, SIZE, SIZE))
        if saturate:
            g[:, :40, :40] = 65535
        bf = np.full((NZ, SIZE, SIZE), 1800.0)
        tifffile.imwrite(folder / f"slide_{k}.ome.tif", np.clip(np.stack([g, bf]), 0, 65535).astype(np.uint16),
                         photometric="minisblack", ome=True,
                         metadata={"axes": "CZYX", "PhysicalSizeX": PX_UM, "PhysicalSizeY": PX_UM,
                                   "PhysicalSizeZ": Z_UM, "Channel": {"Name": ["GFP", "Brightfield"]}})
    return folder


def darks(folder, offset=100.0):
    folder.mkdir(parents=True, exist_ok=True)
    g = np.full((NZ, SIZE, SIZE), offset)
    tifffile.imwrite(folder / "dark.ome.tif", np.stack([g, g]).astype(np.uint16), photometric="minisblack", ome=True,
                     metadata={"axes": "CZYX", "PhysicalSizeX": PX_UM, "PhysicalSizeY": PX_UM,
                               "PhysicalSizeZ": Z_UM, "Channel": {"Name": ["GFP", "Brightfield"]}})
    return folder


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    p = tmp_path_factory.mktemp("ff") / "project"
    make_demo(p, n_per_group=2, **QUIET)
    pipeline.inventory(p, **QUIET)
    return p


def test_a_reference_image_is_corrected_to_an_even_field(project, tmp_path):
    before = json.loads((project / "config.json").read_text(encoding="utf-8"))
    res = FF.measure(project, slides(tmp_path / "ref"), darks(tmp_path / "dark"), **QUIET)
    (s,) = res["sessions"]
    assert s["images"] == 2 and s["dark"] and s["factor"] == 1.0
    assert 55 < s["light_min_pct"] < 75 and s["light_max_pct"] > 100
    cfg = json.loads((project / "config.json").read_text(encoding="utf-8"))
    assert cfg[FF.KEY]["across_field"] and cfg[FF.KEY]["between_sessions"]
    corr = FF.load(project, pipeline.C.load(project))
    img = pipeline.load_images(project)[0]
    raw = np.clip(100 + 2000 * shading()[None].repeat(3, 0), 0, 65535).astype(np.uint16)
    out = corr.apply(raw, img).astype(float) - 100
    assert out.std() / out.mean() < 0.02          # 40% fall-off before, under 2% after
    FF.turn_off(project, **QUIET)
    assert json.loads((project / "config.json").read_text(encoding="utf-8")) == before


def test_build_uses_the_corrected_planes_and_records_it(project, tmp_path):
    FF.measure(project, slides(tmp_path / "ref"), **QUIET)
    pipeline.prepare(project, **QUIET)
    key = read_csv(project / "key" / "key.csv")
    assert all(r["imaging_session"] and r["session_factor"] == "1.0" for r in key)
    code = key[0]["code"]
    d = project / "blind" / code
    assert (d / "saturated.png").is_file()
    log = (project / "log.md").read_text(encoding="utf-8")
    assert "Flat-field correction: across the field and between imaging sessions" in log
    assert "no dark images, camera offset taken as 0" in log
    text = methods.flat_field_sentence(project, pipeline.C.load(project))
    assert "uniform fluorescent reference slide" in text and "camera offset was taken as zero" in text
    # the corner of a coded plane is brighter than it was recorded (the light there was 60%)
    img = next(i for i in pipeline.load_images(project) if i["local_path"] == key[0]["local_path"])
    rec = pipeline.R.read(img, img["marker_channel"])[0].astype(float)
    got = np.array(Image.open(d / "planes" / "z00.png"))
    plane = got[..., 0].astype(float) * 256 + got[..., 1]
    assert plane[:20, :20].mean() > 1.2 * rec[:20, :20].mean()


def test_saturated_pixels_are_still_counted(project, tmp_path):
    FF.measure(project, slides(tmp_path / "ref"), **QUIET)
    img = pipeline.load_images(project)[0]
    corr = FF.load(project, pipeline.C.load(project))
    raw = np.full((3, SIZE, SIZE), 1000, np.uint16)
    raw[1, 5, 5] = 65535
    v_raw = raw.copy()
    from wormscore import measure as M
    out = corr.apply(v_raw, img)
    mask = np.zeros((SIZE, SIZE), bool)
    mask[3:8, 3:8] = True
    filt = M.median_filter_stack(out, 3)
    sat_mask = (raw >= 65535).any(0)
    m = M.measure_region(out, filt, mask, mask, PX_UM, 65535, pipeline.C.load(project), sat_mask)
    assert m["saturated_px_mip"] == 1


def test_saturated_reference_images_are_not_used(project, tmp_path):
    with pytest.raises(SystemExit, match="No reference image could be used"):
        FF.measure(project, slides(tmp_path / "sat", saturate=True), **QUIET)


def test_reference_folder_must_be_outside_the_project(project):
    with pytest.raises(SystemExit, match="outside the project"):
        FF.measure(project, project / "data", **QUIET)


def test_sessions_match_by_microscope_settings_and_day():
    corr = FF.Correction.__new__(FF.Correction)
    corr.sessions = [{"system": "A", "settings": "s", "day": "2026-03-01"},
                     {"system": "A", "settings": "s", "day": "2026-03-02"},
                     {"system": "B", "settings": "", "day": ""}]
    img = lambda system, settings, acquired: {"system": system, "settings": settings, "acquired": acquired}
    assert corr.session_for(img("A", "s", "2026-03-02 10:11:12"))["day"] == "2026-03-02"
    assert corr.session_for(img("A", "s", "2026-03-05 10:00:00")) is None       # no slide that day
    assert corr.session_for(img("A", "s", "")) is None                          # no date, two sessions
    assert corr.session_for(img("A", "other", "2026-03-01")) is None            # other settings
    assert corr.session_for(img("B", "", "2026-03-09"))["system"] == "B"        # the only session, no date


def test_the_session_factor_scales_the_signal_above_the_dark_level():
    corr = FF.Correction.__new__(FF.Correction)
    corr.sessions = [{"index": 0, "system": "A", "settings": "", "day": "", "factor": 2.0, "dark_level": 100.0}]
    corr.arrays = {"flat_0": np.ones((4, 4), np.float32), "dark_0": np.full((4, 4), 100, np.float32)}
    img = {"system": "A", "settings": "", "acquired": ""}
    raw = np.full((2, 4, 4), 600, np.uint16)
    corr.ff = {"between_sessions": True}
    assert (corr.apply(raw, img) == 1100).all()        # 100 + 2 x 500
    corr.ff = {"across_field": True}
    assert (corr.apply(raw, img) == 600).all()         # factor not used


def test_build_stops_when_an_image_has_no_reference_session(project, tmp_path):
    FF.measure(project, slides(tmp_path / "ref"), **QUIET)
    sm = json.loads((project / "outputs" / "flat_field" / "sessions.json").read_text(encoding="utf-8"))
    sm["sessions"][0]["system"] = "another microscope"
    (project / "outputs" / "flat_field" / "sessions.json").write_text(json.dumps(sm), encoding="utf-8")
    with pytest.raises(SystemExit, match="changed after they were made"):
        pipeline.prepare(project, **QUIET)
    corr = FF.Correction.__new__(FF.Correction)
    corr.sessions = sm["sessions"]
    with pytest.raises(SystemExit, match="have no reference images"):
        corr.check(pipeline.load_images(project))


def test_locked_once_scoring_has_started(tmp_path):
    p = tmp_path / "p"
    make_demo(p, n_per_group=1, **QUIET)
    (p / "scoring").mkdir()
    (p / "scoring" / "scores.json").write_text("{}")
    with pytest.raises(SystemExit, match="flat-field correction is locked"):
        FF.measure(p, tmp_path, **QUIET)
    with pytest.raises(SystemExit, match="flat-field correction is locked"):
        FF.turn_off(p, **QUIET)


def test_a_project_without_the_correction_builds_as_before(tmp_path):
    p = tmp_path / "plain"
    make_demo(p, n_per_group=1, **QUIET)
    pipeline.inventory(p, **QUIET)
    pipeline.prepare(p, **QUIET)
    key = read_csv(p / "key" / "key.csv")
    assert "imaging_session" not in key[0]
    assert not list((p / "blind").glob("*/saturated.png"))
    assert "Flat-field" not in (p / "log.md").read_text(encoding="utf-8")
    assert methods.flat_field_sentence(p, pipeline.C.load(p)) == ""


def test_the_home_page_gets_the_setting_without_its_checksum(project, tmp_path):
    from wormscore.home import project_status
    FF.measure(project, slides(tmp_path / "ref"), **QUIET)
    ff = project_status(project)["flat_field"]
    assert ff["on"] == {"across_field": True, "between_sessions": True} and ff["summary"]["reference_images"] == 2
    assert ff["locked"] == ""
