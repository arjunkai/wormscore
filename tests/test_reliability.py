"""Hidden repeat images for scorer consistency, and the measured cutoff for checking calls."""
import json
import shutil

import numpy as np
import pytest

from wormscore import config as C
from wormscore import measure as M
from wormscore import stats
from wormscore.demo import make_demo
from wormscore.pipeline import REPEAT_GAP, inventory, prepare, read_csv
from wormscore.server import make_server
from wormscore.unblind import unblind
from test_analysis_plan import edit_config
from test_results_options import write_scores

quiet = lambda *a: None


@pytest.fixture(scope="module")
def repeat_project(tmp_path_factory):
    """Ten simulated images per genotype, prepared with hidden repeats."""
    project = tmp_path_factory.mktemp("repeats")
    make_demo(project, n_per_group=10, log=quiet)
    edit_config(project, lambda c: c.update(reliability={"repeat_fraction": 0.2}))
    inventory(project, log=quiet)
    prepare(project, log=quiet)
    return project


def blind_files(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_repeats_are_exact_copies_spaced_apart_and_only_in_the_key(repeat_project):
    key = read_csv(repeat_project / "key" / "key.csv")
    reps = [r for r in key if r["repeat_of"]]
    assert len(key) == 24 and len(reps) == 4                       # 20% of 10 per genotype
    assert sorted(r["genotype"] for r in reps) == ["control", "control", "mutant", "mutant"]
    gap = max(2, round(REPEAT_GAP * len(key)))
    blind = repeat_project / "blind"
    for r in reps:
        orig = r["repeat_of"]
        assert abs(int(r["code"][1:]) - int(orig[1:])) >= gap
        a, b = blind_files(blind / orig), blind_files(blind / r["code"])
        assert a.keys() == b.keys()
        assert all(a[f] == b[f] for f in a if f != "meta.json")
        ma, mb = json.loads(a["meta.json"]), json.loads(b["meta.json"])
        assert mb.pop("code") == r["code"] and ma.pop("code") == orig and ma == mb
        assert {k: v for k, v in r.items() if k not in ("code", "repeat_of")} == \
               {k: v for k, v in next(x for x in key if x["code"] == orig).items() if k not in ("code", "repeat_of")}
    log = (repeat_project / "log.md").read_text(encoding="utf-8")
    assert "Hidden repeats for scorer consistency: 4 images" in log
    assert not any(f"{r['code']}" in log for r in reps)
    # The scoring app lists repeats like any other image.
    srv = make_server(repeat_project, port=0)
    try:
        assert len([d for d in blind.iterdir() if (d / "meta.json").exists()]) == 24
    finally:
        srv.server_close()


def test_repeats_do_not_change_display_ranges(repeat_project, tmp_path):
    plain = tmp_path / "plain"
    shutil.copytree(repeat_project, plain)
    edit_config(plain, lambda c: c.update(reliability={"repeat_fraction": 0}))
    prepare(plain, reassign=True, log=quiet)
    assert read_csv(plain / "outputs" / "display_ranges.csv") == \
        read_csv(repeat_project / "outputs" / "display_ranges.csv")
    assert "repeat_of" not in read_csv(plain / "key" / "key.csv")[0]


def test_rebuild_keeps_repeats_and_adding_images_adds_none(repeat_project, tmp_path):
    project = tmp_path / "p"
    shutil.copytree(repeat_project, project)
    before = read_csv(project / "key" / "key.csv")
    edit_config(project, lambda c: c.update(reliability={"repeat_fraction": 0.5}))
    prepare(project, log=quiet)
    assert read_csv(project / "key" / "key.csv") == before
    assert len([d for d in (project / "blind").iterdir()]) == 24


def scores_for(key, flip=None):
    rows = []
    for r in key:
        call = "missing" if r["genotype"] == "mutant" else "normal"
        if flip and r["code"] == flip:
            call = "dim"
        rows.append({"code": r["code"], "usable": "yes", "count_this_image": "yes", "cells_seen": "2",
                     "n1_call": call, "n2_call": "normal", "n1_side": "left", "n2_side": "right"})
    return rows


def test_unblinding_reports_agreement_and_never_counts_repeats(repeat_project, tmp_path):
    project = tmp_path / "p"
    shutil.copytree(repeat_project, project)
    key = read_csv(project / "key" / "key.csv")
    flip = next(r["code"] for r in key if r["repeat_of"] and r["genotype"] == "mutant")
    write_scores(project, scores_for(key, flip))
    out = unblind(project, finished=True, log=quiet)
    pen = {p["genotype"]: p for p in read_csv(out / "penetrance.csv")}
    assert pen["control"]["animals"] == "10" and pen["mutant"]["animals"] == "10"
    rel = {r["measure"]: r for r in read_csv(out / "reliability.csv")}
    assert rel["usable"]["pairs"] == "4" and rel["usable"]["percent_agreement"] == "100.0"
    calls = rel["neuron call"]
    assert calls["pairs"] == "8" and calls["same_answer"] == "7" and calls["kappa_type"] == "linear-weighted"
    assert rel["animal affected (primary)"]["same_answer"] == "4"   # dim and missing both count by default
    methods = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "4 included images were shown a second time" in methods and "| 10 | 0 |" in methods
    assert "Scorer consistency: 4 hidden repeats, 4 scored" in (out / "summary.txt").read_text(encoding="utf-8")


def test_kappa_matches_hand_worked_examples():
    # 2 x 2: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no. Observed 0.7, chance 0.5 -> kappa 0.4.
    pairs = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15
    assert stats.kappa(pairs, ["n", "y"]) == 0.4
    # Linear weights over three ordered calls: observed (1 + 1 + 1 + 0.5) / 4 = 0.875, chance 9/16.
    pairs = [("n", "n"), ("d", "d"), ("m", "m"), ("n", "d")]
    assert stats.kappa(pairs, ["n", "d", "m"], weighted=True) == round((0.875 - 9 / 16) / (1 - 9 / 16), 3)
    assert stats.kappa([("n", "n")] * 3, ["n", "d"]) == ""          # undefined: no variation


def test_neurons_are_matched_by_side_when_recorded():
    cfg = C.merge(C.DEFAULTS, {})
    a = {"score_n1_side": "left", "score_n2_side": "right"}
    assert stats.matched_neurons(a, {"score_n1_side": "right", "score_n2_side": "left"}, cfg) == [2, 1]
    assert stats.matched_neurons(a, {"score_n1_side": "left", "score_n2_side": ""}, cfg) == [1, 2]


# ------------------------------------------------------------- measured cutoff

def test_dim_cutoff_is_off_by_default_and_part_of_the_plan_when_set():
    cfg = C.merge(C.DEFAULTS, {})
    assert C.analysis_plan(cfg) == {}
    raw = np.random.default_rng(1).normal(100, 5, (3, 40, 40)).clip(0).astype(np.uint16)
    mask = np.zeros((40, 40), bool)
    mask[18:22, 18:22] = True
    off = M.measure_region(raw, raw, mask, mask, 0.5, 65535, cfg)
    assert "background_sd_best_plane" not in off
    on_cfg = C.merge(C.DEFAULTS, {"unblinding": {"dim_cutoff_sd": 3}})
    on = M.measure_region(raw, raw, mask, mask, 0.5, 65535, on_cfg)
    assert on["background_sd_best_plane"] == pytest.approx(5, rel=0.3)
    assert {k: v for k, v in on.items() if k != "background_sd_best_plane"} == off
    assert C.analysis_plan(on_cfg)["dim_cutoff_sd"] == 3
    for bad in (-1, "3", True):
        with pytest.raises(SystemExit, match="dim_cutoff_sd"):
            C.check(C.merge(C.DEFAULTS, {"unblinding": {"dim_cutoff_sd": bad}}))
    with pytest.raises(SystemExit, match="repeat_fraction"):
        C.check(C.merge(C.DEFAULTS, {"reliability": {"repeat_fraction": 0.9}}))


def test_dim_check_counts_calls_below_the_cutoff():
    cfg = C.merge(C.DEFAULTS, {"unblinding": {"dim_cutoff_sd": 3}})
    row = lambda call, mean, sd: {"settings_group": "A", "system": "s", "score_n1_call": call,
                                  "n1_roi_mean_best_minus_bg": mean, "n1_roi_background_sd_best_plane": sd}
    rows = [row("dim", 20, 10), row("dim", 50, 10), row("normal", 90, 10), row("normal", "", 10)]
    out = {r["call"]: r for r in stats.dim_check(rows, cfg)}
    assert out["dim"]["neurons_measured"] == 2 and out["dim"]["below_cutoff"] == 1
    assert out["dim"]["signal_to_noise_median"] == 3.5
    assert out["normal"]["neurons_measured"] == 1 and out["normal"]["below_cutoff"] == 0


def test_dim_cutoff_reaches_measurements_and_unblinding(fresh_project):
    edit_config(fresh_project, lambda c: c["unblinding"].update(dim_cutoff_sd=3))
    prepare(fresh_project, log=quiet)
    assert '"dim_cutoff_sd": 3' in (fresh_project / "log.md").read_text(encoding="utf-8")
    assert "background_sd_best_plane" in read_csv(fresh_project / "outputs" / "auto_measurements.csv")[0]
    srv = make_server(fresh_project, port=0)
    try:
        code = read_csv(fresh_project / "key" / "key.csv")[0]["code"]
        srv.project.save_rois(code, [{"label": "Neuron 1", "type": "candidate", "rank": 1}])
    finally:
        srv.server_close()
    (roi,) = read_csv(fresh_project / "scoring" / "roi_measurements.csv")
    assert float(roi["background_sd_best_plane"]) > 0
    key = read_csv(fresh_project / "key" / "key.csv")
    write_scores(fresh_project, scores_for(key))
    out = unblind(fresh_project, finished=True, anyway=True, log=quiet)
    (row,) = read_csv(out / "dim_check.csv")
    assert row["neurons_measured"] == "1" and row["cutoff_sd"] == "3"
    assert "at least 3 times the background noise" in (out / "methods_text.md").read_text(encoding="utf-8")
