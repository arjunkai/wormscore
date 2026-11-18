"""1.26.6: suggested cells are found in a square around each maximum, with exactly the results of the
whole-image passes used before (kept here as the reference)."""
import copy
import time

import numpy as np
from scipy import ndimage

from wormscore import config as C
from wormscore import measure as M


def reference(mip_filtered, px_um, cfg):
    """candidate_spots as it was up to 1.26.5."""
    mc = cfg["measurement"]
    img = mip_filtered.astype(float)
    smooth = ndimage.gaussian_filter(img, mc["smooth_um"] / px_um)
    size = int(mc["min_separation_um"] / px_um) | 1
    peaks = (smooth == ndimage.maximum_filter(smooth, size=size)) & (smooth > 0)
    ys, xs = np.nonzero(peaks)
    order = np.argsort(-smooth[ys, xs], kind="stable")
    yy, xx = np.mgrid[: img.shape[0], : img.shape[1]]
    r_in, r_out = mc["spot_ring_um"]
    min_px = mc["min_object_area_um2"] / px_um ** 2
    found = []
    for k in order:
        y, x = int(ys[k]), int(xs[k])
        d_um = np.hypot(yy - y, xx - x) * px_um
        bg = float(np.median(img[(d_um > r_in) & (d_um <= r_out)]))
        peak = float(img[d_um <= 1.0].max())
        half = bg + 0.5 * (peak - bg)
        labels, _ = ndimage.label((img >= half) & (d_um <= mc["max_radius_um"]))
        lab = labels[y, x]
        if not lab or (labels == lab).sum() < min_px:
            continue
        found.append({"y": y, "x": x, "peak": peak, "local_bg": bg, "prominence": peak - bg, "mask": labels == lab})
        if len(found) == mc["n_candidates"]:
            break
    return found


def field(rng, shape, cells, px):
    img = rng.poisson(100, shape).astype(float)
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    for _ in range(cells):
        y, x = rng.integers(0, shape[0]), rng.integers(0, shape[1])      # also at the edges and corners
        img += rng.uniform(300, 3000) * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * (rng.uniform(0.8, 2.5) / px) ** 2))
    return ndimage.median_filter(img.astype(np.uint16), 3)


def test_suggested_cells_are_exactly_as_before():
    cfg = copy.deepcopy(C.DEFAULTS)
    rng = np.random.default_rng(11)
    for shape, cells, px in (((200, 260), 0, 0.25), ((200, 260), 2, 0.25), ((220, 180), 9, 0.2),
                             ((90, 120), 3, 0.5), ((300, 300), 30, 0.1625), ((40, 50), 1, 1.0)):
        img = field(rng, shape, cells, px)
        got, want = M.candidate_spots(img, px, cfg), reference(img, px, cfg)
        assert len(got) == len(want)
        for a, b in zip(got, want):
            assert (a["y"], a["x"], a["peak"], a["local_bg"], a["prominence"]) == \
                   (b["y"], b["x"], b["peak"], b["local_bg"], b["prominence"])
            assert a["mask"].shape == b["mask"].shape and np.array_equal(a["mask"], b["mask"])


def test_a_field_with_few_cells_is_not_slow():
    """Every local maximum is tried when fewer than four qualify; with whole-image passes a large,
    sparse field took minutes."""
    cfg = copy.deepcopy(C.DEFAULTS)
    img = field(np.random.default_rng(3), (700, 700), 1, 0.1625)
    start = time.perf_counter()
    new = M.candidate_spots(img, 0.1625, cfg)
    fast = time.perf_counter() - start
    start = time.perf_counter()
    old = reference(img, 0.1625, cfg)
    slow = time.perf_counter() - start
    assert len(new) == len(old) and fast < slow / 3
