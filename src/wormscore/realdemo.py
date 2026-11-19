"""A demo project with real worm images, downloaded at run time (never stored in the repo).

The images are four heads from Sun and Hobert (2021), shared on Zenodo under CC BY 4.0: Zeiss
LSM 880 stacks with the flp-17::GFP reporter, four NeuroPAL fluorophores and a transmitted-light
channel. flp-17 marks the BAG pair of sensory neurons, so every image shows the two cells the form
asks about. Two L1 and two L2 larvae stand in for two "genotypes", so the whole workflow (inventory,
blinding, scoring, unblinding) can be tried on real data. (Until 1.26.18 the demo compared flp-17 with
nlp-45, a reporter of other neurons, and asked for the two brightest GFP cells.) Each file is checked
against its MD5 from the Zenodo record and kept in a cache outside the project, so a second
demo does not download it again.
"""
import hashlib
import json
import os
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

RECORD = "https://zenodo.org/records/5602160"
FILES = {  # name on Zenodo: (md5, bytes)
    "flp-17 L1 Head 1.czi": ("c9723275ef3b75aee867a6be93cb33cd", 31553824),
    "flp-17 L1 Head 2.czi": ("46b043a5adf441ab59b4bb37539ff128", 52442656),
    "flp-17 L2 Head 1.czi": ("e41b23afb5316f3d44ace83a3a0cd474", 69824864),
    "flp-17 L2 Head 2.czi": ("47296d3fadcc02bf733eaed992617e4f", 77190144),
}
CREDIT = (
    "Images: Sun H, Hobert O (2021). Temporal transitions in the post-mitotic nervous system of "
    "Caenorhabditis elegans. Nature 600:93-99. doi:10.1038/s41586-021-04071-4. "
    "Data: Zenodo, doi:10.5281/zenodo.5602160, licensed CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/). Files are unchanged."
)


def cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
    return Path(base) / "wormscore" / "demo_cache"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(name, md5, size, cache, log=print):
    """Return the cached file, downloading it first if it is missing or does not match its MD5."""
    cache.mkdir(parents=True, exist_ok=True)
    dst = cache / name
    if dst.exists() and dst.stat().st_size == size and _md5(dst) == md5:
        return dst
    url = f"{RECORD}/files/{urllib.parse.quote(name)}?download=1"
    log(f"Downloading {name} ({size / 1e6:.0f} MB)")
    part = dst.with_name(dst.name + ".part")
    from .update import https_kwargs
    # 1.26.15: a connection that stalls or drops is tried three times, then reported in plain words. One
    # timeout used to end the demo with a Python error (seen on GitHub's Mac while fetching the fourth file).
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(url, timeout=60, **https_kwargs()) as r, open(part, "wb") as fh:
                shutil.copyfileobj(r, fh, 1 << 20)
            if part.stat().st_size != size:     # the connection closed early without an error (1.26.16)
                raise OSError(f"{part.stat().st_size / 1e6:.0f} of {size / 1e6:.0f} MB arrived")
            break
        except OSError as exc:          # no connection, a timeout, a dropped download
            part.unlink(missing_ok=True)
            if attempt == 3:
                raise SystemExit(f"Could not download {name} from Zenodo ({getattr(exc, 'reason', None) or exc}). "
                                 "Check the internet connection and try the demo again; files already "
                                 "downloaded are kept.") from None
            log(f"  the download stopped ({getattr(exc, 'reason', None) or exc}); trying again")
    if _md5(part) != md5:
        part.unlink()
        raise RuntimeError(f"{name}: download does not match the MD5 on the Zenodo record; try again")
    part.replace(dst)
    return dst


def make_real_demo(project, log=print, fetch=download, cache=None):
    project = Path(project)
    from .demo import refuse_existing_project
    refuse_existing_project(project)
    data = project / "data"
    data.mkdir(parents=True, exist_ok=True)
    cache = Path(cache) if cache else cache_dir()
    log(f"Real-worm demo: {len(FILES)} images, about {sum(s for _, s in FILES.values()) / 1e6:.0f} MB, "
        f"cached in {cache}")
    for name, (md5, size) in FILES.items():
        src = fetch(name, md5, size, cache, log)
        if not (data / name).exists():
            shutil.copy2(src, data / name)
    (data / "ATTRIBUTION.txt").write_text(CREDIT + "\n", encoding="utf-8")
    cfg = demo_config(data)
    (project / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    log(CREDIT)
    log(f"Real-worm demo project written to {project}")
    return project


def demo_config(data):
    """The demo's config.json: the BAG pair scored in L1 and L2 heads of flp-17::GFP."""
    return {
        "project_name": "Demo (real worms, Sun and Hobert 2021)",
        "sources": [{"name": "Sun and Hobert 2021 (Zenodo)", "path": str(data), "original_path": RECORD,
                     "note": "collected by others, CC BY 4.0",
                     "genotypes": [{"match": "flp-17 L1*", "genotype": "L1 larvae (flp-17::GFP)"},
                                   {"match": "flp-17 L2*", "genotype": "L2 larvae (flp-17::GFP)"}]}],
        "scoring": {
            "neurons": ["BAG 1", "BAG 2"],
            "neuron_questions": [{"id": "process", "label": "dendrite to the nose visible?",
                                  "options": ["yes", "no", "can't tell"]}],
            "rules": ["Real L1 and L2 heads with flp-17::GFP, which marks the BAG pair of sensory neurons (practice "
                      "only; all are normal animals, and the stage stands in for the genotype). L2 heads are "
                      "larger, so here the group can be guessed from the size: blinding is only practised.",
                      "Score each BAG cell body normal, dim or missing. The two lie close together behind the "
                      "nerve ring: step through the planes to tell them apart.",
                      "Score each cell separately; the animal is the unit for statistics."],
        },
        "unblinding": {"min_scored_worms": 2},
        # 1.23.0: the four NeuroPAL colours shown with the reporter (keys 1, 2 and 3 switch); suggested cells
        # are found on the GFP reporter, since the task is to pick GFP cells
        "identity": {"preset": "NeuroPAL", "spots_from": "reporter"},
        # The frame every image is drawn in is 188 um wide by default (940 x 0.20 um). These heads are 110 to
        # 164 um wide, so a 166 um frame shows them at one scale, the widest at the full width of the window.
        "display": {"frame_px": 830},
    }
