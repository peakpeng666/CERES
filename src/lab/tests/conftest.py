"""Keep optional map tests explicit; CI can require the real assets."""
import os
from functools import lru_cache

import pytest

from urp_lab import paths


def pytest_addoption(parser):
    parser.addoption('--require-maps', action='store_true', help='Fail when a marked official map is missing')


def pytest_configure(config):
    config.addinivalue_line('markers', 'requires_maps(*worlds): needs official TurtleBot4 occupancy maps')


def pytest_runtest_setup(item):
    marker = item.get_closest_marker('requires_maps')
    if marker is None:
        return
    missing = [world for world in marker.args if not (paths.ROS_MAPS / f'{world}.yaml').is_file()]
    if missing:
        reason = f"Missing official maps {', '.join(missing)} in {paths.ROS_MAPS}; run tools/fetch_ros_maps.py"
        if item.config.getoption('--require-maps') or os.environ.get('URP_ROS_MAPS'):
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)


@pytest.fixture(autouse=True)
def no_robustness_batches_unless_asked(monkeypatch):
    """An agent session would run two batches before every report; the tests of that step switch it back on."""
    from urp_lab.agent import tools
    monkeypatch.setattr(tools, 'ROBUSTNESS_RUNS', 0)


@pytest.fixture(autouse=True)
def isolated_evolve_field_cache(request, tmp_path, monkeypatch):
    if not request.node.path.name.startswith('test_evolve_'):
        return
    from urp_lab.library import fields

    isolated = tmp_path / 'isolated-fields2benchmark'
    isolated.mkdir()
    monkeypatch.setattr(fields, 'DIR', isolated)
    monkeypatch.setattr(fields, 'INDEX', isolated / 'index.json')
    # Replace memoization wrappers, rather than clearing or reading host cache entries.
    monkeypatch.setattr(fields, '_index', lru_cache(maxsize=1)(fields._index.__wrapped__))
    monkeypatch.setattr(fields, '_scenario', lru_cache(maxsize=64)(fields._scenario.__wrapped__))

    def blocked_download(*args, **kwargs):
        raise AssertionError('Evolve offline contracts must not download field data')

    monkeypatch.setattr(fields, '_download', blocked_download)


REAL_GAZEBO_GAP = paths.gazebo_gap


@pytest.fixture(autouse=True)
def simulator_present(monkeypatch):
    """Tests replace the simulator with fakes, so whether this machine has ROS must not change their outcome."""
    monkeypatch.setattr('urp_lab.paths.gazebo_gap', lambda: None)
    monkeypatch.setattr('urp_lab.agent.tools.gazebo_gap', lambda: None)
