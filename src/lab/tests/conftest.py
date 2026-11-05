import pytest

from urp_lab import paths

REAL_GAZEBO_GAP = paths.gazebo_gap


@pytest.fixture(autouse=True)
def simulator_present(monkeypatch):
    """Tests replace the simulator with fakes, so whether this machine has ROS must not change their outcome."""
    monkeypatch.setattr('urp_lab.paths.gazebo_gap', lambda: None)
    monkeypatch.setattr('urp_lab.agent.tools.gazebo_gap', lambda: None)
