"""Check the reading: what wormscore reads from one microscope file, as plain text, to hold against what
the microscope software shows for the same image.

It goes through readers.images_in, so every value is the one Scan data would write to inventory.csv
(text a spreadsheet would run as a formula keeps its leading '). Only metadata is read, never a pixel
plane, and nothing is written: no project, no log, no cache.

The lines that say where a value is shown in the vendor's software use only names found in the vendor's
documentation; where it does not name the field, the line names the window and no more. The sources:

- Leica LAS X: right-click the image, Properties (University of Zurich ZMB guide "Metadata"; University
  of Houston "The Leica Experiment Tab"; Duke "User's guide, Leica Stellaris 8"). "Project tab: Directory
  tree for opened files" (Leica TCS SP8 user manual). LAS X Office "for experiment review" (Leica).
  The section and field names were read off LAS X Office 1.4.11's Properties window for a real TCS SP8
  file (19 November 2026); every value there matched this reading.
- Zeiss ZEN: "The Information View is a view type that shows a summary information sheet of all relevant
  image acquisition parameters", with the list used below (LSM 880 operating manual, ZEN black, 6.21).
  "Info View" (ZEN knowledge base). "ZEN lite is the free, unlicensed edition of ZEN" (Zeiss).
  The field names were then read off ZEN 3.15 (ZEN lite) for the real-worm demo's LSM 880 file (19
  November 2026): every value matched. Its Lasers row lists only the first track's lasers; the file
  records the reporter's (here 488 nm at 12% on track 2) with its channel.
- Nikon NIS-Elements: File > Image Properties and its tabs; the image window's status bar (NIS-Elements
  AR online help). "NIS-Elements Viewer is a free, standalone application" (Nikon).
  The names were then read off NIS-Elements Viewer 6.10 for two real files (a Kinetix spinning disk and a
  DS-Fi3 z-stack, 19 November 2026): every value matched. The Experiment Data tab is there for a z-stack
  only; gain and exposure are under Capturing, laser power under Description, per channel.
- Olympus FV3000: "Select the [Property] tab and check the acquiring conditions" (FV3000 operation
  manual). The names below are OlyVIA 5.1's (Evident's free viewer), read for an FV3000 file and an older
  FluoView one (19 November 2026): every value matched once the FV3000 file's UTC time was given in the
  computer's time zone, as OlyVIA gives it. OlyVIA names channels by detector (HSD1), the file by CH1.
"""
import html
import re
from pathlib import Path

from . import __version__
from . import config as C
from . import readers as R

ROLE_WORDS = {"BF": "brightfield", "OTHER": "not used", "UNRESOLVED": "not resolved (the file does not settle it)"}
VALUES = ("objective", "pixel", "zstep", "planes", "bits", "channels", "gain", "laser", "light", "averaging", "exposure")
NAMES = {"objective": "Objective", "pixel": "Pixel size", "zstep": "Z-step", "planes": "Planes", "bits": "Bit depth",
         "channels": "Channels", "gain": "Detector gain or voltage", "laser": "Laser lines and power",
         "light": "Light source", "averaging": "Averaging", "exposure": "Exposure"}
GENERIC = "in the image's properties (acquisition parameters)"


def _all(where):
    return {v: where for v in VALUES}


# format -> (the software, how to get to the image's values, where each value is)
VENDOR = {
    # the section and field names as LAS X Office 1.4.11 shows them for a TCS SP8 file (19 November 2026)
    "Leica LIF": (
        "Leica LAS X",
        "Open the .lif in LAS X (LAS X Office reviews files without the microscope). In the project tree, "
        "right-click the image and choose Properties: the window shows the settings the image was taken with.",
        dict(_all("Properties window"),
             objective="Confocal Settings, ObjectiveName",
             pixel="Dimensions, Pixel Size / Voxel Size, X",
             zstep="Dimensions, Pixel Size / Voxel Size, Z",
             planes="Dimensions, Logical Size, Z", bits="Channels, Resolution", channels="Channels, LUT",
             gain="Detectors, the Active one's Gain (no % or V)", laser="Laser Lines, Intensity",
             averaging="Confocal Settings, LineAverage", acquired="Start Time (this computer's time zone)")),
    "Zeiss CZI": (
        "Zeiss ZEN",
        "Open the .czi in ZEN (ZEN lite is the free edition) and choose the image's Info view "
        "(Information View in ZEN black).",
        # the field names as ZEN 3.15 (ZEN lite) shows them for a real LSM 880 file (19 November 2026)
        {"objective": "Info view, Objective (by its model name)", "pixel": "Info view, Scaling (per Pixel), first two",
         "zstep": "Info view, Scaling (per Pixel), third", "planes": "Info view, Z-Stack (slices)",
         "bits": "Info view, Bit Depth", "channels": "Info view, channel table, Channel Name",
         "gain": "Info view, channel table, Detector Gain",
         "laser": "Info view, Lasers (first track only)",
         "light": "Info view", "averaging": "Info view, Averaging", "exposure": "Info view",
         # ZEN 3.15 shows a UTC time in the computer's time zone, as wormscore now gives it (19 November 2026)
         "acquired": "Info view, Acquisition Start"}),
    # the tab and field names as NIS-Elements Viewer 6.10 shows them for two real files (19 November 2026)
    "Nikon ND2": (
        "Nikon NIS-Elements",
        "Open the .nd2 in NIS-Elements (NIS-Elements Viewer is free) and choose File > Image Properties. "
        "Capturing and Description list each channel in turn (Sample 1 and Plane #1 are the first).",
        {"objective": "Acquisition Details tab, Objective name",
         "pixel": "status bar, first field (two decimals)",
         "zstep": "Experiment Data tab, Z-Stack Loop, Z-Step",
         "planes": "Experiment Data tab, Z-Stack Loop, Count",
         "bits": "status bar (bit depth, then image size)",
         "channels": "Acquisition Details tab, one Sample each",
         "gain": "Image Fields tab, Capturing", "exposure": "Image Fields tab, Capturing",
         "laser": "Image Fields tab, Description (Power)", "light": "Image Fields tab, Description",
         "averaging": "Image Properties", "acquired": "Image Fields tab, Date"}),
    # the names as OlyVIA 5.1 shows them for two real files, an FV3000 and an older FluoView one (19 November
    # 2026); the FV software itself was not at hand
    "Olympus OIR": (
        "the Olympus/Evident software",
        "Open the .oir in the software of the FV3000 (FV31S-SW) or the FV4000 and select the image's [Property] "
        "tab, or open it in the free OlyVIA and see its Properties panel. The names below are OlyVIA's.",
        dict(_all("Properties panel"),
             pixel="Calibration (X), in nm/pixel", zstep="Z Spacing", planes="Z Dimension, first number",
             bits="Adjust Display, Histogram", channels="Channel Count (named by detector)",
             laser="Channel n, Laser Wavelength and Intensity",
             acquired="Creation Time (this computer's time zone)")),
    "Olympus FluoView": (
        "Olympus FluoView (FV10-ASW)",
        "Open the file in FV10-ASW or the FV10-ASW Viewer.",
        _all(GENERIC)),
}
NO_VENDOR = ("This format has no microscope vendor's panel. Compare the values with the record of the "
             "acquisition software that took the image.")


class NotRead(Exception):
    """The file, the project or the image asked for cannot be read; the text is the plain reason."""


def default_settings():
    """The settings of a project that sets nothing: a GFP reporter, no identity channel."""
    return C.apply_identity(C.merge(C.DEFAULTS, {}), None)


def _settings_and_source(path, project):
    """(settings, the project's source that holds the file or None, where the markers come from)."""
    if project is None:
        return default_settings(), None, "built-in defaults"
    try:
        cfg = C.load(Path(project))
    except (Exception, SystemExit) as exc:
        text = str(exc) if isinstance(exc, (FileNotFoundError, SystemExit)) else f"{type(exc).__name__}: {exc}"
        raise NotRead(f"The project's config.json could not be read: {text[:300]}") from None
    source = None
    for src in cfg.get("sources") or []:
        try:
            path.resolve().relative_to(Path(src["path"]).resolve())
        except (ValueError, KeyError, TypeError, OSError):
            continue
        source = src
        break
    return cfg, source, f"project {Path(project).name}"


def _images(path, cfg, source):
    """The images of the file as Scan data reads them, or NotRead with the reason Scan data would note."""
    if not path.exists():
        raise NotRead(f"File not found: {path}")
    if path.is_dir():
        raise NotRead(f"{path} is a folder. Choose one microscope file.")
    role = R.file_role(path)
    tiff = path.suffix.lower() in (".tif", ".tiff")
    if role == R.NOT_USED and tiff and R.is_imagej_stack(path):
        if not (source and source.get("imagej_tiff")):
            raise NotRead(f"{path.name} is an ImageJ TIFF. It has no microscope metadata, so wormscore reads it only "
                          "inside a project whose source states the channel order (\"imagej_tiff\" in config.json). "
                          "Give that project with --project, with the file in its data folder.")
    elif role != "raw image":
        raise NotRead(f"Scan data does not read {path.name} as a microscope image. It lists it as: {role}.")
    try:
        found = R.images_in(path, cfg, source)
    except (Exception, SystemExit) as exc:      # as Scan data: the reader's reason, never a traceback
        raise NotRead(f"wormscore could not read {path.name}: {R._no_formula(str(exc)[:200]) or type(exc).__name__}") from None
    if not found:
        why = R.COLOUR_CAMERA if path.suffix.lower() == ".nd2" else R.NOT_USED
        raise NotRead(f"Scan data does not use {path.name}: {why}.")
    return found


def _role(role, cfg):
    if role == "MARKER":
        return f"reporter ({cfg['marker']['label']})"
    if role == "IDENTITY":
        return f"identity channel ({cfg['identity']['label']})" if cfg.get("identity") else "identity channel"
    if role.startswith("IDENTITY"):
        extras = cfg.get("identity_extra") or []
        k = int(role[8:]) - 2
        return f"identity channel {role[8:]}" + (f" ({extras[k]['label']})" if 0 <= k < len(extras) else "")
    return ROLE_WORDS.get(role, role.lower())


def scan_flags(img, cfg):
    """The flags Scan data would write for the image from its metadata, in its words (pipeline.inventory)."""
    roles, ident = img["channel_roles"], cfg.get("identity")
    flags = list(img["flags"])
    if R.pick(roles, "MARKER") is None:
        flags.append(f"{cfg['marker']['label']} channel UNRESOLVED")
    if ident and R.pick(roles, "IDENTITY") is None:
        flags.append(f"{ident['label']} identity channel UNRESOLVED")
    if R.pick(roles, "BF") is None:
        flags.append("no brightfield channel identified")
    return flags


AVERAGING = re.compile(r"\b(avg|average|kalman|integrat\w*|sum|line|frame)\b", re.I)


def settings_parts(settings):
    """The settings text of the reporter channel, piece by piece: [(what it is, value, which VALUES entry)].
    The pieces are named by their wording in readers.py; one that is not known is listed as it is."""
    out = []
    for piece in (p.strip() for p in settings.split(";")):
        mark = "'" if piece.startswith("'") else ""
        body = piece[len(mark):]
        if not body:
            continue
        laser = re.fullmatch(r"(\d+(?:\.\d+)?)\s*nm\s*@\s*(.+)", body)
        dimmed = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(\S*?)(?: attenuation (.+))?", body)
        gain = re.fullmatch(r"(?:(.+) )?gain (.+)", body, re.I)
        if laser:
            power, voltage = laser.group(2), None
            with_voltage = re.fullmatch(r"(.+?) HV (-?[\d.]+)", power)
            if with_voltage:
                power, voltage = with_voltage.groups()
            out.append((f"Laser {laser.group(1)} nm", mark + power, "laser"))
            if voltage:
                out.append(("Detector voltage (HV)", voltage, "gain"))
        elif dimmed and dimmed.group(2):
            out.append((f"Laser {dimmed.group(1)} {dimmed.group(2)}",
                        f"attenuation {dimmed.group(3)}" if dimmed.group(3) else "power not recorded", "laser"))
        elif body.lower().startswith(("light ", "lamp ")):
            out.append(("Light source", mark + body.split(" ", 1)[1], "light"))
        elif re.match(r"Laser Power\b", body, re.I):
            out.append(("Laser power", mark + body[11:].strip(), "laser"))
        elif gain and (gain.group(1) or "").lower() in ("digital", "em"):
            out.append((f"{gain.group(1)[0].upper()}{gain.group(1)[1:]} gain", gain.group(2), "gain"))
        elif gain:
            out.append(("Detector gain", mark + gain.group(2) + (f" ({gain.group(1)})" if gain.group(1) else ""), "gain"))
        elif re.fullmatch(r"(PMT|detector) (.+)", body):
            out.append(("Detector voltage", mark + body.split(" ", 1)[1], "gain"))
        elif re.match(r"HV ", body):
            out.append(("Detector voltage (HV)", mark + body[3:], "gain"))
        elif "HV" in body:
            out.append(("Detector voltage", mark + body, "gain"))
        elif body.lower().startswith(("offset ", "amplification ", "multiplier ")):
            name, rest = body.split(" ", 1)
            out.append(({"offset": "Detector offset"}.get(name.lower(), name.capitalize()), mark + rest, "gain"))
        elif body.lower().startswith("exposure "):
            out.append(("Exposure", mark + body.split(" ", 1)[1], "exposure"))
        elif AVERAGING.search(body):
            out.append(("Averaging", mark + body, "averaging"))
        else:
            out.append(("Other setting", mark + body, None))
    return out


def written_by(path):
    """The software a TIFF says it was written by (the OME Creator and the TIFF Software tag), or ""."""
    try:
        with R.tifffile.TiffFile(str(path)) as f:
            said = [m.group(1) for m in [re.search(r'<OME[^>]*\bCreator="([^"]*)"', f.ome_metadata or "")] if m]
            tag = f.pages[0].tags.get("Software")
            said.append(str(tag.value) if tag is not None else "")
            if f.is_imagej and (f.imagej_metadata or {}).get("ImageJ"):
                said.append(f"ImageJ {f.imagej_metadata['ImageJ']}")
    except Exception:
        return ""
    said = list(dict.fromkeys(" ".join(html.unescape(s).split())[:120] for s in said if s and s.strip()))
    return R._no_formula("; ".join(said))


LEICA_UNITS = {"HyD": "%", "PMT": "V"}


def _leica_unit(value, system):
    """A TCS SP8 gain with the unit LAS X shows it in: percent for a HyD (10 to 500 %, 100 % when Live
    starts), volts for a PMT (0 to 1250 V), from LAS X 3.1 notes of Kyoto University's imaging facility and
    UCSF's SP8 guide. The file holds the number alone. Other systems keep the number alone: a STELLARIS
    records its HyD S in photon counting, where LAS X turns the gain off (Leica's STELLARIS 5 LAS X basic
    guide) and the file still holds a number such as 2.5; no source was found for the others."""
    found = re.fullmatch(r"(.+) \((HyD|PMT)\)", value)
    if not found or not re.search(r"\bSP8\b", system or ""):
        return value
    return f"{found.group(1)} {LEICA_UNITS[found.group(2)]} ({found.group(2)})"


def _row(label, value, width=18):
    return f"  {label:<{width}}{value}".rstrip()


def describe(img, cfg, path):
    """The lines for one image."""
    roles, names = img["channel_roles"], img["channel_names"]
    mi = R.pick(roles, "MARKER")
    said = lambda v, unit="": f"{v}{unit}" if v else "not recorded in the file"
    lines = [f"{img['format']}, {img['location_in_file']}",
             _row("Microscope", said(img["system"])),
             _row("Acquired", said(img["acquired"])),
             _row("Objective", said(img["objective"]))]
    if img["zoom"]:
        lines.append(_row("Zoom", img["zoom"]))
    lines += [_row("Pixel size", said(img["pixel_um"], " um")),
              _row("Z-step", said(img["z_step_um"], " um") if img["n_z"] > 1 or img["z_step_um"] else "one plane, no z-step"),
              _row("Planes", str(img["n_z"])),
              _row("Image size", f"{img['width']} x {img['height']} pixels"
                   + (f", {img['n_t']} time points (only the first is used)" if img["n_t"] > 1 else "")),
              _row("Bit depth", str(img["bits"])),
              "", "Channels, in the file's order" + (" (Leica: named by colour table)" if img["format"] == "Leica LIF" else "")]
    width = max([18] + [len(n) + 2 for n in names if len(n) < 40])
    lines += [_row(n or "(no name)", _role(r, cfg), width) for n, r in zip(names, roles)]
    lines.append("")
    parts = settings_parts(img["settings"]) if mi is not None else []
    if img["format"] == "Leica LIF":
        parts = [(what, _leica_unit(value, img["system"]), key) if what == "Detector gain" else (what, value, key)
                 for what, value, key in parts]
    if mi is None:
        lines.append("Reporter settings: no single reporter channel was found, so none are read. If the reporter "
                     "is not the one named above, add --project with the project folder.")
    elif not parts:
        lines.append(f"Reporter settings ({names[mi] or 'channel ' + str(mi + 1)}): none recorded in the file.")
    else:
        lines.append(f"Reporter settings ({names[mi] or 'channel ' + str(mi + 1)})")
        wide = max(18, max(len(what) for what, _, _ in parts) + 2)
        lines += [_row(what, value, wide) for what, value, _ in parts]
        lines.append(_row("In inventory.csv", img["settings"], wide))
    flags = scan_flags(img, cfg)
    lines.append("")
    if flags:
        lines += ["Flags Scan data would raise"] + [f"  {flag}" for flag in flags]
    else:
        lines.append("Flags Scan data would raise: none")
    lines.append("")
    if img["format"] in VENDOR:
        software, how, where = VENDOR[img["format"]]
        shown = [v for v in VALUES[:6]] + list(dict.fromkeys(key for _, _, key in parts if key))
        lines += [f"Where to find these in {software}", f"  {how}"]
        if img["acquired"] and "acquired" in where:     # in the order of the values above
            lines.append(_row("Acquired", where["acquired"], 26))
        lines += [_row(NAMES[v], where[v], 26) for v in shown]
    else:
        wrote = written_by(path)
        lines.append(NO_VENDOR)
        if wrote:
            lines.append(_row("Written by", wrote).strip())
        if img["format"] == "ImageJ TIFF":
            lines.append("The channel order, and the microscope and objective if named, come from config.json, not from the file.")
    return lines


def check_reading(path, series=None, project=None):
    """The report for one file as text. series picks one image by the number in its list; without it the
    first is shown. project is a project folder whose config.json sets the markers. Raises NotRead with a
    plain reason if the file, the project or the image cannot be read."""
    path = Path(path)
    cfg, source, markers = _settings_and_source(path, project)
    images = _images(path, cfg, source)
    numbers = [img["_reader"][2] for img in images]
    if series is None:
        img = images[0]
    elif series in numbers:
        img = images[numbers.index(series)]
    else:
        raise NotRead(f"{path.name} has no image {series}. It holds " +
                      (f"images {min(numbers)} to {max(numbers)}." if len(images) > 1 else "one image."))
    ident = cfg.get("identity")
    lines = [f"wormscore {__version__}: reading of {R._no_formula(path.name)}",
             f"Reporter: {cfg['marker']['label']}" + (f"; identity channel: {ident['label']}" if ident else "") + f" ({markers}).",
             "Metadata only. Nothing was written.", ""]
    if len(images) > 1:
        lines.append(f"This file holds {len(images)} images:")
        lines += [f"  {n}  {i['location_in_file']}" for n, i in zip(numbers, images)]
        if series is None:
            lines.append(f"Shown below: {numbers[0]}. For another, add --series and its number.")
        lines.append("")
    lines += describe(img, cfg, path)
    lines += ["", "Flags that need the pixels or the project's other images (identical pixels, settings that differ "
              "from most images) are raised only by Scan data."]
    return "\n".join(lines)


def text_for(path, series=None, project=None):
    """check_reading, with the reason as the text when the file cannot be read."""
    try:
        return check_reading(path, series, project)
    except NotRead as exc:
        return str(exc)
