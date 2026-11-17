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


def test_saturated_slide_images_are_not_used(project, tmp_path):
    with pytest.raises(SystemExit, match="None of the slide images could be used"):
        FF.measure(project, slides(tmp_path / "sat", saturate=True), **QUIET)


def test_only_the_projects_own_image_folders_are_refused(project):
    with pytest.raises(SystemExit, match="holds the project's own images"):
        FF.measure(project, project / "data", **QUIET)
    res = FF.measure(project, slides(project / "slides"), **QUIET)     # a folder inside the project is fine
    assert res["reference_images"] == 2


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


def test_changed_measurements_stop_the_build(project, tmp_path):
    FF.measure(project, slides(tmp_path / "ref"), **QUIET)
    f = project / "outputs" / "flat_field" / "sessions.json"
    sm = json.loads(f.read_text(encoding="utf-8"))
    sm["sessions"][0]["factor"] = 3.0
    f.write_text(json.dumps(sm), encoding="utf-8")
    with pytest.raises(SystemExit, match="changed after they were made"):
        pipeline.prepare(project, **QUIET)


def test_an_image_without_usable_slide_images_is_built_as_recorded_with_a_warning(tmp_path):
    p = tmp_path / "p"
    make_demo(p, n_per_group=1, **QUIET)
    pipeline.inventory(p, **QUIET)
    FF.measure(p, slides(tmp_path / "ref"), **QUIET)
    f = p / "outputs" / "flat_field" / "sessions.json"
    sm = json.loads(f.read_text(encoding="utf-8"))
    sm["sessions"][0]["system"] = "another microscope"
    f.write_text(json.dumps(sm), encoding="utf-8")
    with np.load(p / "outputs" / "flat_field" / "flats.npz") as z:
        flats = {k: z[k] for k in z.files}
    FF._save(p, sm, flats, {"across_field": True, "between_sessions": True})     # as if measured so
    said = []
    pipeline.prepare(p, log=said.append)
    assert any(x.startswith("Warning:") and "used as recorded" in x for x in said)
    key = read_csv(p / "key" / "key.csv")
    assert all(r["flat_field"].startswith("not corrected") for r in key)
    img = next(i for i in pipeline.load_images(p) if i["local_path"] == key[0]["local_path"])
    rec = pipeline.R.read(img, img["marker_channel"])[0]
    got = np.array(Image.open(p / "blind" / key[0]["code"] / "planes" / "z00.png"))
    assert np.array_equal(got[..., 0].astype(np.uint16) * 256 + got[..., 1], rec)
    assert FF.build_note(p) == {"same": 0, "other": 0, "none": 2}
    assert "used as recorded" in methods.flat_field_sentence(p, pipeline.C.load(p))
    assert "Warning:" in (p / "log.md").read_text(encoding="utf-8")


def test_starting_again_and_turning_off_wait_for_scoring_but_adding_does_not(tmp_path):
    p = tmp_path / "p"
    make_demo(p, n_per_group=1, **QUIET)
    (p / "scoring").mkdir()
    (p / "scoring" / "scores.json").write_text("{}")
    with pytest.raises(SystemExit, match="stay as they are"):
        FF.measure(p, tmp_path, **QUIET)
    with pytest.raises(SystemExit, match="stay as they are"):
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


# ------------------------------------------------------------------ 1.21.1: later sessions, nearest day

def test_sessions_can_be_added_after_scoring_began_without_changing_coded_images(tmp_path, monkeypatch):
    p = tmp_path / "p"
    make_demo(p, n_per_group=1, **QUIET)
    pipeline.inventory(p, **QUIET)
    FF.measure(p, slides(tmp_path / "ref"), **QUIET)
    pipeline.prepare(p, **QUIET)
    code = read_csv(p / "key" / "key.csv")[0]["code"]
    plane = (p / "blind" / code / "planes" / "z00.png").read_bytes()
    first = json.loads((p / "outputs" / "flat_field" / "sessions.json").read_text(encoding="utf-8"))["sessions"][0]
    (p / "scoring").mkdir()
    (p / "scoring" / "scores.json").write_text("{}")
    real = FF._images
    def later_day(folder, cfg, log, skipped):     # the demo writes no dates: give the new slides a day
        return [{**r, "key": r["key"][:2] + ("2026-03-02",)} for r in real(folder, cfg, log, skipped)]
    monkeypatch.setattr(FF, "_images", later_day)
    (new,) = FF.add_sessions(p, slides(tmp_path / "later", level=1000), **QUIET)
    # brought to the level the first session's microscope was brought to (half the light: about twice the factor)
    assert new["added_after_scoring_began"] and new["factor"] == pytest.approx(first["group_brightness"]
                                                                               / new["brightness"], abs=1e-3)
    assert 1.8 < new["factor"] < 2.0
    sm = json.loads((p / "outputs" / "flat_field" / "sessions.json").read_text(encoding="utf-8"))
    assert sm["sessions"][0] == first and len(sm["sessions"]) == 2
    corr = FF.load(p, pipeline.C.load(p))                  # the stored checksum matches what was written
    assert np.array_equal(corr.arrays["flat_0"], FF.Correction(p, pipeline.C.load(p)).arrays["flat_0"])
    assert (p / "blind" / code / "planes" / "z00.png").read_bytes() == plane
    log = (p / "log.md").read_text(encoding="utf-8")
    assert "scoring had begun" in log and "(added after scoring began)" in log
    assert "added after scoring began" in methods.flat_field_sentence(p, pipeline.C.load(p))
    with pytest.raises(SystemExit, match="No new session was added"):
        FF.add_sessions(p, slides(tmp_path / "again"), **QUIET)


def _corr(sessions, ff=None):
    corr = FF.Correction.__new__(FF.Correction)
    corr.sessions = [{"settings": "", "factor": 1.0, "dark_level": 0.0, "height": 2, "width": 2, **x} for x in sessions]
    corr.arrays = {}
    for x in corr.sessions:
        corr.arrays[f"flat_{x['index']}"] = np.full((2, 2), x.get("flat", 1.0), np.float32)
        corr.arrays[f"dark_{x['index']}"] = np.zeros((2, 2), np.float32)
    corr.ff = ff or {"across_field": True, "between_sessions": True}
    return corr


def test_a_day_without_slide_images_uses_the_nearest_day():
    corr = _corr([{"index": 0, "system": "A", "day": "2026-03-01", "factor": 2.0, "flat": 0.5},
                  {"index": 1, "system": "A", "day": "2026-03-10", "factor": 0.5}])
    img = {"system": "A", "settings": "", "acquired": "2026-03-04 09:00:00", "height": 2, "width": 2}
    ses = corr.resolve(img)
    assert ses["how"] == "nearest day, 2026-03-01"
    raw = np.full((1, 2, 2), 500, np.uint16)
    assert (corr.apply(raw, img) == 1000).all()            # its flat (0.5), but no brightness factor
    cols = corr.key_columns(img)
    assert cols["flat_field"] == "nearest day, 2026-03-01" and cols["session_factor"] == "not corrected"
    assert "nearest day" in corr.warnings([img])[0]


def test_an_image_without_a_date_uses_the_average_of_several_sessions():
    corr = _corr([{"index": 0, "system": "A", "day": "2026-03-01", "flat": 0.5},
                  {"index": 1, "system": "A", "day": "2026-03-10", "flat": 1.0}])
    img = {"system": "A", "settings": "", "acquired": "", "height": 2, "width": 2}
    assert corr.resolve(img)["how"] == "average of 2 sessions"
    assert (corr.apply(np.full((1, 2, 2), 750, np.uint16), img) == 1000).all()


def test_other_settings_of_the_same_size_stand_in_and_other_sizes_do_not():
    corr = _corr([{"index": 0, "system": "A", "settings": "gain 2", "day": ""}])
    img = {"system": "A", "settings": "gain 5", "acquired": "", "height": 2, "width": 2}
    assert corr.resolve(img)["how"].endswith("(other settings)")
    assert corr.resolve({**img, "height": 4}) is None
    assert corr.not_corrected({**img, "height": 4}) == "slide images of another size"
    assert corr.resolve({**img, "system": "B"}) is None
    raw = np.full((1, 4, 4), 7, np.uint16)
    assert corr.apply(raw, {**img, "system": "B", "height": 4, "width": 4}) is raw


def test_dates_in_locale_forms_give_the_same_day():
    assert FF.acquired_day("2020-09-24 16:01:38") == "2020-09-24"
    assert FF.acquired_day("9/24/2020 4:01:38 PM") == "2020-09-24"       # Nikon, US locale
    assert FF.acquired_day("9/4/2020 4:01:38 PM") == FF.acquired_day("9/4/2020 11:59:00 PM") == "2020-09-04"
    assert FF.acquired_day("24.09.2020 16:01:38") == "2020-09-24"
    assert FF.acquired_day("") == FF.acquired_day("sometime") == ""


def test_the_objective_and_zoom_are_part_of_the_session():
    a = FF.session_key({"system": "A", "settings": "gain 2", "objective": "63x/1.4", "zoom": "2", "acquired": ""})
    b = FF.session_key({"system": "A", "settings": "gain 2", "objective": "10x/0.3", "zoom": "2", "acquired": ""})
    assert a != b and a[1] == "gain 2 | 63x/1.4 | 2"
