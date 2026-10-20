"""Project configuration.

A project is a folder holding config.json plus everything wormscore writes
(outputs/, blind/, key/, scoring/, log.md). Every analysis parameter lives in
config.json; anything left out falls back to DEFAULTS below. Each run copies the
full, merged configuration into log.md so the analysis can be repeated.
"""
import copy
import fnmatch
import json
from pathlib import Path

DEFAULTS = {
    "project_name": "Untitled project",

    # Folders of images. path = where the files are read from (ideally a read-only copy);
    # original_path = where they first came from, recorded in the key for traceability.
    # genotypes: first matching pattern (on the path relative to the source folder) wins.
    "sources": [],

    # Which channel is the reporter. Identified from metadata only, never guessed.
    "marker": {
        "label": "GFP",
        "excitation_nm": [488],
        "emission_band_start_nm": [490, 530],
        "dye_names": ["EGFP", "GFP", "Leica/EGFP"],
        "channel_name_prefixes": ["GFP", "EGFP"],
        "leica_lut": "Green",
    },
    "brightfield_channel_names": ["BF", "Brightfield", "DIC", "Trans", "T-PMT", "TL"],

    "inclusion": {
        "min_objective_magnification": 40,
        "min_z_extent_um": 9.0,
        "max_time_points": 1,
        "same_field_tolerance_um": 5.0,
    },
    # For byte-identical copies of an image, keep the one whose path contains the first
    # matching text here; otherwise the shortest path.
    "duplicate_preference": [],

    "display": {
        "display_um_per_px": 0.20,
        "frame_px": 940,
        "brightfield_contrast_percentiles": [1, 99],
    },
    # Highest value each microscope can record, by system name. Missing systems use the
    # bit depth from the file metadata (255 for 8-bit, 65535 for 16-bit).
    "saturation_value": {},

    "measurement": {
        "median_filter_px": 3,
        "smooth_um": 1.5,
        "min_separation_um": 6.0,
        "spot_ring_um": [6.0, 10.0],
        "max_radius_um": 8.0,
        "min_object_area_um2": 4.0,
        "n_candidates": 4,
        "background_ring_gap_um": 2.0,
        "background_ring_width_um": 4.0,
        "streak_jump_fraction": 0.04,
        "other_channel_qc": True,
    },

    "scoring": {
        "neurons": ["Neuron 1", "Neuron 2"],
        "calls": ["normal", "dim", "missing"],
        "defect_calls": ["dim", "missing"],
        "sides": ["left", "right"],
        "count_question": {"label": "Marker-positive cell bodies seen", "options": ["0", "1", "2", "3+"]},
        "neuron_questions": [],
        "unusable_reasons": [
            "wrong region of the animal",
            "out of focus or moving",
            "wrong stage",
            "looks like a different marker",
            "damaged animal",
            "other (see notes)",
        ],
        "rules": [
            "Normal: cell body clearly visible at the expected brightness.",
            "Dim: visible but weaker than controls imaged at the same settings.",
            "Missing: no cell body in any plane.",
            "Score each neuron separately. The animal is the unit for statistics.",
        ],
    },

    "unblinding": {"min_scored_worms": 30},
}


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def load(project):
    path = Path(project) / "config.json"
    if not path.exists():
        raise FileNotFoundError(f"No config.json in {project}. Run 'wormscore init {project}' first.")
    with open(path, encoding="utf-8") as fh:
        return merge(DEFAULTS, json.load(fh))


def genotype_for(source, rel_path):
    rel = rel_path.replace("\\", "/")
    for rule in source.get("genotypes", []):
        if fnmatch.fnmatch(rel, rule["match"]) or fnmatch.fnmatch(Path(rel).name, rule["match"]):
            return rule["genotype"]
    return source.get("default_genotype", "UNKNOWN")


def template(name="My project"):
    return {
        "project_name": name,
        "sources": [
            {
                "name": "my images",
                "path": "C:/path/to/read-only/copy/of/images",
                "original_path": "where the files first came from (for the record)",
                "genotypes": [
                    {"match": "*control*", "genotype": "control"},
                    {"match": "*mutant*", "genotype": "mutant"},
                ],
                "default_genotype": "UNKNOWN",
            }
        ],
        "marker": DEFAULTS["marker"],
        "scoring": {
            "neurons": DEFAULTS["scoring"]["neurons"],
            "calls": DEFAULTS["scoring"]["calls"],
            "defect_calls": DEFAULTS["scoring"]["defect_calls"],
        },
    }
