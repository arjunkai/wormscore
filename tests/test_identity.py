"""Marker presets and the optional identity channel (a second marker that shows where the cell is)."""
import json
import shutil
import threading

import numpy as np
import pytest
from PIL import Image

from wormscore import config as C
from wormscore import readers as R
from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare, read_csv
from wormscore.server import make_server

from test_app import request

QUIET = dict(log=lambda *a: None)


def write_cfg(project, **changes):
    cfg = json.loads((project / "config.json").read_text())
    for k, v in changes.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    (project / "config.json").write_text(json.dumps(cfg))


@pytest.fixture(scope="module")
def identity_project(tmp_path_factory):
    """Simulated worms with a GFP reporter and an mCherry identity channel that marks every cell."""
    project = tmp_path_factory.mktemp("identity")
    make_demo(project, n_per_group=6, identity=True, **QUIET)
    inventory(project, **QUIET)
    prepare(project, **QUIET)
    return project


# ---------------------------------------------------------------- presets

def test_no_preset_keeps_the_gfp_settings_exactly(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"marker": {"label": "GFP"}}))
    cfg = C.load(tmp_path)
    assert cfg["marker"] == C.DEFAULTS["marker"]
    assert cfg["identity"] is None
    assert C.marker_color(cfg) == [0, 1, 0]


def test_a_preset_replaces_the_gfp_defaults_and_fields_override_it(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"marker": {"preset": "mcherry", "excitation_nm": [594]}}))
    m = C.load(tmp_path)["marker"]
    assert m["label"] == "mCherry" and m["preset"] == "mCherry"
    assert m["excitation_nm"] == [594]                       # the given field wins
    assert "EGFP" not in m["dye_names"] and m["leica_lut"] is None   # nothing left over from GFP
    assert m["emission_nm"] == C.PRESETS["mCherry"]["emission_nm"]


def test_every_preset_is_complete_and_the_gfp_preset_matches_the_defaults():
    for name, p in C.PRESETS.items():
        assert set(C.MARKER_KEYS) - {"emission_nm"} <= set(p), name
    gfp = dict(C.PRESETS["GFP"])
    assert gfp.pop("color") == [0, 1, 0]
    assert gfp == C.DEFAULTS["marker"]


def test_unknown_preset_and_bad_identity_settings_stop_with_a_plain_message(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"marker": {"preset": "dsRed"}}))
    with pytest.raises(SystemExit, match="Choose one of"):
        C.load(tmp_path)
    for ident, msg in [({"label": "red"}, "needs a \"preset\""),
                       ({"preset": "mCherry", "spots_from": "both"}, "spots_from"),
                       ({"preset": "mCherry", "required": "yes"}, "required"),
                       ({"preset": "mCherry", "colour": [1, 0, 1]}, "unknown setting"),
                       ({"preset": "mCherry", "color": [2, 0, 0]}, "color"),
                       ({"preset": "GFP"}, "same label")]:
        (tmp_path / "config.json").write_text(json.dumps({"identity": ident}))
        with pytest.raises(SystemExit, match=msg):
            C.check(C.load(tmp_path))


def test_identity_color_never_matches_the_reporter():
    cfg = {"marker": {"color": [1, 0, 1]}, "identity": {"color": [1, 0, 1]}}
    assert C.identity_color(cfg) == [0, 1, 0]
    cfg = {"marker": {}, "identity": {"color": [0, 1, 0]}}
    assert C.identity_color(cfg) == [1, 0, 1]


# ---------------------------------------------------------------- channel rules

def _cfg(identity=None, marker=None):
    cfg = dict(C.DEFAULTS)
    cfg["marker"] = C.resolve_marker(marker or {})
    cfg["identity"] = C.resolve_identity(identity)
    return cfg


def test_named_channels_get_marker_identity_and_other_roles():
    cfg = _cfg({"preset": "mCherry"})
    assert R.marker_from_named("GFP", None, None, False, cfg) == "MARKER"
    assert R.marker_from_named("mCherry", None, None, False, cfg) == "IDENTITY"
    assert R.marker_from_named("ch3", None, "561", False, cfg) == "IDENTITY"   # by laser line
    assert R.marker_from_named("ch4", None, None, False, cfg, emission=610) == "IDENTITY"   # by emission
    assert R.marker_from_named("DAPI", None, "405", False, cfg) == "OTHER"
    assert R.marker_from_named("Brightfield", None, None, False, cfg) == "BF"
    # without identity settings nothing is ever an identity channel
    assert R.marker_from_named("mCherry", None, "561", False, _cfg()) == "OTHER"


def test_leica_channels_get_the_identity_role_by_laser_and_band():
    cfg = _cfg({"preset": "mCherry"})
    gfp = {"detector": "HyD 2", "lasers": [("488", 5.0)], "band": "495-550"}
    red = {"detector": "HyD 4", "lasers": [("561", 3.0)], "band": "590-650"}
    trans = {"detector": "Trans PMT", "lasers": [], "band": ""}
    assert [R.marker_from_leica(c, l, cfg) for c, l in ((gfp, "Green"), (red, "Red"), (trans, "Gray"))] \
        == ["MARKER", "IDENTITY", "BF"]


def test_a_named_dye_settles_two_identity_matches():
    cfg = _cfg({"preset": "mCherry"})
    roles = ["MARKER", "IDENTITY", "IDENTITY"]
    assert R.prefer_named_dye(roles, ["EGFP", None, "mCherry"], cfg) == ["MARKER", "OTHER", "IDENTITY"]
    assert R.pick(["MARKER", "IDENTITY", "IDENTITY"], "IDENTITY") is None


def test_an_mcherry_reporter_preset_finds_the_red_channel():
    cfg = _cfg(marker={"preset": "mCherry"})
    assert R.marker_from_named("mCherry", None, None, False, cfg) == "MARKER"
    assert R.marker_from_named("GFP", None, "488", False, cfg) == "OTHER"


# ---------------------------------------------------------------- building the blind set

def test_identity_channel_is_found_and_shown_but_never_measured(identity_project):
    inv = read_csv(identity_project / "outputs" / "inventory.csv")
    assert all(r["channel_roles"] == "MARKER/IDENTITY/BF" and r["identity_channel"] == "1" for r in inv)
    w = identity_project / "blind" / "W0001"
    meta = json.loads((w / "meta.json").read_text())
    assert meta["has_identity"] is True
    # display only: 8-bit gray, one per plane plus the projection; the reporter planes are unchanged
    assert Image.open(w / "identity_mip.png").mode == "L"
    assert len(list((w / "identity").glob("z*.png"))) == meta["n_z"]
    assert Image.open(w / "planes" / "z00.png").mode == "RGB"
    key = read_csv(identity_project / "key" / "key.csv")
    assert all(r["identity_channel_used"] == "True" for r in key)
    log = (identity_project / "log.md").read_text()
    assert "Identity channel: mCherry" in log and "never measured" in log


def test_cells_are_found_on_the_identity_channel_even_when_the_reporter_is_missing(identity_project):
    truth = {r["file"]: r for r in read_csv(identity_project / "demo_truth.csv")}
    key = {r["code"]: truth[r["file"]] for r in read_csv(identity_project / "key" / "key.csv")}
    auto = read_csv(identity_project / "outputs" / "auto_measurements.csv")
    assert {r["spots_found_on"] for r in auto} == {"mCherry"}
    checked = 0
    for code, t in key.items():
        cells = [t["cell_1"], t["cell_2"]]
        if "missing" not in cells:
            continue
        top2 = sorted((r for r in auto if r["code"] == code), key=lambda r: int(r["candidate"]))[:2]
        reporter = sorted(float(r["mean_mip_minus_bg"]) for r in top2)
        # both cells were found on the identity channel; the missing one has almost no reporter signal
        assert len(top2) == 2 and reporter[0] < 150 and reporter[1] > 300, (code, cells, reporter)
        checked += 1
    assert checked >= 2


def test_spots_from_reporter_keeps_the_old_behaviour(identity_project, tmp_path):
    p = tmp_path / "p"
    shutil.copytree(identity_project, p)
    write_cfg(p, identity={"preset": "mCherry", "spots_from": "reporter"})
    prepare(p, reassign=True, **QUIET)
    assert {r["spots_found_on"] for r in read_csv(p / "outputs" / "auto_measurements.csv")} == {"GFP"}


def test_images_without_the_identity_channel_are_left_out_or_kept(tmp_path):
    p = tmp_path / "p"
    make_demo(p, n_per_group=2, **QUIET)                   # GFP and brightfield only
    write_cfg(p, identity={"preset": "mCherry"})
    inventory(p, **QUIET)
    assert all("mCherry identity channel UNRESOLVED" in r["flags"]
               for r in read_csv(p / "outputs" / "inventory.csv"))
    with pytest.raises(SystemExit, match="No images passed"):
        prepare(p, **QUIET)
    assert all("identity channel could not be identified" in r["reason"]
               for r in read_csv(p / "outputs" / "excluded.csv"))
    write_cfg(p, identity={"preset": "mCherry", "required": False})
    prepare(p, **QUIET)
    meta = json.loads((p / "blind" / "W0001" / "meta.json").read_text())
    assert meta["has_identity"] is False and not (p / "blind" / "W0001" / "identity").exists()
    assert any(f["id"] == "no_identity" for f in meta["flags"])


def test_projects_without_identity_keep_their_files_and_columns(demo_project):
    meta = json.loads((demo_project / "blind" / "W0001" / "meta.json").read_text())
    assert "has_identity" not in meta
    assert "identity_channel" not in read_csv(demo_project / "outputs" / "inventory.csv")[0]
    assert "spots_found_on" not in read_csv(demo_project / "outputs" / "auto_measurements.csv")[0]
    assert "identity_channel_used" not in read_csv(demo_project / "key" / "key.csv")[0]


def test_imagej_tiff_can_name_an_identity_channel(tmp_path):
    cfg = _cfg({"preset": "mCherry"})
    cfg["sources"] = [{"name": "ij", "path": str(tmp_path), "imagej_tiff": {"channels": ["marker", "identity"]}}]
    C.check(cfg)
    cfg["identity"] = None
    with pytest.raises(SystemExit, match="no \"identity\" settings"):
        C.check(cfg)


# ---------------------------------------------------------------- scoring page

def test_scoring_page_gets_identity_images_but_no_genotype(identity_project, tmp_path):
    p = tmp_path / "p"
    shutil.copytree(identity_project, p)
    srv = make_server(p, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        cfg = json.loads(request(port, "GET", "/api/config")[1])
        assert cfg["identity"] == {"label": "mCherry", "color": [1, 0, 1]}
        assert cfg["marker_color"] == [0, 1, 0]
        for path in ("/img/W0001/identity_mip.png?v=1", "/img/W0001/identity/z00.png?v=1"):
            assert request(port, "GET", path)[0] == 200, path
        text = request(port, "GET", "/api/worm/W0001")[1].decode()
        for leak in ("control", "mutant", "demo_", "genotype"):
            assert leak not in text
    finally:
        srv.shutdown()
        srv.server_close()


def test_identity_display_is_scaled_per_image():
    cfg = {"display": {"identity_contrast_percentiles": [50, 99.9]}}
    dim = np.full((3, 20, 20), 100, np.uint16)
    dim[1, 10, 10] = 400
    bright = dim * 10
    from wormscore.measure import identity_display
    _, mip_dim = identity_display(dim, cfg)
    _, mip_bright = identity_display(bright, cfg)
    assert np.array_equal(mip_dim, mip_bright)        # ten times brighter looks the same
    assert mip_dim.dtype == np.uint8 and mip_dim[10, 10] == 255


def test_methods_text_says_which_channel_found_the_cells():
    from wormscore.methods import identity_sentence
    assert identity_sentence(_cfg()) == []
    text = identity_sentence(_cfg({"preset": "mCherry"}))[0]
    assert "mCherry" in text and "never measured" in text and "without using the reporter" in text
    assert "were kept" in identity_sentence(_cfg({"preset": "mCherry", "required": False, "spots_from": "reporter"}))[0]
