import shutil

import pytest

from wormscore.demo import make_demo
from wormscore.pipeline import inventory, prepare


@pytest.fixture(scope="session")
def demo_project(tmp_path_factory):
    """A small simulated project that has been scanned and prepared (built once per test run)."""
    project = tmp_path_factory.mktemp("demo")
    make_demo(project, n_per_group=3, log=lambda *a: None)
    inventory(project, log=lambda *a: None)
    prepare(project, log=lambda *a: None)
    return project


@pytest.fixture
def fresh_project(demo_project, tmp_path):
    """A private copy of the prepared demo project that a test may change."""
    dst = tmp_path / "project"
    shutil.copytree(demo_project, dst)
    return dst
