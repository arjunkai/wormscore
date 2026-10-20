"""A demo project with real worm images, downloaded at run time (never stored in the repo).

The images are four L1 heads from Sun and Hobert (2021), shared on Zenodo under CC BY 4.0:
Zeiss LSM 880 stacks with an endogenous GFP reporter, four NeuroPAL fluorophores and a
transmitted-light channel. Two reporters stand in for two "genotypes", so the whole workflow
(inventory, blinding, scoring, unblinding) can be tried on real data. Each file is checked
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
    "nlp-45 L1 1 Head.czi": ("d6a1df9dbcabc9911ec2307a27656bdc", 36792352),
    "nlp-45 L1 2 Head.czi": ("afe44fe222d2cbb1003e1a1b75a251a4", 30097440),
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
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as fh:
        shutil.copyfileobj(r, fh, 1 << 20)
    if _md5(part) != md5:
        part.unlink()
        raise RuntimeError(f"{name}: download does not match the MD5 on the Zenodo record; try again")
    part.replace(dst)
    return dst


def make_real_demo(project, log=print, fetch=download, cache=None):
    project = Path(project)
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
    cfg = {
        "project_name": "Demo (real worms, Sun and Hobert 2021)",
        "sources": [{"name": "Sun and Hobert 2021 (Zenodo)", "path": str(data), "original_path": RECORD,
                     "note": "collected by others, CC BY 4.0",
                     "genotypes": [{"match": "flp-17*", "genotype": "reporter A (flp-17::GFP)"},
                                   {"match": "nlp-45*", "genotype": "reporter B (nlp-45::GFP)"}]}],
        "scoring": {
            "neurons": ["Cell 1", "Cell 2"],
            "neuron_questions": [{"id": "process", "label": "process visible?", "options": ["yes", "no", "can't tell"]}],
            "rules": ["Real L1 heads with an endogenous GFP reporter (practice only; both groups are normal animals).",
                      "Pick the two brightest GFP cell bodies and score each one normal, dim or missing.",
                      "Score each cell separately; the animal is the unit for statistics."],
        },
        "unblinding": {"min_scored_worms": 2},
    }
    (project / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    log(CREDIT)
    log(f"Real-worm demo project written to {project}")
    return project
