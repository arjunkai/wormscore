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
    # exclude (optional): [{"match": pattern, "reason": text}]; matching images are left out of
    # the blind set and listed in outputs/excluded.csv with the reason.
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

    # reference_genotype: the control that each other genotype is tested against (Fisher's
    # exact, within each microscope). Empty: every pair of genotypes is tested.
    # question_outcomes (optional): tables built from the per-neuron questions, for example
    #   {"name": "process defect", "questions": ["q1", "q2"], "defect_answers": ["no"],
    #    "unclear_answers": ["can't tell"], "unclear": "exclude_neuron", "not_visible_calls": ["missing"]}
    # A neuron is defective if any listed question has a defect answer. Neurons whose call is in
    # not_visible_calls are not assessed; animals with no assessed neuron are left out.
    # unclear: "exclude_neuron" drops a neuron with an unclear answer, "exclude_animal" drops the animal.
    # interaction (optional): {"single_a": genotype, "single_b": genotype, "double": genotype,
    #   "model": "product" | "additive" | "min", "outcome": "calls" or a question_outcomes name}.
    # Chosen before scoring; prepare records it in log.md and unblinding tests only that model.
    "unblinding": {"min_scored_worms": 30, "reference_genotype": "", "question_outcomes": [],
                   "interaction": {}},

    # Used only for the Methods text written at unblinding. Left empty, it says [name].
    # imager_knew_genotype: "yes", "no" or "" (unknown).
    "blinding": {"coded_by": "", "scored_by": "", "imager_knew_genotype": ""},
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


def matches(pattern, rel_path):
    rel = rel_path.replace("\\", "/")
    return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern)


def genotype_for(source, rel_path):
    for rule in source.get("genotypes", []):
        if matches(rule["match"], rel_path):
            return rule["genotype"]
    return source.get("default_genotype", "UNKNOWN")


def exclude_rule_for(cfg, source_name, rel_path):
    """The config exclude rule matching this image, or None."""
    for src in cfg["sources"]:
        if src.get("name") == source_name:
            for rule in src.get("exclude", []):
                if matches(rule["match"], rel_path):
                    return rule
    return None


INTERACTION_MODELS = ("product", "additive", "min")


def interaction(cfg):
    """The interaction test settings with defaults filled in, or {} if none is configured."""
    ia = cfg["unblinding"]["interaction"]
    return {"model": "product", "outcome": "calls", **ia} if ia else {}


def check(cfg):
    """Stop with a plain message if the optional results settings do not fit the scoring setup."""
    un, sc = cfg["unblinding"], cfg["scoring"]
    questions = {q["id"]: q for q in sc["neuron_questions"]}
    names = []
    for oc in un["question_outcomes"]:
        name = oc.get("name", "")
        if not name or name in names or name == "calls":
            raise SystemExit(f"question_outcomes: each entry needs a unique 'name' other than 'calls' (got {name!r}).")
        names.append(name)
        if not oc.get("questions") or not oc.get("defect_answers"):
            raise SystemExit(f"question_outcomes '{name}': set 'questions' and 'defect_answers'.")
        for qid in oc["questions"]:
            if qid not in questions:
                raise SystemExit(f"question_outcomes '{name}': no neuron question with id {qid!r} in scoring.neuron_questions.")
            unknown = set(oc["defect_answers"]) | set(oc.get("unclear_answers", []))
            unknown -= set(questions[qid]["options"])
            if unknown:
                raise SystemExit(f"question_outcomes '{name}': {sorted(unknown)} are not options of question {qid!r}.")
        if oc.get("unclear", "exclude_neuron") not in ("exclude_neuron", "exclude_animal"):
            raise SystemExit(f"question_outcomes '{name}': 'unclear' must be exclude_neuron or exclude_animal.")
    ia = un["interaction"]
    if ia:
        for k in ("single_a", "single_b", "double"):
            if not ia.get(k):
                raise SystemExit(f"interaction: set '{k}' to a genotype name.")
        if ia.get("model", "product") not in INTERACTION_MODELS:
            raise SystemExit(f"interaction: model must be one of {', '.join(INTERACTION_MODELS)}.")
        if ia.get("outcome", "calls") not in ["calls"] + names:
            raise SystemExit("interaction: outcome must be 'calls' or a question_outcomes name.")


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
