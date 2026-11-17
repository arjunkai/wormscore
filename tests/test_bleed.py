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
    with pytest.raises(SystemExit, match="outside the project"):
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
