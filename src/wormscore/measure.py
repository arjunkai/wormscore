"""Display helpers and every measurement wormscore makes.

All brightness numbers come from functions in this file, so this is the file to read
when writing a Methods section.
"""
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
    return ndimage.median_filter(stack, size=(1, size, size))


def candidate_spots(mip_filtered, px_um, cfg):
    """Automatic suggestions for where a cell body might be. Never a decision.

    1. Smooth the median-filtered projection with a Gaussian about the size of a soma.
    2. Local maxima at least min_separation_um apart are possible cells, brightest first.
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
        found.append({"y": y, "x": x, "peak": peak, "local_bg": bg,
                      "prominence": peak - bg, "mask": labels == lab})
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


def measure_region(raw, filt, mask, exclude, px_um, sat, cfg):
    """All numbers for one region (a drawn region or an automatic suggestion).

    raw, filt: (z, y, x) marker stacks, unfiltered and median-filtered.
    mask: (y, x) region. exclude: (y, x) pixels to keep out of the background ring.
    sat: the highest value this microscope's detector can record."""
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
        "saturated_px_mip": int((mip_raw[mask] >= sat).sum()),
        "ring_px": int(ring.sum()),
    }


def streak_rows(mip_raw, sat, cfg):
    """Rows whose mean is far above their neighbours: horizontal scan-line artifacts."""
    rows = mip_raw.astype(float).mean(1)
    local = ndimage.median_filter(rows, size=9)
    jump = cfg["measurement"]["streak_jump_fraction"] * sat
    return [int(r) for r in np.nonzero(rows - local > jump)[0]]
