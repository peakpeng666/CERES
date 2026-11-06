"""Desktop mode routing without inspecting or starting the host simulator."""
from types import SimpleNamespace

import pytest

from conftest import REAL_GAZEBO_GAP
from urp_lab import paths


@pytest.mark.parametrize('source', [None, '0'])
def test_installed_desktop_requires_container_even_with_ros(monkeypatch, source):
    monkeypatch.setenv('URP_DESKTOP', '1')
    if source is None:
        monkeypatch.delenv('URP_DESKTOP_SOURCE', raising=False)
    else:
        monkeypatch.setenv('URP_DESKTOP_SOURCE', source)
    monkeypatch.setattr(paths, 'Path', lambda _: SimpleNamespace(exists=lambda: True))
    assert '容器后端' in REAL_GAZEBO_GAP()


def test_source_electron_keeps_local_ros_path(monkeypatch):
    monkeypatch.setenv('URP_DESKTOP', '1')
    monkeypatch.setenv('URP_DESKTOP_SOURCE', '1')
    monkeypatch.setattr(paths, 'Path', lambda _: SimpleNamespace(exists=lambda: True))
    assert REAL_GAZEBO_GAP() is None


def test_source_electron_still_reports_missing_ros(monkeypatch):
    monkeypatch.setenv('URP_DESKTOP', '1')
    monkeypatch.setenv('URP_DESKTOP_SOURCE', '1')
    monkeypatch.setattr(paths, 'Path', lambda _: SimpleNamespace(exists=lambda: False))
    assert '没有 ROS 2 和 Gazebo' in REAL_GAZEBO_GAP()
