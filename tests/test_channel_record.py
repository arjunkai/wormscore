"""Which channel each coded image was built from (1.26.22, key/channels.csv). rescan.scan_changes did not
compare a coded image's channel roles between the build and a new scan, so a newer reading that named
another channel the reporter would have shown a scored image's other channel after a rebuild, without a
word. Now the Build card says so before a rebuild (and, once scoring has begun, that images already scored
would change), and the Build log names the codes. key.csv and the results tables are as before. (A hold
that kept the recorded channels was tried and dropped in review: it undid a marker change made with Change
anyway... after scoring had begun, and disagreed with the autofluorescence check and Browse.)"""
import csv
import hashlib
import shutil

import pytest

from wormscore import rescan
from wormscore.demo import make_demo
from wormscore.files import read_csv
from wormscore.pipeline import CHANNELS_FILE, inventory, prepare
from test_results_options import write_scores

QUIET = {"log": lambda *a: None}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A GFP reporter and an mCherry identity channel (channels: reporter, identity, brightfield)."""
    project = tmp_path_factory.mktemp("chan")
    make_demo(project, n_per_group=2, identity=True, **QUIET)
    inventory(project, **QUIET)
    prepare(project, **QUIET)
    return project


@pytest.fixture
def project(built, tmp_path):
    dst = tmp_path / "p"
    shutil.copytree(built, dst)
    rescan._scan_changes.clear()
    return dst


def edit_inventory(project, code, change):
    key = {r["code"]: r for r in read_csv(project / "key" / "key.csv")}
    where = (key[code]["local_path"], key[code]["location_in_file"])
    path = project / "outputs" / "inventory.csv"
    rows = read_csv(path)
    for r in rows:
        if (r["local_path"], r["location_in_file"]) == where:
            change(r)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def swap_reporter_and_identity(project, code, settings=None):
    """What a newer reading could do: name the other fluorescent channel of one coded image the reporter
    (and, as the readers do, write that channel's settings)."""
    def change(r):
        roles = r["channel_roles"].split("/")
        m, i = int(r["marker_channel"]), int(r["identity_channel"])
        roles[m], roles[i] = roles[i], roles[m]
        r.update(channel_roles="/".join(roles), marker_channel=str(i), identity_channel=str(m))
        if settings:
            r["settings"] = settings
    edit_inventory(project, code, change)


def folder_sum(d):
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        h.update(f.relative_to(d).as_posix().encode() + f.read_bytes())
    return h.hexdigest()


def score_all(project):
    write_scores(project, [{"code": r["code"], "usable": "yes", "count_this_image": "yes", "cells_seen": "2",
                            "n1_call": "normal", "n2_call": "normal"} for r in read_csv(project / "key" / "key.csv")])
    rescan._scan_changes.clear()


def test_the_build_records_each_images_channels_outside_key_csv(project):
    rec = read_csv(project / "key" / CHANNELS_FILE)
    key = read_csv(project / "key" / "key.csv")
    assert [r["code"] for r in rec] == sorted(r["code"] for r in key if not r.get("repeat_of"))
    assert {r["channel_roles"] for r in rec} == {"MARKER/IDENTITY/BF"}
    assert all(r["channel_names"].strip("/") for r in rec)
    assert {(r["marker_channel"], r["identity_channel"], r["bf_channel"]) for r in rec} == {("0", "1", "2")}
    assert not any("channel_roles" in k or k == "marker_channel" for k in key[0])        # key.csv as before
    assert rescan.scan_changes(project)["lines"] == []


def test_before_scoring_the_card_says_a_rebuild_uses_the_new_reading(project):
    swap_reporter_and_identity(project, "W0001")
    ch = rescan.scan_changes(project)
    assert ch["kinds"] == ["channels"]
    assert ch["lines"][0] == ("1 coded image would be built from other channels than before, as Scan data now "
                              "reads it (its reporter and identity channels)")
    (line,) = rescan.scan_change_lines(project, ch)
    assert line.endswith("Rebuild, keeping the current codes, to use it.")
    before = folder_sum(project / "blind" / "W0001")
    said = []
    prepare(project, log=said.append)
    assert folder_sum(project / "blind" / "W0001") != before           # not scored yet: built as now read
    assert any(x.startswith("Warning: 1 coded image is built from other channels than before") for x in said)
    assert read_csv(project / "key" / CHANNELS_FILE)[0]["marker_channel"] == "1"
    rescan._scan_changes.clear()
    assert rescan.scan_changes(project)["lines"] == []


def test_once_scoring_has_begun_the_card_says_scored_images_would_change_and_the_log_names_them(project):
    score_all(project)
    swap_reporter_and_identity(project, "W0002")
    ch = rescan.scan_changes(project)
    assert ch["kinds"] == ["channels"]
    (line,) = rescan.scan_change_lines(project, ch)
    assert line.endswith("Rebuild, keeping the current codes, to use it. It is already scored and would look "
                         "different. The Build log will name the codes.")
    before = folder_sum(project / "blind" / "W0002")
    said = []
    prepare(project, log=said.append)
    assert folder_sum(project / "blind" / "W0002") != before            # built as now read, as the card said
    assert "Warning: 1 coded image is built from other channels than before, as Scan data now reads it: W0002. "            "If it was already scored, it looks different now." in said
    assert not any("this build changes what was recorded" in x for x in said)
    assert "W0002" in (project / "log.md").read_text(encoding="utf-8")
    rescan._scan_changes.clear()
    assert rescan.scan_changes(project)["lines"] == []                   # the record follows the build


def test_a_marker_change_after_scoring_is_carried_out_by_the_rebuild(project):
    """Change anyway... after scoring: the markers change, Scan data reads other roles, and the rebuild uses
    them (log.md says it will). The card warns first; the rebuild does what the user asked."""
    score_all(project)
    swap_reporter_and_identity(project, "W0001")
    swap_reporter_and_identity(project, "W0002")
    (line,) = rescan.scan_change_lines(project, rescan.scan_changes(project))
    assert line.startswith("2 coded images would be built from other channels than before, as Scan data now "
                           "reads them (the reporter channel of 2, the identity channel of 2)")
    assert "2 of them are already scored" in line
    prepare(project, **QUIET)
    rec = {r["code"]: r for r in read_csv(project / "key" / CHANNELS_FILE)}
    assert rec["W0001"]["marker_channel"] == rec["W0002"]["marker_channel"] == "1"


def test_a_project_built_before_the_record_compares_the_identity_channel_only(project):
    (project / "key" / CHANNELS_FILE).unlink()
    swap_reporter_and_identity(project, "W0001")
    assert rescan.scan_changes(project)["lines"] == []          # no record: the swap itself cannot be seen
    path = project / "outputs" / "inventory.csv"
    rows = read_csv(path)
    for r in rows:
        r.update(identity_channel="", channel_roles=r["channel_roles"].replace("IDENTITY", "OTHER"))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rescan._scan_changes.clear()
    ch = rescan.scan_changes(project)
    assert ch["kinds"] == ["channels-unrecorded"]
    assert ch["lines"][0].startswith("Scan data no longer finds the identity channel of")
    score_all(project)
    (line,) = rescan.scan_change_lines(project, rescan.scan_changes(project))
    assert "of them are already scored and would look different" in line
    # the Build log names them, record or not (here the identity channel is required, so a rebuild would
    # leave these images out instead; the line is checked as the build makes it)
    from wormscore.pipeline import channel_warning
    codes = [r["code"] for r in read_csv(project / "key" / "key.csv")]
    (warn,) = channel_warning(rescan.scan_changes(project), [{"code": c} for c in codes])
    assert warn.startswith(f"Warning: {len(codes)} coded images are built from other channels than before")         and all(c in warn for c in codes)


@pytest.fixture
def own(tmp_path):
    """A project of its own (its key and config name its own data folder), for adding images."""
    p = tmp_path / "own"
    make_demo(p, n_per_group=2, identity=True, **QUIET)
    inventory(p, **QUIET)
    prepare(p, **QUIET)
    rescan._scan_changes.clear()
    return p


def add_a_new_image(project, tmp_path):
    """One more real image (another demo seed, so not a copy), under a name the genotype rules match."""
    other = tmp_path / "other"
    make_demo(other, n_per_group=1, seed=99, identity=True, **QUIET)
    src = next((other / "data").glob("demo_control_*.ome.tif"))
    shutil.copy2(src, project / "data" / "demo_control_77.ome.tif")
    inventory(project, **QUIET)
    rescan._scan_changes.clear()


def test_adding_images_keeps_the_record_and_adds_the_new_codes(own, tmp_path):
    project = own
    before = read_csv(project / "key" / CHANNELS_FILE)
    add_a_new_image(project, tmp_path)
    prepare(project, add=True, **QUIET)
    after = read_csv(project / "key" / CHANNELS_FILE)
    assert after[:len(before)] == before and len(after) == len(before) + 1
    new = after[-1]
    assert new["code"] not in {r["code"] for r in before} and new["marker_channel"] == "0"


def test_adding_images_to_a_project_without_a_record_starts_none(own, tmp_path):
    project = own
    (project / "key" / CHANNELS_FILE).unlink()
    n_key = len(read_csv(project / "key" / "key.csv"))
    add_a_new_image(project, tmp_path)
    prepare(project, add=True, **QUIET)
    assert len(read_csv(project / "key" / "key.csv")) == n_key + 1     # the add really ran
    assert not (project / "key" / CHANNELS_FILE).exists()


def test_names_alone_are_not_a_change():
    rec = {"channel_names": "GFP/mCherry/BF", "channel_roles": "MARKER/IDENTITY/BF", "marker_channel": "0",
           "identity_channel": "1", "bf_channel": "2"}
    assert not rescan.channels_differ(rec, {**rec, "channel_names": "EGFP/mCherry/TL"})


def test_a_channel_the_coded_image_does_not_use_is_not_a_change():
    rec = {"channel_names": "GFP/mCherry/BF", "channel_roles": "MARKER/IDENTITY/BF", "marker_channel": "0",
           "identity_channel": "1", "bf_channel": "2"}
    assert not rescan.channels_differ(rec, {**rec, "channel_names": "GFP/mCherry/BF/DAPI",
                                            "channel_roles": "MARKER/IDENTITY/BF/OTHER"})


def test_a_changed_image_not_yet_scored_is_not_called_scored(project):
    write_scores(project, [{"code": "W0001", "usable": "yes", "count_this_image": "yes", "cells_seen": "2",
                            "n1_call": "normal", "n2_call": "normal"}])
    rescan._scan_changes.clear()
    swap_reporter_and_identity(project, "W0003")
    (line,) = rescan.scan_change_lines(project, rescan.scan_changes(project))
    assert line.endswith("Rebuild, keeping the current codes, to use it.")


def test_a_channel_read_in_front_or_a_renamed_channel_is_not_a_change():
    rec = {"channel_names": "GFP/mCherry/BF", "channel_roles": "MARKER/IDENTITY/BF", "marker_channel": "0",
           "identity_channel": "1", "bf_channel": "2"}
    front = {"channel_names": "DAPI/GFP/mCherry/BF", "channel_roles": "OTHER/MARKER/IDENTITY/BF",
             "marker_channel": "1", "identity_channel": "2", "bf_channel": "3"}
    assert not rescan.channels_differ(rec, front)
    assert not rescan.channels_differ(rec, {**rec, "channel_names": "EGFP/mCherry/TL"})
    swapped = {**rec, "channel_roles": "IDENTITY/MARKER/BF", "marker_channel": "1", "identity_channel": "0"}
    assert rescan.channels_differ(rec, swapped)
    unnamed = {k: (v.replace("GFP", "").replace("mCherry", "").replace("BF", "") if k == "channel_names" else v)
               for k, v in front.items()}
    assert rescan.channels_differ({**rec, "channel_names": "//"}, unnamed)     # no names: positions decide


def test_a_scored_hidden_repeat_counts_its_original_as_scored(project):
    key = read_csv(project / "key" / "key.csv")
    path = project / "key" / "key.csv"
    fields = list(key[0]) + (["repeat_of"] if "repeat_of" not in key[0] else [])
    key.append({**key[1], "code": "W0099", "repeat_of": key[1]["code"]})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(key)
    write_scores(project, [{"code": "W0099", "usable": "yes", "count_this_image": "yes", "cells_seen": "2",
                            "n1_call": "normal", "n2_call": "normal"}])
    rescan._scan_changes.clear()
    swap_reporter_and_identity(project, key[1]["code"])
    (line,) = rescan.scan_change_lines(project, rescan.scan_changes(project))
    assert "It is already scored and would look different." in line and "W0099" not in line
