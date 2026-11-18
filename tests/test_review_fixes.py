"""1.26.10: what an independent review of 1.26.7 to 1.26.9 found.

Go back with a file of the kept copy open in another program, and settings-group letters after a new scan."""
import sys

import pytest

from test_acquisition_settings import write_ome
from test_update import contents, swap_setup, whole_app
from wormscore import readers
from wormscore import update as U
from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare, read_csv

quiet = lambda *a: None


def kept_setup(tmp_path, monkeypatch):
    paths = swap_setup(tmp_path, monkeypatch)
    whole_app(paths["current"], "2")
    whole_app(paths["previous"], "1")
    U._remember_previous("1.0.0")
    started = []
    monkeypatch.setattr(U, "start_helper", lambda p, new, **kw: started.append(new))
    return paths, started


@pytest.mark.skipif(sys.platform != "win32", reason="an open file blocks a rename only on Windows")
def test_go_back_with_a_file_of_the_kept_copy_open_changes_nothing(tmp_path, monkeypatch):
    """The move fell back to copy-and-delete, which stopped halfway and left the kept copy with one file.
    The next Go back put that folder in the app's place and reported that it had worked."""
    paths, started = kept_setup(tmp_path, monkeypatch)
    before = contents(paths["previous"])
    with open(paths["previous"] / "wormscore.exe", "rb"):
        with pytest.raises(U.UpdateError, match="Nothing was changed"):
            U.rollback(relaunch=False)
    assert contents(paths["previous"]) == before and contents(paths["current"])["VERSION"] == "2"
    assert not started and U.previous_version() == "1.0.0"
    U.rollback(relaunch=False)                  # once the file is free, Go back works
    assert started == [paths["stage"] / "wormscore-previous"] and contents(started[0]) == before


def test_go_back_refuses_a_kept_copy_without_the_program(tmp_path, monkeypatch):
    paths, started = kept_setup(tmp_path, monkeypatch)
    (paths["previous"] / "wormscore.exe").unlink()
    whole_app(paths["stage"] / "wormscore-previous", "1")      # what an interrupted move could have left
    with pytest.raises(U.UpdateError, match="not complete"):
        U.rollback(relaunch=False)
    assert not started and contents(paths["current"])["VERSION"] == "2"
    assert (paths["stage"] / "wormscore-previous" / "wormscore.exe").exists()      # the whole copy is not deleted


def project_with_gains(tmp_path, monkeypatch, gains, old_text):
    """Images at the given gains, scanned and built with the settings text an earlier version read."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    for f in (project / "data").glob("*.tif"):
        f.unlink()
    for k, gain in enumerate(gains):
        write_ome(project / "data" / f"demo_control_{k:02d}.ome.tif", gain=gain, bits=16, seed=k)
    with monkeypatch.context() as m:
        m.setattr(readers, "_ome_settings", old_text)
        inventory(project, log=quiet)
    prepare(project, log=quiet)
    return project


def groups(project):
    return {r["file"]: r["settings_group"] for r in read_csv(project / "key" / "key.csv")}


def test_group_letters_stay_on_the_rebuild_after_a_warned_change(tmp_path, monkeypatch):
    """Three images at gain 4 and one at gain 1, all in group A before. The warned rebuild named gain 1 as
    group B; the next rebuild, with no new scan, gave the letters in sorted order and swapped A and B."""
    project = project_with_gains(tmp_path, monkeypatch, [4, 4, 4, 1], lambda chan: [])
    inventory(project, log=quiet)
    lines = []
    prepare(project, log=lines.append)
    assert any("1 coded image moves from settings group A to B" in x for x in lines)
    first = groups(project)
    assert sorted(first.values()) == ["A", "A", "A", "B"] and first["demo_control_03.ome.tif"] == "B"
    table = (project / "key" / "settings_groups.csv").read_bytes()
    lines = []
    prepare(project, log=lines.append)
    assert groups(project) == first and (project / "key" / "settings_groups.csv").read_bytes() == table
    assert not any(x.startswith("Warning") for x in lines)


def test_an_added_setting_never_gets_a_letter_that_is_in_use(tmp_path, monkeypatch):
    """Two groups that a new scan reads as one leave a gap in the letters (A and C). The next added
    setting was given the letter after the number of groups, which was C again."""
    by_gain = lambda chan: ["old text " + chan.find("o:DetectorSettings", readers.OME_NS).get("Gain")]
    project = project_with_gains(tmp_path, monkeypatch, [1, 2, 4, 4], by_gain)       # groups A, B, C
    assert sorted(groups(project).values()) == ["A", "B", "C", "C"]
    merged = lambda chan: ["gain low" if chan.find("o:DetectorSettings", readers.OME_NS).get("Gain") in ("1", "2")
                           else "gain " + chan.find("o:DetectorSettings", readers.OME_NS).get("Gain")]
    monkeypatch.setattr(readers, "_ome_settings", merged)
    inventory(project, log=quiet)
    prepare(project, log=quiet)
    assert sorted(set(groups(project).values())) == ["A", "C"]
    write_ome(project / "data" / "demo_control_09.ome.tif", gain=8, bits=16, seed=9)
    inventory(project, log=quiet)
    prepare(project, add=True, log=quiet)
    table = read_csv(project / "key" / "settings_groups.csv")
    assert len({r["settings_group"] for r in table}) == len(table) == 3
    new = groups(project)["demo_control_09.ome.tif"]
    assert new == "B" and list(groups(project).values()).count(new) == 1
