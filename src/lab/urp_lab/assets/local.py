"""Resources already on this machine: the Gazebo Fuel cache used by the simulations, installed ROS
robot-description packages and Gazebo example / TurtleBot4 worlds. Read-only; files are served to the
browser previews only from these roots."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from defusedxml import ElementTree as ET

from .. import paths

ROOTS = {
    'fuel': paths.FUEL_CACHE,
    'ros': paths.ROS_SHARE,
    'gz': paths.GAZEBO_SHARE,
}
GZ_WORLD_PICKS = ('diff_drive.sdf', 'diff_drive_skid.sdf', 'ackermann_steering.sdf', 'tracked_vehicle_simple.sdf',
                  'shapes.sdf', 'empty.sdf', 'heightmap.sdf', 'polylines.sdf')


def root_path(key: str) -> Path:
    if key not in ROOTS:
        raise KeyError(key)
    return ROOTS[key]


def local_file(key: str, rel: str) -> Path:
    base = root_path(key).resolve()
    target = (base / unquote(rel)).resolve()
    if base not in target.parents or not target.is_file():
        raise FileNotFoundError(rel)
    return target


def _config(path: Path) -> dict:
    try:
        c = ET.fromstring(path.read_bytes())
    except Exception:  # noqa: BLE001
        return {}
    sdf = c.find('sdf')
    return {'name': (c.findtext('name') or '').strip(), 'version': (c.findtext('version') or '').strip(),
            'description': ' '.join((c.findtext('description') or '').split())[:300],
            'authors': [(a.findtext('name') or '').strip() for a in c.findall('author') if (a.findtext('name') or '').strip()],
            'sdf': (sdf.text or '').strip() if sdf is not None else 'model.sdf'}


def _size(d: Path) -> int:
    return sum(p.stat().st_size for p in d.rglob('*') if p.is_file())


def fuel_models() -> list[dict]:
    base = ROOTS['fuel']
    best: dict[tuple[str, str], dict] = {}
    for cfg in base.glob('*/*/models/*/*/model.config'):
        vdir = cfg.parent
        info = _config(cfg)
        sdf = vdir / (info.get('sdf') or 'model.sdf')
        if not sdf.is_file():
            continue
        owner = vdir.parent.parent.parent.name
        name = unquote(vdir.parent.name)
        try:
            version = int(vdir.name)
        except ValueError:
            version = 0
        thumbs = sorted(p.relative_to(base).as_posix() for p in (vdir / 'thumbnails').glob('*.png'))[:4] if (vdir / 'thumbnails').is_dir() else []
        entry = {'root': 'fuel', 'path': vdir.relative_to(base).as_posix(), 'sdf': sdf.relative_to(base).as_posix(),
                 'owner': owner, 'name': name, 'title': info.get('name') or name, 'version': version,
                 'description': info.get('description', ''), 'authors': info.get('authors', []), 'thumbnails': thumbs,
                 'size_bytes': _size(vdir), 'source_uri': f'https://fuel.gazebosim.org/1.0/{owner}/models/{name}/{version}',
                 'host': vdir.parent.parent.parent.parent.name}
        key = (owner.lower(), name.lower())
        if key not in best or version > best[key]['version']:
            best[key] = entry
    return sorted(best.values(), key=lambda e: (e['owner'].lower(), e['name'].lower()))


def ros_descriptions() -> list[dict]:
    out = []
    for d in sorted(ROOTS['ros'].glob('*_description')):
        files = sorted(p for p in d.rglob('*') if p.suffix in ('.urdf', '.xacro') and p.is_file())
        tops = [p for p in files if p.name.endswith(('.urdf.xacro', '.urdf')) and 'urdf' in p.parts]
        if not tops:
            continue
        out.append({'root': 'ros', 'package': d.name, 'entries': [p.relative_to(ROOTS['ros']).as_posix() for p in tops][:12],
                    'meshes': sum(1 for p in d.rglob('*') if p.suffix.lower() in ('.dae', '.stl', '.obj', '.glb'))})
    return out


def gazebo_worlds() -> list[dict]:
    out = []
    tb4 = ROOTS['ros'] / 'turtlebot4_ignition_bringup' / 'worlds'
    for p in sorted(tb4.glob('*.sdf')):
        out.append({'root': 'ros', 'path': p.relative_to(ROOTS['ros']).as_posix(), 'name': p.stem, 'group': 'TurtleBot4 仿真世界', 'size_bytes': p.stat().st_size})
    gz = ROOTS['gz'] / 'worlds'
    for name in GZ_WORLD_PICKS:
        p = gz / name
        if p.is_file():
            out.append({'root': 'gz', 'path': p.relative_to(ROOTS['gz']).as_posix(), 'name': p.stem, 'group': 'Gazebo Fortress 示例', 'size_bytes': p.stat().st_size})
    return out


def catalog() -> dict:
    return {'fuel_models': fuel_models(), 'ros_descriptions': ros_descriptions(), 'gazebo_worlds': gazebo_worlds(),
            'roots': {k: str(v) for k, v in ROOTS.items()}}
