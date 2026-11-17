"""Bleed-through check from single-label control images (1.20.0)."""
import json

import numpy as np
import pytest
import tifffile

from wormscore import bleed as B
from wormscore import config as C
from wormscore import methods
from wormscore.demo import make_demo

QUIET = {"log": lambda *a: None}


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    p = tmp_path_factory.mktemp("bleed") / "project"
    make_demo(p, n_per_group=3, identity=True, **QUIET)
    return p


def controls(folder, project, kind, pct, n=3, saturate=False):
    """Single-label controls made from the demo's stacks: the labelled channel as recorded, the
    other channel flat background plus pct% of the labelled signal above its background."""
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted((project / "data").glob("*.ome.tif"))[:n]
    rng = np.random.default_rng(0)
    for k, f in enumerate(files):
        gfp, mch, bf = tifffile.imread(f).astype(np.float64)
        src = mch if kind == "identity-only" else gfp
        leak = 100 + rng.normal(0, 2, src.shape) + pct / 100 * (src - np.median(src))
        if saturate:
            leak[src >= np.percentile(src, 99.99)] = 65535
        chans = [leak, mch, bf] if kind == "identity-only" else [gfp, leak, bf]
        tifffile.imwrite(folder / f"control_{k}.ome.tif", np.clip(np.stack(chans), 0, 65535).astype(np.uint16),
                         photometric="minisblack", ome=True,
                         metadata={"axes": "CZYX", "PhysicalSizeX": 0.2, "PhysicalSizeY": 0.2, "PhysicalSizeZ": 1.0,
                                   "Channel": {"Name": ["GFP", "mCherry", "Brightfield"]}})
    return folder


@pytest.mark.parametrize("kind, pct", [("identity-only", 3.0), ("reporter-only", 0.5)])
def test_the_known_bleed_through_is_measured(project, tmp_path, kind, pct):
    res = B.measure(project, controls(tmp_path / kind, project, kind, pct), kind, **QUIET)
    assert res["images"] == 3 and res["median_pct"] == pytest.approx(pct, abs=0.15)
    assert (res["from"], res["into"]) == (("mCherry", "GFP") if kind == "identity-only" else ("GFP", "mCherry"))
    assert B.results(project)[kind]["median_pct"] == res["median_pct"]
    log = (project / "log.md").read_text(encoding="utf-8")
    assert B.LOG_TITLE in log and f"median {res['median_pct']}%" in log
    rows = (project / "outputs" / f"bleed_through_{kind}.csv").read_text(encoding="utf-8")
    assert rows.count("control_") == 3


def test_saturated_pixels_are_left_out(project, tmp_path):
    res = B.measure(project, controls(tmp_path / "sat", project, "identity-only", 2.0, saturate=True),
                    "identity-only", **QUIET)
    assert res["median_pct"] == pytest.approx(2.0, abs=0.15)


def test_the_methods_draft_names_each_direction(project):
    text = methods.bleed_sentence(project)
    assert "single-label control images" in text and "mCherry into the GFP channel" in text


def test_controls_must_be_outside_the_project_and_readable(project, tmp_path):
    with pytest.raises(SystemExit, match="holds the project.s own images"):
        B.measure(project, project / "data", "identity-only", **QUIET)
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit, match="No control image could be measured"):
        B.measure(project, tmp_path / "empty", "identity-only", **QUIET)


def test_a_project_without_an_identity_channel_has_no_check(tmp_path):
    p = tmp_path / "plain"
    make_demo(p, n_per_group=2, **QUIET)
    with pytest.raises(SystemExit, match="no identity channel"):
        B.measure(p, tmp_path, "identity-only", **QUIET)
    assert B.methods_sentence(p) == "" and json.loads(json.dumps(B.results(p))) == {}
    assert C.load(p).get("identity") is None


def test_the_joint_fit_separates_channels_with_different_patterns():
    rng = np.random.default_rng(3)
    shape = (4, 60, 60)
    cells = lambda: (rng.random(shape) > 0.97) * rng.uniform(2000, 8000, shape)
    a, b, c = (100 + cells() + rng.normal(0, 3, shape) for _ in range(3))
    tgt = 100 + rng.normal(0, 2, shape) + 0.02 * (a - np.median(a)) + 0.005 * (b - np.median(b)) + 0.05 * (c - np.median(c))
    got, n, vif = B.joint([a, b, c], tgt)
    assert got == pytest.approx([2.0, 0.5, 5.0], abs=0.15) and vif < B.VIF_MAX and n > 100
    # the same cells in two channels: their light cannot be told apart
    got, n, vif = B.joint([a, 2 * a - 100 + rng.normal(0, 1, shape)], tgt)
    assert got is None and vif > B.VIF_MAX


def test_several_identity_channels_are_also_fitted_together(project, tmp_path):
    import shutil
    p = tmp_path / "two"
    shutil.copytree(project, p)
    raw = json.loads((p / "config.json").read_text(encoding="utf-8"))
    raw["identity"] = [{"preset": "mCherry"}, {"preset": "BFP"}]
    (p / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    (p / "outputs" / B.RESULTS).unlink(missing_ok=True)
    folder = tmp_path / "ctl"
    folder.mkdir()
    rng = np.random.default_rng(0)
    for k, f in enumerate(sorted((project / "data").glob("*.ome.tif"))[:3]):
        gfp, mch, bf = tifffile.imread(f).astype(np.float64)
        bfp = np.roll(mch, 9, axis=2)                    # another pattern of cells, as in NeuroPAL's color code
        leak = 100 + rng.normal(0, 2, mch.shape) + 0.03 * (mch - np.median(mch)) + 0.01 * (bfp - np.median(bfp))
        tifffile.imwrite(folder / f"control_{k}.ome.tif", np.clip(np.stack([leak, mch, bfp, bf]), 0, 65535).astype(np.uint16),
                         photometric="minisblack", ome=True,
                         metadata={"axes": "CZYX", "PhysicalSizeX": 0.2, "PhysicalSizeY": 0.2, "PhysicalSizeZ": 1.0,
                                   "Channel": {"Name": ["GFP", "mCherry", "BFP", "Brightfield"]}})
    res = B.measure(p, folder, "identity-only", **QUIET)
    j = res["joint"]
    assert j["images"] == 3 and [c["from"] for c in j["channels"]] == ["mCherry", "BFP"]
    assert [c["median_pct"] for c in j["channels"]] == pytest.approx([3.0, 1.0], abs=0.2)
    assert "fitted together" in (p / "log.md").read_text(encoding="utf-8")
    assert "joint_BFP_pct" in (p / "outputs" / "bleed_through_identity-only.csv").read_text(encoding="utf-8")
    assert "fitted to all of them together" in B.methods_sentence(p)
    # reporter-only controls, and projects with one identity channel, are measured as before
    assert "joint" not in B.results(project).get("identity-only", {})


def test_channels_sharing_an_offset_are_not_separable_through_the_origin():
    rng = np.random.default_rng(5)
    c = rng.uniform(1000, 1200, 5000)                   # bright pixels: every column mostly the same offset
    X = np.stack([c + rng.normal(0, 5, c.size), c + rng.normal(0, 5, c.size)], axis=1)
    assert B.uncentred_vif(X) > B.VIF_MAX
    assert B.uncentred_vif(np.stack([c, np.zeros_like(c)], axis=1)) == float("inf")
    assert B.uncentred_vif(np.stack([rng.normal(0, 1, 5000), rng.normal(0, 1, 5000)], axis=1)) < 1.1


def test_the_methods_name_the_real_reason_a_joint_fit_failed(tmp_path, monkeypatch):
    res = {"identity-only": {"kind": "identity-only", "from": "A", "into": "GFP", "images": 2, "median_pct": 1.0,
                             "min_pct": 1.0, "max_pct": 1.0, "joint": {"images": 0, "not_separable": 0, "missing": 2,
                                                                       "too_little": 0, "channels": []}}}
    monkeypatch.setattr(B, "results", lambda p: res)
    assert "not every identity channel was found" in B.methods_sentence(tmp_path)
