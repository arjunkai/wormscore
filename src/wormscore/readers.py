"""Find images in source folders, read their metadata, and read their pixels.

Supported raw formats: Leica .lif (one image per series), Zeiss .czi (one per scene),
OME-TIFF (one per series; metadata in the file or in a .companion.ome next to it), Nikon .nd2
(one per stage position), Olympus FluoView .oib and .oif (one per file), Olympus/Evident .oir from the
FV3000, FV4000 and FVMPE-RS (one per file), and, only for sources that turn it on, ImageJ hyperstack
TIFF (channel order stated in config.json).

Every image becomes a dict with the same fields: where it came from, what it is, how
it was taken, and which channel is the marker or brightfield. Channel identity comes
from metadata only. If the metadata does not settle it, the channel is UNRESOLVED.
"""
import hashlib
import logging
import os
import re
import struct
import warnings
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import czifile
import numpy as np
import oiffile
import oirfile
import tifffile
from liffile import LifFile

from . import config as C
from .rescan import tidy_number

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
OIR_PART = "part of an Olympus .oir image (read with its .oir file)"
NOT_USED = "other image (no microscope metadata; not used)"


def file_role(path):
    name, low = path.name, path.name.lower()
    if name.startswith("._") or name in (".DS_Store", "desktop.ini", "Thumbs.db"):
        return "system hidden file (not data)"
    if any(part.lower().endswith(".oif.files") for part in path.parts[:-1]):
        return OIF_PART
    if low.endswith((".lif", ".czi", ".nd2", ".oib", ".oif", ".oir")):
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
    # a large .oir acquisition continues in files named like it, without an extension: scan_00001, scan_00002
    if re.fullmatch(r".+_\d{5}", name) and path.with_name(name[:-6] + ".oir").is_file():
        return OIR_PART
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


# 1.26.20 changed how files are read (see as_before). Until a project's images are scanned with it, its
# slide images are read as 1.26.19 read them, so choosing them again changes nothing (found by review).
_BEFORE = ContextVar("read_as_1_26_19", default=False)


@contextmanager
def as_before():
    """Read files as 1.26.19 did: no scan zoom, PMT gain or lifetime channels; Nikon and Leica settings and
    UTC times as then; the fluorophore names added since unknown."""
    token = _BEFORE.set(True)
    try:
        yield
    finally:
        _BEFORE.reset(token)


# 1.26.21 changed how files are read again (the Leica model, FLIM results, a series' own channels, channel
# types, near laser lines). A project scanned by 1.26.20 reads its slide and control images as 1.26.20
# did until it scans again (found by review: the as_before gate told only before and after 1.26.20).
_BEFORE_1_26_21 = ContextVar("read_as_1_26_20", default=False)


def _before_1_26_21():
    return _BEFORE.get() or _BEFORE_1_26_21.get()


@contextmanager
def as_1_26_20():
    """Read files as 1.26.20 did: without the rules of 1.26.21."""
    token = _BEFORE_1_26_21.set(True)
    try:
        yield
    finally:
        _BEFORE_1_26_21.reset(token)


# 1.26.23 reads the objective and zoom of a Leica image saved by LAS AF (TCS SP5), as LAS X shows them. A
# project scanned by 1.26.21 or 1.26.22 reads its slide and control images as then until it scans again.
_BEFORE_1_26_23 = ContextVar("read_as_1_26_22", default=False)


def _before_1_26_23():
    return _before_1_26_21() or _BEFORE_1_26_23.get()


@contextmanager
def as_1_26_22():
    """Read files as 1.26.21 and 1.26.22 did: without the rules of 1.26.23."""
    token = _BEFORE_1_26_23.set(True)
    try:
        yield
    finally:
        _BEFORE_1_26_23.reset(token)


# 1.26.24 reads what NIS-Elements Viewer and OlyVIA showed for the sample files and wormscore did not: the
# excitation and emission an old Nikon file keeps in its channel's filter record, the Nikon model's name,
# the camera binning and the A1's pinhole and scan speed, the Olympus pinhole, and a time's own offset. A
# project scanned by 1.26.23 reads its slide and control images as then until it scans again.
_BEFORE_1_26_24 = ContextVar("read_as_1_26_23", default=False)


def _before_1_26_24():
    return _before_1_26_23() or _BEFORE_1_26_24.get()


@contextmanager
def as_1_26_23():
    """Read files as 1.26.23 did: without the rules of 1.26.24."""
    token = _BEFORE_1_26_24.set(True)
    try:
        yield
    finally:
        _BEFORE_1_26_24.reset(token)


# 1.26.25 reads a Leica zoom to two decimals, as LAS X shows an SP8's (a TCS SP5 records "3" as 2.99875025381001 and
# 2.99879313401932 in one session; a TCS SP8 1.21130609... and 1.21130795..., both 1.21 in LAS X) and a white
# light laser's output. A project scanned by 1.26.24 reads its slide and control images as then until it scans again.
_BEFORE_1_26_25 = ContextVar("read_as_1_26_24", default=False)


def _before_1_26_25():
    return _before_1_26_24() or _BEFORE_1_26_25.get()


@contextmanager
def as_1_26_24():
    """Read files as 1.26.24 did: without the rules of 1.26.25."""
    token = _BEFORE_1_26_25.set(True)
    try:
        yield
    finally:
        _BEFORE_1_26_25.reset(token)


def reading_as(version):
    """The reading for images read again for a project whose inventory was written by that version (the
    scanned_with column): "" for an inventory from before 1.26.20 (as_before), a version before 1.26.21
    (as_1_26_20), before 1.26.23 (as_1_26_22), before 1.26.24 (as_1_26_23), before 1.26.25 (as_1_26_24), or
    the current reading."""
    import contextlib
    if version is None:
        return contextlib.nullcontext()
    parts = tuple(int(x) for x in re.findall(r"\d+", str(version))[:3])
    if not parts:
        return as_before()
    if parts < (1, 26, 21):
        return as_1_26_20()
    if parts < (1, 26, 23):
        return as_1_26_22()
    if parts < (1, 26, 24):
        return as_1_26_23()
    return as_1_26_24() if parts < (1, 26, 25) else contextlib.nullcontext()


def _dyes(m):
    """A marker's dye names; read as 1.26.19 (as_before), a preset's own list without the names added in
    1.26.20. A list the project wrote itself is kept whole (found by review)."""
    names = list((m or {}).get("dye_names") or [])
    if _BEFORE.get() and (names == list(C.DEFAULTS["marker"]["dye_names"])
                          or any(names == list(p["dye_names"]) for p in C.PRESETS.values())):
        return [d for d in names if d not in C.DYE_NAMES_1_26_20]
    return names


def _named_match(name, fluor, excitation, emission, m):
    em = m.get("emission_nm")
    dyes = _dyes(m)
    return bool((fluor and fluor in dyes)
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
        named = [i for i in hits if fluors[i] and fluors[i] in _dyes(m)]
        if len(hits) > 1 and len(named) == 1:
            roles = ["OTHER" if r == role and i != named[0] else r for i, r in enumerate(roles)]
    return roles


def other_fluorophore(roles, fluors, cfg):
    """Flags for a reporter or identity channel whose dye in the file is one wormscore knows as another
    fluorophore, for example a channel named mCherry taken as GFP by its 488 nm laser line (16 real FV3000
    files did so). The role is kept, as before: a project scanned earlier keeps its reporter."""
    out = []
    for role, m in (("MARKER", cfg["marker"]), ("IDENTITY", cfg.get("identity"))):
        i = pick(roles, role) if m else None
        dye = str(fluors[i] or "").strip() if i is not None and i < len(fluors) else ""
        own = {str(d).casefold() for d in _dyes(m)} if m else set()
        if not dye or dye.casefold() in own:
            continue
        # only another colour: a relative excited by the same lines (EGFP in an mNeonGreen project) is not
        # flagged, or a lab whose software has no entry for its fluorophore sees it on every image
        lines = {int(float(x)) for x in m.get("excitation_nm") or []}
        if any(dye.casefold() in {d.casefold() for d in _dyes(p)} and not own & {d.casefold() for d in _dyes(p)}
               and not lines & set(p["excitation_nm"]) for p in C.PRESETS.values()):
            out.append(f"the {m['label']} channel is named {dye} in the file (another fluorophore): check the channel roles")
    return out


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


# 1.26.21: a preset matched an excitation only at its exact laser lines, so a widefield LED a few nm away
# was not recognised (idr0011: TagYFP at 508 nm, YFP's lines 514 and 515; mRFP at 590 nm, mCherry's 587).
# Within NEAR_NM of a preset's line, every preset protein is still excited at least 45% as well as at its
# peak (FPbase excitation spectra of EGFP, mNeonGreen, EYFP, mCherry, TagRFP-T, mScarlet-I, mKate2, CyOFP1
# and mTagBFP2; mNeptune2.5's far-red lines 633 and 640 nm are themselves at 22% and 12%).
NEAR_NM = 10


def _near_hits(roles, m, near_of):
    """Channels left as OTHER that match m only near one of its lines: [(channel, excitation, line)]."""
    hits = []
    for i, r in enumerate(roles):
        found = near_of(i, m) if r == "OTHER" else None
        if found:
            hits.append((i, *found))
    return hits


def near_match(roles, cfg, near_of):
    """1.26.21: a reporter channel no channel matched at the preset's exact lines, found by an
    excitation within NEAR_NM of one, together with the emission (the recorded emission wavelength, or a
    Leica detection band, within the preset's range). Only when exactly one channel fits, and always with
    a flag, so the scan shows that a near match was used. near_of(i, m) -> (excitation, line) or None.
    Returns (roles, flags). Read as 1.26.20 read (as_1_26_20, as_before): not tried."""
    if _before_1_26_21():
        return roles, []
    out, flags = list(roles), []
    # the reporter only: an image without one was never coded, so no image already scored can change. An
    # identity channel found this way would be drawn on a coded image that had none (found by review)
    for role, m in (("MARKER", cfg["marker"]),):
        if not m or role in out:
            continue
        hits = _near_hits(out, m, near_of)
        if len(hits) == 1:
            i, ex, line = hits[0]
            out[i] = role
            flags.append(f"{m['label']} channel found by its emission and an excitation of {ex:g} nm "
                         f"({abs(ex - line):g} nm from the preset's {line:g} nm line)")
    return out, flags


def named_near(excitations, emissions):
    """near_of for files that record a channel's excitation and emission wavelength."""
    def near_of(i, m):
        ex = _num(excitations[i] if i < len(excitations) else None)
        em = _num(emissions[i] if emissions and i < len(emissions) else None)
        rng = m.get("emission_nm")
        if ex is None or em is None or not rng or not rng[0] <= em <= rng[1]:
            return None
        lines = [float(x) for x in m.get("excitation_nm") or [] if abs(ex - float(x)) <= NEAR_NM]
        return (ex, min(lines, key=lambda x: abs(ex - x))) if lines else None
    return near_of


def leica_near(at, luts):
    """near_of for Leica channels: a laser line within NEAR_NM of the preset's, with the detection band
    starting in the preset's range (and its colour table, if the preset names one), as _leica_match."""
    def near_of(i, m):
        ch = at[i] if i < len(at) else None
        if ch is None:
            return None
        lo = float(ch["band"].split("-")[0]) if ch["band"] else 0
        b0, b1 = m["emission_band_start_nm"]
        if not b0 <= lo <= b1 or (m.get("leica_lut") and luts[i] != m["leica_lut"]):
            return None
        pairs = [(float(ln), float(x)) for ln, _ in ch["lasers"] for x in m.get("excitation_nm") or []
                 if abs(float(ln) - float(x)) <= NEAR_NM]
        return min(pairs, key=lambda p: abs(p[0] - p[1])) if pairs else None
    return near_of


def _strong_match(name, fluor, emission, m):
    """A match on more than the laser line: the dye name, the channel name, or the emission wavelength."""
    em = m.get("emission_nm")
    return bool((fluor and fluor in _dyes(m))
                or any(name.upper().startswith(p.upper()) for p in m["channel_name_prefixes"])
                or (emission and em and em[0] <= float(emission) <= em[1]))


def pick(roles, role):
    return roles.index(role) if roles.count(role) == 1 else None


FIRST_ONLY = "only the first is read"
AXIS_NAMES = {"M": "tile scan ({n} tiles)", "H": "phases ({n})", "R": "rotations ({n})", "I": "illuminations ({n})",
              "V": "views ({n})", "B": "blocks ({n})", "S": "colour samples ({n})", "L": "lambda scan ({n} wavelengths)",
              "Q": "extra dimension ({n})"}


def _extra_axes_flags(axes, shape, known="CZYXT", names=AXIS_NAMES):
    """1.26.8: an image with a dimension wormscore does not use (the tiles of a tile scan, the wavelengths
    of a lambda scan, Airyscan phases). Only its first entry is read, as before, and now a flag says so."""
    return [f"{names.get(a, 'extra dimension ' + a + ' ({n})').format(n=n)}: {FIRST_ONLY}"
            for a, n in zip(axes, shape) if a not in known and n > 1]


# tifffile names a plain run of pages "I" (a sequence), where Zeiss files use I for illuminations (1.26.18)
OME_AXIS_NAMES = dict(AXIS_NAMES, I="{n} images in one file that the metadata does not place")


def _pages_placed(px, axes, shape, path):
    """1.26.18: a Micro-Manager set keeps its metadata in a .companion.ome file and its planes in TIFFs whose
    own description is only "OME-TIFF". tifffile then sees a plain run of pages ("IYX"), and only the first
    was read, flagged as illuminations. The companion file says which page is which plane (TiffData). When
    it names this file for every page, in the order its DimensionOrder gives, and only one of z, channels
    and time points has more than one entry, the pages are that axis. Otherwise the axes are kept as they
    were (the first page is read, and flagged)."""
    if axes != "IYX":
        return axes
    sizes = {a: int(px.get(f"Size{a}") or 1) if (px.get(f"Size{a}") or "1").isdigit() else 0 for a in "ZCT"}
    order = (px.get("DimensionOrder") or "")[2:]
    big = [a for a in "ZCT" if sizes[a] != 1]
    if sorted(order) != ["C", "T", "Z"] or len(big) != 1 or sizes[big[0]] != shape[0]:
        return axes
    covered = []
    for td in px.findall("o:TiffData", OME_NS):
        uuid = td.find("o:UUID", OME_NS)
        if uuid is None or uuid.get("FileName") != Path(path).name:
            return axes
        try:
            # OME: a TiffData with neither IFD nor PlaneCount stands for every page of the file
            ifd = int(td.get("IFD", "0"))
            count = shape[0] if td.get("IFD") is None and td.get("PlaneCount") is None else int(td.get("PlaneCount", "1"))
            first = {a: int(td.get(f"First{a}", "0")) for a in "ZCT"}
        except ValueError:
            return axes
        if first[big[0]] != ifd or any(first[a] for a in "ZCT" if a != big[0]):
            return axes
        covered += range(ifd, ifd + count)
    return big[0] + "YX" if sorted(covered) == list(range(shape[0])) else axes


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


def _leica_detector_kind(d):
    """The kind of detector for the settings text (1.26.13): "HyD" for Leica's hybrid detectors (HyD, HyD S,
    HyD X, as before), otherwise the type the file gives, or its name without the number ("PMT 3" is
    "PMT"). The text said "HyD gain" for every detector, also for a PMT."""
    name, kind = (d.get("Name") or "").strip(), (d.get("Type") or "").strip()
    if "hyd" in name.lower() or "hyd" in kind.lower():
        return "HyD"
    return kind or re.sub(r"[\s\d]+$", "", name) or "detector"


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
    # 1.26.20: a TCS SP5 step can list the same detector and laser line several times, element for element
    # the same (S-BIAD1480: one PMT and one 405 nm line, three times each); a copy is read once. Before, the
    # copies counted as more detectors than channels and the channels were left unresolved.
    # A laser line's copies can differ in display flags only (IsChecked, IsLineDeactivated; a 2015 SP8X file,
    # Zenodo 13752242): the same line, index and intensity is one line.
    once = lambda els: list({tuple(sorted(e.attrib.items())): e for e in els}.values())
    # (the Qualifier tells a visible from a UV laser: two lasers at one line are never one, found by review)
    line_once = lambda els: list({(e.get("LaserLine"), e.get("LineIndex"), e.get("Qualifier"), e.get("IntensityDev")): e
                                  for e in els}.values())
    if _BEFORE.get():
        once = line_once = list
    lines = lambda s: [(l.get("LaserLine"), float(l.get("IntensityDev") or 0))
                       for l in line_once(s.iter("LaserLineSetting")) if float(l.get("IntensityDev") or 0) > 0]
    chans = []
    for s in steps:
        lasers = lines(s)
        if master is not None and next(s.iter("LaserLineSetting"), None) is None:
            lasers = lines(master)                  # a step that lists no laser lines at all
        bands = {b.get("Channel"): b for b in s.iter("MultiBand")}
        for d in once(s.iter("Detector")):
            if d.get("IsActive") != "1":
                continue
            band = bands.get(d.get("Channel"), m_bands.get(d.get("Channel")))
            gain = d.get("Gain")
            if gain is None and d.get("Channel") in m_dets:
                gain = m_dets[d.get("Channel")].get("Gain")
            line_avg = s.get("LineAverage") or (master.get("LineAverage") if master is not None else None)
            chans.append({"detector": d.get("Name"), "kind": _leica_detector_kind(d), "gain": float(gain or 0),
                          "band": f"{band.get('LeftWorld')}-{band.get('RightWorld')}" if band is not None else "",
                          "lasers": lasers, "line_avg": line_avg, "scan": _leica_scan(s, master)})
    return chans


# 1.26.23: settings that change how bright an image is but are not in the settings text, as LAS X Office 1.4.11
# names them under Confocal Settings (Scan Speed, PinholeAiry, FrameAverage, LineAccumulation,
# FrameAccumulation; 22 November 2026). Real files mix them: 400 and 600 Hz in one SP8 file, pinholes of 2
# and 4 AU in another, line accumulation 3 in a third. They go into their own inventory column and a flag, not
# into the settings text, so no existing settings group or flat-field session moves.
LEICA_SCAN = (("ScanSpeed", "{} Hz"), ("PinholeAiry", "pinhole {} AU"), ("FrameAverage", "frame avg {}"),
              ("Line_Accumulation", "line acc {}"), ("FrameAccumulation", "frame acc {}"))


def _leica_scan(step, master):
    """The scan settings of a channel's step (missing values from the master), as text, or ""."""
    if _before_1_26_23():
        return ""
    parts = []
    for name, text in LEICA_SCAN:
        value = step.get(name) or (master.get(name) if master is not None else None)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        parts.append(text.format(f"{round(number, 2):g}"))
    if not _before_1_26_25():
        # 1.26.25: a white light laser's output, which LAS X shows in the Lasers table as "WLL On, 70.00 %"
        # (OutputPowerPercentage 0.7; read off LAS X Office 1.4.11 for an SP8 and a STELLARIS FLIM file,
        # 23 November 2026). It scales every line the laser gives, on top of each line's intensity. The file
        # does not say which laser gave a line, so it is recorded whenever the laser is on.
        wll = next((l for el in (step, master) if el is not None for l in el.iter("Laser")
                    if l.get("LaserName") == "WLL"), None)
        try:
            if wll is not None and wll.get("PowerState") == "On":
                parts.append(f"WLL {round(float(wll.get('OutputPowerPercentage')) * 100, 1):g}%")
        except (TypeError, ValueError):
            pass
    return "; ".join(parts)


LOCAL_ZONE = None       # the time zone dates are given in: None is the computer's own (tests set one)


def _leica_acquired(elem):
    """The series' start time as LAS X shows it (Properties, Start Time): "YYYY-MM-DD HH:MM:SS +HH:MM" in
    the scanning computer's time zone, or "" (1.26.18; before, Leica images had no date). LAS X writes each
    frame's time in TimeStampList as a Windows file time (100 ns steps since 1601, UTC), in hexadecimal, or
    in older files as <TimeStamp HighInteger LowInteger>, the two halves of the same number. Bio-Formats
    reads the same value. The computer's time zone, not UTC, so that an evening session keeps its day."""
    import datetime as dt
    try:
        stamps = elem.find("./Data/Image/TimeStampList")
        old = stamps.find("TimeStamp")
        ticks = ((int(old.get("HighInteger")) << 32) + int(old.get("LowInteger")) if old is not None
                 else int(stamps.text.split()[0], 16))
        when = dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=ticks // 10)
        if when.year < 1990:
            return ""
        local = when.astimezone(LOCAL_ZONE)
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError, OSError):
        return ""
    return _local_text(local)


def _local_text(local):
    zone = local.strftime("%z")
    return local.strftime("%Y-%m-%d %H:%M:%S ") + zone[:3] + ":" + zone[3:5]


UTC_FORMATS = ("Zeiss CZI", "Olympus OIR", "OME-TIFF")


def _iso_acquired(text):
    """(the date to show, the date as 1.26.19 and before gave it). ZEN and the FV3000 software 2.x write the
    acquisition time in UTC ("2019-12-16T16:45:40.311Z"); it was shown as written, so an evening session in
    the Americas (or a morning one in Asia) had the next or the previous day. Such a time is now given in
    the scanning computer's time zone, as for Leica: "2019-12-16 11:45:40 -05:00", as OlyVIA shows it.
    A time written with its own offset ("2015-10-28T16:29:23.944-04:00", older FluoView) is the lab's
    clock already and is given as before, as is a time without a zone. 1.26.24: such a time shows its
    offset, "2024-12-02 09:37:02 (UTC+08:00)": an FV4000 in Asia records +08:00 and OlyVIA shows the time
    in this computer's zone, 13 hours and a day away in the Americas. The clock and the day are the file's,
    as before; the brackets keep it apart from a time given in this computer's zone (" -05:00")."""
    import datetime as dt
    before = text[:19].replace("T", " ")
    own = re.search(r"([+-]\d\d):?(\d\d)$", text) if len(text) > 19 and not _before_1_26_24() else None
    if own and re.fullmatch(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?[+-]\d\d:?\d\d", text):
        return f"{before} (UTC{own.group(1)}:{own.group(2)})", before
    if not text.endswith("Z") or len(text) < 19 or _BEFORE.get():
        return before, before
    try:
        when = dt.datetime.strptime(before, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        return _local_text(when.astimezone(LOCAL_ZONE)), before
    except (ValueError, OverflowError, OSError):
        return before, before


def _with_acquired(img, text):
    """Set the image's date from an ISO time; the date as given before, if other, is kept in _acquired_before
    for the slide images of a project scanned before (flatfield._images)."""
    img["acquired"], before = _iso_acquired(text)
    if before != img["acquired"]:
        img["_acquired_before"] = before
    return img


LIFETIME_CHANNELS = ("MeanArrivalTime",)
# 1.26.21: the channel types LAS X writes for light that was measured (S-BIAD961, Zenodo 6606445's
# Project007, Zenodo 13752242's FLIM files). No public file or document lists the other types (TauSense
# gating or separation, for example), so an image with a channel of another type is flagged.
MEASURED_CHANNELS = ("PhotonCounting", "PhotonIntegration")
# 1.26.21: LAS X FLIM keeps what it computed from a lifetime scan (Fast Flim, Phasor, FlimDecayTime,
# FretEfficiency and so on: 29 kinds in Zenodo 13752242's FLIM250523.lif) as series inside the scan's
# "FLIM Compressed" folder. They were left unresolved as "0 detectors for 1 channels".
FLIM_RESULTS = "FLIM Compressed"


def _own(elem):
    """A series without the series nested in it (1.26.21): LAS X FLIM nests its computed results inside the
    scan's element, and a search through the whole element found their channels and settings too (the
    IRF series of FLIM250523.lif, one channel, listed ten channel names)."""
    import xml.etree.ElementTree as ET
    own = ET.Element(elem.tag, elem.attrib)
    own.extend(c for c in elem if c.tag != "Children")
    return own


def _leica_without_lifetime(elem, chans):
    """The detector of each channel, None for a lifetime channel that LAS X computed (1.26.20), or None if
    that cannot be told. A STELLARIS with TauContrast adds to each photon-counting channel a channel of mean
    arrival times (ChannelType MeanArrivalTime, in seconds): more channels than detectors, so no channel was
    resolved and such files could not be used (S-BIAD961). The other channels are given their detectors
    only if each names the same detector, in the same order, as the scan settings list them."""
    descs = list(elem.iter("ChannelDescription"))
    props = [{p.findtext("Key"): p.findtext("Value") for p in c.iter("ChannelProperty")} for c in descs]
    computed = [p.get("ChannelType") in LIFETIME_CHANNELS for p in props]
    measured = [p for p, c in zip(props, computed) if not c]
    if not any(computed) or len(measured) != len(chans):
        return None
    if [(p.get("DetectorName") or "").strip() for p in measured] != [(c["detector"] or "").strip() for c in chans]:
        return None
    it = iter(chans)
    return [None if c else next(it) for c in computed]


def _leica_record(elem, tag, field, name):
    """The Variant of the image's first record of that kind whose field is name, stripped ("" if none)."""
    return next(((r.get("Variant") or "").strip() for r in elem.iter(tag)
                 if r.get(field) == name and (r.get("Variant") or "").strip()), "")


def leica_images(path, cfg):
    lf = _lif(path)
    system = next((a.get("SystemTypeName") for a in lf.xml_element.iter("Attachment") if a.get("SystemTypeName")), "")
    # 1.26.21: a file without a SystemTypeName (LAS AF, TCS SP5) names its model as the scanner's SystemType.
    # It is shown where people read the microscope (Check the reading, Browse, the Methods draft); the
    # microscope's name as the project keys it (flat-field sessions, settings groups, results tables) stays
    # "Leica", so no existing session or group moves. Bio-Formats reads the same place (Microscope Model).
    model = "" if system or _before_1_26_21() else next(
        (r.get("Variant", "").strip() for r in lf.xml_element.iter("ScannerSettingRecord")
         if r.get("Identifier") == "SystemType" and r.get("Variant", "").strip()), "")
    out = []
    for i, im in enumerate(_lif_series(lf)):
        st, elem = im["settings"], im["element"]
        if not _before_1_26_21():
            elem = _own(elem)
        luts = [c.get("LUTName") for c in elem.iter("ChannelDescription")]
        chans = _leica_channels(elem)
        flags = _extra_axes_flags([a for a, _ in im["extra"]], [v for _, v in im["extra"]])
        at = chans if len(chans) == im["channels"] else None if _BEFORE.get() else _leica_without_lifetime(elem, chans)
        if at is not None and len(at) != im["channels"]:
            at = None           # the descriptions found are not this image's channels (found by review)
        result = not _before_1_26_21() and FLIM_RESULTS in im["name"].split("/")[:-1]
        if result:
            roles = ["OTHER"] * im["channels"]
            flags.append("lifetime result computed by LAS X (FLIM), not scored")
        elif at is not None:
            roles = [marker_from_leica(c, l, cfg) if c is not None else "OTHER" for c, l in zip(at, luts)]
            roles = with_extras(roles, cfg, lambda i, m: at[i] is not None and _leica_match(at[i], luts[i], m))
            roles, near = near_match(roles, cfg, leica_near(at, luts))
            flags += near
            computed = sum(1 for c in at if c is None)
            if computed:
                flags.append(f"{computed} lifetime channel{'s' if computed != 1 else ''} computed by LAS X, not scored")
            if not _before_1_26_21():
                # every channel here has its own detector (a computed channel adds channels beyond the
                # detectors, and such an image is set aside above or left unresolved), so the roles stay;
                # the flag shows the type, so it can be checked (found by review: turning the channel's role
                # to OTHER could, after a new scan, drop an image already scored)
                kinds = [{p.findtext("Key"): (p.findtext("Value") or "").strip() for p in c.iter("ChannelProperty")}
                         .get("ChannelType") or "" for c in elem.iter("ChannelDescription")]
                unknown = sorted({k for k in kinds if k and k not in MEASURED_CHANNELS + LIFETIME_CHANNELS})
                if unknown:
                    flags.append(f"channel type {', '.join(unknown)} not known to wormscore: check that the "
                                 "reporter channel holds measured light")
        else:
            roles = ["UNRESOLVED"] * im["channels"]
            flags.append(f"{len(chans)} detectors for {im['channels']} channels")
        mi = pick(roles, "MARKER")
        g = at[mi] if mi is not None else {}
        sx, sz = im["scale_x"], im["scale_z"]
        bits = max(im["bit_depth"])
        objective, zoom = st.get("ObjectiveName", "").strip(), st.get("Zoom", "")
        if not st and model and not _before_1_26_23():      # a LAS AF file only (its model is named; found by review)
            # 1.26.23: an image saved by LAS AF (TCS SP5) has no settings definition of its own; LAS X shows its
            # objective from the turret's record and its zoom from the scanner's (LAS X Office 1.4.11, 22
            # November 2026; Bio-Formats reads the same). The definitions inside its sequential block name a
            # placeholder objective ("Dummy2") and zoom 1, so they are not used.
            objective = _leica_record(elem, "FilterSettingRecord", "Attribute", "Objective")
            zoom = _leica_record(elem, "ScannerSettingRecord", "Identifier", "dblZoom")
        if zoom and not _before_1_26_25():
            # 1.26.25: two decimals, as LAS X shows an SP8 or STELLARIS zoom (one for a LAS AF file); the file's
            # many decimals differ between images taken at one zoom, which split one flat-field session into
            # several. Rounded from the text, half up, as a display rounds (found by review: float rounding
            # sends 1.215 down)
            from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
            try:
                zoom = f"{float(Decimal(str(zoom).strip()).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)):g}"
            except (InvalidOperation, ValueError, TypeError):
                pass
        out.append({
            "format": "Leica LIF", "system": f"Leica {system}".strip(), **({"model": f"Leica {model}"} if model else {}),
            "location_in_file": f"series {i} ({im['name']})",
            "objective": objective,
            "n_z": im["z"], "n_t": im["t"], "width": im["x"], "height": im["y"],
            "pixel_um": round(1 / sx, 4) if sx else 0.0, "z_step_um": round(abs(1 / sz), 3) if sz else 0.0,
            "bits": bits, "channel_roles": roles, "channel_names": luts,
            "settings": (f"{g.get('kind', 'HyD')} gain {g['gain']:.1f}; " + ";".join(f"{ln}nm@{p:.1f}%" for ln, p in g["lasers"])
                         + f"; line avg {g['line_avg']}") if g else "",
            "zoom": zoom, "acquired": _leica_acquired(elem),
            **({"scan_settings": g.get("scan", "")} if g.get("scan") else {}),
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
    if system in (cfg.get("saturation_value") or {}) or _no_formula(system) in (cfg.get("saturation_value") or {}):
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
    # 1.26.12: ZEN blue camera files give a channel no name, only its dye; the dye is then listed as the
    # name in inventory.csv. The roles are worked out from what the file has, as before.
    shown = [n or (f or "").strip() for n, f in zip(names, fluors)]
    names_for_roles = names
    for c in chans:
        roles.append(marker_from_named(c.get("Name") or "", c.findtext("Fluor"), c.findtext("ExcitationWavelength"),
                                       c.findtext("IlluminationType") == "Transmitted", cfg))
    roles = prefer_named_dye(roles, fluors, cfg)
    roles = named_extras(roles, cfg, names_for_roles, fluors, [c.findtext("ExcitationWavelength") for c in chans],
                         [None] * len(roles))
    roles, near = near_match(roles, cfg, named_near([c.findtext("ExcitationWavelength") for c in chans],
                                                    [c.findtext("EmissionWavelength") for c in chans]))
    names = shown
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
    flags = flags + near + other_fluorophore(roles, fluors, cfg)
    # 1.26.20: the scan zoom of a laser scanning microscope (LaserScanInfo/ZoomX, as ZEN shows it). The first
    # element named Zoom, read before, is a detector's and is 1 whatever the scan zoom (found by holding
    # wormscore against what two datasets' authors wrote: Zenodo 7994589, "scan magnification: 0.6").
    before = text("Zoom").replace(",", ".")
    scan = None if _BEFORE.get() else _num(text("ZoomX").replace(",", "."))
    # a scan zoom equal to the old value keeps the old text ("1.0"), so its session does not move (found by review)
    zoom = before if not scan or _num(before) == round(scan, 4) else f"{round(scan, 4):g}"
    return {"system": system, "objective": objective, "pixel_um": round(scale.get("X", 0), 4),
            "z_step_um": round(scale.get("Z", 0), 3), "bits": int(bits), "channel_roles": roles, "channel_names": names,
            "settings": _zeiss_settings(chans[mi], md) if mi is not None else "",
            "zoom": zoom, "acquired": text("AcquisitionDateAndTime"),      # ISO, as written
            **({"_zoom_before": before} if zoom != before else {}),
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
        out.append(_with_acquired({
            "format": "Zeiss CZI", "system": meta["system"],
            "location_in_file": f"whole file (scene {i})" if len(scenes) == 1 else f"scene {i}",
            "objective": meta["objective"],
            "n_z": shape[dims.index("Z")] if "Z" in dims else 1,
            "n_t": shape[dims.index("T")] if "T" in dims else 1,
            "width": shape[dims.index("X")], "height": shape[dims.index("Y")],
            "pixel_um": meta["pixel_um"], "z_step_um": meta["z_step_um"],
            "bits": meta["bits"], "channel_roles": meta["channel_roles"], "channel_names": meta["channel_names"],
            "settings": meta["settings"], "zoom": meta["zoom"],
            **({"_zoom_before": meta["_zoom_before"]} if "_zoom_before" in meta else {}),
            "stage_x": None, "stage_y": None, "flags": list(meta["flags"]) + _extra_axes_flags(dims.replace("0", ""), [n for a, n in zip(dims, shape) if a != "0"]),
            "_reader": ("czi", str(path), i, dims),
        }, meta["acquired"]))
    return out


# ------------------------------------------------------------------ OME-TIFF

INCOMPLETE = "file is incomplete (planes missing)"


def _ome_settings(chan, md=None):
    """Detector and light-source settings of one channel (1.26.7), as the file states them: gain, voltage,
    and each light source's wavelength and attenuation. Files without them give nothing, as before.
    Bio-Formats writes the gain on the instrument's Detector and has the channel point to it by ID
    (1.26.12, found with a real file converted from Zeiss): a value the channel does not give is taken
    from the Detector or light source it names."""
    if chan is None:
        return []
    named = lambda tag, ref: next((e for e in md.iter("{" + OME_NS["o"] + "}" + tag) if e.get("ID") == ref), None) \
        if md is not None and ref else None
    parts = []
    ds = chan.find("o:DetectorSettings", OME_NS)
    if ds is not None:
        det = named("Detector", ds.get("ID"))
        get = lambda key: _num(ds.get(key, "")) if _num(ds.get(key, "")) is not None \
            else _num(det.get(key, "")) if det is not None else None
        gain, volt, amp = get("Gain"), get("Voltage"), get("AmplificationGain")
        if gain is not None:
            parts.append(f"gain {gain:g}")
        if volt is not None:
            unit = ds.get("VoltageUnit") or (det.get("VoltageUnit") if det is not None else None) or "V"
            parts.append(f"detector {volt:g} {unit}")
        if amp is not None and amp != 1:
            parts.append(f"amplification {amp:g}")
    for ls in chan.findall("o:LightSourceSettings", OME_NS):
        src = next((e for e in (md.iter() if md is not None else []) if e.get("ID") == ls.get("ID")
                    and e.tag.split("}")[-1] in ("Laser", "LightEmittingDiode", "Arc", "Filament", "GenericExcitationSource",
                                                 "LightSource")), None)
        if src is not None and src.tag.split("}")[-1] == "LightSource":     # schemas up to 2008-09: the laser is inside it
            src = next((e for e in src if e.get("Wavelength")), src)
        w, a = _num(ls.get("Wavelength", "")), _num(ls.get("Attenuation", ""))
        unit = ls.get("WavelengthUnit", "nm")
        if w is None and src is not None and _num(src.get("Wavelength", "")) is not None:
            w, unit = _num(src.get("Wavelength")), src.get("WavelengthUnit", "nm")
        if w is None and a is None:
            continue
        parts.append((f"{w:g}{unit}" if w is not None else "light") + (f" attenuation {a:g}" if a is not None else ""))
    return parts


# 2016-DEV0 is the name development builds of Bio-Formats 5.2.0 wrote (September 2015 to June 2016), before
# the schema was published as 2016-06. It began as 2015-01 and ended as 2016-06, and every state between
# them (checked in the Bio-Formats history) names what is read here as those two do. No other development
# name is taken: a schema that was not checked is not guessed.
OME_SCHEMA = re.compile(r"http://www\.openmicroscopy\.org/Schemas/OME/(?:\d{4}-\d{2}|2016-DEV0)")
MICROMETRES = {"\u00b5m": 1, "\u03bcm": 1, "um": 1, "micron": 1, "nm": 1e-3, "pm": 1e-6, "\u00c5": 1e-4, "mm": 1e3, "cm": 1e4, "m": 1e6}


def _ome_root(md):
    """Files written to an older OME schema (2008 to 2015, and the development name 2016-DEV0) keep the same
    names for everything read here, under another namespace (1.26.8). They were listed as "no microscope
    metadata"."""
    old = OME_SCHEMA.match(md.tag[1:]) if md.tag.startswith("{") else None
    if old and old.group(0) != OME_NS["o"]:
        for el in md.iter():
            if el.tag.startswith("{" + old.group(0) + "}"):
                el.tag = "{" + OME_NS["o"] + "}" + el.tag.split("}", 1)[1]
        _ome_logical_channels(md)
    return md


CHANNELS_NOT_STATED = "the file does not say which channel is which"


def _ome_logical_channels(md):
    """The schemas up to 2008-09 describe channels in LogicalChannel elements next to Pixels, each naming
    the pixel channels it covers (ChannelComponent Index), and give the objective's values as elements
    (1.26.13; names checked against the 2008-09 schema and its sample files). They are rewritten here in
    the current form, so the rest of the reader finds them. Such a file was read with no channels at all.
    If the components do not name every pixel channel exactly once, the channels are left unresolved."""
    q = lambda tag: "{" + OME_NS["o"] + "}" + tag
    for obj in md.iter(q("Objective")):
        for tag in ("NominalMagnification", "LensNA", "Immersion"):
            if obj.get(tag) is None and (obj.findtext(q(tag)) or "").strip():
                obj.set(tag, obj.findtext(q(tag)).strip())
    for plane in md.iter(q("Plane")):       # the exposure is on PlaneTiming (1.26.16)
        timing = plane.find(q("PlaneTiming"))
        if plane.get("ExposureTime") is None and timing is not None and timing.get("ExposureTime"):
            plane.set("ExposureTime", timing.get("ExposureTime"))
    for img in md.iter(q("Image")):
        logical = img.findall(q("LogicalChannel"))
        several = len(img.findall(q("Pixels"))) > 1
        for px in img.findall(q("Pixels")) if logical else []:
            if px.find(q("Channel")) is not None:
                continue
            if several:     # 1.26.16: which Pixels the planes of the file belong to is not worked out
                px.set("ChannelsNotStated", "1")
                continue
            owner = {}
            for lc in logical:
                for cc in lc.findall(q("ChannelComponent")):
                    if cc.get("Pixels") in (None, px.get("ID")) and (cc.get("Index") or "").isdigit():
                        owner.setdefault(int(cc.get("Index")), []).append(lc)
            n = int(px.get("SizeC") or 0) if (px.get("SizeC") or "").isdigit() else 0
            # n comes from the file: it is compared, never used to build a list (a file can state any number)
            if not 0 < n <= 64 or len(owner) != n or any(not 0 <= k < n or len(v) != 1 for k, v in owner.items()):
                px.set("ChannelsNotStated", "1")
                continue
            for k in range(n):
                lc = owner[k][0]
                ch = ET.SubElement(px, q("Channel"))
                for was, now in (("Name", "Name"), ("Fluor", "Fluor"), ("ExWave", "ExcitationWavelength"),
                                 ("EmWave", "EmissionWavelength"), ("IlluminationType", "IlluminationType")):
                    if lc.get(was) is not None:
                        ch.set(now, lc.get(was))
                for was, now in (("DetectorRef", "DetectorSettings"), ("LightSourceRef", "LightSourceSettings")):
                    for ref in lc.findall(q(was)):
                        ET.SubElement(ch, q(now), dict(ref.attrib))


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
    companion_used = md is None or md.find(".//o:Pixels", OME_NS) is None
    if companion_used:
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
    # 1.26.13: numbers as a program stored them ("40.0x/1.2000000000000002 Water", from Bio-Formats) are
    # written plainly ("40x/1.2 Water"); the objective is named in the Methods draft
    objective = (f"{tidy_number(obj.get('NominalMagnification'))}x/{tidy_number(obj.get('LensNA'))} {obj.get('Immersion', '')}".strip()
                 if obj is not None and obj.get("NominalMagnification") else "")
    model = md.find(".//o:Microscope", OME_NS)
    system = (model.get("Model") if model is not None and model.get("Model") else "") or "OME-TIFF (instrument not named)"
    images = md.findall(".//o:Image", OME_NS)
    out = []
    for i, (axes, shape, dtype) in enumerate(series):
        img = images[i] if i < len(images) else images[0]
        px = img.find("o:Pixels", OME_NS)
        if companion_used and len(series) == 1:
            axes = _pages_placed(px, axes, shape, path)
        names, roles = [], []
        for c in px.findall("o:Channel", OME_NS):
            names.append(c.get("Name", ""))
            roles.append(marker_from_named(c.get("Name", ""), c.get("Fluor"), c.get("ExcitationWavelength"),
                                           c.get("IlluminationType") == "Transmitted", cfg))
        roles = prefer_named_dye(roles, [c.get("Fluor") for c in px.findall("o:Channel", OME_NS)], cfg)
        roles = named_extras(roles, cfg, names, [c.get("Fluor") for c in px.findall("o:Channel", OME_NS)],
                             [c.get("ExcitationWavelength") for c in px.findall("o:Channel", OME_NS)], None)
        roles, near = near_match(roles, cfg, named_near(
            [c.get("ExcitationWavelength") for c in px.findall("o:Channel", OME_NS)],
            [c.get("EmissionWavelength") for c in px.findall("o:Channel", OME_NS)]))
        mi = pick(roles, "MARKER")
        exposures = sorted({p.get("ExposureTime") for p in px.findall("o:Plane", OME_NS)
                            if p.get("TheC") == str(mi) and p.get("ExposureTime")})
        bits = int(px.get("SignificantBits") or np.iinfo(dtype).bits if np.issubdtype(dtype, np.integer) else 16)
        flags = [INCOMPLETE] if i in cut else []
        if not px.get("SignificantBits") and bits == 16:
            flags += _no_bits_flag(system, cfg, 65535)
        chan = px.findall("o:Channel", OME_NS)[mi] if mi is not None else None
        flags += _extra_axes_flags(axes, shape, names=OME_AXIS_NAMES)
        n_z = shape[axes.index("Z")] if "Z" in axes else 1
        if len(flags) == len([INCOMPLETE] if i in cut else []) + (1 if any(NO_BITS in x for x in flags) else 0) \
                and (px.get("SizeZ") or "").isdigit() and int(px.get("SizeZ")) != n_z:
            # 1.26.8: one file of a set that stores each plane in its own file
            flags.append(f"the metadata lists {px.get('SizeZ')} planes, this file holds {n_z} (one file of a multi-file set)")
        if px.get("ChannelsNotStated"):         # 1.26.13: a schema up to 2008-09 whose channels are not all named
            n_c = shape[axes.index("C")] if "C" in axes else 1
            names, roles = [""] * n_c, ["UNRESOLVED"] * n_c
            flags.append(f"{CHANNELS_NOT_STATED} (an OME schema from before 2009)")
        flags += near if not px.get("ChannelsNotStated") else []
        flags += other_fluorophore(roles, [c.get("Fluor") for c in px.findall("o:Channel", OME_NS)], cfg)
        pixel_um, z_step_um = _ome_um(px, "X", flags), _ome_um(px, "Z", flags)
        out.append(_with_acquired({
            "format": "OME-TIFF", "system": system,
            "location_in_file": "whole file (series 0)" if len(series) == 1 else f"series {i}",
            "objective": objective,
            "n_z": shape[axes.index("Z")] if "Z" in axes else 1,
            "n_t": shape[axes.index("T")] if "T" in axes else 1,
            "width": shape[axes.index("X")], "height": shape[axes.index("Y")],
            "pixel_um": pixel_um, "z_step_um": z_step_um,
            "bits": bits, "channel_roles": roles, "channel_names": names,
            "settings": "; ".join(_ome_settings(chan, md) + (["exposure " + ",".join(exposures)] if exposures else [])),
            "zoom": "",
            "stage_x": None, "stage_y": None, "flags": flags,
            "_reader": ("ome", str(path), i, axes),
        }, img.findtext("o:AcquisitionDate", namespaces=OME_NS) or img.findtext("o:AcquiredDate", namespaces=OME_NS) or ""))
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
COLOUR_CAMERA = "a colour camera image (red, green and blue in one picture), which wormscore does not read"
CAMERA_LINE = r"(Exposure|Gain|Multiplier):\s*([^\n]+)"


def _nikon_block(description, name, n_channels):
    """The part of the file's description that belongs to the channel called name: its "Plane #n:" block,
    or the whole description of a one-channel file. None if it cannot be told which part is the channel's."""
    text = description.replace("\r\n", "\n")
    blocks = re.split(r"^\s*Plane #\d+:\s*$", text, flags=re.M)
    if len(blocks) > 1:
        tail = re.split(r"^\s*(?:Time Loop|Z Stack Loop|XY Loop|Multipoint Loop)\b", blocks[-1], flags=re.M)[0]
        mine = [b for b in blocks[1:-1] + [tail] if re.search(r"^\s*Name:\s*(.*?)\s*$", b, re.M)
                and re.search(r"^\s*Name:\s*(.*?)\s*$", b, re.M).group(1) == (name if _BEFORE.get() else name.strip())]   # " mCherry" (1.26.20)
        return mine[0] if len(mine) == 1 else None
    return text if n_channels == 1 and text.strip() else None


def _without_closed_sources(block):
    """The channel's description without the laser lines of a light source whose shutter is closed (1.26.20):
    "NIDAQ, Shutter(AOTF): Closed" then "NIDAQ, MultiLaser(AOTF): Line:1; ExW:488; Power:100.0; On". Such a
    line lit nothing but was read as in use, so a source left on behind a closed shutter could split one
    session into two settings groups. Found in a Ti2 file with three light sources (Zenodo 18609221)."""
    if _BEFORE.get():
        return block
    closed = {(d.strip(), n) for d, n in re.findall(r"^\s*([^,\n]+),\s*Shutter\(([^)]*)\):\s*Closed\s*$", block, re.M)}
    if not closed:
        return block
    out, skip = [], False
    for line in block.split("\n"):
        source = re.match(r"^\s*([^,\n]+),\s*MultiLaser\(([^)]*)\):\s*$", line)
        if source:
            skip = (source.group(1).strip(), source.group(2)) in closed
        elif not re.match(r"^\s*Line:", line):
            skip = skip and not line.strip()      # a blank line does not end the source's list (found by review)
        elif skip:
            continue
        out.append(line)
    return "\n".join(out)


A1_CHANNEL = re.compile(r"CH(\d+)\s*\{Laser Wavelength\}:\s*([\d.]+)\s*\{Laser Power\}:\s*([\d.]+)"
                        r"(?:\s*\{PMT HV\}:\s*(-?[\d.]+))?")


def _nikon_confocal(block, excitation):
    """The A1 and AX point-scanning confocals (1.26.11) list every detector channel in each channel's part:
    "CH2 {Laser Wavelength}: 488.0 {Laser Power}: 1.5 / {PMT HV}: 114". The reporter's is the one line
    with its excitation wavelength; if that does not single one out, all of them are kept, so images
    that differ in any of them are never put in one settings group. Checked against a real A1 file
    (Zenodo 10814759)."""
    found = [(float(w), float(pw), hv) for _, w, pw, hv in A1_CHANNEL.findall(block)]
    if not found:
        return []
    mine = [x for x in found if excitation and abs(x[0] - float(excitation)) < 1]
    parts = [f"{w:g}nm@{pw:g}" + (f" HV {float(hv):g}" if hv else "") for w, pw, hv in (mine if len(mine) == 1 else found)]
    avg = re.search(r"\{Line Average/Integrate Count\}:\s*(\d+)", block)
    mode = re.search(r"\{Line Average Mode\}:\s*(\w+)", block)
    if avg and (_BEFORE.get() or avg.group(1) != "0" and not (mode and mode.group(1) == "None")):     # "line none 0" (1.26.20)
        parts.append(f"line {mode.group(1).lower() if mode else 'average'} {avg.group(1)}")
    return parts


def _nikon_settings(description, capturing, name, n_channels, excitation=None):
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
    lines = re.findall(r"Line:\s*\d+;\s*(?:ExW|Excitation wavelength):\s*([\d.]+);[^\n]*?Power:\s*([\d.]+)(?:;\s*(\w+))?",
                       _without_closed_sources(block))
    marked = any(state for _, _, state in lines)       # some files mark the lines in use (Active, On), some list only those
    lasers = [f"{float(w):g}nm@{float(p):g}" for w, p, state in lines
              if float(p) > 0 and (state in ("Active", "On") if marked else True)]
    if lasers:
        parts.append(";".join(lasers))
    parts += [f"lamp {k.lower()} {v.strip()}" for k, v in re.findall(r"Light \(EPI\)[^:\n]*?(voltage|intensity):\s*([^\n]+)", block, re.I)
              if v.strip().upper() != "NA"]
    confocal = _nikon_confocal(block, excitation)
    parts += confocal or [f"{k.strip()} {v}" for k, v in
                          re.findall(r"((?:[A-Za-z]+ )?HV(?:\([A-Za-z]+\))?|Laser Power)\s*:\s*([\d.]+)", block)]
    return "; ".join(parts)


def _nikon_sim_lasers(f, index):
    """The N-SIM's laser lines that are on, with their power, for channel index (1.26.20): "488nm@30". The
    N-SIM records them only in the channel's camera settings (NSimLaser488Power), not in the description
    text the other Nikon systems use, so two SIM sessions at another laser power shared a settings group.
    Checked against Zenodo 15046688, whose authors write "488nm of excitation with 30% laser power"."""
    # an extra: any error here leaves the image as 1.26.19 read it, never unreadable (found by review)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")     # nd2 warns when a file has no such block (most files)
            planes = f.unstructured_metadata(include={"ImageMetadataSeqLV|0"})["ImageMetadataSeqLV|0"]["SLxPictureMetadata"]["PicturePlanes"]
        sample = planes["SampleSetting"]
        sample = sample[index] if isinstance(sample, list) else sample.get(str(index), sample.get(index, sample))
        props = sample["CameraSetting"]["PropertiesFast"]
        return [f"{int(m.group(1))}nm@{float(v):g}" for k, v in sorted(props.items())
                for m in [re.fullmatch(r"NSimLaser(\d+)Power", str(k))] if m and isinstance(v, (int, float)) and v > 0]
    except Exception:
        return []


def _nikon_filter_peaks(f, n_channels):
    """{channel index: (excitation, emission)} from each channel's filter record in a file saved by older
    NIS-Elements (1.26.24), or {}. The nd2 library reads the channels' wavelengths from this record, the
    image's own (PicturePlanes), but in these files' XML it renames every element called Wavelength, so the
    value is lost: Exception_2.nd2's PSM_GFP channel, which NIS-Elements Viewer shows as 488 nm excitation
    and 520 nm emission, had neither, and its reporter was never found. Only a peak (point type 4, as the
    nd2 library takes it) is used, and only when the channel has one filter with one point each: a filter
    cube's band (rising and falling edges) is a range, not a line, and is never read as one. The filter
    ranges of the experiment's plan (SLxExperiment) are not used either: in a sample file they name another
    cube than the image's own record. Any error leaves the channels as before."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            meta = f.unstructured_metadata(include={"ImageMetadataSeq|0", "ImageMetadataSeqLV|0"})
        if "ImageMetadataSeqLV|0" in meta:      # a newer file: nd2 reads its Wavelength itself (found by review)
            return {}
        seq = meta.get("ImageMetadataSeq|0") or {}
        planes = (seq.get("PicturePlanes") or {}).get("Plane") or {}
        planes = list(planes.values()) if isinstance(planes, dict) else list(planes)
        if len(planes) != n_channels:
            return {}
        out = {}
        for i, plane in enumerate(planes):
            filters = ((plane.get("FilterPath") or {}).get("Filter") or {})
            filters = list(filters.values()) if isinstance(filters, dict) else list(filters)
            if plane.get("CompCount", 1) != 1 or len(filters) != 1:
                continue
            got = []
            for kind in ("ExcitationSpectrum", "EmissionSpectrum"):
                points = ((filters[0].get(kind) or {}).get("Point") or {})
                points = list(points.values()) if isinstance(points, dict) else list(points)
                peaks = [float(v) for p in points if isinstance(p, dict) and p.get("Type") == 4
                         for k, v in p.items() if re.fullmatch(r"i\d{10}", str(k))
                         and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0]
                got.append(peaks[0] if len(points) == 1 and len(peaks) == 1 else None)
            if any(got):
                out[i] = tuple(got)
        return out
    except Exception:
        return {}


# Nikon's own point-scanning confocals, whose name is the first line of the camera text ("Nikon A1plus"); on
# other systems that line is the camera ("Nikon DS-Qi2"), which is not the microscope's model (found by review)
NIKON_CONFOCAL = re.compile(r"Nikon (?:A1|AX|C1|C2)(?!\d)")
NIKON_SCAN = ((r"^\s*Binning:\s*(\d+\s*x\s*\d+)\s*$", "binning {}"),
              (r"\{Pinhole Size\(um\)\}:\s*(\d+(?:\.\d+)?)\s*$", "pinhole {} um"),
              (r"^\s*\{Scan Speed\}:\s*(\d+(?:\.\d+)?)\s*$", "scan speed {}"))


def _nikon_part(description, capturing, name, n_channels):
    """The channel's own part of the description, or the camera text where it has none (as _nikon_settings)."""
    block = _nikon_block(description, name, n_channels) if name is not None else None
    return capturing if block is None else block


def _nikon_scan(description, capturing, name, n_channels):
    """1.26.24: the camera binning (it sums pixels: 4x4 makes each pixel 16 times brighter) and an A1 or AX's
    pinhole and scan speed, as NIS-Elements shows them under Capturing, for the scan_settings column; ""
    without them. The scan speed of a stimulation (Stim1) is not the image's and is not read."""
    if _before_1_26_24():
        return ""
    cut = lambda t: re.split(r"^\s*Stim\d", t, maxsplit=1, flags=re.M)[0]
    parts = [cut(_nikon_part(description, capturing, name, n_channels)), cut(capturing)]
    out = []
    for pattern, text in NIKON_SCAN:
        found = next((m for m in (re.search(pattern, t, re.M) for t in parts) if m), None)
        if found:
            value = re.sub(r"\s+", "", found.group(1))
            try:
                out.append(text.format(value if "x" in value else f"{round(float(value), 3):g}"))
            except ValueError:      # never fatal (found by review)
                continue
    return "; ".join(out)


def _nikon_camera_fields(description, capturing, name, n_channels):
    """1.26.24: the names NIS-Elements gives the camera values of the settings text, in its order, joined by
    "/" ("Exposure/Calibrated Gain/Visual Gain"), for the labels of Check the reading and the Methods draft
    (inventory column camera_fields). The settings text keeps only "gain" and "exposure", so Visual Gain
    read as a detector gain. Each name is the field's whole name when it starts its line ("EM Gain
    Multiplier"), else the word alone, so text before it on the line is never taken for its name."""
    block = _nikon_block(description, name, n_channels) if name is not None else None
    text = block if block is not None and re.search(CAMERA_LINE, block) else capturing
    names = []
    for found in re.finditer(CAMERA_LINE, text):        # the same matches, in the same order, as _nikon_settings
        line = text[text.rfind("\n", 0, found.start()) + 1:found.start()]
        whole = re.fullmatch(r"\s*((?:[A-Za-z]+ )*)", line)
        names.append(((whole.group(1) if whole else "") + found.group(1)).strip())
    return "/".join(names)


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
        # an N-SIM's laser power is not in the description text (only read where that text has no power)
        sim = ({i: _nikon_sim_lasers(f, i) for i in range(len(chans))}
               if not _BEFORE.get() and not re.search(r"Power\s*:", text.get("description") or "") else {})
        # 1.26.24: an older file's wavelengths that the nd2 library misses (only where it gives none)
        peaks = ({} if _before_1_26_24() or all(c.channel.excitationLambdaNm and c.channel.emissionLambdaNm for c in chans)
                 else _nikon_filter_peaks(f, len(chans)))
        positions = []
        for p in range(sizes.get("P", 1)):
            first = next((i for i, ix in enumerate(f.loop_indices) if ix.get("P", 0) == p), 0)
            positions.append(f.frame_metadata(first).channels[0].position.stagePositionUm)
    names = [c.channel.name or "" for c in chans]
    ex = [c.channel.excitationLambdaNm or peaks.get(i, (None, None))[0] for i, c in enumerate(chans)]
    em = [c.channel.emissionLambdaNm or peaks.get(i, (None, None))[1] for i, c in enumerate(chans)]
    roles = [marker_from_named(c.channel.name or "", None, ex[i],
                               "brightfield" in c.microscope.modalityFlags, cfg, em[i])
             for i, c in enumerate(chans)]
    mic = chans[0].microscope
    objective = (mic.objectiveName or "").strip()
    if mic.objectiveMagnification and not re.search(r"\d+x", objective):
        objective = f"{mic.objectiveMagnification:g}x {objective}".strip()
    capturing = (text.get("capturing") or "").replace("\r\n", "\n")
    camera = capturing.split("\n", 1)[0].strip()
    roles = named_extras(prefer_named_dye(roles, [None] * len(roles), cfg), cfg, names, None, ex, em)
    roles, near = near_match(roles, cfg, named_near(ex, em))
    mi = pick(roles, "MARKER")
    mine = (text.get("description") or "", capturing, names[mi] if mi is not None else None, len(chans))
    # the settings text uses the wavelength nd2 gives, as 1.26.23 did: a peak read now finds the reporter but never
    # changes which A1 detector line is kept, so no settings group is reworded (found by review)
    settings = _nikon_settings(*mine, chans[mi].channel.excitationLambdaNm if mi is not None else None)
    scan = _nikon_scan(*mine) if mi is not None else ""
    # 1.26.24: "Nikon A1plus" is the model; the microscope's name "Nikon Nikon A1plus" keys settings groups and
    # sessions, so it stays, and the model is what the Methods draft and Check the reading show
    model = camera if NIKON_CONFOCAL.match(camera) and not _before_1_26_24() else ""
    if mi is not None and sim.get(mi) and "nm@" not in settings:
        settings = "; ".join(x for x in (settings, ";".join(sim[mi])) if x)
    dims = "".join(sizes)
    out = []
    for p, stage in enumerate(positions):
        out.append({
            "format": "Nikon ND2", "system": f"Nikon {camera}".strip(), **({"model": model} if model else {}),
            "location_in_file": "whole file" if len(positions) == 1 else f"position {p}",
            "objective": objective,
            "n_z": sizes.get("Z", 1), "n_t": sizes.get("T", 1), "width": sizes["X"], "height": sizes["Y"],
            "pixel_um": round(vox.x, 4), "z_step_um": round(vox.z, 3) if "Z" in sizes else 0.0,
            "bits": bits, "channel_roles": list(roles), "channel_names": names,
            "settings": settings, "zoom": f"{mic.zoomMagnification:g}" if mic.zoomMagnification else "",
            **({"scan_settings": scan} if scan else {}),
            **({"camera_fields": _nikon_camera_fields(*mine)} if mi is not None and not _before_1_26_24() else {}),
            "acquired": " ".join((text.get("date") or "").split()),
            "stage_x": stage.x, "stage_y": stage.y,
            # 1.26.8: an uncalibrated file reads as 1 um per pixel, which sets the display scale and the cell search
            "flags": ([] if calibrated else [f"pixel size {NOT_CALIBRATED} (1 um per pixel is a placeholder)"])
            + _extra_axes_flags(dims.replace("P", ""), [n for a, n in sizes.items() if a != "P"]) + near,
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


def _olympus_or_none(v):
    """_olympus_number, or None for a value that is missing or not a number."""
    if v is None or v == "":
        return None
    try:
        return _olympus_number(v)
    except (TypeError, ValueError):
        return None


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
    roles, near = near_match(roles, cfg, named_near([c.get("ExcitationWavelength") for c in chans],
                                                    [c.get("EmissionWavelength") for c in chans]))
    if len(chans) != size.get("C", 1):
        roles = ["UNRESOLVED"] * size.get("C", 1)
        near = []
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
    # 1.26.20: the PMT gain when it is not 1x (AnalogPMTGain 1000), and the zoom (ZoomValue), as FV10-ASW shows
    # them; both were left out (found by holding wormscore against S-BIAD1612 and S-BIAD1944: "with gain set
    # at 1000", "with a 3.5x zoom"). Zoom is used only by the flat-field correction.
    # (with _olympus_number: a computer set to a decimal comma writes '3,5.0'; found by review)
    gain = _olympus_or_none(chans[mi].get("AnalogPMTGain")) if mi is not None and not _BEFORE.get() else None
    zoom = None if _BEFORE.get() else _olympus_or_none(acq.get("ZoomValue"))
    zoom = f"{round(zoom, 4):g}" if zoom else ""
    settings = "; ".join(x for x in [
        f"PMT {chans[mi].get('AnalogPMTVoltage')} V" if mi is not None and chans[mi].get("AnalogPMTVoltage") else "",
        f"gain {gain / 1000:g}x" if gain and gain != 1000 else "",
        ";".join(f"{w}nm@{_olympus_number(t):g}%" for w, t in lasers if w and t is not None),
        f"{acq.get('IntegrationType')} {acq.get('IntegrationCount')}" if acq.get("IntegrationType") not in (None, "None") else ""] if x)
    system = md.get("Version Info", {}).get("SystemName", "") or acq.get("Acquisition Device", "")
    return [{
        "format": "Olympus FluoView", "system": f"Olympus {system}".strip(),
        "location_in_file": "whole file", "objective": objective,
        "n_z": size.get("Z", 1), "n_t": size.get("T", 1), "width": size["X"], "height": size["Y"],
        "pixel_um": round(_olympus_number(ref.get("WidthConvertValue", 0)), 4), "z_step_um": round(z_step, 3),
        "bits": int(ref.get("ValidBitCounts") or 16), "channel_roles": roles, "channel_names": names,
        "settings": settings, "zoom": zoom, **({"_zoom_before": ""} if zoom else {}),
        "acquired": str(acq.get("ImageCaputreDate", "")),
        "stage_x": None, "stage_y": None, "_reader": ("oif", str(path), 0, axes),
        "flags": _extra_axes_flags(axes, shape) + near + other_fluorophore(roles, [c.get("DyeName") for c in chans], cfg),
    }]


# ------------------------------------------------------------------ Olympus / Evident OIR

# 1.26.14: the .oir files of the FV3000, FV4000 and FVMPE-RS (FluoView 1.x, FV31S 2.x, FV40S 3.x). The
# container is read with oirfile (BSD-3); everything below is read from the file's own XML, by the names
# found in real files (sources in tests/test_olympus_oir.py). Each channel has an entry under imageInfo
# (it has pixels; "order" is its place in the file) and one under acquisition/phase (its detector, dye
# and laser), tied by the channel's id. Detector voltage, offset and gain are in pmt[channelId]; the
# laser's share let through is in mainLaser[laserDataId].

OIR_MAGIC = b"OLYMPUSRAWFORMAT"
OIR_UNITS = {"MICRO_METER": 1.0, "NANO_METER": 1e-3, "MILLI_METER": 1e3}
OIR_TRANSMITTED = re.compile(r"TD_D_\d")        # detector ids "__FV31-LETD_D_1__" (FV3000), "__FV40-LETD_D_1__" (FV4000)


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def _at(el, path):
    """The elements at a path of tag names below el. Namespace prefixes are not part of the names."""
    found = [el] if el is not None else []
    for name in path.split("/"):
        found = [k for e in found for k in e if _tag(k) == name]
    return found


def _said(el, path):
    return next(((e.text or "").strip() for e in _at(el, path) if (e.text or "").strip()), "")


def _span(values, unit=""):
    """A value as text, or the lowest and the highest when it is not the same in every plane."""
    vs = sorted(set(values))
    return f"{vs[0]:g}{unit}" if len(vs) == 1 else f"{vs[0]:g}-{vs[-1]:g}{unit}"


def _oir_channels(root):
    """The channels that have pixels, in the file's order, each with what its own blocks state."""
    acq = next(iter(_at(root, "acquisition")), None)
    param = next(iter(_at(acq, "imagingParam")), None)
    own = {c.get("id"): (phase, c) for phase in _at(acq, "phase") for c in _at(phase, "group/channel")}
    pmts = {p.get("channelId"): p for p in _at(param, "pmt")}
    shares = {l.get("laserDataId"): l for l in _at(param, "mainLaser")}
    out = []
    for info in _at(root, "imageInfo/phase/group/channel"):
        phase, ch = own.get(info.get("id"), (None, None))
        lit = {l.get("id"): _num(_said(l, "wavelength")) for l in _at(phase, "imagingMainLaser") if l.get("enable") == "true"}
        mine = [i for i in (l.text for l in _at(ch, "laserDataId")) if i in lit]
        pmt = pmts.get(info.get("id"))
        nd = next(iter(_at(phase, "productData/lightpath/mainLaserNDFilter")), None)
        device, detector = _said(ch, "deviceName"), (ch.get("detectorId") or "") if ch is not None else ""
        out.append({
            "id": info.get("id"), "order": _num(info.get("order") or ""), "stated": ch is not None,
            "name": _said(info, "name") or _said(ch, "name"), "dye": _said(ch, "dyeName"),
            "device": device, "detector": detector,
            # transmitted light: the detector is the transmitted-light detector (device "TD")
            "transmitted": bool(TRANSMITTED_NAME.fullmatch(device) or OIR_TRANSMITTED.search(detector)),
            # its own laser lines that are on; "lit" is every line that is on in its phase
            **{key: [{"id": i, "nm": lit[i], "share": _num(_said(shares.get(i), "transmissivity")),
                      "set": _num(_said(shares.get(i), "optionalTransmissivity")),
                      "power": _num(_said(shares.get(i), "power"))} for i in ids]
               for key, ids in (("lasers", mine), ("lit", list(lit)))},
            "emission": _num(_said(ch, "dyeData/emissionWavelength")),
            "bits": _num(_said(info, "imageDefinition/bitCounts")),
            "pmt": {k: _num(_said(pmt, k)) for k in ("voltage", "offset", "gain")} if pmt is not None else {},
            "nd": _num(_said(nd, "transmissivity")) if nd is not None and _said(nd, "name") != "Empty" else None,
            "length": {a: _num(_said(info, f"length/{a}")) for a in "xz"},
            "unit": _said(info, "pixelUnit/x"),
            # 1.26.24: the channel's pinhole, as OlyVIA shows it (127 um for an FV3000 file)
            "pinhole": _num(_said(info, "productData/pinholeDiameter")),
        })
    return out


def _oir_planes(frames, ch):
    """What each plane's own block records for one channel: detector voltage, offset and gain, and the
    share of each of its lasers."""
    seen = {"voltage": [], "offset": [], "gain": [], **{l["id"]: [] for l in ch["lasers"]},
            **{("set", l["id"]): [] for l in ch["lasers"]}}
    for xml in frames:
        value = next(iter(_at(ET.fromstring(xml), "acquisitionValue")), None)
        for p in _at(value, "pmt"):
            if p.get("channelId") == ch["id"]:
                for k in ("voltage", "offset", "gain"):
                    seen[k].append(_num(_said(p, k)))
        for l in _at(value, "laser"):
            if l.get("laserDataId") in seen:
                seen[l.get("laserDataId")].append(_num(_said(l, "transmissivity")))
                seen[("set", l.get("laserDataId"))].append(_num(_said(l, "optionalTransmissivity")))
    return {k: sorted({v for v in vs if v is not None}) for k, vs in seen.items()}


def _oir_settings(ch, root, frames):
    """(settings text, flags) of the reporter channel, from its own values: detector voltage (HV), gain and
    offset when they are not 1 and 0, each of its lasers that is on with the share let through, a neutral
    density filter in the laser path, and averaging. If the channel names no laser of its own, every laser
    that is on in its phase is listed, so a change of any of them changes the text.

    The values are those of the image's settings block, which is also what Bio-Formats reports. Each
    plane has a record of its own. With brightness correction along z the planes differ, and the text
    gives the range over the planes. Without it, 10 of 23 real files have a first plane whose laser share
    ("transmissivity") is another number than the image's (for example 1% against 0.1%).

    1.26.17: the file settles most of these itself. The share is recorded a second time in both places, as
    the value set in the software before a neutral density filter ("optionalTransmissivity"), and that
    record is the same in the image and in the plane in every one of the 23 files; it also fits the image's
    share (equal to it, or ten times it with the 10% filter in). So the image's share has three of the
    four records behind it, and the plane's own "transmissivity" is the one left over. In four series of
    one session, two of them with a plane that says 1% where the image says 0.1%, the pixels are equally
    bright, not ten times brighter. The image's share is therefore used alone when the plane's second
    record agrees with the image's. Where a file has no second record, or it does not agree, both shares
    are written and the image is flagged, as in 1.26.14."""
    ch = {**ch, "lasers": ch["lasers"] or ch["lit"]}
    corrected = _said(root, "acquisition/imagingParam/brightnessAdjustmentParam/enable") == "true"
    planes = _oir_planes(frames if corrected else frames[:1], ch)
    parts, varies, differs = [], [], []

    def value(key, stated, what, unit="", settled=False):
        """(every value recorded, its text): one value, the range over the planes, or both records.
        settled: the file's second record of this value agrees between the image and the plane."""
        seen = planes.get(key, [])
        every = sorted(set(seen + ([stated] if stated is not None else [])))
        if corrected and len(every) > 1:
            varies.append(what)
        elif stated is not None and seen and seen != [stated] and settled:
            return [stated], f"{stated:g}{unit}"
        elif stated is not None and seen and seen != [stated]:
            differs.append(f"{what} {stated:g}{unit} in the image's settings, {_span(seen, unit)} in the first plane's record")
            return every, f"{stated:g}{unit} (first plane: {_span(seen, unit)})"
        return every, _span(every, unit) if every else ""

    volt, volt_text = value("voltage", ch["pmt"].get("voltage"), "detector voltage")
    gain, gain_text = value("gain", ch["pmt"].get("gain"), "gain")
    offset, offset_text = value("offset", ch["pmt"].get("offset"), "offset")
    if any(volt):
        parts.append(f"HV {volt_text} V")
    if gain and set(gain) != {1.0}:
        parts.append(f"gain {gain_text}")
    if any(offset):
        parts.append(f"offset {offset_text}")
    lasers = []
    for l in ch["lasers"]:
        share, share_text = value(l["id"], l["share"], f"{l['nm']:g} nm laser" if l["nm"] is not None else "laser", "%",
                                  settled=l.get("set") is not None and planes.get(("set", l["id"])) == [l["set"]])
        if l["nm"] is None or not share:
            continue
        lasers.append(f"{l['nm']:g}nm@{share_text}" + (f" of {l['power']:g}%" if l["power"] not in (None, 100.0) else ""))
    if lasers:
        # the share is what the software shows; in the files with an ND filter it already includes the filter
        parts.append(";".join(lasers) + (f" (ND filter {ch['nd']:g}% in)" if ch["nd"] is not None else ""))
    integration = next(iter(_at(root, "acquisition/imagingParam/method/integration")), None)
    kind, count = _said(integration, "type"), _said(integration, "count")
    if kind and kind != "None" and count:
        how = {"true": "average", "false": "sum"}.get(integration.get("average"), "integration")
        parts.append(f"{kind.lower()} {how} {count}")
    flags = []
    if varies:
        flags.append(f"brightness is corrected along z: {', '.join(dict.fromkeys(varies))} not the same in every plane "
                     "(the settings give the range)")
    if differs:
        flags.append("the file records two values: " + "; ".join(differs) + " (check in the Olympus software)")
    return "; ".join(parts), flags


def _oir_meta(xml, frames, cfg):
    """Everything wormscore reads from an .oir file's metadata: xml is its image settings (the lsmimage
    block), frames the per-plane blocks. Pixels are not needed, so this is tested on the XML of real files."""
    root = ET.fromstring(xml)
    chans = _oir_channels(root)
    flags = []
    orders = [c["order"] for c in chans]
    ordered = None not in orders and len(set(orders)) == len(orders) and all(c["stated"] for c in chans)
    chans.sort(key=lambda c: c["order"] if ordered else 0)
    # the laser of a channel is used for its role only when it is one line: a lambda channel lists several
    excitation = [c["lasers"][0]["nm"] if len(c["lasers"]) == 1 else None for c in chans]
    # emission: the emission peak the file gives for the channel's dye; a channel without a dye has none
    emission = [c["emission"] for c in chans]
    dyes = [c["dye"] or None for c in chans]
    named = [c["dye"] or c["name"] for c in chans]
    if ordered:
        roles = [marker_from_named(n, d, ex, c["transmitted"], cfg, em)
                 for c, n, d, ex, em in zip(chans, named, dyes, excitation, emission)]
        roles = named_extras(prefer_named_dye(roles, dyes, cfg), cfg, named, dyes, excitation, emission)
        roles, near = near_match(roles, cfg, named_near(excitation, emission))
        flags += near
    else:
        roles = ["UNRESOLVED"] * len(chans)
        flags.append("the file does not state the order of its channels, or the settings of one of them")
    flags += other_fluorophore(roles, dyes, cfg)
    mi = pick(roles, "MARKER")
    settings, varying = _oir_settings(chans[mi], root, frames) if mi is not None else ("", [])
    system = _said(root, "system/systemName")
    if system.endswith("(Modified)"):
        flags.append(f"made or changed in the Olympus software after the scan (the file's system is '{system}')")
        system = system[:-len("(Modified)")].strip()
    lens = next(iter(_at(root, "acquisition/microscopeConfiguration/objectiveLens")), None)
    mag, na, lens_name = _num(_said(lens, "magnification")), _num(_said(lens, "naValue")), " ".join(_said(lens, "name").split())
    objective = (f"{mag:g}x" + (f"/{na:g}" if na else "") + (f" ({lens_name})" if lens_name else "")) if mag else lens_name
    first = chans[0] if chans else {"length": {}, "unit": ""}
    pixel_um, z_um = first["length"].get("x") or 0.0, first["length"].get("z") or 0.0
    if first["unit"] and first["unit"] not in OIR_UNITS:
        flags.append(f"pixel size is given in '{first['unit']}', which is not read")
        pixel_um = z_um = 0.0
    scale = OIR_UNITS.get(first["unit"], 1.0)
    bits = [int(c["bits"]) for c in chans if c["bits"]]
    planned = [int(_num(_said(a, "maxSize")) or 0) for a in _at(root, "acquisition/imagingParam/axis")
               if _said(a, "axis") == "ZSTACK" and a.get("enable") == "true" and a.get("paramEnable") != "false"]
    zoom = _num(_said(root, "acquisition/imagingParam/area/zoom"))
    return {"system": f"Olympus {system}".strip(), "objective": objective,
            "pixel_um": round(pixel_um * scale, 4), "z_step_um": round(abs(z_um) * scale, 3),
            "bits": max(bits) if bits else 0,
            "channel_roles": roles, "channel_names": [f"{c['name']} {c['dye'] or ('TD' if c['transmitted'] else '')}".strip() for c in chans],
            "channel_ids": [c["id"] for c in chans], "settings": settings,
            "zoom": f"{zoom:g}" if zoom else "", "acquired": _said(root, "general/creationDateTime"),      # ISO, as written
            "scan_size": (int(_num(_said(root, "imageInfo/width")) or 0), int(_num(_said(root, "imageInfo/height")) or 0)),
            "planned_z": planned[0] if planned else 0, "flags": flags + varying,
            "scan_settings": "" if mi is None or not chans[mi]["pinhole"] or _before_1_26_24()
            else f"pinhole {round(chans[mi]['pinhole'], 2):g} um"}


def _oir_tiles(xml, file_name):
    """The number of tiles of the tile scan that file_name is one tile of, or None. xml is the text of
    matl.omp2info, which the Olympus software writes next to the files of a multi-area scan: each group
    lists its areas and the file of each. The .oir file itself does not say that it is a tile."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    for group in _at(root, "group"):
        files = [_said(a, "image").lower() for a in _at(group, "area")]
        if len(files) > 1 and file_name.lower() in files:
            return len(files)
    return None


def _oir_open(path):
    """An .oir file opened with oirfile. A file cut short is refused with the reason: oirfile reports it
    as a damaged index."""
    with open(path, "rb") as fh:
        head = fh.read(48)
    if len(head) < 48 or head[:16] != OIR_MAGIC:
        raise ValueError("not an Olympus .oir file (it does not begin as one)")
    stated, size = struct.unpack_from("<Q", head, 32)[0], os.path.getsize(path)
    if stated > size:
        raise ValueError(f"the file is incomplete: it holds {size / 2**20:.1f} of {stated / 2**20:.1f} MB "
                         "(a copy that did not finish, or the scan was interrupted)")
    try:
        return oirfile.OirFile(str(path), squeeze=False)
    except (oirfile.OirFileError, NotImplementedError) as exc:
        raise ValueError(f"the file's structure could not be read ({str(exc)[:60]})") from None


class _OirWarnings:
    """What oirfile reports while it reads pixels. It fills a plane that is cut short with zeros and only
    logs it; here that is an error, so a cell that is there never looks missing."""

    def __enter__(self):
        self.said, self.log = [], logging.getLogger("oirfile")
        self.handler = logging.Handler()
        self.handler.emit = lambda record: self.said.append(record.getMessage())
        self.keep = self.log.propagate
        self.log.addHandler(self.handler)
        self.log.propagate = False
        return self.said

    def __exit__(self, *exc):
        self.log.removeHandler(self.handler)
        self.log.propagate = self.keep


def _oir_held(f):
    """(z planes that can be read, what is wrong or None). A scan stopped by hand ends in the middle of a
    plane; oirfile fills the rest of that plane with zeros (found in a real file, Zenodo 20703055, whose
    sixth plane is 42% scanned; Bio-Formats leaves that plane out). In a plain z-stack the last plane is
    then left out here too. Planes missing anywhere else make the file incomplete: it is flagged and
    not used. The check uses oirfile's index of pixel blocks; tests/test_olympus_oir.py holds it in place."""
    sizes, index = dict(f.sizes), getattr(f, "_pixel_map", None)
    n_z = sizes.get("Z", 1)
    if not index or "Y" not in sizes:
        return n_z, None
    need = sizes["Y"] * sizes["X"] * f.dtype.itemsize
    t0, l0, z0 = (min(k[i] for k in index) for i in range(3))
    chans = {k[3] for k in index}
    short = [(t, l, z) for t in range(sizes.get("T", 1)) for l in range(sizes.get("L", 1)) for z in range(n_z)
             for c in chans
             if sum(b[2] for b in index.get((t + t0, l + l0, z + z0, c), ())) < need]
    if not short:
        return n_z, None
    if sizes.get("T", 1) == 1 and sizes.get("L", 1) == 1 and n_z > 1 and all(z == n_z - 1 for _, _, z in short):
        return n_z - 1, f"the scan stopped during plane {n_z}: that plane is left out"
    return n_z, f"{INCOMPLETE}: {len(short)} of {len(index)} are missing or cut short"


def oir_images(path, cfg):
    with _oir_open(path) as f:
        sizes, bits = dict(f.sizes), f.bitspersample
        whole_z, problem = _oir_held(f)
        blocks = f.xml_metadata
        xml = (blocks.get(oirfile.METADATA.LSMIMAGE) or [""])[0]
        frames = list(blocks.get(oirfile.METADATA.FRAMEPROPERTIES) or [])
    if not sizes or not xml:
        raise ValueError("the file holds no image (for example the overview map of a multi-area scan)")
    if "Y" not in sizes or "X" not in sizes:
        raise ValueError("a line scan, not an image")
    if "S" in sizes:
        raise ValueError("a colour picture, not a fluorescence image")
    axes, shape = "".join(sizes), list(sizes.values())
    meta = _oir_meta(xml, frames, cfg)
    flags = list(meta["flags"])
    n_c, n_z = sizes.get("C", 1), whole_z
    roles = meta["channel_roles"]
    if len(roles) != n_c:
        flags.append(f"the metadata describes {len(roles)} channels, the file holds {n_c}")
        roles = ["UNRESOLVED"] * n_c
    names = meta["channel_names"] if len(meta["channel_names"]) == n_c else [""] * n_c
    if problem:
        flags.append(problem)
    if meta["planned_z"] > n_z:
        flags.append(f"the z-stack was set to {meta['planned_z']} planes, the file holds {n_z} whole planes "
                     "(stopped early, or a continuation file is missing)")
    if all(meta["scan_size"]) and meta["scan_size"] != (sizes["X"], sizes["Y"]):
        flags.append(f"the image is {sizes['X']} x {sizes['Y']} pixels, the scan was {meta['scan_size'][0]} x "
                     f"{meta['scan_size'][1]}: the pixel size may not apply")
    sidecar = Path(path).with_name("matl.omp2info")
    tiles = _oir_tiles(sidecar.read_text(encoding="utf-8", errors="replace"), Path(path).name) if sidecar.is_file() else None
    if tiles:
        flags.append(f"one tile of a tile scan ({tiles} tiles, listed in matl.omp2info): the tiles are not joined")
    bits = meta["bits"] or bits
    if not bits:
        bits = 16
        flags += _no_bits_flag(meta["system"], cfg, 65535)
    return [_with_acquired({
        "format": "Olympus OIR", "system": meta["system"], "location_in_file": "whole file", "objective": meta["objective"],
        "n_z": n_z, "n_t": sizes.get("T", 1), "width": sizes["X"], "height": sizes["Y"],
        "pixel_um": meta["pixel_um"], "z_step_um": meta["z_step_um"] if n_z > 1 else 0.0,
        "bits": int(bits), "channel_roles": roles, "channel_names": names,
        "settings": meta["settings"] if roles == meta["channel_roles"] else "", "zoom": meta["zoom"],
        **({"scan_settings": meta["scan_settings"]} if meta["scan_settings"] and roles == meta["channel_roles"] else {}),
        "stage_x": None, "stage_y": None, "flags": flags + _extra_axes_flags(axes, shape),
        "_reader": ("oir", str(path), 0, axes),
    }, meta["acquired"])]


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
    """Every image of one file. Text from the file that a spreadsheet would run as a formula gets a leading '
    here (1.26.16; 1.26.15 did it at Scan data only, so slide images and control images read the same
    microscope under another name)."""
    found = _images_in(path, cfg, source)
    for img in found:
        for field in ("system", "objective", "settings", "zoom", "acquired"):
            img[field] = _no_formula(img.get(field, ""))
        img["channel_names"] = [_no_formula(n) for n in img["channel_names"]]
        img["flags"] = [_no_formula(x) for x in img["flags"]]
    return found


def _images_in(path, cfg, source=None):
    low = path.name.lower()
    if low.endswith(".lif"):
        return leica_images(path, cfg)
    if low.endswith(".czi"):
        return zeiss_images(path, cfg)
    if low.endswith(".nd2"):
        return nikon_images(path, cfg)
    if low.endswith((".oib", ".oif")):
        return olympus_images(path, cfg)
    if low.endswith(".oir"):
        return oir_images(path, cfg)
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
    elif kind == "oir":
        with _oir_open(path) as f, _OirWarnings() as said:
            (whole_z, problem), arr, dims = _oir_held(f), f.asarray(), "".join(f.dims)
        if "Z" in dims and whole_z < arr.shape[dims.index("Z")]:
            arr = np.take(arr, range(whole_z), axis=dims.index("Z"))        # a last plane that was not finished
        elif problem or said:
            raise ValueError(f"{Path(path).name}: {problem or said[0]}")
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


def _no_formula(text):
    """Text taken from a microscope file goes into inventory.csv and the key, which people open in a
    spreadsheet. Text that would be run as a formula there (it starts with = + @, or with - and is not
    a number) gets a leading ' (1.26.15), as answers in scores.csv do. Other text is unchanged."""
    if isinstance(text, str) and (text[:1] in ("=", "+", "@", "\t", "\r") or (text[:1] == "-" and _num(text) is None)):
        return "'" + text
    return text


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
                row["note"] = _no_formula(str(exc)[:200])
                log(f"  could not read {row['relative_path']}: {exc}")
                continue
            if not found:
                row["role"] = NOT_USED
                if p.suffix.lower() == ".nd2":      # 1.26.13: say why (nikon_images gives nothing only for these)
                    row["note"] = COLOUR_CAMERA
                    log(f"  not used: {row['relative_path']}: {COLOUR_CAMERA}")
                continue
            for img in found:
                img.update({"source": row["source"], "file": p.name, "relative_path": row["relative_path"],
                            "local_path": row["local_path"], "original_path": row["original_path"],
                            "file_sha256": row["sha256"], "genotype": C.genotype_for(src, row["relative_path"])})
                images.append(img)
        manifest += rows
    return manifest, images
