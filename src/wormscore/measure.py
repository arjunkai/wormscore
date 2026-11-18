"""Display helpers and every measurement wormscore makes.

All brightness numbers come from functions in this file, so this is the file to read
when writing a Methods section.
"""
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy import ndimage


# ---------------------------------------------------------------- display

def window(img, lo, hi):
    """Fixed linear display window: lo -> 0, hi -> 255, values outside are clipped."""
    out = (img.astype(float) - lo) / float(hi - lo)
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


def brightfield_plane(bf_stack, cfg):
    """Middle brightfield plane, contrast-stretched per image (brightfield is never measured)."""
    plane = bf_stack[len(bf_stack) // 2].astype(float)
    p_lo, p_hi = np.percentile(plane, cfg["display"]["brightfield_contrast_percentiles"])
    return window(plane, p_lo, max(p_hi, p_lo + 1))


def identity_display(stack, cfg):
    """The identity channel for display only: one window per image, from the median of its maximum
    projection (background) to a high percentile (bright cells), applied to every plane. Scaled per
    image on purpose: the identity marker is only for finding the cell, and if its brightness differed
    between genotypes, a fixed range could hint at the genotype. It is never measured.
    Returns 8-bit (z, y, x) planes and the 8-bit projection."""
    mip = stack.max(0).astype(float)
    p_lo, p_hi = cfg["display"]["identity_contrast_percentiles"]
    lo, hi = np.percentile(mip, [p_lo, p_hi])
    hi = max(hi, lo + 1)
    return window(stack, lo, hi), window(mip, lo, hi)


def overlay(marker_display, bf_display):
    """Brightfield in gray at half strength with the marker added in the green channel."""
    bf = bf_display.astype(float) * 0.5
    g = np.clip(bf + marker_display.astype(float), 0, 255)
    return np.dstack([bf, g, bf]).astype(np.uint8)


def pack16(img):
    """Exact 16-bit values in an RGB PNG: red = high byte, green = low byte."""
    v = img.astype(np.uint16)
    return np.dstack([(v >> 8).astype(np.uint8), (v & 255).astype(np.uint8), np.zeros(v.shape, np.uint8)])


def unpack16(rgb):
    return rgb[..., 0].astype(np.uint16) * 256 + rgb[..., 1]


# ---------------------------------------------------------------- measurement

def median_filter_stack(stack, size):
    """Median filter on each plane separately. Removes single hot pixels and one-pixel
    scan-line streaks so they cannot become the 'peak'."""
    if size != 3 or stack.ndim != 3 or min(stack.shape[1:]) < 2 or not np.issubdtype(stack.dtype, np.number) \
            or (np.issubdtype(stack.dtype, np.floating) and np.isnan(stack).any()):
        return ndimage.median_filter(stack, size=(1, size, size))
    # 1.26.10: the usual 3 x 3 filter, worked out as the median of the nine neighbours with whole-plane
    # minimum and maximum steps, a few planes at a time on separate threads. The values are exactly those
    # of ndimage.median_filter (a test compares them, edges included); on a 2048 x 2048 x 50 stack it took
    # about 40 s of the time to build or open an image.
    out = np.empty_like(stack)
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        list(pool.map(lambda z: _median9(stack[z], out[z]), range(stack.shape[0])))
    return out


_MEDIAN9 = ((1, 2), (4, 5), (7, 8), (0, 1), (3, 4), (6, 7), (1, 2), (4, 5), (7, 8), (0, 3), (5, 8), (4, 7),
            (3, 6), (1, 4), (2, 5), (4, 7), (4, 2), (6, 4), (4, 2))


def _median9(plane, out):
    """Median of each pixel's 3 x 3 neighbourhood, edges mirrored as ndimage's default ("reflect") does."""
    p = np.pad(plane, 1, mode="symmetric")
    h, w = plane.shape
    v = [p[dy:dy + h, dx:dx + w].copy() for dy in range(3) for dx in range(3)]
    for a, b in _MEDIAN9:           # each step leaves the smaller value in a and the larger in b
        lo = np.minimum(v[a], v[b])
        np.maximum(v[a], v[b], out=v[b])
        v[a] = lo
    out[...] = v[4]


def candidate_spots(mip_filtered, px_um, cfg):
    """Automatic suggestions for where a cell body might be. Never a decision.

    1. Smooth the median-filtered projection with a Gaussian about the size of a soma.
    2. Local maxima (each the brightest point of the min_separation_um square around it, so maxima
       about half that distance apart are kept) are possible cells, brightest first.
    3. Around each maximum, the local background is the median of a ring (spot_ring_um).
    4. The region is every connected pixel at or above half of the way from the
       background to the peak (full width at half maximum), within max_radius_um.
    5. Regions smaller than min_object_area_um2 are skipped (too small for a soma).
    An image with no cell still returns its brightest spot; its prominence will be small."""
    mc = cfg["measurement"]
    img = mip_filtered.astype(float)
    smooth = ndimage.gaussian_filter(img, mc["smooth_um"] / px_um)
    size = int(mc["min_separation_um"] / px_um) | 1
    peaks = (smooth == ndimage.maximum_filter(smooth, size=size)) & (smooth > 0)
    ys, xs = np.nonzero(peaks)
    order = np.argsort(-smooth[ys, xs], kind="stable")
    r_in, r_out = mc["spot_ring_um"]
    min_px = mc["min_object_area_um2"] / px_um ** 2
    # 1.26.6: each maximum is worked out in the square that holds everything it uses (the ring, the
    # 1 um peak disc and max_radius_um), not over the whole image. The numbers and regions are exactly
    # the same; a 2048 x 2048 field with few cells took minutes because every local maximum was tried
    # with whole-image passes.
    reach = int(np.ceil(max(r_out, mc["max_radius_um"], 1.0) / px_um)) + 1
    h, w = img.shape
    found = []
    for k in order:
        y, x = int(ys[k]), int(xs[k])
        y0, y1, x0, x1 = max(0, y - reach), min(h, y + reach + 1), max(0, x - reach), min(w, x + reach + 1)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d_um = np.hypot(yy - y, xx - x) * px_um
        part = img[y0:y1, x0:x1]
        bg = float(np.median(part[(d_um > r_in) & (d_um <= r_out)]))
        peak = float(part[d_um <= 1.0].max())
        half = bg + 0.5 * (peak - bg)
        labels, _ = ndimage.label((part >= half) & (d_um <= mc["max_radius_um"]))
        lab = labels[y - y0, x - x0]
        if not lab or (labels == lab).sum() < min_px:
            continue
        mask = np.zeros(img.shape, bool)
        mask[y0:y1, x0:x1] = labels == lab
        found.append({"y": y, "x": x, "peak": peak, "local_bg": bg,
                      "prominence": peak - bg, "mask": mask})
        if len(found) == mc["n_candidates"]:
            break
    return found


def background_ring(mask, exclude, px_um, cfg):
    """Ring around the region: starts gap_um outside its edge and is width_um wide.
    Pixels belonging to other regions are left out of the ring."""
    gap = cfg["measurement"]["background_ring_gap_um"]
    width = cfg["measurement"]["background_ring_width_um"]
    dist_um = ndimage.distance_transform_edt(~mask) * px_um
    return (dist_um > gap) & (dist_um <= gap + width) & ~exclude & ~mask


def measure_region(raw, filt, mask, exclude, px_um, sat, cfg, sat_mask=None):
    """All numbers for one region (a drawn region or an automatic suggestion).

    raw, filt: (z, y, x) marker stacks, unfiltered and median-filtered.
    mask: (y, x) region. exclude: (y, x) pixels to keep out of the background ring.
    sat: the highest value this microscope's detector can record.
    sat_mask: (y, x) pixels at that maximum in any plane, for flat-field corrected stacks (1.21.0),
    whose values no longer show it; None counts pixels of the projection at sat, as before."""
    mip_raw = raw.max(0)
    mip_f = filt.max(0)
    ring = background_ring(mask, exclude, px_um, cfg)
    zprof = filt[:, mask].mean(1)
    best_z = int(np.argmax(zprof))
    best = raw[best_z].astype(float)
    bg_mip = float(np.median(mip_raw[ring])) if ring.any() else float("nan")
    bg_best = float(np.median(best[ring])) if ring.any() else float("nan")
    # Sum projection with each plane's own ring background subtracted: keeps the light from
    # every plane (a max projection keeps only the brightest), and planes that miss the cell
    # add only noise. Depends on plane count and z-step, so compare stacks of the same shape.
    bg_z = np.median(raw[:, ring].astype(float), axis=1) if ring.any() else np.full(raw.shape[0], np.nan)
    stack_sum = float((raw[:, mask].astype(float) - bg_z[:, None]).sum())
    mean_mip = float(mip_raw[mask].mean())
    mean_best = float(best[mask].mean())
    peak_f = float(mip_f[mask].max())
    extra = {}
    if cfg["unblinding"]["dim_cutoff_sd"]:
        # Background noise for the dim cutoff: robust SD (1.4826 x median absolute deviation)
        # of the ring in the sharpest plane, so a stray bright pixel in the ring barely moves it.
        # Only measured when a cutoff is set, so other projects' tables keep their columns.
        ring_best = best[ring]
        extra["background_sd_best_plane"] = (
            round(1.4826 * float(np.median(np.abs(ring_best - np.median(ring_best)))), 2)
            if ring.any() else float("nan"))
    return {
        "area_um2": round(float(mask.sum()) * px_um ** 2, 2),
        "peak_raw": int(mip_raw[mask].max()),
        "peak_filtered": round(peak_f, 1),
        "mean_mip": round(mean_mip, 2),
        "background_mip": round(bg_mip, 2),
        "peak_minus_bg": round(peak_f - bg_mip, 2),
        "mean_mip_minus_bg": round(mean_mip - bg_mip, 2),
        "best_z": best_z,
        "n_z": int(raw.shape[0]),
        "best_z_at_stack_edge": best_z in (0, raw.shape[0] - 1),
        "mean_best_plane": round(mean_best, 2),
        "background_best_plane": round(bg_best, 2),
        "mean_best_minus_bg": round(mean_best - bg_best, 2),
        "integrated_best_minus_bg": round(float((best[mask] - bg_best).sum()), 1),
        "stack_sum_minus_bg": round(stack_sum, 1),
        "saturated_px_mip": int((mip_raw[mask] >= sat).sum() if sat_mask is None else sat_mask[mask].sum()),
        "ring_px": int(ring.sum()),
    } | extra


def streak_rows(mip_raw, sat, cfg):
    """Rows whose mean is far above their neighbours: horizontal scan-line artifacts."""
    rows = mip_raw.astype(float).mean(1)
    local = ndimage.median_filter(rows, size=9)
    jump = cfg["measurement"]["streak_jump_fraction"] * sat
    return [int(r) for r in np.nonzero(rows - local > jump)[0]]
