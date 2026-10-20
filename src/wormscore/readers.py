"""Find images in source folders, read their metadata, and read their pixels.

Supported raw formats: Leica .lif (one image per series), Zeiss .czi (one per scene),
OME-TIFF (one per series; metadata in the file or in a .companion.ome next to it).

Every image becomes a dict with the same fields: where it came from, what it is, how
it was taken, and which channel is the marker or brightfield. Channel identity comes
from metadata only. If the metadata does not settle it, the channel is UNRESOLVED.
"""
import hashlib
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import czifile
import numpy as np
import tifffile
from readlif.reader import LifFile

from . import config as C

OME_NS = {"o": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}
DERIVED = re.compile(r"^(MAX_|SUM_|AVG_|STD_|MIN_|MED_|Projections? of |C\d+-|Composite|RGB_)", re.I)
OTHER_IMAGE = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".gif", ".avi", ".mp4", ".bmp"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def file_role(path):
    name, low = path.name, path.name.lower()
    if name.startswith("._") or name in (".DS_Store", "desktop.ini", "Thumbs.db"):
        return "system hidden file (not data)"
    if low.endswith(".lif") or low.endswith(".czi"):
        return "raw image"
    if low.endswith(".ome.tif") or low.endswith(".ome.tiff"):
        return "derived (made later from a raw image)" if DERIVED.search(name) else "raw image"
    if low.endswith(".companion.ome"):
        return "metadata sidecar of an OME-TIFF"
    if path.suffix.lower() in OTHER_IMAGE:
        if DERIVED.search(name):
            return "derived (made later from a raw image)"
        return "other image (no microscope metadata; not used)"
    if low.endswith((".lof", ".xlef", ".xlif", ".lock")):
        return "Leica LAS X project file (not read; export to .lif)"
    return "reference (not an image)"


# ------------------------------------------------------------------ channel rules

def marker_from_leica(ch, lut, cfg):
    m = cfg["marker"]
    if ch["detector"] and "trans" in ch["detector"].lower():
        return "BF"
    lines = {int(float(ln)) for ln, _ in ch["lasers"]}
    lo = float(ch["band"].split("-")[0]) if ch["band"] else 0
    b0, b1 = m["emission_band_start_nm"]
    lut_ok = not m.get("leica_lut") or lut == m["leica_lut"]
    if lines & set(m["excitation_nm"]) and b0 <= lo <= b1 and lut_ok:
        return "MARKER"
    return "OTHER"


def marker_from_named(name, fluor, excitation, transmitted, cfg):
    m = cfg["marker"]
    if transmitted or any(name.upper().startswith(p.upper()) for p in cfg["brightfield_channel_names"]):
        return "BF"
    if (fluor and fluor in m["dye_names"]) \
            or any(name.upper().startswith(p.upper()) for p in m["channel_name_prefixes"]) \
            or (excitation and int(float(excitation)) in m["excitation_nm"]):
        return "MARKER"
    return "OTHER"


def pick(roles, role):
    return roles.index(role) if roles.count(role) == 1 else None


# ------------------------------------------------------------------ Leica LIF

@lru_cache(maxsize=None)
def _lif(path):
    return LifFile(str(path))


def _leica_channels(elem):
    seq = elem.find(".//LDM_Block_Sequential_List")
    steps = seq.findall(".//ATLConfocalSettingDefinition") if seq is not None else []
    if not steps:
        steps = elem.findall(".//ATLConfocalSettingDefinition")[:1]
    chans = []
    for s in steps:
        lasers = [(l.get("LaserLine"), float(l.get("IntensityDev") or 0))
                  for l in s.iter("LaserLineSetting") if float(l.get("IntensityDev") or 0) > 0]
        bands = {b.get("Channel"): b for b in s.iter("MultiBand")}
        for d in s.iter("Detector"):
            if d.get("IsActive") != "1":
                continue
            band = bands.get(d.get("Channel"))
            chans.append({"detector": d.get("Name"), "gain": float(d.get("Gain") or 0),
                          "band": f"{band.get('LeftWorld')}-{band.get('RightWorld')}" if band is not None else "",
                          "lasers": lasers, "line_avg": s.get("LineAverage")})
    return chans


def leica_images(path, cfg):
    lf = _lif(path)
    elems = [e for e in lf.xml_root.iter("Element") if e.find("./Data/Image") is not None]
    aligned = [e.get("Name") for e in elems] == [im["name"] for im in lf.image_list]
    system = next((a.get("SystemTypeName") for a in lf.xml_root.iter("Attachment") if a.get("SystemTypeName")), "")
    out = []
    for i, im in enumerate(lf.image_list):
        st = im.get("settings", {})
        elem = elems[i] if aligned else None
        luts = [c.get("LUTName") for c in elem.iter("ChannelDescription")] if elem is not None else []
        chans = _leica_channels(elem) if elem is not None else []
        flags = [] if aligned else ["XML/series order mismatch"]
        if len(chans) == im["channels"]:
            roles = [marker_from_leica(c, l, cfg) for c, l in zip(chans, luts)]
        else:
            roles = ["UNRESOLVED"] * im["channels"]
            flags.append(f"{len(chans)} detectors for {im['channels']} channels")
        mi = pick(roles, "MARKER")
        g = chans[mi] if mi is not None else {}
        sx, sz = im["scale"][0], im["scale"][2]
        bits = max(im["bit_depth"])
        out.append({
            "format": "Leica LIF", "system": f"Leica {system}".strip(),
            "location_in_file": f"series {i} ({im['name']})",
            "objective": st.get("ObjectiveName", "").strip(),
            "n_z": im["dims"].z, "n_t": im["dims"].t, "width": im["dims"].x, "height": im["dims"].y,
            "pixel_um": round(1 / sx, 4) if sx else 0.0, "z_step_um": round(abs(1 / sz), 3) if sz else 0.0,
            "bits": bits, "channel_roles": roles, "channel_names": luts,
            "settings": (f"HyD gain {g['gain']:.1f}; " + ";".join(f"{ln}nm@{p:.1f}%" for ln, p in g["lasers"])
                         + f"; line avg {g['line_avg']}") if g else "",
            "zoom": st.get("Zoom", ""), "acquired": "",
            "stage_x": float(st["StagePosX"]) * 1e6 if "StagePosX" in st else None,
            "stage_y": float(st["StagePosY"]) * 1e6 if "StagePosY" in st else None,
            "flags": flags, "_reader": ("lif", str(path), i),
        })
    return out


# ------------------------------------------------------------------ Zeiss CZI

def zeiss_images(path, cfg):
    with czifile.CziFile(str(path)) as f:
        md = f.xml_element if not callable(f.xml_element) else f.xml_element()
        # f.scenes maps scene number -> scene, like a dict
        scenes = [(k, f.scenes[k].dims, f.scenes[k].shape) for k in f.scenes]
    text = lambda tag: next((e.text.strip() for e in md.iter(tag) if e.text and e.text.strip()), "")
    scale = {d.get("Id"): float(d.findtext("Value")) * 1e6 for d in md.iter("Distance") if d.get("Id") in ("X", "Z")}
    chans = md.find(".//Information/Image/Dimensions/Channels")
    names, roles, volts = [], [], []
    for c in chans if chans is not None else []:
        names.append(c.get("Name") or "")
        volts.append(c.findtext("DetectorSettings/Voltage") or "")
        roles.append(marker_from_named(c.get("Name") or "", c.findtext("Fluor"), c.findtext("ExcitationWavelength"),
                                       c.findtext("IlluminationType") == "Transmitted", cfg))
    mi = pick(roles, "MARKER")
    lights = sorted({(l.findtext("Wavelength"), l.findtext("Transmission")) for l in md.iter("LightSourceSettings")
                     if l.findtext("Wavelength")})
    settings = "; ".join(x for x in [
        f"PMT {float(volts[mi]):.0f} V" if mi is not None and volts[mi] else "",
        ";".join(f"{w}nm@{float(t) * 100:.2f}%" for w, t in lights if t),
        f"avg {text('Averaging')}" if text("Averaging") else ""] if x)
    bits = int(text("EffectiveBitsPerPixel") or text("BitsPerPixel") or 16)
    out = []
    for i, dims, shape in scenes:
        dims = "".join(dims)
        out.append({
            "format": "Zeiss CZI", "system": f"Zeiss {text('System')}".strip(),
            "location_in_file": f"whole file (scene {i})" if len(scenes) == 1 else f"scene {i}",
            "objective": text("ObjectiveName"),
            "n_z": shape[dims.index("Z")] if "Z" in dims else 1,
            "n_t": shape[dims.index("T")] if "T" in dims else 1,
            "width": shape[dims.index("X")], "height": shape[dims.index("Y")],
            "pixel_um": round(scale.get("X", 0), 4), "z_step_um": round(scale.get("Z", 0), 3),
            "bits": bits, "channel_roles": roles, "channel_names": names, "settings": settings,
            "zoom": text("Zoom").replace(",", "."),
            "acquired": text("AcquisitionDateAndTime")[:19].replace("T", " "),
            "stage_x": None, "stage_y": None, "flags": [], "_reader": ("czi", str(path), i, dims),
        })
    return out


# ------------------------------------------------------------------ OME-TIFF

def ome_images(path, cfg):
    with tifffile.TiffFile(str(path)) as f:
        series = [(s.axes, s.shape, s.dtype) for s in f.series]
        xml = f.ome_metadata
    md = ET.fromstring(xml) if xml else None
    if md is None or md.find(".//o:Pixels", OME_NS) is None:
        companion = Path(re.sub(r"(_t\d+_p\d+)?\.ome\.tiff?$", ".companion.ome", str(path)))
        if not companion.exists():
            return []
        md = ET.parse(str(companion)).getroot()
    obj = md.find(".//o:Objective", OME_NS)
    objective = (f"{obj.get('NominalMagnification')}x/{obj.get('LensNA')} {obj.get('Immersion', '')}".strip()
                 if obj is not None and obj.get("NominalMagnification") else "")
    model = md.find(".//o:Microscope", OME_NS)
    system = (model.get("Model") if model is not None and model.get("Model") else "") or "OME-TIFF (instrument not named)"
    images = md.findall(".//o:Image", OME_NS)
    out = []
    for i, (axes, shape, dtype) in enumerate(series):
        img = images[i] if i < len(images) else images[0]
        px = img.find("o:Pixels", OME_NS)
        names, roles = [], []
        for c in px.findall("o:Channel", OME_NS):
            names.append(c.get("Name", ""))
            roles.append(marker_from_named(c.get("Name", ""), c.get("Fluor"), c.get("ExcitationWavelength"),
                                           c.get("IlluminationType") == "Transmitted", cfg))
        mi = pick(roles, "MARKER")
        exposures = sorted({p.get("ExposureTime") for p in px.findall("o:Plane", OME_NS)
                            if p.get("TheC") == str(mi) and p.get("ExposureTime")})
        bits = int(px.get("SignificantBits") or np.iinfo(dtype).bits if np.issubdtype(dtype, np.integer) else 16)
        out.append({
            "format": "OME-TIFF", "system": system,
            "location_in_file": "whole file (series 0)" if len(series) == 1 else f"series {i}",
            "objective": objective,
            "n_z": shape[axes.index("Z")] if "Z" in axes else 1,
            "n_t": shape[axes.index("T")] if "T" in axes else 1,
            "width": shape[axes.index("X")], "height": shape[axes.index("Y")],
            "pixel_um": float(px.get("PhysicalSizeX", 0) or 0), "z_step_um": float(px.get("PhysicalSizeZ", 0) or 0),
            "bits": bits, "channel_roles": roles, "channel_names": names,
            "settings": "exposure " + ",".join(exposures) if exposures else "",
            "zoom": "", "acquired": (img.findtext("o:AcquisitionDate", namespaces=OME_NS) or "")[:19].replace("T", " "),
            "stage_x": None, "stage_y": None, "flags": [], "_reader": ("ome", str(path), i, axes),
        })
    return out


def images_in(path, cfg):
    low = path.name.lower()
    if low.endswith(".lif"):
        return leica_images(path, cfg)
    if low.endswith(".czi"):
        return zeiss_images(path, cfg)
    return ome_images(path, cfg)


# ------------------------------------------------------------------ pixels

def read(img, channel):
    """One channel as a (z, y, x) array, first time point, original bit depth."""
    kind, path, i = img["_reader"][:3]
    if kind == "lif":
        im = _lif(path).get_image(i)
        return np.stack([np.asarray(im.get_frame(z=k, c=channel, t=0)) for k in range(img["n_z"])])
    if kind == "czi":
        with czifile.CziFile(path) as f:
            scene = f.scenes[i]
            arr, dims = scene.asarray(), "".join(scene.dims)
    else:
        arr, dims = tifffile.imread(path, series=i), img["_reader"][3]
    if "C" in dims:
        arr = np.take(arr, channel, axis=dims.index("C"))
        dims = dims.replace("C", "")
    for ax in [a for a in dims if a not in "ZYX"]:
        arr = np.take(arr, 0, axis=dims.index(ax))
        dims = dims.replace(ax, "")
    if "Z" not in dims:
        arr = arr[None]
    return arr


def scan(cfg, log=print):
    """Walk every source folder. Returns (manifest rows for every file, image records)."""
    manifest, images = [], []
    for src in cfg["sources"]:
        root = Path(src["path"])
        if not root.is_dir():
            raise SystemExit(f"Source folder not found: {root}\nFix 'path' for source "
                             f"'{src.get('name', root)}' in config.json.")
        original = Path(src.get("original_path") or src["path"])
        files = sorted(p for p in root.rglob("*") if p.is_file())
        log(f"Scanning {root} ({len(files)} files)")
        by_hash = {}
        rows = []
        for p in files:
            rel = str(p.relative_to(root))
            h = sha256(p)
            row = {"source": src.get("name", str(root)), "relative_path": rel, "local_path": str(p),
                   "original_path": str(original / rel), "size_mb": round(p.stat().st_size / 2**20, 2),
                   "sha256": h, "role": file_role(p), "note": ""}
            rows.append(row)
            if row["role"] == "raw image":
                by_hash.setdefault(h, []).append(row)
        prefs = cfg["duplicate_preference"]
        for group in by_hash.values():
            if len(group) < 2:
                continue
            keep = min(group, key=lambda r: (not any(t in r["relative_path"] for t in prefs),
                                             len(r["relative_path"]), r["relative_path"]))
            for r in group:
                if r is not keep:
                    r["role"] = "duplicate (byte-identical copy of another file)"
                    r["note"] = f"same file as {keep['relative_path']}"
        for row in rows:
            if row["role"] != "raw image":
                continue
            p = Path(row["local_path"])
            try:
                found = images_in(p, cfg)
            except Exception as exc:
                row["role"] = "unreadable image"
                row["note"] = str(exc)[:200]
                log(f"  could not read {row['relative_path']}: {exc}")
                continue
            if not found:
                row["role"] = "other image (no microscope metadata; not used)"
                continue
            for img in found:
                img.update({"source": row["source"], "file": p.name, "relative_path": row["relative_path"],
                            "local_path": row["local_path"], "original_path": row["original_path"],
                            "file_sha256": row["sha256"], "genotype": C.genotype_for(src, row["relative_path"])})
                images.append(img)
        manifest += rows
    return manifest, images
