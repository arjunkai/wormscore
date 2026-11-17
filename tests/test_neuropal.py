"""Several identity channels, for NeuroPAL (1.23.0)."""
import json

import numpy as np
import pytest
import tifffile

from wormscore import config as C
from wormscore import readers as R
from wormscore.demo import PX_UM, Z_UM, make_demo
from wormscore.pipeline import inventory, load_images, prepare, read_csv

QUIET = {"log": lambda *a: None}
NAMES = ["GFP", "TagRFP-T", "mNeptune2.5", "CyOFP1", "TagBFP", "Brightfield"]


def test_the_neuropal_preset_is_four_channels_with_the_primary_first():
    cfg = C.apply_identity(C.merge(C.DEFAULTS, {}), {"preset": "NeuroPAL", "spots_from": "reporter"})
    assert cfg["identity"]["label"] == "NeuroPAL" and cfg["identity"]["spots_from"] == "reporter"
    assert [x["label"] for x in cfg["identity_extra"]] == ["mNeptune2.5", "CyOFP1", "mTagBFP2"]
    assert [x["color"] for x in cfg["identity_extra"]] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_one_identity_channel_adds_nothing():
    cfg = C.apply_identity(C.merge(C.DEFAULTS, {}), {"preset": "mCherry"})
    assert "identity_extra" not in cfg
    assert R.with_extras(["MARKER", "OTHER"], cfg, lambda i, m: True) == ["MARKER", "OTHER"]


def test_a_list_of_identity_channels_and_bad_lists():
    cfg = C.apply_identity(C.merge(C.DEFAULTS, {}), [{"preset": "mCherry"}, {"preset": "BFP"}])
    assert cfg["identity"]["label"] == "mCherry" and [x["label"] for x in cfg["identity_extra"]] == ["BFP"]
    with pytest.raises(SystemExit, match="1 to 4"):
        C.identity_entries([{"preset": "BFP"}] * 5)


def test_a_reporter_tie_at_488_leaves_the_other_channel_to_cyofp():
    cfg = C.apply_identity(C.merge(C.DEFAULTS, {}), {"preset": "NeuroPAL"})
    names, fluors, ex = ["ChS1", "Ch2"], ["EGFP", "mRFP1.2"], ["488", "488"]
    roles = [R.marker_from_named(n, f, e, False, cfg) for n, f, e in zip(names, fluors, ex)]
    roles = R.prefer_named_dye(roles, fluors, cfg)
    assert R.named_extras(roles, cfg, names, fluors, ex, None) == ["MARKER", "IDENTITY3"]


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """The demo's worms rewritten as six-channel NeuroPAL images: GFP, the four NeuroPAL colours, brightfield."""
    p = tmp_path_factory.mktemp("np") / "p"
    make_demo(p, n_per_group=2, identity=True, **QUIET)
    rng = np.random.default_rng(2)
    for f in sorted((p / "data").glob("*.ome.tif")):
        gfp, mch, bf = tifffile.imread(f).astype(np.float64)
        extra = [100 + rng.normal(0, 5, gfp.shape) + 0.5 * (mch - 100) for _ in range(3)]
        tifffile.imwrite(f, np.clip(np.stack([gfp, mch, *extra, bf]), 0, 65535).astype(np.uint16), photometric="minisblack",
                         ome=True, metadata={"axes": "CZYX", "PhysicalSizeX": PX_UM, "PhysicalSizeY": PX_UM,
                                             "PhysicalSizeZ": Z_UM, "Channel": {"Name": NAMES}})
    raw = json.loads((p / "config.json").read_text(encoding="utf-8"))
    raw["identity"] = {"preset": "NeuroPAL"}
    (p / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    inventory(p, **QUIET)
    prepare(p, **QUIET)
    return p


def test_each_neuropal_channel_is_found_and_shown_never_measured(project):
    img = load_images(project)[0]
    assert img["channel_roles"] == ["MARKER", "IDENTITY", "IDENTITY2", "IDENTITY3", "IDENTITY4", "BF"]
    assert img["identity_extra_channels"] == [2, 3, 4]
    code = read_csv(project / "key" / "key.csv")[0]["code"]
    d = project / "blind" / code
    meta = json.loads((d / "meta.json").read_text())
    assert meta["has_identity"] and meta["identity_extra"] == [True, True, True]
    for k in (2, 3, 4):
        assert (d / f"identity{k}_mip.png").is_file() and len(list((d / f"identity{k}").glob("z*.png"))) == meta["n_z"]
    auto = (project / "outputs" / "auto_measurements.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "mNeptune" not in auto and "CyOFP" not in auto
    assert "Other identity channels, shown with it and never measured: mNeptune2.5, CyOFP1, mTagBFP2" in \
        (project / "log.md").read_text(encoding="utf-8")


def test_the_scoring_page_gets_each_channel_and_its_colour(project):
    import threading
    from wormscore.server import make_server
    from test_identity import request
    srv = make_server(project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        cfg = json.loads(request(port, "GET", "/api/config")[1])
        assert cfg["identity"]["label"] == "NeuroPAL"
        assert [(x["label"], x["color"]) for x in cfg["identity_extra"]] == [
            ("mNeptune2.5", [1, 0, 0]), ("CyOFP1", [0, 1, 0]), ("mTagBFP2", [0, 0, 1])]
        for path in ("/img/W0001/identity2_mip.png?v=1", "/img/W0001/identity4/z00.png?v=1"):
            assert request(port, "GET", path)[0] == 200, path
        text = request(port, "GET", "/api/worm/W0001")[1].decode()
        for leak in ("control", "mutant", "demo_", "genotype"):
            assert leak not in text
    finally:
        srv.shutdown()
        srv.server_close()
