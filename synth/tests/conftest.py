import pytest
from toolscout_synth import build_catalog, load_taskset, load_world


@pytest.fixture(scope="session")
def taskset():
    return load_taskset()


@pytest.fixture(scope="session")
def world():
    return load_world()


@pytest.fixture(scope="session")
def catalog():
    return build_catalog()
