"""Robot models from the installed ROS 2 packages, served to the browser's 3D views.

The URDF is expanded from the official TurtleBot4 xacro with the system ROS environment
(Python 3.10, kept apart from this 3.12 environment) and cached; meshes are read straight
from the package share directories that the URDF's ``package://`` URIs point to.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path, PurePosixPath

from . import paths

SHARE = Path('/opt/ros/humble/share')
PACKAGES = ('turtlebot4_description', 'irobot_create_description')
MODELS = {
    'turtlebot4': 'turtlebot4_description/urdf/standard/turtlebot4.urdf.xacro',
    'turtlebot4_lite': 'turtlebot4_description/urdf/lite/turtlebot4.urdf.xacro',
}
MEDIA = {'.dae': 'model/vnd.collada+xml', '.stl': 'model/stl', '.obj': 'model/obj', '.mtl': 'text/plain',
         '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}


def urdf(model: str = 'turtlebot4') -> str:
    if model not in MODELS:
        raise KeyError(model)
    src = SHARE / MODELS[model]
    if not src.is_file():
        raise FileNotFoundError(str(src))
    cache = paths.RUNTIME / 'library' / 'robots' / f'{model}.urdf'
    if cache.is_file() and cache.stat().st_mtime >= src.stat().st_mtime:
        return cache.read_text(encoding='utf-8')
    run = subprocess.run(['bash', '-c', f'source /opt/ros/humble/setup.bash && exec xacro {shlex.quote(str(src))}'],
                         capture_output=True, text=True, timeout=60)
    if run.returncode or '<robot' not in run.stdout:
        raise RuntimeError((run.stderr or run.stdout).strip()[-400:] or 'xacro 没有输出')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(run.stdout, encoding='utf-8')
    return run.stdout


def package_file(package: str, rel: str) -> tuple[Path, str]:
    """A file inside an allowed ROS package share directory, with its media type."""
    clean = PurePosixPath(rel)
    if package not in PACKAGES or clean.is_absolute() or '..' in clean.parts:
        raise PermissionError(package)
    target = SHARE / package / clean
    if not target.is_file():
        raise FileNotFoundError(str(target))
    return target, MEDIA.get(target.suffix.lower(), 'application/octet-stream')
