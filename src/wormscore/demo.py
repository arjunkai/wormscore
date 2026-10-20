"""A demo project with simulated worms, so anyone can try wormscore without real data.

Each simulated image is an OME-TIFF z-stack of a worm head with two marker-positive
cell bodies (each with a faint dendrite), gut-like autofluorescence, camera offset and
noise, plus a brightfield channel. Controls have two normal cells; mutants have each
cell normal, dim or missing at random. The truth is written to demo_truth.csv so the
measurements can be checked after unblinding. No real data is involved.
"""
import csv
import json
from pathlib import Path

import numpy as np
import tifffile
from scipy import ndimage

PX_UM, Z_UM, NZ, SIZE = 0.25, 1.0, 16, 384
LEVELS = {"normal": 3000.0, "dim": 700.0, "missing": 0.0}


def _worm(rng, cells):
    """One simulated head: returns (marker stack, brightfield stack) as uint16 (z, y, x)."""
    yy, xx = np.mgrid[:SIZE, :SIZE].astype(float)
    angle = rng.uniform(0, np.pi)
    c, s = np.cos(angle), np.sin(angle)
    u = ((xx - SIZE / 2) * c + (yy - SIZE / 2) * s) * PX_UM     # um along the body (nose at negative u)
    v = (-(xx - SIZE / 2) * s + (yy - SIZE / 2) * c) * PX_UM    # um across the body
    half_width = np.clip(8 + 0.15 * (u + 48), 6, 22)            # head widens away from the nose
    body = (np.abs(v) < half_width) & (u > -45)
    zz = np.arange(NZ)[:, None, None].astype(float)

    marker = np.full((NZ, SIZE, SIZE), 100.0)                   # camera offset
    gut = (rng.random((SIZE, SIZE)) < 0.004) & body & (u > 15)
    gut_img = np.zeros((SIZE, SIZE))
    gut_img[gut] = rng.uniform(150, 400, gut.sum())
    gut_img = ndimage.gaussian_filter(gut_img, 1.5) * 9
    marker += gut_img[None] * np.exp(-((zz - NZ / 2) ** 2) / 30)
    marker += body[None] * 60.0                                  # tissue autofluorescence

    for k, level in enumerate(cells):
        cu = rng.uniform(-6, 2)                                  # um along the body
        cv = (-1) ** k * rng.uniform(3, 7)                       # um either side of the midline
        cz = rng.uniform(5, NZ - 6)
        r2 = (u - cu) ** 2 + (v - cv) ** 2
        soma = np.exp(-r2 / (2 * 1.4 ** 2))[None] * np.exp(-((zz - cz) ** 2) / (2 * 1.6 ** 2))
        marker += LEVELS[level] * soma
        if level != "missing":                                   # faint dendrite toward the nose
            along = (u < cu - 2) & (u > -44) & (np.abs(v - cv * (u + 44) / (cu + 44)) < 0.4)
            marker += 0.15 * LEVELS[level] * along[None] * np.exp(-((zz - cz) ** 2) / 8)

    marker = rng.poisson(np.clip(marker, 0, None)).astype(np.float64)
    bf = 1800 + 600 * body + rng.normal(0, 60, (SIZE, SIZE))
    bf = np.repeat(bf[None], NZ, axis=0) + rng.normal(0, 30, (NZ, SIZE, SIZE))
    return np.clip(marker, 0, 65535).astype(np.uint16), np.clip(bf, 0, 65535).astype(np.uint16)


def make_demo(project, n_per_group=10, seed=7, log=print):
    project = Path(project)
    data = project / "data"
    data.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    truth = []
    for group in ("control", "mutant"):
        for i in range(1, n_per_group + 1):
            if group == "control":
                cells = ["normal", "normal"]
            else:
                cells = list(rng.choice(["normal", "dim", "missing"], size=2, p=[0.4, 0.35, 0.25]))
            marker, bf = _worm(rng, cells)
            name = f"demo_{group}_{i:02d}.ome.tif"
            tifffile.imwrite(data / name, np.stack([marker, bf]), photometric="minisblack", ome=True,
                             metadata={"axes": "CZYX", "PhysicalSizeX": PX_UM, "PhysicalSizeY": PX_UM,
                                       "PhysicalSizeZ": Z_UM, "Channel": {"Name": ["GFP", "Brightfield"]}})
            truth.append({"file": name, "genotype": group, "cell_1": cells[0], "cell_2": cells[1]})
    (data / "notes.txt").write_text("Simulated data made by 'wormscore demo'. Not real images.\n")
    with open(project / "demo_truth.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(truth[0]))
        w.writeheader()
        w.writerows(truth)
    cfg = {
        "project_name": "Demo (simulated worms)",
        "sources": [{"name": "simulated", "path": str(data), "original_path": str(data),
                     "genotypes": [{"match": "*control*", "genotype": "control"},
                                   {"match": "*mutant*", "genotype": "mutant"}]}],
        "inclusion": {"min_z_extent_um": 9.0},
        "scoring": {
            "neurons": ["Neuron 1", "Neuron 2"],
            "neuron_questions": [{"id": "dendrite", "label": "dendrite visible?", "options": ["yes", "no", "can't tell"]}],
            "rules": ["Simulated data. Normal: bright cell body. Dim: faint but visible. Missing: nothing there.",
                      "Score each neuron separately; the animal is the unit for statistics."],
        },
        "unblinding": {"min_scored_worms": 5},
    }
    (project / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    log(f"Demo project with {2 * n_per_group} simulated worms written to {project}")
    return project
