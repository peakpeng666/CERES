"""Vehicle / facility model inspection: glTF 2.0 (GLB or .gltf + .bin + textures), URDF, SDF models and plain meshes.

Only reads files. glTF bounds come from each primitive's POSITION accessor min/max (required by the
glTF spec) pushed through the node hierarchy; units are metres and +Y is up (glTF convention), so the
size is reported in both glTF axes and the robot frame (x forward, y left, z up) after the user picks
the model's forward axis. URDF / SDF are parsed with defusedxml; mesh URIs must resolve inside the
import or an installed ROS package, remote URIs are reported, never fetched.
"""
from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path

import numpy as np
from defusedxml import ElementTree as ET

from .safety import clean_relpath, ImportRejected
from .. import paths

GLB_MAGIC, JSON_CHUNK, BIN_CHUNK = 0x46546C67, 0x4E4F534A, 0x004E4942
ROS_SHARE = paths.ROS_SHARE
DRIVE_PATTERNS = (
    ('ackermann', re.compile(r'ackermann', re.I)),
    ('tracked', re.compile(r'tracked', re.I)),
    ('skid_steer', re.compile(r'skid', re.I)),
    ('differential', re.compile(r'diff[-_ ]?drive|DiffDrive', re.I)),
    ('omni', re.compile(r'mecanum|omni|planar[-_ ]?move', re.I)),
)


# ----------------------------------------------------------------- glTF --
def read_gltf(path: Path) -> tuple[dict, bytes | None]:
    data = path.read_bytes()
    if path.suffix.lower() == '.glb' or data[:4] == b'glTF':
        if len(data) < 20:
            raise ValueError('GLB 文件过短')
        magic, ver, length = struct.unpack_from('<III', data, 0)
        if magic != GLB_MAGIC:
            raise ValueError('不是 GLB 文件（文件头不对）')
        if ver != 2:
            raise ValueError(f'GLB 版本 {ver}，只支持 glTF 2.0')
        if length != len(data):
            raise ValueError(f'GLB 长度字段 {length} 与文件大小 {len(data)} 不符（文件可能被截断）')
        off, doc, binary = 12, None, None
        while off + 8 <= len(data):
            clen, ctype = struct.unpack_from('<II', data, off)
            chunk = data[off + 8: off + 8 + clen]
            if ctype == JSON_CHUNK:
                doc = json.loads(chunk.decode('utf-8'))
            elif ctype == BIN_CHUNK and binary is None:
                binary = chunk
            off += 8 + clen + ((4 - clen % 4) % 4 if clen % 4 else 0)
        if doc is None:
            raise ValueError('GLB 缺少 JSON 块')
        return doc, binary
    return json.loads(data.decode('utf-8')), None


def _matrix(node: dict) -> np.ndarray:
    if 'matrix' in node:
        return np.array(node['matrix'], float).reshape(4, 4).T
    t = np.array(node.get('translation', [0, 0, 0]), float)
    x, y, z, w = node.get('rotation', [0, 0, 0, 1])
    s = np.array(node.get('scale', [1, 1, 1]), float)
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * s
    m[:3, 3] = t
    return m


def gltf_bounds(doc: dict) -> dict | None:
    nodes, meshes, acc = doc.get('nodes', []), doc.get('meshes', []), doc.get('accessors', [])
    scenes = doc.get('scenes') or [{'nodes': list(range(len(nodes)))}]
    scene = scenes[doc.get('scene', 0)] if scenes else {'nodes': []}
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    triangles = 0
    stack = [(i, np.eye(4)) for i in scene.get('nodes', [])]
    seen = 0
    while stack and seen < 100000:
        i, parent = stack.pop()
        seen += 1
        node = nodes[i]
        world = parent @ _matrix(node)
        if 'mesh' in node:
            for prim in meshes[node['mesh']].get('primitives', []):
                pa = prim.get('attributes', {}).get('POSITION')
                if pa is None or 'min' not in acc[pa] or 'max' not in acc[pa]:
                    continue
                mn, mx = acc[pa]['min'], acc[pa]['max']
                corners = np.array([[x, y, z, 1] for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])])
                pts = (world @ corners.T).T[:, :3]
                lo, hi = np.minimum(lo, pts.min(0)), np.maximum(hi, pts.max(0))
                idx = prim.get('indices')
                count = acc[idx]['count'] if idx is not None else acc[pa]['count']
                if prim.get('mode', 4) == 4:
                    triangles += count // 3
        stack.extend((c, world) for c in node.get('children', []))
    if not np.isfinite(lo).all():
        return None
    return {'min': [round(float(v), 4) for v in lo], 'max': [round(float(v), 4) for v in hi],
            'size': [round(float(v), 4) for v in hi - lo], 'triangles': int(triangles)}


def inspect_gltf(path: Path, root: Path) -> dict:
    out = {'format': 'glb' if path.suffix.lower() == '.glb' else 'gltf', 'errors': [], 'warnings': [], 'external': []}
    try:
        doc, binary = read_gltf(path)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, struct.error) as exc:
        out['errors'].append(f'{path.name}：{exc}')
        return out
    asset = doc.get('asset') or {}
    if str(asset.get('version', '')).split('.')[0] != '2':
        out['errors'].append(f"glTF 版本 {asset.get('version')}，只支持 2.0")
    out.update(generator=asset.get('generator'), copyright=asset.get('copyright'),
               counts={k: len(doc.get(k, [])) for k in ('scenes', 'nodes', 'meshes', 'materials', 'textures', 'images', 'animations', 'skins')},
               extensions_used=doc.get('extensionsUsed', []), extensions_required=doc.get('extensionsRequired', []))
    for ext in doc.get('extensionsRequired', []):
        if ext not in ('KHR_texture_transform', 'KHR_materials_emissive_strength', 'KHR_mesh_quantization', 'KHR_draco_mesh_compression', 'KHR_texture_basisu'):
            out['warnings'].append(f'需要扩展 {ext}，浏览器预览可能不支持')
    for kind in ('buffers', 'images'):
        for i, item in enumerate(doc.get(kind, [])):
            uri = item.get('uri')
            if uri is None:
                if kind == 'buffers' and i == 0 and binary is None and out['format'] == 'glb':
                    out['errors'].append('GLB 缺少二进制块')
                continue
            if uri.startswith('data:'):
                continue
            entry = {'kind': kind[:-1], 'uri': uri}
            if re.match(r'^[a-z][a-z0-9+.-]*://', uri, re.I) or uri.startswith('/'):
                entry['status'] = 'blocked'
                out['errors'].append(f'{kind[:-1]} 引用外部地址 {uri}：导入不访问外部资源，请把文件一起打包')
            else:
                try:
                    from urllib.parse import unquote
                    rel = clean_relpath((path.parent.relative_to(root) / unquote(uri)).as_posix())
                    entry['status'] = 'ok' if (root / rel).is_file() else 'missing'
                    entry['path'] = rel
                except (ImportRejected, ValueError):
                    entry['status'] = 'blocked'
                if entry['status'] == 'missing':
                    (out['errors'] if kind == 'buffers' else out['warnings']).append(
                        f"缺少{'缓冲' if kind == 'buffers' else '贴图'}文件 {uri}" + ('' if kind == 'buffers' else '（模型可显示，但没有该贴图）'))
                elif entry['status'] == 'blocked':
                    out['errors'].append(f'不安全的相对路径 {uri}')
            out['external'].append(entry)
    b = gltf_bounds(doc)
    if b is None:
        out['errors'].append('没有可显示的网格（POSITION 访问器缺少 min/max）')
    else:
        out['bounds'] = b
        big = max(b['size'])
        if big > 60:
            out['warnings'].append(f'模型最大尺寸 {big:.1f}（按 glTF 约定应为米）：可能以毫米或厘米导出，请在下一步设置缩放')
        elif big < 0.05:
            out['warnings'].append(f'模型最大尺寸只有 {big:.3f} m，可能单位不对')
    return out


# ------------------------------------------------------------ plain meshes --
def inspect_mesh(path: Path) -> dict:
    out = {'format': path.suffix.lower().lstrip('.'), 'errors': [], 'warnings': []}
    try:
        if out['format'] == 'stl':
            pts = _stl_points(path)
        elif out['format'] == 'obj':
            pts = np.array([[float(v) for v in line.split()[1:4]] for line in path.read_text(errors='replace').splitlines()
                            if line.startswith('v ')], float)
        else:
            return {**out, 'bounds': None, 'warnings': ['DAE 的尺寸在浏览器载入后测量']}
    except Exception as exc:  # noqa: BLE001
        out['errors'].append(f'{path.name} 无法解析：{exc}')
        return out
    if len(pts) == 0:
        out['errors'].append(f'{path.name} 没有顶点')
        return out
    lo, hi = pts.min(0), pts.max(0)
    out['bounds'] = {'min': lo.round(4).tolist(), 'max': hi.round(4).tolist(), 'size': (hi - lo).round(4).tolist()}
    return out


def _stl_points(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if data[:5].lower() == b'solid' and b'facet' in data[:1024]:
        return np.array([[float(v) for v in m.split()] for m in re.findall(rb'vertex\s+([-\d.eE+]+\s+[-\d.eE+]+\s+[-\d.eE+]+)', data)])
    (n,) = struct.unpack_from('<I', data, 80)
    if 84 + 50 * n != len(data):
        raise ValueError('二进制 STL 长度与三角形数不符')
    arr = np.frombuffer(data, dtype=np.dtype([('n', '<3f4'), ('v', '<9f4'), ('a', '<u2')]), count=n, offset=84)
    return arr['v'].reshape(-1, 3).astype(float)


# -------------------------------------------------------------- URDF / SDF --
def resolve_uri(uri: str, base: Path, root: Path) -> tuple[str, Path | None]:
    """'bundle' / 'ros' / 'missing' / 'blocked' and the resolved file."""
    from urllib.parse import unquote
    uri = uri.strip()
    if uri.startswith('package://'):
        pkg, _, rel = uri[len('package://'):].partition('/')
        for cand in (root / pkg / rel, ROS_SHARE / pkg / rel):
            if cand.is_file() and (root in cand.parents or ROS_SHARE in cand.parents):
                return ('bundle' if root in cand.parents else 'ros'), cand
        return 'missing', None
    if uri.startswith('model://'):
        name, _, rel = uri[len('model://'):].partition('/')
        for cand in (root / name / rel, root / rel):
            if cand.is_file():
                return 'bundle', cand
        return 'missing', None
    if uri.startswith('file://'):
        uri = uri[len('file://'):]
        if uri.startswith('/'):
            return 'blocked', None
    if re.match(r'^[a-z][a-z0-9+.-]*://', uri, re.I) or uri.startswith('/'):
        return 'blocked', None
    cand = (base / unquote(uri)).resolve()
    if root.resolve() not in cand.parents:
        return 'blocked', None
    return ('bundle', cand) if cand.is_file() else ('missing', None)


def _geom(el) -> dict | None:
    if el is None:
        return None
    for kind in ('box', 'cylinder', 'sphere', 'mesh', 'plane', 'capsule', 'ellipsoid', 'polyline'):
        g = el.find(kind)
        if g is not None:
            d = {'type': kind, **{k: v for k, v in g.attrib.items()}}
            for child in g:
                if child.text and child.text.strip():
                    d[child.tag] = child.text.strip()
            return d
    return None


def _drive(texts: list[str]) -> tuple[str | None, str | None]:
    for kind, pat in DRIVE_PATTERNS:
        for t in texts:
            if pat.search(t):
                return kind, t
    return None, None


def inspect_urdf(path: Path, root: Path) -> dict:
    out = {'format': 'urdf', 'errors': [], 'warnings': [], 'meshes': []}
    try:
        tree = ET.fromstring(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 - malformed or hostile XML
        out['errors'].append(f'{path.name} 不是有效的 URDF/XML：{exc}')
        return out
    if tree.tag != 'robot':
        out['errors'].append(f'{path.name} 根元素是 <{tree.tag}>，不是 <robot>')
        return out
    if any(k.startswith('{http://www.ros.org/wiki/xacro}') or k == 'xmlns:xacro' for k in tree.attrib) or path.read_bytes().find(b'xacro:') >= 0:
        out['errors'].append('这是 Xacro（含 xacro: 宏）。上传的 Xacro 不在工作台里展开（可执行任意逻辑）；请先用 xacro 展开成 URDF 再导入，或引用本机已安装的 ROS 描述包')
        return out
    links = tree.findall('link')
    collision_links = [l.get('name') for l in links if l.find('collision') is not None]
    inertial_links = [l.get('name') for l in links if l.find('inertial') is not None]
    for l in links:
        for tag in ('visual', 'collision'):
            for v in l.findall(tag):
                g = _geom(v.find('geometry'))
                if g and g['type'] == 'mesh':
                    status, file = resolve_uri(g.get('filename', ''), path.parent, root)
                    out['meshes'].append({'link': l.get('name'), 'use': tag, 'uri': g.get('filename'), 'status': status,
                                          'path': file.relative_to(root).as_posix() if file is not None and root in file.parents else (str(file) if file else None)})
    def link_of(j, tag):
        el = j.find(tag)
        return el.get('link') if el is not None else None
    joints = [{'name': j.get('name'), 'type': j.get('type'), 'parent': link_of(j, 'parent'), 'child': link_of(j, 'child')}
              for j in tree.findall('joint')]
    plugin_texts = [p.get('filename', '') + ' ' + p.get('name', '') for p in tree.iter('plugin')]
    ros2_control = tree.find('ros2_control') is not None
    controllers = [p.text or '' for p in tree.iter('parameters')]
    drive, drive_src = _drive(plugin_texts + [h.get('name', '') + ' ' + ' '.join(c.get('name', '') for c in h) for h in tree.findall('ros2_control')])
    sensors = sorted({s.get('type') for s in tree.iter('sensor') if s.get('type')})
    missing = [m for m in out['meshes'] if m['status'] in ('missing', 'blocked')]
    for m in missing:
        out['errors' if m['use'] == 'visual' else 'warnings'].append(f"{m['link']} 的 {m['use']} 网格 {m['uri']} {'缺失' if m['status'] == 'missing' else '路径不允许'}")
    out.update(robot=tree.get('name'), links=len(links), joints=joints, collision_links=collision_links, inertial_links=inertial_links,
               drive=drive, drive_source=drive_src, ros2_control=ros2_control, controller_params=controllers[:4], sensors=sensors,
               wheel_joints=[j['name'] for j in joints if j['type'] == 'continuous'], plugins=[t.strip() for t in plugin_texts if t.strip()])
    if not links:
        out['errors'].append('URDF 没有 link')
    return out


def inspect_sdf(path: Path, root: Path) -> dict:
    out = {'format': 'sdf', 'errors': [], 'warnings': [], 'meshes': []}
    try:
        tree = ET.fromstring(path.read_bytes())
    except Exception as exc:  # noqa: BLE001
        out['errors'].append(f'{path.name} 不是有效的 SDF/XML：{exc}')
        return out
    if tree.tag != 'sdf':
        out['errors'].append(f'{path.name} 根元素是 <{tree.tag}>，不是 <sdf>')
        return out
    world = tree.find('world')
    if world is not None:
        includes = [{'uri': (i.findtext('uri') or '').strip(), 'name': (i.findtext('name') or '').strip()} for i in world.findall('include')]
        out.update(kind='world', world=world.get('name'), models=[m.get('name') for m in world.findall('model')], includes=includes,
                   plugins=[p.get('filename') for p in world.findall('plugin')], sdf_version=tree.get('version'))
        return out
    model = tree.find('model')
    if model is None:
        out['errors'].append('SDF 里既没有 <world> 也没有 <model>')
        return out
    links = model.findall('link')
    for l in links:
        for tag in ('visual', 'collision'):
            for v in l.findall(tag):
                g = _geom(v.find('geometry'))
                if g and g['type'] == 'mesh':
                    status, file = resolve_uri(g.get('uri', ''), path.parent, root)
                    out['meshes'].append({'link': l.get('name'), 'use': tag, 'uri': g.get('uri'), 'status': status,
                                          'path': file.relative_to(root).as_posix() if file is not None and root in file.parents else (str(file) if file else None)})
    plugin_texts = [(p.get('filename') or '') + ' ' + (p.get('name') or '') for p in model.iter('plugin')]
    drive, drive_src = _drive(plugin_texts)
    sensors = sorted({s.get('type') for s in model.iter('sensor') if s.get('type')})
    for m in out['meshes']:
        if m['status'] in ('missing', 'blocked'):
            out['errors' if m['use'] == 'visual' else 'warnings'].append(f"{m['link']} 的 {m['use']} 网格 {m['uri']} {'缺失' if m['status'] == 'missing' else '路径不允许'}")
    cfg = path.parent / 'model.config'
    if cfg.is_file():
        try:
            c = ET.fromstring(cfg.read_bytes())
            out['config'] = {'name': (c.findtext('name') or '').strip(), 'version': (c.findtext('version') or '').strip(),
                             'description': (c.findtext('description') or '').strip()[:400],
                             'authors': [(a.findtext('name') or '').strip() for a in c.findall('author')]}
        except Exception:  # noqa: BLE001
            out['warnings'].append('model.config 无法解析')
    out.update(kind='model', model=model.get('name'), static=(model.findtext('static') or '').strip() == 'true', links=len(links),
               collision_links=[l.get('name') for l in links if l.find('collision') is not None],
               inertial_links=[l.get('name') for l in links if l.find('inertial') is not None],
               joints=[{'name': j.get('name'), 'type': j.get('type')} for j in model.findall('joint')],
               drive=drive, drive_source=drive_src, sensors=sensors, plugins=[t.strip() for t in plugin_texts if t.strip()],
               sdf_version=tree.get('version'))
    return out


def gltf_size_robot(bounds: dict, forward: str = '+z', up: str = '+y', scale: float = 1.0) -> dict:
    """Size in the robot frame (x forward, y left, z up) for a chosen forward / up axis of the model."""
    axis = {'x': 0, 'y': 1, 'z': 2}
    size = [bounds['size'][axis[forward[-1]]], 0.0, bounds['size'][axis[up[-1]]]]
    side = ({'x', 'y', 'z'} - {forward[-1], up[-1]}).pop()
    size[1] = bounds['size'][axis[side]]
    return {'length_m': round(size[0] * scale, 4), 'width_m': round(size[1] * scale, 4), 'height_m': round(size[2] * scale, 4)}


def circumscribed_radius(length: float, width: float) -> float:
    return math.hypot(length / 2, width / 2)
