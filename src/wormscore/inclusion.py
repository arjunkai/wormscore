"""Build's inclusion rules, kept apart from the image libraries (1.26.23): the Build card checks them for
coded images, and the home screen, which shows that card's status, must not wait for numpy to load."""
import re

from . import config as C

INCOMPLETE = "file is incomplete"       # inventory flag (readers.ome_images), an exclusion reason at Build


def typed(row):
    """The fields include_reason reads, from an inventory.csv row (text) as pipeline.load_images gives them."""
    num = lambda k, f: f(row[k]) if str(row.get(k, "")) != "" else None
    return {**row, "marker_channel": num("marker_channel", int), "identity_channel": num("identity_channel", int),
            "pixel_um": float(row.get("pixel_um") or 0), "z_step_um": float(row.get("z_step_um") or 0),
            "n_z": int(row["n_z"]), "n_t": int(row["n_t"])}


def below_minimum(objective, cfg):
    """Whether the objective's magnification is below the minimum (an objective with none is not)."""
    # 1.26.5: "63.0x" was read as 0x (the digits before the x), so such images were left out as below the minimum
    mag = re.search(r"(\d+(?:\.\d+)?)\s*x", objective or "")
    return bool(mag) and float(mag.group(1)) < cfg["inclusion"]["min_objective_magnification"]


def include_reason(img, cfg, coded=False):
    """'' if the image passes the inclusion rules, otherwise why it fails. coded: the image already has a code
    and the build keeps the codes. 1.26.23: the minimum magnification decides which images are coded; a coded
    image is not left out for it later. A TCS SP5 image coded before its objective was read (20x, say) would
    otherwise stop every later build of a project scored on it (found by review). The Build log says so;
    a rebuild with new codes applies the minimum to every image."""
    inc = cfg["inclusion"]
    rule = C.exclude_rule_for(cfg, img["source"], img["relative_path"])
    if rule:
        return f"excluded by config rule '{rule['match']}': {rule.get('reason') or 'no reason given'}"
    if img["marker_channel"] is None:
        return f"{cfg['marker']['label']} channel could not be identified from metadata"
    ident = cfg.get("identity")
    if ident and ident["required"] and img.get("identity_channel") is None:
        return (f"{ident['label']} identity channel could not be identified from metadata (identity.required is "
                "true, so every scored image has its cells found the same way)")
    # 1.26.5: images Build cannot use are left out with the reason. They used to stop the whole build
    # with a Python error that named no file.
    flags = str(img.get("flags") or "")
    if "pixels could not be read" in flags:
        return "the image could not be read (damaged or incomplete file)"
    if INCOMPLETE in flags:
        return "the file is incomplete: some planes are missing (damaged, or still being copied)"
    if not img["pixel_um"] or img["pixel_um"] <= 0:
        return "the file does not record its pixel size, which the measurements and the display scale need"
    if below_minimum(img["objective"], cfg) and not coded:
        return f"objective is {img['objective']} (minimum {inc['min_objective_magnification']}x)"
    if img["n_t"] > inc["max_time_points"]:
        return f"time series ({img['n_t']} time points)"
    extent = (img["n_z"] - 1) * img["z_step_um"]
    if extent < inc["min_z_extent_um"]:
        return f"z-stack covers {extent:.1f} um ({img['n_z']} planes; minimum {inc['min_z_extent_um']} um)"
    return ""
