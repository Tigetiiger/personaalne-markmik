"""Fixtures of the Meedia tests; the sample files are made in samples.py."""

import pytest

from app.meedia import store
from tests.meedia import samples


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "meedia"
    store.ensure(r)
    return r


@pytest.fixture(scope="session", autouse=True)
def _sample_dir(tmp_path_factory):
    samples.DIR[:] = [tmp_path_factory.mktemp("meedia-samples")]
