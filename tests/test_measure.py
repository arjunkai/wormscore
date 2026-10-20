import numpy as np

from wormscore import config as C
from wormscore import measure as M

CFG = C.DEFAULTS


def spot_stack(level=1000.0, bg=100.0, center=(40, 50), z=4, px=0.25):
    yy, xx = np.mgrid[:96, :96]
    r2 = ((yy - center[0]) ** 2 + (xx - center[1]) ** 2) * px ** 2
    zz = np.arange(9)[:, None, None]
    stack = bg + level * np.exp(-r2 / (2 * 1.4 ** 2))[None] * np.exp(-((zz - z) ** 2) / 4.0)
    return stack.astype(np.uint16)


def test_pack16_round_trip():
    v = np.array([[0, 1, 255, 256], [4095, 30000, 65534, 65535]], dtype=np.uint16)
    assert np.array_equal(M.unpack16(M.pack16(v)), v)


def test_window_is_fixed_and_clipped():
    out = M.window(np.array([0, 50, 100, 200]), 0, 100)
    assert out.tolist() == [0, 127, 255, 255]


def test_candidate_finds_the_planted_spot():
    stack = spot_stack()
    filt = M.median_filter_stack(stack, 3)
    found = M.candidate_spots(filt.max(0), 0.25, CFG)
    assert found, "no candidate found"
    assert abs(found[0]["y"] - 40) <= 1 and abs(found[0]["x"] - 50) <= 1
    assert found[0]["prominence"] > 800


def test_measure_region_reports_background_and_sharpest_plane():
    stack = spot_stack(z=6)
    filt = M.median_filter_stack(stack, 3)
    c = M.candidate_spots(filt.max(0), 0.25, CFG)[0]
    m = M.measure_region(stack, filt, c["mask"], c["mask"], 0.25, 65535, CFG)
    assert m["best_z"] == 6
    assert m["background_mip"] == 100
    assert m["mean_mip_minus_bg"] > 300
    assert m["saturated_px_mip"] == 0
    assert not m["best_z_at_stack_edge"]


def test_stack_sum_keeps_light_a_max_projection_drops():
    """Two cells with the same brightest plane: the max projection cannot tell them apart,
    the background-subtracted sum over planes can (and is exact on a flat background)."""
    mask = np.zeros((64, 64), bool)
    mask[27:37, 27:37] = True
    m = {}
    for name, profile in (("thin", [0, 0, 200, 0, 0]), ("thick", [0, 100, 200, 100, 0])):
        stack = np.full((5, 64, 64), 100, np.uint16)
        for z, v in enumerate(profile):
            stack[z][mask] += v
        m[name] = M.measure_region(stack, stack, mask, mask, 0.25, 65535, CFG)
    assert m["thin"]["mean_mip_minus_bg"] == m["thick"]["mean_mip_minus_bg"] == 200
    assert m["thin"]["stack_sum_minus_bg"] == 100 * 200
    assert m["thick"]["stack_sum_minus_bg"] == 100 * 400


def test_single_hot_pixel_is_not_the_peak():
    stack = np.full((5, 64, 64), 100, np.uint16)
    stack[2, 30, 30] = 60000
    filt = M.median_filter_stack(stack, 3)
    assert filt.max() == 100


def test_saturation_is_counted():
    stack = spot_stack(level=70000.0)
    stack = np.clip(stack.astype(float), 0, 255).astype(np.uint16)
    filt = M.median_filter_stack(stack, 3)
    c = M.candidate_spots(filt.max(0), 0.25, CFG)[0]
    m = M.measure_region(stack, filt, c["mask"], c["mask"], 0.25, 255, CFG)
    assert m["saturated_px_mip"] > 0


def test_genotype_rules_first_match_wins():
    src = {"genotypes": [{"match": "*control*", "genotype": "ctrl"}, {"match": "*", "genotype": "other"}]}
    assert C.genotype_for(src, "a/b/my_control_01.czi") == "ctrl"
    assert C.genotype_for(src, "a\\b\\mut_01.czi") == "other"
    assert C.genotype_for({"default_genotype": "x"}, "y.lif") == "x"
