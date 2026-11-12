"""Asset library: import hygiene, map semantics (ROS grid, GeoJSON, calibrated image), models, versions."""
import io
import json
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ceres_lab import paths
from ceres_lab.assets import imagecal, models, rosmap, safety


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from ceres_lab.gateway import Config, create_app
    monkeypatch.setattr(paths, 'RUNTIME', tmp_path)
    monkeypatch.setattr(paths, 'USER_SCENARIOS', tmp_path / 'scenarios')
    app = create_app(Config(runtime=tmp_path, background=False))
    return TestClient(app, base_url='http://127.0.0.1:4100', headers={'X-Workbench-Upload': '1'})


def _pgm(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), 'L').save(buf, 'PPM' if False else 'PNG')
    return buf.getvalue()


def test_ros_map_semantics_follow_map_server():
    meta = {'negate': 0, 'occupied_thresh': 0.65, 'free_thresh': 0.196, 'mode': 'trinary', 'resolution': 0.1, 'origin': [1.0, 2.0, 0.0]}  # map_saver defaults
    gray = np.array([[0, 254, 205]], float)          # black = occupied, white = free, grey = unknown
    assert rosmap.occupancy(gray, None, meta).tolist() == [[100, 0, -1]]
    fr = rosmap.frame(meta, 3, 1)
    # pixel (u=0, v=0) centre: x = 1 + 0.05, y = 2 + (1 - 0.5) * 0.1
    assert rosmap.to_world(np.array([[0.5, 0.5]]), fr['affine']) == [[1.05, 2.05]]
    assert fr['bounds'] == [1.0, 2.0, pytest.approx(1.3), pytest.approx(2.1)]


def test_ros_map_import_lock_and_geometry(client):
    grid = np.full((60, 80), 254, np.uint8)
    grid[0, :] = grid[-1, :] = 0
    grid[:, 0] = grid[:, -1] = 0
    grid[20:30, 30:40] = 0                             # an obstacle block inside
    yaml_text = b'image: field.png\nresolution: 0.1\norigin: [-4.0, -3.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n'
    r = client.post('/lab/api/assets/inspect', files=[('files', ('field.yaml', yaml_text)), ('files', ('field.png', _pgm(grid)))])
    rep = r.json()
    assert r.status_code == 200 and rep['subtype'] == 'ros_grid' and not rep['errors']
    assert rep['inspection']['frame']['bounds'] == [-4.0, -3.0, pytest.approx(4.0), pytest.approx(3.0)]
    m = client.post('/lab/api/assets/commit', json={'token': rep['token'], 'name': 'test grid'}).json()
    aid = m['id']
    assert client.post(f'/lab/api/assets/{aid}/v/1/lock', json={}).status_code == 422          # frame not confirmed
    client.post(f'/lab/api/assets/{aid}/v/1/meta', json={'meta': {'confirmed': {'frame': True}}})
    assert client.post(f'/lab/api/assets/{aid}/v/1/lock', json={}).json()['versions'][0]['locked']
    geo = client.post(f'/lab/api/assets/{aid}/v/1/planning-geometry', json={}).json()
    assert len(geo['obstacles']) == 1
    ox = [p[0] for p in geo['obstacles'][0]['polygon']]
    assert min(ox) == pytest.approx(-1.0, abs=0.15) and max(ox) == pytest.approx(0.0, abs=0.15)   # columns 30..39 → x -1.0..0.0
    forked = client.post(f'/lab/api/assets/{aid}/v/1/meta', json={'meta': {'label': 'edited'}}).json()
    assert forked['current'] == 2 and forked['versions'][0]['locked']                          # locked v1 untouched


def test_missing_resource_bad_archive_and_traversal(client):
    r = client.post('/lab/api/assets/inspect', files=[('files', ('m.yaml', b'image: gone.pgm\nresolution: 0.05\norigin: [0, 0, 0]\n'))])
    assert r.status_code == 200 and any('gone.pgm' in e for e in r.json()['errors'])
    assert client.post('/lab/api/assets/commit', json={'token': r.json()['token']}).status_code == 422
    assert client.post('/lab/api/assets/inspect', files=[('files', ('x.zip', b'PK\x03\x04junk'))]).status_code == 422
    assert client.post('/lab/api/assets/inspect', files=[('files', ('../escape.txt', b'x'))]).status_code == 422
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('../../evil.yaml', 'x')
        z.writestr('ok/readme.txt', 'x')
    r = client.post('/lab/api/assets/inspect', files=[('files', ('bundle.zip', buf.getvalue()))])
    assert any('不安全' in w for w in r.json()['warnings'])
    plain = TestClient(client.app, base_url='http://127.0.0.1:4100')
    assert plain.post('/lab/api/assets/inspect', files=[('files', ('a.txt', b'x'))]).status_code == 415   # no upload header


def test_geojson_lonlat_is_projected_not_degrees(client):
    lon, lat = 116.30, 39.99
    d = 0.001                                                          # ≈ 85 m east-west, 111 m north-south
    doc = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'role': 'boundary'},
         'geometry': {'type': 'Polygon', 'coordinates': [
             [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]],
             [[lon + 0.0004, lat + 0.0004], [lon + 0.0006, lat + 0.0004], [lon + 0.0006, lat + 0.0006], [lon + 0.0004, lat + 0.0006], [lon + 0.0004, lat + 0.0004]]]}},
        {'type': 'Feature', 'properties': {'role': 'depot'}, 'geometry': {'type': 'Point', 'coordinates': [lon + 0.0001, lat + 0.0001]}},
        {'type': 'Feature', 'properties': {'name': '采样点 1'}, 'geometry': {'type': 'Point', 'coordinates': [lon + 0.0008, lat + 0.0008]}}]}
    r = client.post('/lab/api/assets/inspect', files=[('files', ('farm.geojson', json.dumps(doc).encode()))]).json()
    assert r['subtype'] == 'vector' and r['inspection']['crs']['kind'] == 'geographic'
    assert r['inspection']['transform']['projected_crs'] == 'EPSG:32650'
    w, h = r['inspection']['summary']['size_m']
    assert 80 < w < 90 and 105 < h < 115 and r['inspection']['summary']['holes'] == 1
    m = client.post('/lab/api/assets/commit', json={'token': r['token']}).json()
    geo = client.post(f"/lab/api/assets/{m['id']}/v/1/planning-geometry", json={}).json()
    assert geo['depot'] and len(geo['tasks']) == 1 and any(o['name'] == '边界内孔洞' for o in geo['obstacles'])


def test_unknown_projected_coordinates_need_confirmation(client):
    doc = {'type': 'Feature', 'properties': {'role': 'boundary'}, 'geometry': {'type': 'Polygon', 'coordinates': [[[500000, 4000000], [500100, 4000000], [500100, 4000100], [500000, 4000000]]]}}
    r = client.post('/lab/api/assets/inspect', files=[('files', ('p.geojson', json.dumps(doc).encode()))]).json()
    assert r['errors'] and '米' in r['errors'][0]
    r2 = client.post(f"/lab/api/assets/staging/{r['token']}/reinspect", json={'assume_metres': True}).json()
    assert not r2['errors'] and r2['inspection']['transform']['assumed']


def test_image_calibration_recovers_scale_and_rotation():
    pts = [{'px': [0, 0], 'world': [0, 0]}, {'px': [100, 0], 'world': [5, 0]}, {'px': [0, 100], 'world': [0, -5]}]
    sol = imagecal.solve(pts, 'similarity')
    assert sol['scale_m_per_px'] == pytest.approx(0.05) and sol['rmse_m'] < 1e-6
    assert imagecal.apply(sol['matrix'], [[200, 200]]) == [[10.0, -10.0]]
    kd = imagecal.known_distance([0, 0], [0, 50], 10.0, [0, 0])
    assert imagecal.apply(kd['matrix'], [[10, 10]]) == [[2.0, -2.0]]


def _tiny_glb(scale: float = 1.0, image_uri: str | None = None) -> bytes:
    pos = np.array([[0, 0, 0], [scale, 0, 0], [0, scale * 0.5, 2 * scale]], np.float32)
    binary = pos.tobytes()
    doc = {'asset': {'version': '2.0', 'generator': 'test'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
           'nodes': [{'mesh': 0, 'translation': [1, 0, 0]}], 'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}],
           'buffers': [{'byteLength': len(binary)}], 'bufferViews': [{'buffer': 0, 'byteLength': len(binary)}],
           'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3', 'min': pos.min(0).tolist(), 'max': pos.max(0).tolist()}]}
    if image_uri:
        doc['images'] = [{'uri': image_uri}]
    js = json.dumps(doc).encode()
    js += b' ' * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    return struct.pack('<III', 0x46546C67, 2, total) + struct.pack('<II', len(js), 0x4E4F534A) + js + struct.pack('<II', len(binary), 0x004E4942) + binary


def test_gltf_bounds_units_and_missing_texture(tmp_path):
    p = tmp_path / 'v.glb'
    p.write_bytes(_tiny_glb(1.0, 'tex/body.png'))
    ins = models.inspect_gltf(p, tmp_path)
    assert ins['bounds']['min'] == [1.0, 0.0, 0.0] and ins['bounds']['size'] == [1.0, 0.5, 2.0]
    assert any('body.png' in w for w in ins['warnings'])
    size = models.gltf_size_robot(ins['bounds'], forward='+z', up='+y')
    assert size == {'length_m': 2.0, 'width_m': 1.0, 'height_m': 0.5}
    p.write_bytes(_tiny_glb(1000.0))
    assert any('毫米' in w for w in models.inspect_gltf(p, tmp_path)['warnings'])


def test_vehicle_layers_are_separate(client):
    r = client.post('/lab/api/assets/inspect', files=[('files', ('cart.glb', _tiny_glb(1.0)))], data={'kind': 'vehicle'}).json()
    m = client.post('/lab/api/assets/commit', json={'token': r['token'], 'kind': 'vehicle', 'name': 'cart'}).json()
    caps = m['versions'][0]['capabilities']
    assert caps['level'] in ('display', 'incomplete') and not caps['simulation']['ok']
    assert any(i['key'] == 'adapter' and not i['ok'] for i in caps['simulation']['items'])
    patch = {'planning': {'footprint': {'type': 'rect', 'length_m': 2.0, 'width_m': 1.0}, 'capacity': 8, 'speed_mps': 1.2,
                          'kinematics': 'ackermann', 'min_turn_radius_m': 3.5}, 'display': {'confirmed': True}}
    m = client.post(f"/lab/api/assets/{m['id']}/v/1/meta", json={'meta': patch}).json()
    caps = m['versions'][0]['capabilities']
    assert caps['planning']['ok'] and caps['level'] == 'planning'
    fleet_map = caps['planning']['solver']['fleet_routing']
    assert fleet_map['min_turn_radius_m'] == 3.5 and not any('转弯半径' in s for s in fleet_map['ignored'])


def test_xacro_upload_is_refused(client):
    x = b'<robot name="r" xmlns:xacro="http://www.ros.org/wiki/xacro"><xacro:property name="a" value="1"/><link name="base"/></robot>'
    r = client.post('/lab/api/assets/inspect', files=[('files', ('r.urdf', x))]).json()
    assert r['errors'] and 'Xacro' in r['errors'][0]


def test_builtin_vehicles_and_local_catalog(client):
    ids = {a['id']: a for a in client.get('/lab/api/assets').json()['assets']}
    assert ids['vehicle-field-diffdrive-builtin']['level'] == 'simulation'
    assert client.delete('/lab/api/assets/vehicle-field-diffdrive-builtin').status_code == 403
    cat = client.get('/lab/api/assets/local/catalog').json()
    assert {'fuel_models', 'ros_descriptions', 'gazebo_worlds'} <= set(cat)


def test_vehicle_cannot_claim_another_models_simulation(client):
    r = client.post('/lab/api/assets/inspect', files=[('files', ('cart.glb', _tiny_glb(1.0)))], data={'kind': 'vehicle'}).json()
    aid = client.post('/lab/api/assets/commit', json={'token': r['token'], 'kind': 'vehicle', 'name': 'cart'}).json()['id']
    bad = client.post(f'/lab/api/assets/{aid}/v/1/meta', json={'meta': {'simulation': {'adapter': 'fieldsim-diffdrive'}}})
    assert bad.status_code == 422 and bad.json()['error'] == 'adapter_incompatible'
    caps = client.get(f'/lab/api/assets/{aid}').json()['versions'][0]['capabilities']
    assert {o['id']: o['ok'] for o in caps['simulation']['adapters']} == {'fieldsim-diffdrive': False, 'tb4-nav2': False}
    # built-in vehicles are fixed: their simulation body matches the profile
    assert client.post('/lab/api/assets/vehicle-field-diffdrive-builtin/v/1/meta', json={'meta': {'planning': {'capacity': 99}}}).status_code == 403
    assert client.post('/lab/api/assets/vehicle-field-diffdrive-builtin/v/1/fork', json={}).status_code == 403


def test_meta_values_are_validated_and_frame_recomputed(client):
    grid = np.full((20, 30), 254, np.uint8)
    yaml_text = b'image: g.png\nresolution: 0.1\norigin: [0.0, 0.0, 0.0]\n'
    rep = client.post('/lab/api/assets/inspect', files=[('files', ('g.yaml', yaml_text)), ('files', ('g.png', _pgm(grid)))]).json()
    aid = client.post('/lab/api/assets/commit', json={'token': rep['token']}).json()['id']
    # the client may send any affine: the server recomputes it from resolution and origin
    m = client.post(f'/lab/api/assets/{aid}/v/1/meta', json={'meta': {'frame': {'resolution': 0.05, 'origin': [1.0, 2.0, 0.0], 'affine': [[9, 9, 9], [9, 9, 9]]}}}).json()
    fr = m['versions'][0]['meta']['frame']
    assert fr['affine'][0] == [0.05, 0.0, 1.0] and fr['bounds'] == [1.0, 2.0, pytest.approx(2.5), pytest.approx(3.0)]
    assert client.post(f'/lab/api/assets/{aid}/v/1/meta', json={'meta': {'frame': {'resolution': -1, 'origin': [0, 0, 0]}}}).status_code == 400

    r = client.post('/lab/api/assets/inspect', files=[('files', ('cart.glb', _tiny_glb(1.0)))], data={'kind': 'vehicle'}).json()
    vid = client.post('/lab/api/assets/commit', json={'token': r['token'], 'kind': 'vehicle'}).json()['id']
    for bad in ({'planning': {'speed_mps': 400}}, {'planning': {'capacity': 1.5}}, {'planning': {'kinematics': 'hover'}},
                {'display': {'forward': '+x', 'up': '-x'}}, {'planning': {'footprint': {'type': 'rect', 'length_m': 0}}}):
        assert client.post(f'/lab/api/assets/{vid}/v/1/meta', json={'meta': bad}).status_code == 400, bad
    client.post(f'/lab/api/assets/{vid}/v/1/meta', json={'meta': {'planning': {'footprint': {'type': 'rect', 'length_m': 2.0, 'width_m': 1.0}}}})
    m = client.post(f'/lab/api/assets/{vid}/v/1/meta', json={'meta': {'planning': {'footprint': {'type': 'circle', 'radius_m': 0.4}}}}).json()
    assert m['versions'][0]['meta']['planning']['footprint'] == {'type': 'circle', 'radius_m': 0.4}   # replaced, not merged
    src = client.post(f'/lab/api/assets/{vid}/v/1/meta', json={'meta': {}, 'source': {'license': 'CC-BY-4.0', 'type': 'forged'}}).json()['versions'][0]['source']
    assert src['license'] == 'CC-BY-4.0' and src['type'] == 'upload'                                  # provenance type stays as recorded


def test_geojson_obstacle_kinds_map_to_editor_kinds(client):
    doc = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'role': 'boundary'}, 'geometry': {'type': 'Polygon', 'coordinates': [[[0, 0], [50, 0], [50, 40], [0, 40], [0, 0]]]}},
        {'type': 'Feature', 'properties': {'type': 'pole', 'name': 'P1'}, 'geometry': {'type': 'Point', 'coordinates': [10, 10]}},
        {'type': 'Feature', 'properties': {'role': 'nogo'}, 'geometry': {'type': 'Polygon', 'coordinates': [[[30, 30], [35, 30], [35, 35], [30, 35], [30, 30]]]}}]}
    rep = client.post('/lab/api/assets/inspect', files=[('files', ('f.geojson', json.dumps(doc).encode()))], data={'assume_metres': 'true'}).json()
    aid = client.post('/lab/api/assets/commit', json={'token': rep['token']}).json()['id']
    geo = client.post(f'/lab/api/assets/{aid}/v/1/planning-geometry', json={}).json()
    kinds = {o['name']: (o['kind'], o.get('nogo')) for o in geo['obstacles']}
    assert kinds['P1'] == ('other', False) and ('other', True) in kinds.values()
