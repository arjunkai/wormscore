"""Bio-Formats as an independent referee for what wormscore reads from microscope files.

For every raw image file under the folders given, this runs Bio-Formats' `showinf -nopix -omexml`
(no pixels are read) and compares its reading with wormscore's own (readers.images_in, which is what
Scan data writes to outputs/inventory.csv): image size, bit depth, pixel size, z-step, channel names and,
for the reporter channel wormscore picked, detector gain or voltage, laser attenuation and exposure.
Every difference is printed. Numbers are compared, not the wording of wormscore's settings text.

It is not part of the test suite: it needs Java, Bio-Formats and real files. See tools/README.md.

    python -I -X utf8 tools/bioformats_referee.py FOLDER [FOLDER ...] --bftools DIR [--out FILE]

Bio-Formats is not always right either (for example it reports 16 significant bits for 12-bit Zeiss
camera files), so a difference is something to look at in the microscope software, not a verdict.
"""
import argparse
import hashlib
import os
import re
import shutil
import site
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
GET_BFTOOLS = ("Download bftools.zip (free) from https://downloads.openmicroscopy.org/bio-formats/ "
               "(the 'artifacts' folder of a release), unzip it, and pass its folder with --bftools "
               "(or set BFTOOLS, or put it on PATH).")
GET_JAVA = ("Install a Java runtime, version 8 or later for Bio-Formats 8 (11 or later for Bio-Formats 9), "
            "for example the free Temurin build from https://adoptium.net, and make sure `java` is on PATH.")
UNITS_UM = {"nm": 1e-3, "µm": 1.0, "μm": 1.0, "um": 1.0, "micron": 1.0, "mm": 1e3, "cm": 1e4, "m": 1e6,
            "Å": 1e-4, "pm": 1e-6}
UNITS_S = {"s": 1.0, "ms": 1e-3, "µs": 1e-6, "μs": 1e-6, "us": 1e-6, "ns": 1e-9, "min": 60.0}
HINT = re.compile(r"(gain|voltage|attenuation|transmission|laser.{0,12}power|intensitydev|exposure|"
                  r"componentbitcount|bitcountrange|bpcsignificant|significantbits|validbitcounts)", re.I)


# ------------------------------------------------------------------ Java and Bio-Formats

def java_version():
    """Major Java version (8 for "1.8.0_x"), or None when java is not on PATH."""
    if not shutil.which("java"):
        return None
    out = subprocess.run(["java", "-version"], capture_output=True, text=True)
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not m:
        return None
    return int(m.group(2)) if m.group(1) == "1" and m.group(2) else int(m.group(1))


def find_bftools(arg):
    """The folder that holds bioformats_package.jar: --bftools, then BFTOOLS, then showinf on PATH."""
    for d in (arg, os.environ.get("BFTOOLS"), os.path.dirname(shutil.which("showinf") or "")):
        if d and (Path(d) / "bioformats_package.jar").is_file():
            return Path(d)
    return None


def check_tools(arg):
    problems = []
    major = java_version()
    if major is None:
        problems.append("Java was not found. " + GET_JAVA)
    bf = find_bftools(arg)
    if bf is None:
        problems.append("Bio-Formats command-line tools (showinf) were not found. " + GET_BFTOOLS)
    version = ""
    if not problems:
        out = subprocess.run(["java", "-cp", str(bf / "bioformats_package.jar"), "loci.formats.tools.ImageInfo",
                              "-version"], capture_output=True, text=True)
        m = re.search(r"Version:\s*(\S+)", out.stdout + out.stderr)
        version = m.group(1) if m else ""
        if not version:
            problems.append(f"Bio-Formats in {bf} did not start with Java {major}: "
                            f"{(out.stderr or out.stdout).strip()[:300]}\n{GET_JAVA}")
        elif int(version.split(".")[0]) >= 9 and major < 11:
            problems.append(f"Bio-Formats {version} needs Java 11 or later; this is Java {major}. {GET_JAVA} "
                            "Or use bftools 8.x, which runs on Java 8.")
    if problems:
        raise SystemExit("\n".join(problems))
    return bf, version


def showinf(path, bf, version, cache):
    """Text printed by `showinf -nopix -omexml` (run through the jar, which is what the showinf script does;
    this avoids quoting trouble with spaces in file names on Windows). Kept per file, size and time changed."""
    st = path.stat()
    tag = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{version}".encode()).hexdigest()[:20]
    kept = cache / f"{tag}.txt"
    if kept.is_file():
        return kept.read_text(encoding="utf-8")
    cmd = ["java", "-Xmx2g", "-Dfile.encoding=UTF-8", "-cp", str(bf / "bioformats_package.jar"),
           "loci.formats.tools.ImageInfo", "-nopix", "-omexml", "-novalid", "-no-upgrade", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    raw = out.stdout + b"\n" + out.stderr
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    cache.mkdir(parents=True, exist_ok=True)
    kept.write_text(text, encoding="utf-8")
    return text


# ------------------------------------------------------------------ Bio-Formats' reading

def _local(tag):
    return tag.split("}", 1)[-1]


def _kids(el, name):
    return [k for k in el if _local(k.tag) == name]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _um(value, unit):
    v = _num(value)
    if v is None:
        return None
    return v * UNITS_UM[unit] if unit in UNITS_UM else None


def parse_showinf(text):
    """(series, original metadata {key: value}, error). Each series is a dict of what Bio-Formats read."""
    m = re.search(r"<OME[\s>].*</OME>", text, re.S)
    if not m:
        # Bio-Formats prints a harmless log4j "ClassNotFoundException" at start; the real reason comes later
        said = [ln.strip() for ln in text.splitlines() if not ln.startswith("	") and "log4j" not in ln]
        at = next((k for k, ln in enumerate(said) if ln.startswith("Failure")), None)
        why = (" ".join(said[at:at + 2]) if at is not None
               else next((ln for ln in said if "Exception" in ln or "rror" in ln), "no OME-XML printed"))
        return [], {}, "TIMEOUT (10 minutes)" if text == "TIMEOUT" else why[:200]
    original = {}
    for ln in text[:m.start()].splitlines():
        k, sep, v = ln.partition(": ")
        if sep and v.strip() and not ln.startswith(("\t", "<")) and len(k) < 300:
            original[k.strip()] = v.strip()
    root = ET.fromstring(m.group(0))
    detectors, lasers = {}, {}
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "Detector":
            detectors[el.get("ID")] = el
        elif tag in ("Laser", "LightEmittingDiode", "Arc", "Filament", "GenericExcitationSource"):
            lasers[el.get("ID")] = el
    series = []
    for img in _kids(root, "Image"):
        px = _kids(img, "Pixels")[0]
        chans = []
        for c in _kids(px, "Channel"):
            ch = {"name": c.get("Name") or "", "fluor": c.get("Fluor") or "", "gain": [], "lights": []}
            for ds in _kids(c, "DetectorSettings"):
                det = detectors.get(ds.get("ID"))
                for el, what in ((ds, "DetectorSettings"), (det, "Detector")):
                    for attr in ("Gain", "Voltage", "AmplificationGain"):
                        v = _num(el.get(attr)) if el is not None else None
                        if v is not None:
                            ch["gain"].append((f"{what} {attr}", v))
            for ls in _kids(c, "LightSourceSettings"):
                src = lasers.get(ls.get("ID"))
                wl = _num(ls.get("Wavelength")) or (_num(src.get("Wavelength")) if src is not None else None)
                ch["lights"].append({"wavelength": wl, "attenuation": _num(ls.get("Attenuation")),
                                     "power": _num(src.get("Power")) if src is not None else None})
            chans.append(ch)
        exposure = {}
        for p in _kids(px, "Plane"):
            v = _num(p.get("ExposureTime"))
            if v is not None:
                unit = p.get("ExposureTimeUnit", "s")
                exposure.setdefault(int(p.get("TheC", 0)), set()).add(round(v * UNITS_S.get(unit, 1.0), 9))
        series.append({
            "name": img.get("Name") or "", "x": int(px.get("SizeX")), "y": int(px.get("SizeY")),
            "z": int(px.get("SizeZ")), "t": int(px.get("SizeT")), "c": len(chans) or int(px.get("SizeC")),
            "bits": int(px.get("SignificantBits")) if px.get("SignificantBits") else None, "type": px.get("Type"),
            "pixel_um": _um(px.get("PhysicalSizeX"), px.get("PhysicalSizeXUnit", "µm")),
            "pixel_unit": px.get("PhysicalSizeXUnit", "µm"), "has_pixel": px.get("PhysicalSizeX") is not None,
            "z_um": _um(px.get("PhysicalSizeZ"), px.get("PhysicalSizeZUnit", "µm")),
            "channels": chans, "exposure_s": exposure,
        })
    return series, original, ""


# ------------------------------------------------------------------ comparison

def numbers(text):
    return [float(x) for x in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", text or "")]


def near(a, b, rel=0.01, absolute=0.0):
    return abs(a - b) <= max(absolute, rel * max(abs(a), abs(b)))


def match_series(images, series):
    """The Bio-Formats series for each wormscore image: by position when both list the same number,
    otherwise the first unused series of the same size (and, if any, the same name)."""
    if len(images) == len(series):
        return list(series)
    out, used = [], set()
    for im in images:
        size = (im["width"], im["height"], im["n_z"])
        hits = [k for k, s in enumerate(series) if k not in used and (s["x"], s["y"], s["z"]) == size]
        named = [k for k in hits if series[k]["name"] and series[k]["name"] in im["location_in_file"]]
        k = (named or hits or [None])[0]
        used.add(k)
        out.append(series[k] if k is not None else None)
    return out


def compare(im, s, original, fmt):
    """(differences, notes) for one image. A difference: both read a value and they disagree, or Bio-Formats
    reads a brightness setting that is not in wormscore's settings text. A note: nothing to compare with."""
    diffs, notes = [], []

    def diff(kind, text):
        diffs.append((kind, text))

    for label, a, b in (("width", im["width"], s["x"]), ("height", im["height"], s["y"]),
                        ("planes", im["n_z"], s["z"]), ("time points", im["n_t"], s["t"]),
                        ("channels", len(im["channel_roles"]), s["c"])):
        if a != b:
            diff("size", f"{label}: Bio-Formats {b}, wormscore {a}")
    if s["bits"] is None:
        notes.append("bit depth: Bio-Formats gives none")
    elif s["bits"] != im["bits"]:
        diff("bit depth", f"bit depth: Bio-Formats {s['bits']}, wormscore {im['bits']}")
    bit_hints = sorted({f"{k.split('|')[-1]}={v}" for k, v in original.items()
                        if re.search(r"componentbitcount|bitcountrange|bpcsignificant|validbitcounts", k, re.I)})
    if bit_hints and any(str(im["bits"]) != h.split("=")[1] or (s["bits"] and str(s["bits"]) != h.split("=")[1])
                         for h in bit_hints):
        notes.append("bit depth in the file's own metadata: " + ", ".join(bit_hints[:6]))
    if not s["has_pixel"]:
        if im["pixel_um"]:
            notes.append(f"pixel size: Bio-Formats gives none, wormscore {im['pixel_um']} um")
    elif s["pixel_um"] is None:
        notes.append(f"pixel size: Bio-Formats unit {s['pixel_unit']!r} not understood by this script")
    elif not near(s["pixel_um"], im["pixel_um"], rel=0.005, absolute=0.0002):
        diff("pixel size", f"pixel size: Bio-Formats {s['pixel_um']:.5g} um, wormscore {im['pixel_um']:.5g} um")
    if s["z"] > 1 or im["n_z"] > 1:
        if s["z_um"] is None:
            if im["z_step_um"]:
                notes.append(f"z-step: Bio-Formats gives none, wormscore {im['z_step_um']} um")
        elif not near(abs(s["z_um"]), im["z_step_um"], rel=0.005, absolute=0.002):
            diff("z-step", f"z-step: Bio-Formats {abs(s['z_um']):.5g} um, wormscore {im['z_step_um']:.5g} um")
    theirs, mine = [c["name"] for c in s["channels"]], list(im["channel_names"])
    same = len(theirs) == len(mine) and all(
        a.strip().lower() == b.strip().lower() or (a.strip() and b.strip() and
                                                    (a.strip().lower() in b.lower() or b.strip().lower() in a.lower()))
        for a, b in zip(theirs, mine))
    if not same and fmt == "Leica LIF":
        # by design: for Leica, wormscore lists each channel's colour table (LUT), which the file always has
        notes.append(f"channel names: wormscore lists the Leica colour tables {mine}; Bio-Formats names {theirs}")
    elif not same:
        diff("channel names", f"channel names: Bio-Formats {theirs}, wormscore {mine}")

    roles = im["channel_roles"]
    mi = roles.index("MARKER") if roles.count("MARKER") == 1 else None
    if mi is None or mi >= len(s["channels"]):
        notes.append(f"no single reporter channel picked by wormscore (roles {'/'.join(roles)}): gain, laser and "
                     "exposure not compared (use --project with a project whose markers fit these files)")
        for k, c in enumerate(s["channels"]):       # what Bio-Formats has, so it can be read by eye
            parts = [f"{w} {v:g}" for w, v in c["gain"]]
            parts += [f"{l['wavelength'] or '?':g} nm at {100 * (1 - l['attenuation']):.3g}%" if l["wavelength"]
                      else f"light at {100 * (1 - l['attenuation']):.3g}%" for l in c["lights"] if l["attenuation"] is not None]
            parts += [f"exposure {e * 1000:g} ms" for e in sorted(s["exposure_s"].get(k, ()))[:3]]
            if parts:
                notes.append(f"Bio-Formats channel {k} {c['name'] or c['fluor']!r}: " + ", ".join(parts))
        return diffs, notes
    ch, text = s["channels"][mi], im["settings"]
    have = numbers(text)

    # detector gain or voltage
    gains = list(ch["gain"])
    if fmt == "Zeiss CZI" and f"Information|Image|Channel|Gain #{mi + 1}" in original:
        v = _num(original[f"Information|Image|Channel|Gain #{mi + 1}"])
        if v is not None:
            gains.append(("file metadata Channel Gain", v))
    if fmt == "Nikon ND2":
        for k, v in original.items():
            if k.strip() in ("Gain", "Multiplier") and numbers(v):
                gains.append((f"file metadata {k.strip()}", numbers(v)[0]))
    real = [(w, v) for w, v in gains if v > 0 and v != 1.0] or gains     # 1, 0 and -1 are often placeholders
    if real and not any(near(v, h, rel=0.002, absolute=0.05) for _, v in real for h in have):
        diff("detector gain", "detector gain: Bio-Formats " + ", ".join(f"{v:g} ({w})" for w, v in real)
             + f"; not in wormscore settings {text!r}")
    elif not gains:
        notes.append("detector gain: Bio-Formats gives none")

    # laser lines: attenuation 0.88 is 12% transmission
    lights = [l for l in ch["lights"] if l["attenuation"] is not None]
    on = [l for l in lights if l["attenuation"] < 0.9999]
    if fmt == "Zeiss CZI" and not lights:
        atts = sorted({_num(v) for k, v in original.items() if k.startswith("Information|Image|Channel|Attenuation")}
                      - {None})
        pct = [100 * (1 - a) for a in atts if a < 0.9999]
        mine_pct = [float(x) for x in re.findall(r"@\s*(-?\d+(?:\.\d+)?)\s*%", text)]
        if pct and not mine_pct:
            diff("laser power", "laser power: the file's channels list attenuation " + ", ".join(f"{a:g}" for a in atts)
                 + " (" + ", ".join(f"{p:.3g}%" for p in pct) + f"); none in wormscore settings {text!r}")
        for p in mine_pct:
            if pct and not any(near(p, q, absolute=0.05) for q in pct):
                diff("laser power", f"laser power: wormscore {p:g}% is not one of the file's "
                     + ", ".join(f"{q:.3g}%" for q in pct))
        if not atts:
            notes.append("laser power: Bio-Formats gives none")
    elif on:
        for l in on:
            p = 100 * (1 - l["attenuation"])
            if not any(near(p, h, absolute=0.51) for h in have):      # Bio-Formats rounds attenuation to 0.01
                wl = f"{l['wavelength']:g} nm " if l["wavelength"] else ""
                diff("laser power", f"laser power: Bio-Formats {wl}at {p:.3g}% (attenuation {l['attenuation']:g}); "
                     f"not in wormscore settings {text!r}")
    elif not lights:
        notes.append("laser power: Bio-Formats gives none")

    # exposure
    exp = sorted(s["exposure_s"].get(mi, ()))
    if exp:
        if not any(near(e, h, rel=0.002) or near(e * 1000, h, rel=0.002) for e in exp for h in have):
            diff("exposure", "exposure: Bio-Formats " + ", ".join(f"{e * 1000:g} ms" for e in exp[:4])
                 + f"; not in wormscore settings {text!r}")
    hints = sorted(f"{k}: {v}" for k, v in original.items() if HINT.search(k) and len(v) < 40
                   and not re.search(r"bitcount|bpcsignificant|significantbits", k, re.I))
    if any(k in ("detector gain", "laser power", "exposure") for k, _ in diffs) and hints:
        notes.append(f"brightness entries in the file's own metadata ({len(hints)}), first few: " + "; ".join(hints[:4]))
    return diffs, notes


# ------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="folders (searched in depth) or files")
    ap.add_argument("--bftools", help="folder with bioformats_package.jar and showinf (or set BFTOOLS)")
    ap.add_argument("--cache", default=str(Path.home() / ".cache" / "wormscore-referee"),
                    help="where showinf's output is kept between runs (outside the repository)")
    ap.add_argument("--src", default=str(HERE.parent / "src"),
                    help="the wormscore source to check (default: this checkout's src)")
    ap.add_argument("--project", help="a wormscore project folder whose config.json sets the markers "
                                      "(default: the built-in defaults, a GFP reporter)")
    ap.add_argument("--out", help="also write the report to this file")
    ap.add_argument("--max-mb", type=float, default=0, help="skip files larger than this (0: none)")
    args = ap.parse_args(argv)

    bf, version = check_tools(args.bftools)
    # python -I ignores PYTHONPATH and the user's site-packages (so nothing next to the sample files is
    # imported); both are added back here, by path
    site.addsitedir(site.getusersitepackages())
    sys.path.insert(0, str(Path(args.src).resolve()))
    import wormscore
    from wormscore import config as C
    from wormscore import readers as R
    cfg = C.load(Path(args.project)) if args.project else C.apply_identity(C.merge(C.DEFAULTS, {}), None)

    lines = []

    def say(text=""):
        print(text, flush=True)
        lines.append(text)

    say(f"Bio-Formats {version} (Java {java_version()}) against wormscore {wormscore.__version__} "
        f"({Path(wormscore.__file__).parent})")
    say(f"reporter: {cfg['marker']['label']}" + ("" if args.project else " (built-in defaults; --project uses a project's markers)"))
    files = []
    for a in args.paths:
        p = Path(a)
        found = sorted(q for q in p.rglob("*") if q.is_file()) if p.is_dir() else [p]
        files += [(q, p if p.is_dir() else p.parent) for q in found if R.file_role(q) == "raw image"]
    tally, n_images, n_clean = {}, 0, 0
    for path, root in files:
        rel = path.relative_to(root).as_posix()
        say(f"\n== {rel}  ({path.stat().st_size / 2**20:.1f} MB)")
        if args.max_mb and path.stat().st_size > args.max_mb * 2**20:
            say("  skipped (larger than --max-mb)")
            continue
        try:
            images, err = R.images_in(path, cfg), ""
        except (Exception, SystemExit) as exc:
            images, err = [], f"{type(exc).__name__}: {str(exc)[:160]}"
        series, original, bf_err = parse_showinf(showinf(path, bf, version, Path(args.cache)))
        fmt = images[0]["format"] if images else path.suffix.lower()
        if err or bf_err or not images:
            ws = f"could not read ({err})" if err else f"{len(images)} image(s)" if images else "no image (not used)"
            theirs = f"could not read ({bf_err})" if bf_err else f"{len(series)} series"
            if bool(err or not images) != bool(bf_err):
                tally[(fmt, "readable")] = tally.get((fmt, "readable"), 0) + 1
                say(f"  DIFF readable: wormscore {ws}; Bio-Formats {theirs}")
            else:
                say(f"  both: wormscore {ws}; Bio-Formats {theirs}")
            continue
        if len(images) != len(series):
            tally[(fmt, "series count")] = tally.get((fmt, "series count"), 0) + 1
            say(f"  DIFF series count: Bio-Formats {len(series)}, wormscore {len(images)} (matched by size and name)")
        for im, s in zip(images, match_series(images, series)):
            n_images += 1
            roles = im["channel_roles"]
            mi = roles.index("MARKER") if roles.count("MARKER") == 1 else None
            say(f"  {im['location_in_file']}: {im['width']}x{im['height']}x{im['n_z']}, bits {im['bits']}, "
                f"pixel {im['pixel_um']} um, z-step {im['z_step_um']} um, roles {'/'.join(roles)}, "
                f"reporter {'none' if mi is None else repr(im['channel_names'][mi])}, settings {im['settings']!r}"
                + (f", flags {im['flags']}" if im["flags"] else ""))
            if s is None:
                tally[(fmt, "series count")] = tally.get((fmt, "series count"), 0) + 1
                say("    DIFF no Bio-Formats series of this size")
                continue
            diffs, notes = compare(im, s, original, fmt)
            for kind, text in diffs:
                tally[(fmt, kind)] = tally.get((fmt, kind), 0) + 1
                say(f"    DIFF {text}")
            for text in notes:
                say(f"    note {text}")
            n_clean += not diffs
    say(f"\n== Summary: {len(files)} files, {n_images} images compared, {n_clean} without a difference")
    for (fmt, kind), n in sorted(tally.items()):
        say(f"  {fmt}: {kind}: {n}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
