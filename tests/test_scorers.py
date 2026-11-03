"""Second-scorer packages: what leaves the project, what comes back, and what unblinding adds."""
import csv
import json
import shutil
import urllib.request

import pytest

from wormscore import config as C
from wormscore import scorers
from wormscore.pipeline import inventory, prepare, read_csv
from wormscore.server import make_server, score_fields
from wormscore.unblind import unblind
from test_results_options import write_scores

quiet = lambda *a: None


def primary_rows(key):
    return [{"code": r["code"], "usable": "yes", "count_this_image": "yes", "cells_seen": "2",
             "n1_call": "missing" if r["genotype"] == "mutant" else "normal", "n2_call": "normal"} for r in key]


def score_package(pkg, change=None):
    """Score every image of a package the way the primary scorer did (from the demo's design),
    optionally changing some answers, as the second scorer's app would save them."""
    codes = json.loads((pkg / "config.json").read_text(encoding="utf-8"))["scorer_package"]["order"]
    rows = [{"code": c, "usable": "yes", "count_this_image": "yes", "cells_seen": "2",
             "n1_call": "normal", "n2_call": "normal"} for c in codes]
    if change:
        change(rows)
    write_scores(pkg, rows, saved="2099-01-02 10:00:00")
    return rows


@pytest.fixture
def made(fresh_project, tmp_path):
    pkg = tmp_path / "for_sam"
    scorers.export_package(fresh_project, "Sam K", pkg, fraction=0.5, log=quiet)
    return fresh_project, pkg


def text_of(folder):
    out = []
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".txt", ".csv", ".md"):
            out.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


def test_package_holds_coded_images_and_the_form_only(made):
    project, pkg = made
    assert sorted(x.name for x in pkg.iterdir()) == ["README.txt", "blind", "config.json"]
    cfg = json.loads((pkg / "config.json").read_text(encoding="utf-8"))
    assert set(cfg) <= {"project_name", "marker", "identity", "scoring", "display", "measurement",
                        "unblinding", "scorer_package"}
    assert "sources" not in cfg and set(cfg.get("unblinding", {})) <= {"dim_cutoff_sd"}
    text = text_of(pkg)
    for leak in ("control", "mutant", "genotype", str(project), "key.csv", "sha256"):
        assert leak not in text, leak
    key = read_csv(project / "key" / "key.csv")
    for r in key:                       # no checksum of any source file or of any coded folder
        for k in ("file_sha256", "pixel_sha256"):
            assert not r.get(k) or r[k] not in text
    rec = json.loads((project / "scorers" / scorers.scorer_ids(project)[0] / "package.json").read_text())
    assert set(rec["blind_sha256"]) == set(cfg["scorer_package"]["order"])
    assert sorted(x.name for x in (pkg / "blind").iterdir()) == sorted(cfg["scorer_package"]["order"])


def test_share_is_taken_from_every_genotype_and_logged(made):
    project, pkg = made
    key = {r["code"]: r["genotype"] for r in read_csv(project / "key" / "key.csv")}
    codes = json.loads((pkg / "config.json").read_text(encoding="utf-8"))["scorer_package"]["order"]
    picked = [key[c] for c in codes]
    assert picked.count("control") == 2 and picked.count("mutant") == 2       # round(0.5 * 3) of each
    log = (project / "log.md").read_text(encoding="utf-8")
    assert "second-scorer package made" in log and "Scorer: Sam K. 4 of 6 included images (50% of each genotype" in log


def test_package_scoring_page_lists_images_in_the_scorers_own_order(made):
    _, pkg = made
    order = json.loads((pkg / "config.json").read_text(encoding="utf-8"))["scorer_package"]["order"]
    srv = make_server(pkg, port=0)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/worms", timeout=30) as r:
            worms = json.loads(r.read())
    finally:
        srv.shutdown()
        srv.server_close()
    listed = [w["code"] for w in (worms["worms"] if isinstance(worms, dict) else worms)]
    assert listed == order


def test_a_package_can_only_be_scored(made, tmp_path):
    project, pkg = made
    for step in (lambda: inventory(pkg, log=quiet), lambda: prepare(pkg, log=quiet),
                 lambda: unblind(pkg, finished=True, log=quiet),
                 lambda: scorers.export_package(pkg, "Lee", tmp_path / "x", log=quiet)):
        with pytest.raises(SystemExit, match="second-scorer package"):
            step()


@pytest.mark.parametrize("name, fraction, where, message", [
    ("", 0.3, "new", "short name"),
    ("Sam/../x", 0.3, "new", "short name"),
    ("Sam", 0, "new", "more than 0"),
    ("Sam", 1.5, "new", "more than 0"),
    ("Sam", 0.3, "inside", "outside the project"),
    ("Sam", 0.3, "full", "not empty"),
])
def test_export_refuses_bad_requests(fresh_project, tmp_path, name, fraction, where, message):
    dest = {"new": tmp_path / "pkg", "inside": fresh_project / "pkg", "full": tmp_path / "full"}[where]
    if where == "full":
        dest.mkdir()
        (dest / "something.txt").write_text("x")
    with pytest.raises(SystemExit, match=message):
        scorers.export_package(fresh_project, name, dest, fraction=fraction, log=quiet)
    assert not (fresh_project / "scorers").exists()


def test_export_needs_a_blind_set(tmp_path):
    from wormscore.demo import make_demo
    make_demo(tmp_path / "p", n_per_group=2, log=quiet)
    with pytest.raises(SystemExit, match="Build the blind set first"):
        scorers.export_package(tmp_path / "p", "Sam", tmp_path / "pkg", log=quiet)


def test_import_keeps_the_scores_apart_from_the_primary_ones(made):
    project, pkg = made
    score_package(pkg)
    before = {p.name: p.read_bytes() for p in (project / "scoring").glob("*")} if (project / "scoring").is_dir() else {}
    own = scorers.import_scores(project, pkg, log=quiet)
    assert (own / "scoring" / "scores.csv").read_bytes() == (pkg / "scoring" / "scores.csv").read_bytes()
    after = {p.name: p.read_bytes() for p in (project / "scoring").glob("*")} if (project / "scoring").is_dir() else {}
    assert after == before
    assert "second-scorer scores imported" in (project / "log.md").read_text(encoding="utf-8")
    (info,) = scorers.packages(project)
    assert info["imported"] and info["scored"] == info["images"] == 4


def damage_image(pkg):
    f = next(p for p in (pkg / "blind").rglob("*") if p.is_file() and p.suffix != ".json")
    f.write_bytes(f.read_bytes()[:-1] + b"\x00")


def add_code(pkg):
    first = next((pkg / "blind").iterdir())
    shutil.copytree(first, pkg / "blind" / "W9999")


def extra_row(pkg):
    rows = read_csv(pkg / "scoring" / "scores.csv")
    rows.append(dict(rows[0], code="W9999"))
    rewrite(pkg, rows)


def odd_answer(pkg):
    rows = read_csv(pkg / "scoring" / "scores.csv")
    rows[0]["n1_call"] = "=HYPERLINK(\"x\")"
    rewrite(pkg, rows)


def odd_column(pkg):
    rows = read_csv(pkg / "scoring" / "scores.csv")
    rewrite(pkg, [dict(r, genotype="control") for r in rows], extra=["genotype"])


def changed_form(project):
    path = project / "config.json"
    cfg = json.loads(path.read_text())
    cfg.setdefault("scoring", {})["calls"] = ["normal", "faint", "missing"]
    path.write_text(json.dumps(cfg))


def rewrite(pkg, rows, extra=()):
    fields = score_fields(C.load(pkg)) + list(extra)
    with open(pkg / "scoring" / "scores.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


@pytest.mark.parametrize("spoil, message", [
    (damage_image, "were changed"),
    (add_code, "codes added or removed"),
    (extra_row, "codes it never had"),
    (odd_answer, "not on the form"),
    (odd_column, "columns the scoring form does not"),
])
def test_import_refuses_a_changed_package(made, spoil, message):
    project, pkg = made
    score_package(pkg)
    spoil(pkg)
    with pytest.raises(SystemExit, match=message):
        scorers.import_scores(project, pkg, log=quiet)
    assert not (project / "scorers" / scorers.scorer_ids(project)[0] / "scoring").exists()


def test_import_refuses_other_projects_packages_and_a_changed_form(made, demo_project, tmp_path):
    project, pkg = made
    score_package(pkg)
    other = tmp_path / "other"
    shutil.copytree(demo_project, other)
    with pytest.raises(SystemExit, match="not made from this project"):
        scorers.import_scores(other, pkg, log=quiet)
    with pytest.raises(SystemExit, match="not a second-scorer package"):
        scorers.import_scores(project, other, log=quiet)
    changed_form(project)
    with pytest.raises(SystemExit, match="scoring form changed"):
        scorers.import_scores(project, pkg, log=quiet)


def test_import_needs_scores(made):
    project, pkg = made
    with pytest.raises(SystemExit, match="no scores yet"):
        scorers.import_scores(project, pkg, log=quiet)


def test_form_hash_ignores_settings_that_do_not_change_answers(fresh_project):
    cfg = C.load(fresh_project)
    h = scorers.form_hash(cfg)
    cfg["scoring"]["a_future_display_setting"] = True
    cfg["scoring"]["rules_text"] = "new wording"
    assert scorers.form_hash(cfg) == h
    cfg["scoring"]["calls"] = cfg["scoring"]["calls"] + ["faint"]
    assert scorers.form_hash(cfg) != h


def unblinded_files(out):
    return {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}


def test_unblinding_compares_scorers_and_leaves_primary_results_alone(made, tmp_path):
    project, pkg = made
    key = read_csv(project / "key" / "key.csv")
    write_scores(project, primary_rows(key))
    plain = tmp_path / "plain"
    shutil.copytree(project, plain)
    shutil.rmtree(plain / "scorers")
    out_plain = unblind_files_after(plain)

    genotype = {r["code"]: r["genotype"] for r in key}
    # the second scorer agrees with the primary scorer except on one mutant
    def like_primary(rows):
        first = True
        for r in rows:
            if genotype[r["code"]] == "mutant":
                r["n1_call"] = "missing" if not first else "dim"
                first = False
    score_package(pkg, like_primary)
    scorers.import_scores(project, pkg, log=quiet)
    out = unblind(project, finished=True, log=quiet)
    files = unblinded_files(out)
    for name, data in out_plain.items():
        if name not in ("methods_text.md", "summary.txt"):
            assert files[name] == data, name           # every primary table is unchanged
    assert set(files) - set(out_plain) == {"between_scorers.csv", "scorer_disagreements.csv"}
    calls = {r["measure"]: r for r in read_csv(out / "between_scorers.csv")}["neuron call"]
    assert calls["pairs"] == "8" and calls["same_answer"] == "7"
    (d,) = read_csv(out / "scorer_disagreements.csv")
    assert (d["second_scorer"], d["answer"], d["primary"], d["second"]) == ("Sam K", "neuron 1 call", "missing", "dim")
    assert "genotype" not in d and "control" not in json.dumps(d) and "mutant" not in json.dumps(d)
    methods = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "A second scorer (Sam K) scored 4 of the included images (50% of each genotype" in methods
    assert "Results use the first scorer's answers only" in methods
    assert "Agreement between scorers" in (out / "summary.txt").read_text(encoding="utf-8")


def unblind_files_after(project):
    return unblinded_files(unblind(project, finished=True, log=quiet))


def test_nothing_changes_for_projects_without_a_second_scorer(fresh_project):
    write_scores(fresh_project, primary_rows(read_csv(fresh_project / "key" / "key.csv")))
    out = unblind(fresh_project, finished=True, log=quiet)
    assert not (fresh_project / "scorers").exists()
    assert not (out / "between_scorers.csv").exists() and not (out / "scorer_disagreements.csv").exists()
    assert "second scorer" not in (out / "methods_text.md").read_text(encoding="utf-8").lower()
    assert scorers.packages(fresh_project) == []
