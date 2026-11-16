"""Scoring criteria as one file, to share between projects and labs (1.11.0).

A criteria file holds what the builder holds (neurons, calls, questions and their answers, the
definition and reference image of each answer, outcomes, the primary outcome, rules and unusable
reasons), a name, a version and an optional citation, with the reference images inside it. Its
checksum is the SHA-256 of the criteria themselves (the builder's checked form, with each reference
image's own SHA-256 in place of the picture), so the same criteria give the same checksum in every
project, whatever the file is called or when it was exported.

A project remembers where its criteria came from (scoring.criteria_source: name, version, citation
and the checksum at import). log.md records the checksum of the criteria in use with the form, and
the Methods draft names the source and says whether the criteria were changed after import. Like a
definition, the source is wording: it never decides whether the planned tests run.
"""
import base64
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from . import __version__
from . import scoreform as S

FORMAT = "wormscore-criteria"
FORMAT_VERSION = 1
SUFFIX = ".wormscore-criteria.json"
MAX_FILE_BYTES = 60 * 1024 * 1024
MAX_IMAGES = 100
CHECKSUM_LOG = "Scoring criteria checksum (SHA-256): "


def _images(model):
    """Every reference image path the criteria use, in a fixed order."""
    paths = [c.get("image") for c in model["calls"]]
    paths += [d.get("image") for q in model["questions"] for d in (q.get("defs") or {}).values()]
    return sorted({p for p in paths if p})


def checksum(model, project=None):
    """SHA-256 of the criteria (a model as the builder sends it): the checked form, without where
    it came from, with each reference image as the SHA-256 of its file."""
    m = S.clean(model)
    m.pop("source", None)
    images = {}
    for p in _images(m):
        f = S.image_file(project, p) if project else None
        images[p] = hashlib.sha256(f.read_bytes()).hexdigest() if f is not None and f.is_file() else "missing"
    text = json.dumps({"criteria": m, "images": images}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cfg_checksum(cfg, project=None):
    return checksum(S.to_model_cfg(cfg), project)


def _meta(text, what, limit, required=False):
    return S._text(text if isinstance(text, str) else "", what, required=required, limit=limit)


def export(project, model, name, version="", citation=""):
    """(file name, file text) of the criteria in the builder, with their reference images inside."""
    name = _meta(name, "The name", 80, required=True)
    version = _meta(version, "The version", 20)
    citation = _meta(citation, "The citation", 300)
    m = S.clean(model)
    m.pop("source", None)
    images = {}
    for p in _images(m):
        f = S.image_file(project, p)
        if f is None or not f.is_file():
            raise ValueError(f"The reference image {p} is not in this project's criteria folder. Add it again first.")
        images[p] = base64.b64encode(f.read_bytes()).decode("ascii")
    doc = {"format": FORMAT, "format_version": FORMAT_VERSION, "name": name, "version": version,
           "citation": citation, "exported": f"{dt.datetime.now():%Y-%m-%d %H:%M}",
           "wormscore": __version__, "sha256": checksum(m, project), "criteria": m, "images": images}
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:50] or "criteria"
    return f"{slug}{'-v' + re.sub(r'[^A-Za-z0-9.]+', '-', version) if version else ''}{SUFFIX}", \
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def write_export(project, folder, model, name, version="", citation=""):
    """Write the criteria file into folder (never over an existing file). Returns its path."""
    filename, text = export(project, model, name, version, citation)
    path = Path(folder) / filename
    n = 2
    while path.exists():
        path = Path(folder) / filename.replace(SUFFIX, f"-{n}{SUFFIX}")
        n += 1
    path.write_text(text, encoding="utf-8")
    return path


def load(project, text):
    """A criteria file read into this project: {"model", "source", "notes"}. Its reference images
    are copied into the project's criteria/ folder. Nothing in config.json changes until the
    builder is saved."""
    if not isinstance(text, str) or len(text) > MAX_FILE_BYTES:
        raise ValueError("That file is too large to be a criteria file.")
    try:
        doc = json.loads(text)
    except ValueError:
        raise ValueError("That file is not a wormscore criteria file (it is not JSON).")
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError(f"That file is not a wormscore criteria file. Criteria files end in {SUFFIX}.")
    if not isinstance(doc.get("format_version"), int) or doc["format_version"] > FORMAT_VERSION:
        raise ValueError("That criteria file was made by a newer wormscore. Update wormscore to read it.")
    images = doc.get("images") or {}
    if not isinstance(images, dict) or len(images) > MAX_IMAGES:
        raise ValueError("The criteria file's images could not be read.")
    model = doc.get("criteria")
    if not isinstance(model, dict):
        raise ValueError("The criteria file has no criteria in it.")
    # The images go in first, so the criteria point at files that exist in this project. Their
    # paths are their own checksums, so a picture changed inside the file gets a new path.
    moved = {}
    for p, data in images.items():
        moved[p] = S.save_image(project, data, p)
    for c in model.get("calls") or []:
        if isinstance(c, dict) and c.get("image"):
            c["image"] = moved.get(c["image"], c["image"])
    for q in model.get("questions") or []:
        for d in ((q.get("defs") or {}).values() if isinstance(q, dict) and isinstance(q.get("defs"), dict) else []):
            if isinstance(d, dict) and d.get("image"):
                d["image"] = moved.get(d["image"], d["image"])
    try:
        m = S.clean(model)
    except ValueError as exc:
        raise ValueError(f"The criteria in that file could not be used: {exc}")
    missing = [p for p in _images(m) if not (Path(project) / p).is_file()]
    if missing:
        raise ValueError(f"The criteria file is missing {len(missing)} reference image(s).")
    got = checksum(m, project)
    notes = []
    if doc.get("sha256") and doc["sha256"] != got:
        notes.append("The file was changed after it was exported: its checksum does not match its criteria. "
                     "The checksum of the criteria as they are now is recorded instead.")
    source = {"name": _meta(doc.get("name"), "The name", 80, required=True),
              "version": _meta(doc.get("version"), "The version", 20),
              "citation": _meta(doc.get("citation"), "The citation", 300), "sha256": got}
    m["source"] = source
    return {"model": m, "source": source, "notes": notes}


# ------------------------------------------------------------------ log and Methods

def log_lines(cfg, project=None):
    src = cfg["scoring"].get("criteria_source")
    line = CHECKSUM_LOG + cfg_checksum(cfg, project)
    if isinstance(src, dict) and src.get("name"):
        line += f" (imported from \"{src['name']}\"" + (f" version {src['version']}" if src.get("version") else "") \
            + (", unchanged since)" if src.get("sha256") == cfg_checksum(cfg, project) else ", changed since)")
    return [line]


def methods_sentence(cfg, project=None):
    """Which criteria were used, so a paper can say exactly: the source if they were imported, and
    the checksum either way."""
    now = cfg_checksum(cfg, project)
    src = cfg["scoring"].get("criteria_source")
    short = f"SHA-256 {now[:16]}, full value in log.md"
    if not (isinstance(src, dict) and src.get("name")):
        return f"The scoring criteria are identified by their checksum ({short}). "
    what = f"\"{src['name']}\"" + (f" version {src['version']}" if src.get("version") else "") \
        + (f" ({src['citation']})" if src.get("citation") else "")
    if src.get("sha256") == now:
        return f"The scoring criteria were {what}, used as published ({short}). "
    return (f"The scoring criteria were adapted from {what} [describe what was changed] ({short}; the "
            f"published criteria have SHA-256 {str(src.get('sha256', ''))[:16]}). ")
