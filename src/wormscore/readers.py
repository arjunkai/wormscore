"""Find images in source folders, read their metadata, and read their pixels.

Supported raw formats: Leica .lif (one image per series), Zeiss .czi (one per scene),
OME-TIFF (one per series; metadata in the file or in a .companion.ome next to it), Nikon .nd2
(one per stage position), Olympus FluoView .oib and .oif (one per file), and, only for sources
that turn it on, ImageJ hyperstack TIFF (channel order stated in config.json).

Every image becomes a dict with the same fields: where it came from, what it is, how
it was taken, and which channel is the marker or brightfield. Channel identity comes
from metadata only. If the metadata does not settle it, the channel is UNRESOLVED.
"""
import hashlib
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import czifile
import numpy as np
import oiffile
import tifffile
from liffile import LifFile

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


OIF_PART = "part of an Olympus .oif image (read with its .oif file)"
NOT_USED = "other image (no microscope metadata; not used)"


def file_role(path):
    name, low = path.name, path.name.lower()
    if name.startswith("._") or name in (".DS_Store", "desktop.ini", "Thumbs.db"):
        return "system hidden file (not data)"
    if any(part.lower().endswith(".oif.files") for part in path.parts[:-1]):
        return OIF_PART
    if low.endswith((".lif", ".czi", ".nd2", ".oib", ".oif")):
        return "raw image"
    if low.endswith(".ome.tif") or low.endswith(".ome.tiff"):
        return "derived (made later from a raw image)" if DERIVED.search(name) else "raw image"
    if low.endswith(".companion.ome"):
        return "metadata sidecar of an OME-TIFF"
    if path.suffix.lower() in OTHER_IMAGE:
        if DERIVED.search(name):
            return "derived (made later from a raw image)"
        return NOT_USED
    if low.endswith((".lof", ".xlef", ".xlif", ".lock")):
        return "Leica LAS X project file (not read; export to .lif)"
    return "reference (not an image)"


# ------------------------------------------------------------------ channel rules

# A channel is the marker (the reporter being scored) if it matches the "marker" settings, and the
# identity channel if it does not but matches the optional "identity" settings. The reporter is
# tested first, so a channel can never be both.

def _leica_match(ch, lut, m):
    lines = {int(float(ln)) for ln, _ in ch["lasers"]}
    lo = float(ch["band"].split("-")[0]) if ch["band"] else 0
    b0, b1 = m["emission_band_start_nm"]
    lut_ok = not m.get("leica_lut") or lut == m["leica_lut"]
    return bool(lines & set(m["excitation_nm"]) and b0 <= lo <= b1 and lut_ok)


def marker_from_leica(ch, lut, cfg):
    if ch["detector"] and "trans" in ch["detector"].lower():
        return "BF"
    if _leica_match(ch, lut, cfg["marker"]):
        return "MARKER"
    if cfg.get("identity") and _leica_match(ch, lut, cfg["identity"]):
        return "IDENTITY"
    return "OTHER"


def _named_match(name, fluor, excitation, emission, m):
    em = m.get("emission_nm")
    return bool((fluor and fluor in m["dye_names"])
                or any(name.upper().startswith(p.upper()) for p in m["channel_name_prefixes"])
                or (excitation and int(float(excitation)) in m["excitation_nm"])
                or (emission and em and em[0] <= float(emission) <= em[1]))


def _fluor_name(name, cfg):
    """True if the channel name starts with a name of one of the project's own fluorophores (1.26.0): "BFP"
    begins like the brightfield name "BF", so in a project with a BFP channel it is that channel, not brightfield."""
    ms = [cfg["marker"], cfg.get("identity")] + list(cfg.get("identity_extra") or [])
    # prefixes under 3 letters are ignored, so a hand-edited "T" or "" cannot turn "Trans" or "DIC" into a fluorophore
    return any(name.upper().startswith(p.upper()) for m in ms if m
               for p in m.get("channel_name_prefixes", []) if isinstance(p, str) and len(p) >= 3)


# 1.26.8: transmitted-light detectors, whatever config.json lists. Only the detector's name itself, with a
# number or a track ("TD", "TD1", "TPMT-T2"): "td-Tomato" and "TD Tomato" are a fluorophore (1.26.10).
TRANSMITTED_NAME = re.compile(r"(TD|TPMT)\s*\d*([-_ ]T\d+)?", re.I)
WHOLE_WORD = ("BF", "TL")


def _brightfield_name(name, cfg):
    """True if the channel name is a brightfield name: it begins with one of brightfield_channel_names,
    as before. "BF" and "TL" must be the whole word (1.26.8): "BFP" and "TLR-4" begin like them and were
    read as brightfield. "TL DIC", "BF 2" and "TLDIC" still are."""
    names = [p.upper() for p in cfg["brightfield_channel_names"] if p]
    up = name.upper().strip()
    if TRANSMITTED_NAME.fullmatch(up):
        return True
    for p in names:
        if not up.startswith(p):
            continue
        rest = up[len(p):]
        if p not in WHOLE_WORD or not rest[:1].isalpha() or any(rest.startswith(q) for q in names):
            return True
    return False


def marker_from_named(name, fluor, excitation, transmitted, cfg, emission=None):
    """emission (nm) is used only if the marker settings give an emission_nm range."""
    if transmitted or (_brightfield_name(name, cfg) and not _fluor_name(name, cfg)):
        return "BF"
    if _named_match(name, fluor, excitation, emission, cfg["marker"]):
        return "MARKER"
    if cfg.get("identity") and _named_match(name, fluor, excitation, emission, cfg["identity"]):
        return "IDENTITY"
    return "OTHER"


def prefer_named_dye(roles, fluors, cfg):
    """Several channels can match the marker by laser line (for example a second fluorophore
    excited at 488 nm). If exactly one of them names a configured dye, that one is the marker.
    The same rule picks the identity channel."""
    for role, m in (("MARKER", cfg["marker"]), ("IDENTITY", cfg.get("identity"))):
        if not m:
            continue
        hits = [i for i, r in enumerate(roles) if r == role]
        named = [i for i in hits if fluors[i] and fluors[i] in m["dye_names"]]
        if len(hits) > 1 and len(named) == 1:
            roles = ["OTHER" if r == role and i != named[0] else r for i, r in enumerate(roles)]
    return roles


def with_extras(roles, cfg, match):
    """Channels left as OTHER that match an extra identity channel (1.23.0, for example NeuroPAL's) get
    the role IDENTITY2, IDENTITY3, ... in the order config.json lists them. match(i, settings) -> bool.
    Without extra identity channels the roles are returned unchanged."""
    extras = cfg.get("identity_extra") or []
    if not extras:
        return roles
    out = list(roles)
    for k, m in enumerate(extras, start=2):
        for i, r in enumerate(out):
            if r == "OTHER" and match(i, m):
                out[i] = f"IDENTITY{k}"
    return out


def named_extras(roles, cfg, names, fluors, excitations, emissions):
    get = lambda xs, i: xs[i] if xs and i < len(xs) else None
    if roles.count("MARKER") > 1 and cfg.get("identity_extra"):
        roles = list(roles)
        for i, r in enumerate(roles):
            if r == "MARKER" and any(_strong_match(get(names, i) or "", get(fluors, i), get(emissions, i), m)
                                     for m in cfg["identity_extra"]):
                roles[i] = "OTHER"
    return with_extras(roles, cfg, lambda i, m: _named_match(get(names, i) or "", get(fluors, i), get(excitations, i),
                                                             get(emissions, i), m))


def _strong_match(name, fluor, emission, m):
    """A match on more than the laser line: the dye name, the channel name, or the emission wavelength."""
    em = m.get("emission_nm")
    return bool((fluor and fluor in m["dye_names"])
                or any(name.upper().startswith(p.upper()) for p in m["channel_name_prefixes"])
                or (emission and em and em[0] <= float(emission) <= em[1]))


def pick(roles, role):
    return roles.index(role) if roles.count(role) == 1 else None


FIRST_ONLY = "only the first is read"
AXIS_NAMES = {"M": "tile scan ({n} tiles)", "H": "phases ({n})", "R": "rotations ({n})", "I": "illuminations ({n})",
              "V": "views ({n})", "B": "blocks ({n})", "S": "colour samples ({n})", "L": "lambda scan ({n} wavelengths)",
              "Q": "extra dimension ({n})"}


def _extra_axes_flags(axes, shape, known="CZYXT"):
    """1.26.8: an image with a dimension wormscore does not use (the tiles of a tile scan, the wavelengths
    of a lambda scan, Airyscan phases). Only its first entry is read, as before, and now a flag says so."""
    return [f"{AXIS_NAMES.get(a, 'extra dimension ' + a + ' ({n})').format(n=n)}: {FIRST_ONLY}"
            for a, n in zip(axes, shape) if a not in known and n > 1]


LEICA_DIMS = {5: "L", 9: "L", 10: "M"}      # DimID: lambda, excitation lambda, mosaic (tiles)


# ------------------------------------------------------------------ Leica LIF

@lru_cache(maxsize=None)
def _lif(path):
    return LifFile(str(path))


def _lif_series(lf):
    """Image series in the order, and with the names and numbers, that wormscore 0.1.x
    got from readlif. Series numbers are part of an image's identity in the key, so
    this walk must not change."""
    pixels = {id(im.xml_element): im for im in lf.images}
    out = []

    def walk(tree, path):
        children = tree.findall("./Children/Element") or tree.findall("./Element")
        for item in children:
            sub = item.attrib["Name"] if path == "" else f"{path}/{item.attrib['Name']}"
            if item.find("./Data/Image") is not None:
                dims = item.findall("./Data/Image/ImageDescription/Dimensions/")
                n = {int(d.get("DimID")): int(d.get("NumberOfElements")) for d in dims}
                scale = {}
                for d in dims:
                    k = int(d.get("DimID"))
                    try:
                        scale[k] = (n[k] - 1) / (float(d.get("Length")) * (1 if k == 4 else 10**6))
                    except (TypeError, ZeroDivisionError):
                        scale[k] = None
                chans = item.findall("./Data/Image/ImageDescription/Channels/ChannelDescription")
                st = item.find("./Data/Image/Attachment/ATLConfocalSettingDefinition")
                out.append({
                    "name": "/".join(f"{path}/{item.attrib['Name']}".split("/")[1:]),
                    "x": n.get(1, 1), "y": n.get(2, 1), "z": n.get(3, 1), "t": n.get(4, 1),
                    "scale_x": scale.get(1), "scale_z": scale.get(3),
                    "extra": [(LEICA_DIMS.get(k, "Q"), v) for k, v in sorted(n.items()) if k not in (1, 2, 3, 4)],
                    "bit_depth": [int(c.get("Resolution")) for c in chans], "channels": len(chans),
                    "settings": dict(st.attrib) if st is not None else {},
                    "element": item, "image": pixels.get(id(item)),
                })
            if item.findall("./Children/Element/Data"):
                walk(item, sub)

    walk(lf.xml_element, "")
    return out


def _leica_channels(elem):
    seq = elem.find(".//LDM_Block_Sequential_List")
    steps = seq.findall(".//ATLConfocalSettingDefinition") if seq is not None else []
    if not steps:
        steps = elem.findall(".//ATLConfocalSettingDefinition")[:1]
    # 1.26.8: current LAS X (SP8 3.5, Stellaris 4.4) writes for each step of a sequential scan only what
    # differs from the image's master setting: the step names its active detectors and its laser lines,
    # the master holds the detection bands, the averaging and often the gain. A step's missing values are
    # taken from the master. Before, such a step had no detection band, so its reporter was never found.
    master = elem.find("./Data/Image/Attachment/ATLConfocalSettingDefinition")
    if master is None:
        master = elem.find(".//LDM_Block_Sequential_Master//ATLConfocalSettingDefinition")
    if master is None or any(s is master for s in steps):
        master = None
    m_bands = {b.get("Channel"): b for b in master.iter("MultiBand")} if master is not None else {}
    m_dets = {d.get("Channel"): d for d in master.iter("Detector")} if master is not None else {}
    lines = lambda s: [(l.get("LaserLine"), float(l.get("IntensityDev") or 0))
                       for l in s.iter("LaserLineSetting") if float(l.get("IntensityDev") or 0) > 0]
    chans = []
    for s in steps:
        lasers = lines(s)
        if master is not None and next(s.iter("LaserLineSetting"), None) is None:
            lasers = lines(master)                  # a step that lists no laser lines at all
        bands = {b.get("Channel"): b for b in s.iter("MultiBand")}
        for d in s.iter("Detector"):
            if d.get("IsActive") != "1":
                continue
            band = bands.get(d.get("Channel"), m_bands.get(d.get("Channel")))
            gain = d.get("Gain")
            if gain is None and d.get("Channel") in m_dets:
                gain = m_dets[d.get("Channel")].get("Gain")
            line_avg = s.get("LineAverage") or (master.get("LineAverage") if master is not None else None)
            chans.append({"detector": d.get("Name"), "gain": float(gain or 0),
                          "band": f"{band.get('LeftWorld')}-{band.get('RightWorld')}" if band is not None else "",
                          "lasers": lasers, "line_avg": line_avg})
    return chans


def leica_images(path, cfg):
    lf = _lif(path)
    system = next((a.get("SystemTypeName") for a in lf.xml_element.iter("Attachment") if a.get("SystemTypeName")), "")
    out = []
    for i, im in enumerate(_lif_series(lf)):
        st, elem = im["settings"], im["element"]
        luts = [c.get("LUTName") for c in elem.iter("ChannelDescription")]
        chans = _leica_channels(elem)
        flags = _extra_axes_flags([a for a, _ in im["extra"]], [v for _, v in im["extra"]])
        if len(chans) == im["channels"]:
            roles = [marker_from_leica(c, l, cfg) for c, l in zip(chans, luts)]
            roles = with_extras(roles, cfg, lambda i, m: _leica_match(chans[i], luts[i], m))
        else:
            roles = ["UNRESOLVED"] * im["channels"]
            flags.append(f"{len(chans)} detectors for {im['channels']} channels")
        mi = pick(roles, "MARKER")
        g = chans[mi] if mi is not None else {}
        sx, sz = im["scale_x"], im["scale_z"]
        bits = max(im["bit_depth"])
        out.append({
            "format": "Leica LIF", "system": f"Leica {system}".strip(),
            "location_in_file": f"series {i} ({im['name']})",
            "objective": st.get("ObjectiveName", "").strip(),
            "n_z": im["z"], "n_t": im["t"], "width": im["x"], "height": im["y"],
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

NO_BITS = "bit depth not recorded"


def _no_bits_flag(system, cfg, top):
    """1.26.7: a 16-bit file that does not say how many bits the detector used (a 12- or 14-bit camera
    saves into 16). The top of the range is not guessed: the image is flagged, unless config.json
    states the microscope's saturation_value."""
    if system in (cfg.get("saturation_value") or {}):
        return []
    return [f"{NO_BITS}: saturation is taken as {top}; for a 12- or 14-bit camera set saturation_value in config.json"]


def _num(v):
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return None


def _zeiss_settings(c, md):
    """What sets the brightness of one channel (1.26.7), read from that channel's own block: detector gain
    or voltage, each laser line with the share of its power let through, lamp intensity, camera exposure
    and averaging. ZEN writes the gain as DetectorSettings/Gain and the laser as LightSourceSettings/
    Attenuation (the share held back). Before 1.26.7 only Voltage and Transmission were looked for, which
    those files do not have, so images taken at different gain shared a settings group."""
    get = lambda path: (c.findtext(path) or "").strip()
    parts = []
    volt, gain = _num(get("DetectorSettings/Voltage")), _num(get("DetectorSettings/Gain"))
    if volt is not None:
        parts.append(f"PMT {volt:.0f} V")
    elif gain is not None:
        parts.append(f"gain {gain:g}")
    for tag, label in (("DigitalGain", "digital gain"), ("EMGain", "EM gain")):
        v = _num(get(f"DetectorSettings/{tag}"))
        if v is not None and v not in (0, 1):
            parts.append(f"{label} {v:g}")
    lights = []
    for l in c.iter("LightSourceSettings"):
        text = lambda tag: (l.findtext(tag) or "").strip()
        w, t, a = _num(text("Wavelength")), _num(text("Transmission")), _num(text("Attenuation"))
        share = t if t is not None else 1 - a if a is not None else None
        if w is not None and share is not None:
            lights.append(f"{w:.0f}nm@{share * 100:.2f}%")
        elif text("Intensity"):
            lights.append(f"light {text('Intensity')}")
    if not lights:      # no laser in the channel's block: every line of the experiment, as before 1.26.7
        lights = [f"{w}nm@{float(t) * 100:.2f}%" for w, t in sorted(
            {(l.findtext("Wavelength"), l.findtext("Transmission")) for l in md.iter("LightSourceSettings")
             if l.findtext("Wavelength")}) if t]
    if lights:
        parts.append(";".join(lights))
    exposure = _num(get("ExposureTime"))        # cameras; in nanoseconds
    if exposure:
        parts.append(f"exposure {exposure / 1e6:g} ms")
    avg = get("LaserScanInfo/Averaging") or next((e.text.strip() for e in md.iter("Averaging") if e.text and e.text.strip()), "")
    if avg:
        parts.append(f"avg {avg}")
    return "; ".join(parts)


def _zeiss_meta(md, cfg):
    """Everything read from a CZI's metadata XML; it is the same for every scene of the file."""
    text = lambda tag: next((e.text.strip() for e in md.iter(tag) if e.text and e.text.strip()), "")
    scale = {d.get("Id"): float(d.findtext("Value")) * 1e6 for d in md.iter("Distance") if d.get("Id") in ("X", "Z")}
    chans = md.find(".//Information/Image/Dimensions/Channels")
    chans = list(chans) if chans is not None else []
    names, roles, fluors = [], [], []
    for c in chans:
        names.append(c.get("Name") or "")
        fluors.append(c.findtext("Fluor"))
        roles.append(marker_from_named(c.get("Name") or "", c.findtext("Fluor"), c.findtext("ExcitationWavelength"),
                                       c.findtext("IlluminationType") == "Transmitted", cfg))
    roles = prefer_named_dye(roles, fluors, cfg)
    roles = named_extras(roles, cfg, names, fluors, [c.findtext("ExcitationWavelength") for c in chans],
                         [None] * len(roles))
    mi = pick(roles, "MARKER")
    objective = text("ObjectiveName")
    if not objective and text("NominalMagnification"):
        na = f"/{float(text('LensNA')):g}" if text("LensNA") else ""
        objective = f"{text('NominalMagnification')}x{na} {text('Immersion')}".strip()
    system = f"Zeiss {text('System')}".strip()
    # 1.26.7: cameras record the bits they use as ComponentBitCount (12 or 14, saved into 16). It was not
    # read, so saturation was taken as 65535 and never flagged.
    image = md.find(".//Information/Image")
    own = lambda tag: ((chans[mi].findtext(tag) if mi is not None else None)
                       or (image.findtext(tag) if image is not None else None) or "").strip()
    flags = []
    bits = text("EffectiveBitsPerPixel") or text("BitsPerPixel") or own("ComponentBitCount")
    if not bits:
        bits = "8" if own("PixelType") == "Gray8" else "16"
        if bits == "16":
            flags = _no_bits_flag(system, cfg, 65535)
    return {"system": system, "objective": objective, "pixel_um": round(scale.get("X", 0), 4),
            "z_step_um": round(scale.get("Z", 0), 3), "bits": int(bits), "channel_roles": roles, "channel_names": names,
            "settings": _zeiss_settings(chans[mi], md) if mi is not None else "",
            "zoom": text("Zoom").replace(",", "."), "acquired": text("AcquisitionDateAndTime")[:19].replace("T", " "),
            "flags": flags}


def zeiss_images(path, cfg):
    with czifile.CziFile(str(path)) as f:
        md = f.xml_element if not callable(f.xml_element) else f.xml_element()
        # f.scenes maps scene number -> scene, like a dict
        scenes = [(k, f.scenes[k].dims, f.scenes[k].shape) for k in f.scenes]
    meta = _zeiss_meta(md, cfg)
    out = []
    for i, dims, shape in scenes:
        dims = "".join(dims)
        out.append({
            "format": "Zeiss CZI", "system": meta["system"],
            "location_in_file": f"whole file (scene {i})" if len(scenes) == 1 else f"scene {i}",
            "objective": meta["objective"],
            "n_z": shape[dims.index("Z")] if "Z" in dims else 1,
            "n_t": shape[dims.index("T")] if "T" in dims else 1,
            "width": shape[dims.index("X")], "height": shape[dims.index("Y")],
            "pixel_um": meta["pixel_um"], "z_step_um": meta["z_step_um"],
            "bits": meta["bits"], "channel_roles": meta["channel_roles"], "channel_names": meta["channel_names"],
            "settings": meta["settings"], "zoom": meta["zoom"], "acquired": meta["acquired"],
            "stage_x": None, "stage_y": None, "flags": list(meta["flags"]) + _extra_axes_flags(dims.replace("0", ""), [n for a, n in zip(dims, shape) if a != "0"]),
            "_reader": ("czi", str(path), i, dims),
        })
    return out


# ------------------------------------------------------------------ OME-TIFF

INCOMPLETE = "file is incomplete (planes missing)"


def _ome_settings(chan):
    """Detector and light-source settings of one channel (1.26.7), as the file states them: gain, voltage,
    and each light source's wavelength and attenuation. Files without them give nothing, as before."""
    if chan is None:
        return []
    parts = []
    ds = chan.find("o:DetectorSettings", OME_NS)
    if ds is not None:
        gain, volt = _num(ds.get("Gain", "")), _num(ds.get("Voltage", ""))
        if gain is not None:
            parts.append(f"gain {gain:g}")
        if volt is not None:
            parts.append(f"detector {volt:g} {ds.get('VoltageUnit', 'V')}")
    for ls in chan.findall("o:LightSourceSettings", OME_NS):
        w, a = _num(ls.get("Wavelength", "")), _num(ls.get("Attenuation", ""))
        if w is None and a is None:
            continue
        unit = ls.get("WavelengthUnit", "nm")
        parts.append((f"{w:g}{unit}" if w is not None else "light") + (f" attenuation {a:g}" if a is not None else ""))
    return parts


OME_SCHEMA = re.compile(r"http://www\.openmicroscopy\.org/Schemas/OME/\d{4}-\d{2}")
MICROMETRES = {"\u00b5m": 1, "\u03bcm": 1, "um": 1, "micron": 1, "nm": 1e-3, "pm": 1e-6, "\u00c5": 1e-4, "mm": 1e3, "cm": 1e4, "m": 1e6}


def _ome_root(md):
    """Files written to an older OME schema (2008 to 2015) keep the same names for everything read here,
    under another namespace (1.26.8). They were listed as "no microscope metadata"."""
    old = OME_SCHEMA.match(md.tag[1:]) if md.tag.startswith("{") else None
    if old and old.group(0) != OME_NS["o"]:
        for el in md.iter():
            if el.tag.startswith("{" + old.group(0) + "}"):
                el.tag = "{" + OME_NS["o"] + "}" + el.tag.split("}", 1)[1]
    return md


def _ome_um(px, axis, flags):
    """A physical size in micrometres. The unit defaults to micrometres; other units are converted (1.26.8,
    they were read as micrometres), and a size in a unit that is not a length is flagged and not used."""
    value, unit = _num(px.get(f"PhysicalSize{axis}", "") or ""), px.get(f"PhysicalSize{axis}Unit", "\u00b5m")
    if value is None:
        return 0.0
    if unit not in MICROMETRES:
        flags.append(f"pixel size is given in '{unit}', not a length")
        return 0.0
    return value if MICROMETRES[unit] == 1 else round(value * MICROMETRES[unit], 6)


def ome_images(path, cfg):
    with tifffile.TiffFile(str(path)) as f:
        series = [(s.axes, s.shape, s.dtype) for s in f.series]
        xml = f.ome_metadata
        # 1.26.5: a file cut short (a copy that did not finish) still opens, and its missing planes read
        # as black, so a cell that is there would look missing. Planes stored past the end of the file
        # are found here and the image is flagged and left out.
        size, cut = f.filehandle.size, set()
        for k, s in enumerate(f.series):
            for page in s.pages:
                if page is None:        # a plane the file (or a missing file of its set) no longer has
                    cut.add(k)
                    break
                if getattr(page, "parent", f) is not f:
                    continue            # a plane kept in another file of the set
                if any(o + n > size for o, n in zip(page.dataoffsets, page.databytecounts)):
                    cut.add(k)
                    break
    md = _ome_root(ET.fromstring(xml)) if xml else None
    if md is None or md.find(".//o:Pixels", OME_NS) is None:
        # the metadata is in another file: the one the file names (1.26.8), or <name>.companion.ome next to it
        named = md.find("o:BinaryOnly", OME_NS).get("MetadataFile") if md is not None and md.find("o:BinaryOnly", OME_NS) is not None else None
        companion = Path(re.sub(r"(_t\d+_p\d+)?\.ome\.tiff?$", ".companion.ome", str(path)))
        # only a plain file name next to this file: the value comes from the file, so a path elsewhere
        # (another folder, a network address) is never opened
        if named and re.fullmatch(r"[^\\/:]+", named) and named not in (".", "..") and (Path(path).parent / named).is_file():
            companion = Path(path).parent / named
        if not companion.exists():
            return []
        if companion.name.lower().endswith((".tif", ".tiff")):      # the set's metadata is inside one of its TIFFs
            with tifffile.TiffFile(str(companion)) as cf:
                md = _ome_root(ET.fromstring(cf.ome_metadata or "<none/>"))
        else:
            md = _ome_root(ET.parse(str(companion)).getroot())
        if md.find(".//o:Pixels", OME_NS) is None:
            return []
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
        roles = prefer_named_dye(roles, [c.get("Fluor") for c in px.findall("o:Channel", OME_NS)], cfg)
        roles = named_extras(roles, cfg, names, [c.get("Fluor") for c in px.findall("o:Channel", OME_NS)],
                             [c.get("ExcitationWavelength") for c in px.findall("o:Channel", OME_NS)], None)
        mi = pick(roles, "MARKER")
        exposures = sorted({p.get("ExposureTime") for p in px.findall("o:Plane", OME_NS)
                            if p.get("TheC") == str(mi) and p.get("ExposureTime")})
        bits = int(px.get("SignificantBits") or np.iinfo(dtype).bits if np.issubdtype(dtype, np.integer) else 16)
        flags = [INCOMPLETE] if i in cut else []
        if not px.get("SignificantBits") and bits == 16:
            flags += _no_bits_flag(system, cfg, 65535)
        chan = px.findall("o:Channel", OME_NS)[mi] if mi is not None else None
        flags += _extra_axes_flags(axes, shape)
        n_z = shape[axes.index("Z")] if "Z" in axes else 1
        if len(flags) == len([INCOMPLETE] if i in cut else []) + (1 if any(NO_BITS in x for x in flags) else 0) \
                and (px.get("SizeZ") or "").isdigit() and int(px.get("SizeZ")) != n_z:
            # 1.26.8: one file of a set that stores each plane in its own file
            flags.append(f"the metadata lists {px.get('SizeZ')} planes, this file holds {n_z} (one file of a multi-file set)")
        pixel_um, z_step_um = _ome_um(px, "X", flags), _ome_um(px, "Z", flags)
        out.append({
            "format": "OME-TIFF", "system": system,
            "location_in_file": "whole file (series 0)" if len(series) == 1 else f"series {i}",
            "objective": objective,
            "n_z": shape[axes.index("Z")] if "Z" in axes else 1,
            "n_t": shape[axes.index("T")] if "T" in axes else 1,
            "width": shape[axes.index("X")], "height": shape[axes.index("Y")],
            "pixel_um": pixel_um, "z_step_um": z_step_um,
            "bits": bits, "channel_roles": roles, "channel_names": names,
            "settings": "; ".join(_ome_settings(chan) + (["exposure " + ",".join(exposures)] if exposures else [])),
            "zoom": "", "acquired": (img.findtext("o:AcquisitionDate", namespaces=OME_NS) or img.findtext("o:AcquiredDate", namespaces=OME_NS) or "")[:19].replace("T", " "),
            "stage_x": None, "stage_y": None, "flags": flags,
            "_reader": ("ome", str(path), i, axes),
        })
    return out


# ------------------------------------------------------------------ Nikon ND2

def _nd2():
    """The Nikon reader is loaded only when an .nd2 file is scanned (it is large)."""
    try:
        import nd2
    except ImportError:
        raise RuntimeError('Nikon .nd2 support is not installed. Run: pip install "wormscore[nd2]"') from None
    return nd2


NOT_CALIBRATED = "is not calibrated in the file"
CAMERA_LINE = r"(Exposure|Gain|Multiplier):\s*([^\n]+)"


def _nikon_block(description, name, n_channels):
    """The part of the file's description that belongs to the channel called name: its "Plane #n:" block,
    or the whole description of a one-channel file. None if it cannot be told which part is the channel's."""
    text = description.replace("\r\n", "\n")
    blocks = re.split(r"^\s*Plane #\d+:\s*$", text, flags=re.M)
    if len(blocks) > 1:
        tail = re.split(r"^\s*(?:Time Loop|Z Stack Loop|XY Loop|Multipoint Loop)\b", blocks[-1], flags=re.M)[0]
        mine = [b for b in blocks[1:-1] + [tail] if re.search(r"^\s*Name:\s*(.*?)\s*$", b, re.M)
                and re.search(r"^\s*Name:\s*(.*?)\s*$", b, re.M).group(1) == name]
        return mine[0] if len(mine) == 1 else None
    return text if n_channels == 1 and text.strip() else None


def _nikon_settings(description, capturing, name, n_channels):
    """Camera and light settings of the reporter channel (1.26.7), from that channel's own part of the
    description: exposure, gain and multiplier as before, and now each laser line that is on with its
    power, the fluorescence lamp, and a confocal detector's voltage. Before 1.26.7 only the camera text
    of the file was read, so images taken at different laser power shared a settings group. Without a
    part for the channel, the camera text is used as before."""
    block = _nikon_block(description, name, n_channels) if name is not None else None
    if block is None:
        return "; ".join(f"{k.lower()} {v.strip()}" for k, v in re.findall(CAMERA_LINE, capturing))
    # some files list the camera once for all channels, under the first: then the file's camera text stands
    parts = [f"{k.lower()} {v.strip()}" for k, v in re.findall(CAMERA_LINE, block) or re.findall(CAMERA_LINE, capturing)]
    lines = re.findall(r"Line:\s*\d+;\s*(?:ExW|Excitation wavelength):\s*([\d.]+);[^\n]*?Power:\s*([\d.]+)(?:;\s*(\w+))?", block)
    marked = any(state for _, _, state in lines)       # some files mark the lines in use (Active, On), some list only those
    lasers = [f"{float(w):g}nm@{float(p):g}" for w, p, state in lines
              if float(p) > 0 and (state in ("Active", "On") if marked else True)]
    if lasers:
        parts.append(";".join(lasers))
    parts += [f"lamp {k.lower()} {v.strip()}" for k, v in re.findall(r"Light \(EPI\)[^:\n]*?(voltage|intensity):\s*([^\n]+)", block, re.I)
              if v.strip().upper() != "NA"]
    parts += [f"{k.strip()} {v}" for k, v in re.findall(r"((?:[A-Za-z]+ )?HV(?:\([A-Za-z]+\))?|Laser Power)\s*:\s*([\d.]+)", block)]
    return "; ".join(parts)


def nikon_images(path, cfg):
    with _nd2().ND2File(str(path)) as f:
        sizes = dict(f.sizes)
        if f.is_rgb:
            return []          # a colour camera picture, not a fluorescence stack
        if f.is_legacy:
            # files from NIS-Elements before 2010 (JPEG2000 inside); they gave a Python error naming no cause
            raise ValueError("an old Nikon .nd2 format that wormscore does not read. "
                             "Open it in NIS-Elements and save it again, or export to OME-TIFF")
        if any(n <= 0 for n in sizes.values()) or not 1 <= f.attributes.bitsPerComponentSignificant <= 32:
            raise ValueError("the file's header is damaged (image size or bit depth is not possible)")
        chans = getattr(f.metadata, "channels", None)
        if not chans:
            raise ValueError("the file records no channel information")
        vox = f.voxel_size()
        calibrated = bool(chans[0].volume.axesCalibrated[0])
        text = f.text_info
        bits = f.attributes.bitsPerComponentSignificant
        positions = []
        for p in range(sizes.get("P", 1)):
            first = next((i for i, ix in enumerate(f.loop_indices) if ix.get("P", 0) == p), 0)
            positions.append(f.frame_metadata(first).channels[0].position.stagePositionUm)
    names = [c.channel.name or "" for c in chans]
    roles = [marker_from_named(c.channel.name or "", None, c.channel.excitationLambdaNm,
                               "brightfield" in c.microscope.modalityFlags, cfg, c.channel.emissionLambdaNm)
             for c in chans]
    mic = chans[0].microscope
    objective = (mic.objectiveName or "").strip()
    if mic.objectiveMagnification and not re.search(r"\d+x", objective):
        objective = f"{mic.objectiveMagnification:g}x {objective}".strip()
    capturing = (text.get("capturing") or "").replace("\r\n", "\n")
    camera = capturing.split("\n", 1)[0].strip()
    roles = named_extras(prefer_named_dye(roles, [None] * len(roles), cfg), cfg, names, None,
                         [c.channel.excitationLambdaNm for c in chans], [c.channel.emissionLambdaNm for c in chans])
    mi = pick(roles, "MARKER")
    settings = _nikon_settings(text.get("description") or "", capturing, names[mi] if mi is not None else None, len(chans))
    dims = "".join(sizes)
    out = []
    for p, stage in enumerate(positions):
        out.append({
            "format": "Nikon ND2", "system": f"Nikon {camera}".strip(),
            "location_in_file": "whole file" if len(positions) == 1 else f"position {p}",
            "objective": objective,
            "n_z": sizes.get("Z", 1), "n_t": sizes.get("T", 1), "width": sizes["X"], "height": sizes["Y"],
            "pixel_um": round(vox.x, 4), "z_step_um": round(vox.z, 3) if "Z" in sizes else 0.0,
            "bits": bits, "channel_roles": list(roles), "channel_names": names,
            "settings": settings, "zoom": f"{mic.zoomMagnification:g}" if mic.zoomMagnification else "",
            "acquired": " ".join((text.get("date") or "").split()),
            "stage_x": stage.x, "stage_y": stage.y,
            # 1.26.8: an uncalibrated file reads as 1 um per pixel, which sets the display scale and the cell search
            "flags": ([] if calibrated else [f"pixel size {NOT_CALIBRATED} (1 um per pixel is a placeholder)"])
            + _extra_axes_flags(dims.replace("P", ""), [n for a, n in sizes.items() if a != "P"]),
            "_reader": ("nd2", str(path), p, dims),
        })
    return out


# ------------------------------------------------------------------ Olympus FluoView OIB / OIF

def _olympus_number(v):
    """FluoView writes numbers with a decimal comma and a trailing '.0', for example '0,207.0' for
    0.207 (pixel size in um) or '1,35.0' for an NA of 1.35."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(r"\s*(-?\d+),(\d+)(?:\.0+)?\s*", str(v))
    return float(f"{m.group(1)}.{m.group(2)}") if m else float(v)


def _olympus_plane_info(f):
    """Objective lines from the first plane's property file (.pty)."""
    fs = f.filesystem
    pty = next((n for n in sorted(fs.files()) if n.lower().endswith(".pty") and "thumb" not in n.lower()), None)
    if pty is None:
        return {}
    raw = fs.open_file(pty).read()
    textual = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("latin-1")
    return dict(re.findall(r'^\s*([^=\r\n]+?)=\s*"?([^"\r\n]*)"?\s*$', textual, re.M))


def olympus_images(path, cfg):
    with oiffile.OifFile(str(path)) as f:
        axes, shape, md = f.axes, f.shape, f.mainfile
        plane = _olympus_plane_info(f)
    size = dict(zip(axes, shape))
    acq = md.get("Acquisition Parameters Common", {})
    chans = [md[f"Channel {i} Parameters"] for i in range(1, size.get("C", 1) + 1) if f"Channel {i} Parameters" in md]
    names = [f"{c.get('CH Name', '')} {c.get('DyeName', '')}".strip() for c in chans]
    roles = [marker_from_named(str(c.get("DyeName", "")), c.get("DyeName"), c.get("ExcitationWavelength"),
                               "trans" in str(c.get("LightType", "")).lower() or str(c.get("CH Name", "")).upper().startswith("TD"),
                               cfg, c.get("EmissionWavelength")) for c in chans]
    roles = prefer_named_dye(roles, [c.get("DyeName") for c in chans], cfg)
    roles = named_extras(roles, cfg, [str(c.get("DyeName", "")) for c in chans], [c.get("DyeName") for c in chans],
                         [c.get("ExcitationWavelength") for c in chans], [c.get("EmissionWavelength") for c in chans])
    if len(chans) != size.get("C", 1):
        roles = ["UNRESOLVED"] * size.get("C", 1)
    mi = pick(roles, "MARKER")
    ref = md.get("Reference Image Parameter", {})
    zaxis = next((a for k, a in md.items() if k.startswith("Axis ") and a.get("AxisCode") == "Z"), {})
    z_step = 0.0
    if size.get("Z", 1) > 1 and zaxis.get("Interval"):
        z_step = abs(_olympus_number(zaxis["Interval"])) / (1000 if zaxis.get("PixUnit") == "nm" else 1)
    mag, na = plane.get("Magnification"), plane.get("ObjectiveLens NAValue")
    lens = " ".join(plane.get("ObjectiveLens Name", "").split())
    objective = (f"{_olympus_number(mag):g}x" + (f"/{_olympus_number(na):g}" if na else "") + (f" ({lens})" if lens else "")) \
        if mag else lens
    lasers = [(acq.get(f"LaserWavelength{i:02d}"), acq.get(f"LaserTransmissivity{i:02d}"))
              for i in range(1, int(acq.get("Number of use Laser", 0) or 0) + 1)]
    settings = "; ".join(x for x in [
        f"PMT {chans[mi].get('AnalogPMTVoltage')} V" if mi is not None and chans[mi].get("AnalogPMTVoltage") else "",
        ";".join(f"{w}nm@{_olympus_number(t):g}%" for w, t in lasers if w and t is not None),
        f"{acq.get('IntegrationType')} {acq.get('IntegrationCount')}" if acq.get("IntegrationType") not in (None, "None") else ""] if x)
    system = md.get("Version Info", {}).get("SystemName", "") or acq.get("Acquisition Device", "")
    return [{
        "format": "Olympus FluoView", "system": f"Olympus {system}".strip(),
        "location_in_file": "whole file", "objective": objective,
        "n_z": size.get("Z", 1), "n_t": size.get("T", 1), "width": size["X"], "height": size["Y"],
        "pixel_um": round(_olympus_number(ref.get("WidthConvertValue", 0)), 4), "z_step_um": round(z_step, 3),
        "bits": int(ref.get("ValidBitCounts") or 16), "channel_roles": roles, "channel_names": names,
        "settings": settings, "zoom": "", "acquired": str(acq.get("ImageCaputreDate", "")),
        "stage_x": None, "stage_y": None, "flags": _extra_axes_flags(axes, shape), "_reader": ("oif", str(path), 0, axes),
    }]


# ------------------------------------------------------------------ ImageJ hyperstack TIFF

IMAGEJ_ROLES = {"marker": "MARKER", "identity": "IDENTITY", "brightfield": "BF", "other": "OTHER",
                "identity2": "IDENTITY2", "identity3": "IDENTITY3", "identity4": "IDENTITY4"}


def is_imagej_stack(path):
    try:
        with tifffile.TiffFile(str(path)) as f:
            return bool(f.is_imagej) and "S" not in f.series[0].axes
    except Exception:
        return False


def imagej_images(path, cfg, setting):
    """ImageJ files carry no dye or laser metadata, so the source in config.json states the channel
    order ("imagej_tiff": {"channels": ["marker", "brightfield"]}); it is copied into log.md."""
    with tifffile.TiffFile(str(path)) as f:
        s = f.series[0]
        axes, shape, ij = s.axes, s.shape, f.imagej_metadata or {}
        page = s.pages[0]
        res = page.tags.get("XResolution")
        bits = page.bitspersample
    size = dict(zip(axes, shape))
    n_c = size.get("C", 1)
    wanted = [IMAGEJ_ROLES[c] for c in setting["channels"]]
    flags = []
    if len(wanted) != n_c:
        flags.append(f"config.json lists {len(wanted)} channels, the file has {n_c}")
        wanted = ["UNRESOLVED"] * n_c
    pixel_um = 0.0
    if res and str(ij.get("unit", "")).lower() in ("micron", "um", "\u00b5m", "microns"):
        num, den = res.value
        pixel_um = round(den / num, 4) if num else 0.0
    if not pixel_um:
        flags.append("pixel size unknown (no calibration in micrometres)")
    return [{
        "format": "ImageJ TIFF", "system": setting.get("system") or "ImageJ TIFF (instrument not named)",
        "location_in_file": "whole file", "objective": setting.get("objective", ""),
        "n_z": size.get("Z", 1), "n_t": size.get("T", 1), "width": size["X"], "height": size["Y"],
        "pixel_um": pixel_um, "z_step_um": round(float(ij.get("spacing", 0) or 0), 3) if "Z" in size else 0.0,
        "bits": bits, "channel_roles": wanted, "channel_names": list(setting["channels"])[:n_c] if not flags else [""] * n_c,
        "settings": "", "zoom": "", "acquired": "",
        "stage_x": None, "stage_y": None, "flags": flags, "_reader": ("ij", str(path), 0, axes),
    }]


def images_in(path, cfg, source=None):
    low = path.name.lower()
    if low.endswith(".lif"):
        return leica_images(path, cfg)
    if low.endswith(".czi"):
        return zeiss_images(path, cfg)
    if low.endswith(".nd2"):
        return nikon_images(path, cfg)
    if low.endswith((".oib", ".oif")):
        return olympus_images(path, cfg)
    if source and source.get("imagej_tiff") and not low.endswith((".ome.tif", ".ome.tiff")):
        return imagej_images(path, cfg, source["imagej_tiff"])
    return ome_images(path, cfg)


# ------------------------------------------------------------------ pixels

def pixel_sha256(img):
    """Checksum of every pixel of an image (all channels, planes and time points), to find the
    same image saved inside two different files."""
    arr, _ = _raw(img)
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()


def _raw(img):
    kind, path, i = img["_reader"][:3]
    if kind == "lif":
        im = _lif_series(_lif(path))[i]["image"]
        if im is None:
            raise ValueError(f"{Path(path).name} series {i}: no pixel data found")
        arr, dims = im.asarray(), "".join(im.dims)
    elif kind == "czi":
        with czifile.CziFile(path) as f:
            scene = f.scenes[i]
            arr, dims = scene.asarray(), "".join(scene.dims)
    elif kind == "nd2":
        dims = img["_reader"][3]
        with _nd2().ND2File(path) as f:
            if "P" in dims:          # one stage position, read on its own
                arr = np.asarray(f.to_dask()[(slice(None),) * dims.index("P") + (i,)])
                dims = dims.replace("P", "")
            else:
                arr = f.asarray()
    elif kind == "oif":
        with oiffile.OifFile(path) as f:
            arr, dims = f.asarray(), f.axes
    elif kind == "ij":
        arr, dims = tifffile.imread(path, series=0), img["_reader"][3]
    else:
        arr, dims = tifffile.imread(path, series=i), img["_reader"][3]
    return arr, dims


def read(img, channel):
    """One channel as a (z, y, x) array, first time point, original bit depth."""
    arr, dims = _raw(img)
    if "C" in dims:
        arr = np.take(arr, channel, axis=dims.index("C"))
        dims = dims.replace("C", "")
    for ax in [a for a in dims if a not in "ZYX"]:
        arr = np.take(arr, 0, axis=dims.index(ax))
        dims = dims.replace(ax, "")
    if "Z" not in dims:
        arr = arr[None]
    return arr


def scan(cfg, log=print, progress=None):
    """Walk every source folder. Returns (manifest rows for every file, image records).
    progress, if given, is called with (bytes checksummed so far, total bytes)."""
    manifest, images = [], []
    for src in cfg["sources"]:
        if not Path(src["path"]).is_dir():
            raise SystemExit(f"Source folder not found: {src['path']}\nFix 'path' for source "
                             f"'{src.get('name', src['path'])}' in config.json.")
    all_files = {src["path"]: sorted(p for p in Path(src["path"]).rglob("*") if p.is_file()) for src in cfg["sources"]}
    total = sum(p.stat().st_size for files in all_files.values() for p in files) or 1
    # Checksums run on a few threads at once (hashing releases Python's lock, and each thread
    # holds only a 1 MB block), which roughly halves the time on large data. Results are
    # looked up by path, so the order of rows does not depend on which thread finished first.
    hashes, done = {}, 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(sha256, p): p for files in all_files.values() for p in files}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                hashes[p] = fut.result()
            except OSError as exc:      # 1.26.5: open in another program (acquisition software), or unreadable
                hashes[p] = ""
                log(f"  could not read {p.name}: {exc.strerror or type(exc).__name__}")
            done += p.stat().st_size
            if progress:
                progress(done, total)
    for src in cfg["sources"]:
        root = Path(src["path"])
        original = Path(src.get("original_path") or src["path"])
        files = all_files[src["path"]]
        log(f"Scanning {root} ({len(files)} files)")
        by_hash = {}
        rows = []
        for p in files:
            rel = str(p.relative_to(root))
            h = hashes[p]
            row = {"source": src.get("name", str(root)), "relative_path": rel, "local_path": str(p),
                   "original_path": str(original / rel), "size_mb": round(p.stat().st_size / 2**20, 2),
                   "sha256": h, "role": file_role(p), "note": ""}
            rows.append(row)
            if not h:
                row["role"], row["note"] = "unreadable file", "could not be opened (in use by another program?)"
                continue
            # ImageJ TIFF has no microscope metadata: used only if this source turns it on
            if row["role"] == NOT_USED and src.get("imagej_tiff") and p.suffix.lower() in (".tif", ".tiff") \
                    and is_imagej_stack(p):
                row["role"] = "raw image"
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
                found = images_in(p, cfg, src)
            except Exception as exc:
                row["role"] = "unreadable image"
                row["note"] = str(exc)[:200]
                log(f"  could not read {row['relative_path']}: {exc}")
                continue
            if not found:
                row["role"] = NOT_USED
                continue
            for img in found:
                img.update({"source": row["source"], "file": p.name, "relative_path": row["relative_path"],
                            "local_path": row["local_path"], "original_path": row["original_path"],
                            "file_sha256": row["sha256"], "genotype": C.genotype_for(src, row["relative_path"])})
                images.append(img)
        manifest += rows
    return manifest, images
