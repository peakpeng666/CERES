"""Fail-closed local SDF/Fuel dependency audit; never downloads or edits assets."""
import hashlib
from pathlib import Path
from urllib.parse import urlsplit, unquote
import xml.etree.ElementTree as ET
from . import paths

ROOT = Path(__file__).resolve().parents[3]
CACHE = paths.FUEL_CACHE
SHARE = paths.ROS_SHARE


def inspect_world(world):
    if world not in ('maze', 'warehouse', 'depot'):
        raise ValueError('未知官方世界')
    source = SHARE / 'turtlebot4_ignition_bringup/worlds' / f'{world}.sdf'
    files, missing, models, references, inactive = {}, [], set(), [], []
    parsed = set()
    counts = {'include_elements': 0, 'collision_elements': 0}

    def consume(path):
        path = path.resolve()
        if path.is_dir():
            for item in sorted(path.rglob('*')):
                if item.is_file():
                    consume(item)
            return
        if not path.is_file():
            missing.append(str(path))
            return
        key = str(path)
        if key in parsed:
            return
        parsed.add(key)
        files[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix in ('.sdf', '.world', '.dae'):
            try:
                tree = ET.parse(path)
            except ET.ParseError as exc:
                missing.append(f'{path}: {exc}')
                return
            counts['collision_elements'] += len(tree.findall('.//collision'))
            counts['include_elements'] += len(tree.findall('.//include'))
            classic_uris = set(tree.findall('.//material/script/uri'))
            for elem in classic_uris:
                inactive.append({'source': str(path), 'uri': (elem.text or '').strip(),
                                 'reason': 'Gazebo Classic material scripts do not apply to Fortress; PBR textures are audited separately'})
            for elem in tree.findall('.//uri'):
                if elem in classic_uris:
                    continue
                resolve((elem.text or '').strip(), path)
            for tag in ('albedo_map', 'normal_map', 'roughness_map', 'metalness_map', 'emissive_map', 'light_map', 'environment_map', 'specular_map', 'glossiness_map'):
                for elem in tree.findall('.//pbr//'+tag):
                    resolve((elem.text or '').strip(), path)
            if path.suffix == '.dae':
                for elem in tree.iter():
                    if elem.tag.endswith('init_from') and elem.text and '.' in elem.text and not elem.text.startswith('#'):
                        resolve(elem.text.strip(), path)
        elif path.suffix.lower() == '.obj':
            for line in path.read_text(errors='replace').splitlines():
                if line.strip().startswith('mtllib '):
                    consume(path.parent / line.strip()[7:].strip())
        elif path.suffix.lower() == '.mtl':
            for line in path.read_text(errors='replace').splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2 and parts[0].lower() in ('map_kd', 'map_ka', 'map_ks', 'map_bump', 'bump', 'map_d'):
                    consume(path.parent / parts[1])

    def resolve(uri, parent):
        if not uri or uri.startswith('data:'):
            return
        if uri.startswith(('http://', 'https://')):
            url = urlsplit(uri)
            parts = [unquote(x) for x in url.path.split('/') if x]
            try:
                idx = parts.index('models')
                owner, name = parts[idx - 1], parts[idx + 1]
                tail = parts[idx + 2:]
            except (ValueError, IndexError):
                missing.append(f'不支持的外部依赖：{uri}')
                return
            references.append(uri)
            model_id = f'{url.netloc.lower()}/{owner.lower()}/models/{name.lower()}'
            models.add(model_id)
            candidates = [p for p in CACHE.glob('*/*/models/*/*/model.sdf')
                          if p.parents[4].name.lower() == url.netloc.lower()
                          and unquote(p.parents[1].name).lower() == name.lower()
                          and p.parents[3].name.lower() == owner.lower()]
            if tail and tail[0].isdigit():
                candidates = [p for p in candidates if p.parent.name == tail[0]]
                tail = tail[1:]
            if not candidates:
                missing.append(uri)
                return
            sdf = max(candidates, key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0)
            if tail and tail[0] == 'files':
                consume(sdf.parent.joinpath(*tail[1:]))
            elif tail:
                missing.append(f'无法解析 Fuel 资源路径：{uri}')
            else:
                consume(sdf)
        elif uri.startswith('model://'):
            name, _, tail = unquote(uri[8:]).partition('/')
            options = [p for p in CACHE.glob('*/*/models/*/*') if unquote(p.parent.name).lower() == name.lower()]
            if not options:
                missing.append(f'{parent}: {uri}')
            else:
                model = max(options, key=lambda p: int(p.name) if p.name.isdigit() else 0)
                models.add(f'{model.parents[3].name}/{model.parents[2].name}/models/{unquote(model.parent.name)}'.lower())
                consume(model / (tail or 'model.sdf'))
        else:
            local = unquote(uri[7:]) if uri.startswith('file://') else unquote(uri)
            consume(Path(local) if Path(local).is_absolute() else parent.parent / local)

    consume(source)
    return dict(world=world, source=str(source), complete=not missing, missing=sorted(set(missing)),
                scope='Gazebo Fortress SDF geometry, mesh, PBR/mesh textures', inactive_classic_materials=inactive,
                material_scope_reference='https://gazebosim.org/docs/fortress/comparison/',
                unique_fuel_models=len(models), model_uris=sorted(models),
                fuel_uri_occurrences=len(references), fuel_uris=sorted(set(references)), **counts, files=files)


if __name__ == '__main__':
    import json, sys
    result = inspect_world(sys.argv[1])
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['complete'] else 1)
