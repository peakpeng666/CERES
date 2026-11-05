"""Repository locations shared by the gateway, worker and core modules."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / 'src' / 'lab'


def location(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default).expanduser().resolve()


DATA_HOME = location('URP_DATA_HOME', REPO / '.local-state' / 'workbench-data')
DATA_ROOT = location('URP_DATA_ROOT', REPO / 'data')
# Explicit runtime wins; existing source checkouts retain their default until deliberately migrated.
RUNTIME = location('URP_LAB_RUNTIME', DATA_HOME / 'runtime' if os.environ.get('URP_DATA_HOME') else LAB / 'runtime')
ROS_SHARE = location('URP_ROS_SHARE', Path('/opt/ros/humble/share'))
GAZEBO_SHARE = location('URP_GAZEBO_SHARE', Path('/usr/share/ignition/ignition-gazebo6'))
FUEL_CACHE = location('URP_FUEL_CACHE', REPO / 'setup/2026-10-15/cache/ignition-fuel')
RESEARCH_SRC = REPO / 'src' / 'research' / 'src'
FRONTEND = REPO / 'src' / 'frontend'
FRONTEND_DIST = FRONTEND / 'dist'
FRONTEND_ROUTING = FRONTEND / 'core' / 'routing'
DEPLOYMENT = REPO / 'setup' / '2026-10-15' / 'deployment'
ROS_INSTALL_MAPS = ROS_SHARE / 'turtlebot4_navigation/maps'
BUNDLED_MAPS = Path(__file__).resolve().parent / 'data' / 'maps'


def official_maps(installed: Path = ROS_INSTALL_MAPS) -> Path:
    """Where the official maps are read from. The bundled unmodified copies keep map scenarios working without ROS;
    they do not make ROS/Gazebo execution available."""
    explicit = os.environ.get('URP_ROS_MAPS')
    if explicit:
        return Path(explicit).expanduser().resolve()
    return installed.resolve() if installed.is_dir() else BUNDLED_MAPS


ROS_MAPS = official_maps()
USER_SCENARIOS = RUNTIME / 'scenarios'
CVRP_SELECTION_DATA = DATA_ROOT / 'research' / 'cvrp-selection-crc'


def node_command() -> list[str] | None:
    """The Node runtime for the JS baselines. The desktop app points this at its own binary instead of shipping another one."""
    explicit = os.environ.get('URP_NODE')
    if explicit:
        return [explicit]
    found = shutil.which('node')
    return [found] if found else None


def gazebo_gap() -> str | None:
    """Why Gazebo cannot start here, or None. Physics previews do not depend on it.

    The ROS bridge is compiled and fingerprinted on the machine that runs it, so an installed app has no accepted bridge
    even where ROS exists; Gazebo then comes from the container backend, on Linux as on Windows.
    """
    if os.environ.get('URP_DESKTOP') == '1':
        return '安装版的 Gazebo 在容器后端里运行，可以在 设置 → 桌面应用 开启；MuJoCo 物理预演不受影响'
    if Path('/opt/ros/humble/setup.bash').exists():
        return None
    return '这台电脑没有 ROS 2 和 Gazebo；MuJoCo 物理预演不受影响'


def ensure_research_path() -> Path:
    """Make the frozen research package importable (never modified, only imported)."""
    path = str(RESEARCH_SRC)
    if path not in sys.path:
        sys.path.insert(0, path)
    return RESEARCH_SRC


def rel(path: Path | str) -> str:
    """Repository-relative path for labels; absolute paths outside the repo are kept."""
    p = Path(path)
    try:
        return p.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(p)

LOGS = RUNTIME / 'logs'
SIM = RUNTIME / 'sim'
EXPORTS = RUNTIME / 'exports'
