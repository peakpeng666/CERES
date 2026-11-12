"""Strict, ROS-independent v1 route scene → visualization primitives.

A benchmark visit sequence is never a collision-free or time-parametrized path.
No commands, navigation goals, or TF links to robot frames are produced here.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_TOTAL_VISITS = 100000


class SceneError(ValueError):
    pass


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneError(f"{label} must be a finite number")
    if abs(value) > 1e9 or not math.isfinite(value):
        raise SceneError(f"{label} is nonfinite or exceeds the 1e9 safety bound")
    return float(value)


def frame_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_/]*", value):
        raise SceneError("invalid frame ID; no leading slash or whitespace allowed")
    return value


def identifier(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneError(f"{label} must be an integer")
    return value


def read_json(path):
    path = Path(path)
    # Bound the actual read as well as the file size (a file can grow after stat).
    with path.open('rb') as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise SceneError("JSON exceeds 10 MiB limit")
    def reject_constant(value):
        raise SceneError(f"non-standard JSON numeric value: {value}")
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise SceneError("JSON number exceeds finite floating-point range")
        return result
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SceneError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        document = json.loads(data.decode('utf-8'), parse_constant=reject_constant,
                              parse_float=finite_float, object_pairs_hook=unique_object)
        pending = [(document, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 64:
                raise SceneError('JSON nesting exceeds 64 levels')
            children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
            pending.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
        return document
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise SceneError(f"invalid UTF-8 JSON: {exc}") from exc


@dataclass(frozen=True)
class SceneView:
    frame: str
    metric: bool
    label: str
    points: dict
    routes: tuple
    depot_id: int
    unserved: frozenset
    point_size: float
    identity_label: str
    status_label: str
    experiment_fingerprint: str


def _mapping(scene, mapping):
    if mapping is None:
        return frame_id(scene['frame']['id']), False, lambda x, y: (x, y)
    required = ('source_frame', 'source_units', 'target_frame', 'meters_per_unit',
                'yaw_radians', 'translation_m', 'calibration_evidence', 'calibrated')
    if not isinstance(mapping, dict) or any(key not in mapping for key in required):
        raise SceneError("physical display needs a complete explicit calibration")
    if mapping['calibrated'] is not True or not isinstance(mapping['calibration_evidence'], str) or not mapping['calibration_evidence'].strip():
        raise SceneError("calibration must be attested with nonempty evidence")
    if mapping['source_frame'] != scene['frame']['id'] or mapping['source_units'] != scene['units']['coordinate']:
        raise SceneError("calibration does not match scene frame/units")
    target = frame_id(mapping['target_frame'])
    if target == scene['frame']['id']:
        raise SceneError("physical display must use a distinct frame")
    scale = number(mapping['meters_per_unit'], 'meters_per_unit')
    if not 0 < scale <= 1e6:
        raise SceneError("meters_per_unit must be positive and bounded")
    yaw = number(mapping['yaw_radians'], 'yaw_radians')
    translation = mapping['translation_m']
    if not isinstance(translation, list) or len(translation) != 2:
        raise SceneError("translation_m needs two explicitly metric entries")
    tx, ty = [number(v, 'translation_m') for v in translation]
    c, s = math.cos(yaw), math.sin(yaw)
    def project(x, y):
        return (number(scale * (c*x - s*y) + tx, 'mapped x'),
                number(scale * (s*x + c*y) + ty, 'mapped y'))
    return target, True, project


def prepare_scene(scene, mapping=None, require_metric=False):
    try:
        if not isinstance(scene, dict) or type(scene.get('schema_version')) is not int or scene['schema_version'] != 1:
            raise SceneError("only schema_version 1 is supported")
        if scene['scene_type'] != 'route_scene' or scene['geometry_kind'] != 'visit_order_polyline':
            raise SceneError("expected visit-order route_scene, not a navigation path")
        if scene['simulation_ready'] is not False:
            raise SceneError("v1 cannot claim simulation readiness")
        if scene['frame']['id'] != 'benchmark_xy':
            raise SceneError("v1 source frame must be benchmark_xy; a robot frame cannot be inferred")
        if scene['frame']['axis_order'] != ['x', 'y'] or scene['frame']['is_geographic'] is not False:
            raise SceneError("only explicit non-geographic XY coordinates are supported")
        if scene['frame'].get('is_road_network') is not False:
            raise SceneError("v1 lines cannot claim a road network")
        if scene['units']['coordinate'] != 'arbitrary_distance_units':
            raise SceneError("v1 benchmark coordinates must retain arbitrary_distance_units")
        if scene['frame'].get('physical_frame_id') is not None or scene['frame'].get('meters_per_coordinate_unit') is not None:
            raise SceneError("v1 scene cannot supply implicit physical mapping")
        output_frame, metric, project = _mapping(scene, mapping)
        if require_metric and not metric:
            raise SceneError("arbitrary benchmark units cannot be treated as metres without explicit calibration")
        def display_text(value, label):
            if (not isinstance(value, str) or not 1 <= len(value) <= 512
                    or any(ord(char) < 32 for char in value)):
                raise SceneError(f"{label} must be nonempty text without control characters (max 512)")
            return value
        experiment = display_text(scene['experiment_id'], 'experiment_id')
        run = display_text(scene['run_id'], 'run_id')
        instance = display_text(scene['instance_id'], 'instance_id')
        revision = identifier(scene['revision'], 'revision')
        fingerprint = scene['experiment_fingerprint']
        if revision < 1 or not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            raise SceneError("scene needs a positive revision and SHA256 experiment fingerprint")
        status = scene['status']
        execution = display_text(status['execution_status'], 'execution_status')
        solution = display_text(status['solution_status'], 'solution_status')
        for key in ('has_candidate', 'full_service_success'):
            if type(status[key]) is not bool:
                raise SceneError(f"status.{key} must be boolean")
        identity_label = f"{experiment} / revision {revision} / {run} / instance {instance}"
        status_label = (f"execution={execution}; solution={solution}; "
                        f"candidate={status['has_candidate']}; full_service={status['full_service_success']}")
        if not isinstance(scene['task_nodes'], list) or not isinstance(scene['vehicles'], list):
            raise SceneError("task_nodes and vehicles must be lists")
        nodes = [scene['depot']] + scene['task_nodes']
        if not 1 <= len(nodes) <= 10000:
            raise SceneError("scene must contain 1..10000 nodes")
        raw_points = {}
        for node in nodes:
            nid = identifier(node['node_id'], 'node_id')
            if nid in raw_points:
                raise SceneError("duplicate node_id")
            raw_points[nid] = (number(node['x'], 'node x'), number(node['y'], 'node y'))
        depot = identifier(scene['depot']['node_id'], 'depot_id')
        points = {nid: project(*xy) for nid, xy in raw_points.items()}
        routes = []
        vehicle_ids = set()
        total_visits = 0
        if len(scene['vehicles']) > 1000:
            raise SceneError("too many vehicles")
        for route in scene['vehicles']:
            vehicle = identifier(route['vehicle_id'], 'vehicle_id')
            if vehicle in vehicle_ids:
                raise SceneError("duplicate vehicle_id")
            vehicle_ids.add(vehicle)
            visits = route['visit_node_ids']
            if not isinstance(visits, list) or not 2 <= len(visits) <= 20000:
                raise SceneError("route visit list must have 2..20000 entries")
            total_visits += len(visits)
            if total_visits > MAX_TOTAL_VISITS:
                raise SceneError(f"scene exceeds {MAX_TOTAL_VISITS} total route visits")
            if not isinstance(route['polyline'], list) or len(route['polyline']) != len(visits):
                raise SceneError("polyline and visit list disagree")
            for i, nid in enumerate(visits):
                identifier(nid, 'visit ID')
                if nid not in raw_points:
                    raise SceneError("route references an unknown node")
                pair = route['polyline'][i]
                if not isinstance(pair, list) or len(pair) != 2:
                    raise SceneError("polyline point must have XY components")
                pair = tuple(number(v, 'polyline') for v in pair)
                if pair != raw_points[nid]:
                    raise SceneError("polyline disagrees with node coordinates")
            if visits[0] != depot or visits[-1] != depot:
                raise SceneError("v1 route must start/end at its depot")
            # Preserve native vehicle IDs and repeated visits, do not deduplicate.
            routes.append((vehicle, tuple(visits), tuple(points[n] for n in visits)))
        if not isinstance(scene['unserved_task_ids'], list):
            raise SceneError("unserved_task_ids must be a list")
        unserved = frozenset(identifier(n, 'unserved node') for n in scene['unserved_task_ids'])
        if len(unserved) != len(scene['unserved_task_ids']):
            raise SceneError("duplicate unserved task ID")
        if depot in unserved or not unserved.issubset(points):
            raise SceneError("invalid unserved task ID")
        xs, ys = zip(*points.values())
        span = max(max(xs)-min(xs), max(ys)-min(ys), 1.0)
        label = ('CALIBRATED DISPLAY (m); visit order only, NOT navigation' if metric else
                 'BENCHMARK DIAGRAM; arbitrary units, NOT metres / navigation')
        return SceneView(output_frame, metric, label, points, tuple(routes), depot, unserved,
                         max(0.03, min(span * 0.012, 1e6)), identity_label, status_label, fingerprint)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise SceneError(f"malformed route-scene: {exc}") from exc
