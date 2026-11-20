"""1.26.7: the Methods draft names every wormscore version log.md records from building the blind set to
unblinding. It used to name only the version that ran Unblind, although the app warns that an update in
between "would cite two versions"."""
from wormscore import __version__, methods
from wormscore.unblind import unblind

from test_unblind_safeguards import codes, write_scores

quiet = lambda *a: None


def draft(project):
    write_scores(project, {c: "2026-01-05 10:00:00" for c in codes(project)})
    unblind(project, finished=True, anyway=True, log=quiet)
    return (project / "outputs" / "unblinded" / "methods_text.md").read_text(encoding="utf-8")


def test_one_version_reads_as_before(fresh_project):
    assert methods.versions_used(fresh_project) == [__version__]
    text = draft(fresh_project)
    assert f"Images were then coded and scored blind with wormscore {__version__} (https://github.com" in text


def test_an_update_between_build_and_unblind_names_both_versions(fresh_project):
    log = fresh_project / "log.md"
    text = log.read_text(encoding="utf-8")
    assert text.count(f'"wormscore": "{__version__}"') == 2          # Scan data, then Build
    # as if Scan data and Build ran with 1.8.1 and the app was updated before unblinding
    log.write_text(text.replace(f'"wormscore": "{__version__}"', '"wormscore": "1.8.1"'), encoding="utf-8")
    assert methods.versions_used(fresh_project) == ["1.8.1", __version__]
    assert (f"scored blind with wormscore 1.8.1 and {__version__} (the versions log.md records from building the "
            f"blind set to unblinding; the results were computed with {__version__}) (https://github.com") in draft(fresh_project)


def test_versions_from_before_the_current_blind_set_are_left_out(fresh_project):
    log = fresh_project / "log.md"
    old = ('\n## 2020-01-01 09:00 inventory\n- Software: {"wormscore": "0.9.0"}\n'
           '\n## 2020-01-01 09:05 prepare\n- Software: {"wormscore": "0.9.0"}\n')
    log.write_text(old + log.read_text(encoding="utf-8"), encoding="utf-8")       # an earlier key, since replaced
    (fresh_project / "key" / "superseded_20200201_120000").mkdir()                  # as a build with new codes leaves it
    assert methods.versions_used(fresh_project) == [__version__]


def test_a_copied_project_still_names_the_version_that_built_it(fresh_project):
    """The start was found from the date of key/seed.txt, which copying or syncing a project can reset: the
    version that built the blind set was then left out (1.26.10)."""
    import os
    log = fresh_project / "log.md"
    old = ('\n## 2020-01-01 09:00 inventory\n- Software: {"wormscore": "0.9.0"}\n'
           '\n## 2020-01-01 09:05 prepare\n- Software: {"wormscore": "0.9.0"}\n')
    log.write_text(old, encoding="utf-8")
    os.utime(fresh_project / "key" / "seed.txt")            # now: later than every build in the log
    assert methods.versions_used(fresh_project) == ["0.9.0", __version__]


def test_the_day_the_codes_were_assigned_survives_a_copy(fresh_project):
    """Found on a copy of a real project (1.26.21): the Methods draft took that day from the modified time of
    key/seed.txt, so a copy that did not keep file times gave the day of the copy. Build now writes it."""
    import datetime as dt
    import os
    key = fresh_project / "key"
    assert (key / "coded_on.txt").read_text().strip() == f"{dt.date.today():%Y-%m-%d}"
    (key / "coded_on.txt").write_text("2020-02-01\n")
    os.utime(key / "seed.txt")                                 # a copy: the seed's time is now
    assert methods.coded_date(fresh_project) == "2020-02-01"
    (key / "coded_on.txt").unlink()                            # a key built before 1.26.21: as before
    stamp = dt.datetime(2020, 3, 4, 12).timestamp()
    os.utime(key / "seed.txt", (stamp, stamp))
    assert methods.coded_date(fresh_project) == "2020-03-04"
    (key / "coded_on.txt").write_text("not a date\n")
    assert methods.coded_date(fresh_project) == "2020-03-04"
