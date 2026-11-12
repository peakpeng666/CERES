"""Non-ideal field execution: ground friction partitioning, localization observation
error model and the estimator the controller is allowed to use.

Scope declarations (these travel with every record):
- Ground zones are *Coulomb friction partitions*: coplanar low-grip patches in the rigid
  plane. They are a low-adhesion proxy, not a soil-mechanics model (no sinkage,
  compaction or shear-strength parameters); every μ is an uncalibrated engineering
  assumption chosen for synthetic sensitivity tests, not inferred from any soil sample.
- Slip itself is *not* injected: it emerges in DART when the demanded contact force
  exceeds μ·N. Wheel odometry integrates wheel angles, so it inherits the slip error;
  no synthetic odometry noise is added on top by default.
- Absolute-position fixes are synthetic observations of the truth pose under a
  declared error model (bias + random-walk drift + noise + latency + outage
  windows). The estimator fuses wheel-derived body twists with those fixes; the
  controller only ever sees estimator output. Truth is recorded for independent
  evaluation only and never feeds control in this mode.
"""
from __future__ import annotations

import copy
import math
import random
from xml.etree import ElementTree as ET

SCHEMA = 'urp.field-execution/2'

MU_MIN, MU_MAX = 0.03, 1.0
# Buried support boxes have coplanar top faces: no raised patch and no underlying
# overlapping plane. The same tiling with uniform mu is the geometry control.
ZONE_THICKNESS_M = 0.10
PLANE_Z = -0.01                     # matches the base ground plane pose in fieldworld
MAX_FIX_GAP_S = 15.0                # controller stops after this long without a fix
SLIP_GATE_M = 0.6                   # sustained estimator innovation → slip suspicion
SLIP_SUSTAIN_S = 3.0
SLIP_CLEAR_M = 0.3
SLIP_CLEAR_S = 2.0
SLIP_CREEP_SCALE = 0.35
SLIP_CREEP_MAX_S = 30.0

GROUND_PRESETS = {
    'none': {'label': '均匀地面（理想参考）', 'zones': []},
    'wet_patch': {'label': '低附着斑块 μ≈0.22', 'zones': [
        {'mu': 0.22, 'size': [9.0, 7.0], 'at_xy': [0.45, 0.5], 'label': '低附着斑块'}]},
    'loose_strip': {'label': '低附着带 μ≈0.12 ×2', 'zones': [
        {'mu': 0.12, 'size': [10.0, 6.0], 'at_xy': [0.35, 0.35], 'label': '低附着带 A'},
        {'mu': 0.12, 'size': [10.0, 6.0], 'at_xy': [0.70, 0.65], 'label': '低附着带 B'}]},
    'mud_extreme': {'label': '极低附着 μ≈0.05（压力测试）', 'zones': [
        {'mu': 0.05, 'size': [14.0, 10.0], 'at_xy': [0.45, 0.5], 'label': '极低附着区（压力测试）'}]},
}
LOCALIZATION_PRESETS = {
    'survey': {'label': '合成低偏置：σ2 cm · 偏置≈4 cm（未标定）', 'gnss': {
        'fix_hz': 2.0, 'noise_std_m': 0.02, 'bias_xy_m': [0.03, -0.02], 'walk_std_m_sqrt_s': 0.001,
        'latency_s': 0.15, 'outages': []}},
    'truth': {'label': '真值定位（理想参考）', 'gnss': None},
    'light': {'label': '轻度误差：2 Hz · σ5 cm · 偏置≈15 cm', 'gnss': {
        'fix_hz': 2.0, 'noise_std_m': 0.05, 'bias_xy_m': [0.12, -0.08], 'walk_std_m_sqrt_s': 0.0015,
        'latency_s': 0.1, 'outages': []}},
    'drift_outage': {'label': '中度漂移 + 10 s 失锁', 'gnss': {
        'fix_hz': 1.0, 'noise_std_m': 0.10, 'bias_xy_m': [0.25, 0.18], 'walk_std_m_sqrt_s': 0.004,
        'latency_s': 0.2, 'outages': [{'t_start_s': 60.0, 'duration_s': 10.0}]}},
    'severe': {'label': '严重漂移 + 20 s 失锁（压力测试）', 'gnss': {
        'fix_hz': 1.0, 'noise_std_m': 0.25, 'bias_xy_m': [0.8, -0.6], 'walk_std_m_sqrt_s': 0.01,
        'latency_s': 0.3, 'outages': [{'t_start_s': 50.0, 'duration_s': 20.0}, {'t_start_s': 130.0, 'duration_s': 8.0}]}},
    'hold': {'label': '轻度误差 + 20 s 失锁（保持路径专测）', 'gnss': {
        'fix_hz': 2.0, 'noise_std_m': 0.05, 'bias_xy_m': [0.1, -0.1], 'walk_std_m_sqrt_s': 0.001,
        'latency_s': 0.1, 'outages': [{'t_start_s': 60.0, 'duration_s': 20.0}]}},
}
PROVENANCE = {
    'ground': '合成敏感性参数：无实测或可迁移标定依据；仅低附着代理，不代表指定土壤或轮胎',
    'localization': '合成定位观测参数：偏置/随机游走/噪声/延迟/失锁；无接收机或现场标定依据',
    'stress': '压力测试参数：用于触发保护或失败路径，不得解读为可执行性结论',
}


def reset_guard_evidence(state):
    """An interruption cannot count toward sustained high or low innovation."""
    state['since'] = state['clear_since'] = None


def advance_innovation_guard(state, innovation, fresh, driving, t, cfg):
    """Pure transition over controller-side evidence; returns a new guard event.

    Preserve the total degraded-mode deadline across interrupted observations,
    but require a continuous fresh low-innovation interval before recovery.
    This does not identify physical slip or certify actual body stopping.
    """
    if fresh and innovation > cfg['slip_gate_m']:
        if state['since'] is None:
            state['since'] = t
        state['clear_since'] = None
        if state['creep_since'] is None and t - state['since'] >= cfg['slip_sustain_s'] and driving:
            state['creep_since'] = t
            return 'creep'
    elif state['creep_since'] is not None and fresh and innovation < cfg['slip_clear_m']:
        if state['clear_since'] is None:
            state['clear_since'] = t
        if t - state['clear_since'] >= cfg['slip_clear_s']:
            state.update(since=None, creep_since=None, clear_since=None)
            return 'recovered'
    else:
        reset_guard_evidence(state)
    if state['creep_since'] is not None and t - state['creep_since'] > cfg['slip_creep_max_s']:
        return 'stop'
    return None


def slip_experiment(execution, physics) -> bool:
    """True when a field session is outside the frozen no-slip reference: declared
    perturbed ground geometry, or physical parameters deviating from the reference profile
    (friction or wheel contact). Such sessions measure slip instead of gating it."""
    if (execution or {}).get('ground', {}).get('zones'):
        return True
    p = physics or {}
    return (p.get('ground_friction', 1.0) != 1.0 or p.get('wheel_friction', 1.0) != 1.0
            or p.get('wheel_contact', 'sphere') != 'sphere'
            or p.get('wheel_lateral_friction') not in (None, 1.0))


def _polyline_stats(points):
    total, cum = 0.0, [0.0]
    for a, b in zip(points, points[1:]):
        total += math.dist(a, b)
        cum.append(total)
    return total, cum


def _point_at(points, cum, s):
    if not points:
        return None
    s = max(0.0, min(cum[-1], s))
    for i in range(1, len(points)):
        if cum[i] >= s or i == len(points) - 1:
            seg = cum[i] - cum[i - 1]
            u = 0.0 if seg < 1e-9 else (s - cum[i - 1]) / seg
            a, b = points[i - 1], points[i]
            return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
    return tuple(points[-1])


def _rects_overlap(a, b, tol=1e-6):
    return not (a[2] <= b[0] + tol or b[2] <= a[0] + tol or a[3] <= b[1] + tol or b[3] <= a[1] + tol)


def _place_zone(spec, routes, bounds, keep_clear):
    """Map-fixed placement, independent of the solved route or algorithm."""
    minx, miny, maxx, maxy = bounds
    fx, fy = spec['at_xy']
    cx, cy = minx + fx * (maxx - minx), miny + fy * (maxy - miny)
    w, d = spec['size']
    for _ in range(24):
        x0, y0, x1, y1 = cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2
        x0, y0 = max(minx + 0.5, x0), max(miny + 0.5, y0)
        x1, y1 = min(maxx - 0.5, x1), min(maxy - 0.5, y1)
        if x1 - x0 < 0.6 * w or y1 - y0 < 0.6 * d:
            raise ValueError('地面分区在田块边界内放不下，换更小的预设')
        rect = [x0, y0, x1, y1]
        if all(not _rects_overlap(rect, [px - r, py - r, px + r, py + r]) for px, py, r in keep_clear):
            return rect
        cx += 4.0
    raise ValueError('地面分区无法避开车库/停车位，拒绝生成')


def validate_execution(body, inst, routes):
    """Normalize the POSTed execution config; returns None for the ideal reference."""
    if body is None or body == {}:
        return None
    if not isinstance(body, dict):
        raise ValueError('execution 须为 JSON 对象')
    unknown = set(body) - {'ground_preset', 'ground_zones', 'localization_preset', 'seed', 'slip_protection'}
    if unknown:
        raise ValueError(f'execution 含未知字段：{sorted(unknown)}')
    ground_key = body.get('ground_preset', 'none')
    loc_key = body.get('localization_preset', 'truth')
    if not isinstance(ground_key, str) or ground_key not in GROUND_PRESETS:
        raise ValueError(f'ground_preset 仅支持 {sorted(GROUND_PRESETS)}')
    if not isinstance(loc_key, str) or loc_key not in LOCALIZATION_PRESETS:
        raise ValueError(f'localization_preset 仅支持 {sorted(LOCALIZATION_PRESETS)}')
    seed = body.get('seed', 20261018)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise ValueError('execution.seed 须为 0–2^31-1 的整数')
    protection = body.get('slip_protection', True)
    if not isinstance(protection, bool):
        raise ValueError('execution.slip_protection 须为布尔值')
    if ground_key == 'none' and loc_key == 'truth' and 'ground_zones' not in body:
        return None                       # nothing non-ideal requested

    bounds = tuple(inst['boundary'] and _bounds(inst['boundary']))
    depot = inst['depot']
    keep_clear = [(float(d[0]), float(d[1]), 2.5) for d in inst.get('depots') or [depot]]
    from shapely.geometry import Polygon, box, Point
    boundary = Polygon(inst['boundary'])
    zones = []
    preset = GROUND_PRESETS[ground_key]
    for spec in preset['zones']:
        mu = spec['mu']
        if not MU_MIN <= mu <= MU_MAX:
            raise ValueError(f'地面分区 μ={mu} 超出支持范围 {MU_MIN}–{MU_MAX}')
        rect = _place_zone(spec, routes, bounds, keep_clear)
        if not boundary.covers(box(*rect)):
            raise ValueError('预设地面分区超出实际多边形边界，请指定边界内的固定分区')
        for other in zones:
            if _rects_overlap(rect, other['rect']):
                raise ValueError('地面分区之间不允许重叠')
        zones.append({'label': spec['label'], 'mu': float(mu), 'rect': [round(c, 3) for c in rect],
                      'provenance': PROVENANCE['stress'] if mu <= 0.08 else PROVENANCE['ground']})
    if 'ground_zones' in body:
        if ground_key != 'none':
            raise ValueError('ground_zones 与 ground_preset 不能同时指定')
        supplied = body['ground_zones']
        if not isinstance(supplied, list) or not 1 <= len(supplied) <= 8:
            raise ValueError('ground_zones 需要 1–8 个地图固定矩形')
        for i, zone in enumerate(supplied):
            if not isinstance(zone, dict) or set(zone) != {'rect', 'mu'}:
                raise ValueError('地面分区仅接受 rect 和 mu')
            r, mu = zone['rect'], zone['mu']
            if (not isinstance(r, list) or len(r) != 4 or any(isinstance(x, bool) or not isinstance(x, (int,float)) or not math.isfinite(x) for x in r)
                    or isinstance(mu, bool) or not isinstance(mu, (int,float)) or not math.isfinite(mu) or not MU_MIN <= mu <= MU_MAX):
                raise ValueError('地面分区坐标或摩擦无效')
            if r[2] - r[0] < .1 or r[3] - r[1] < .1 or not boundary.covers(box(*r)):
                raise ValueError('地面分区须位于任务边界内且尺寸为正')
            if any(_rects_overlap(r, z['rect']) for z in zones) or any(box(*r).distance(Point(d)) < 2.5 for d in inst.get('depots') or [depot]):
                raise ValueError('地面分区重叠或过于接近车库')
            zones.append({'label': f'固定分区 {i+1}', 'rect': list(r), 'mu': float(mu), 'provenance': PROVENANCE['ground']})
        ground_key = 'custom'
        preset = {'label': '地图固定摩擦分区'}
    gnss = copy.deepcopy(LOCALIZATION_PRESETS[loc_key]['gnss'])
    if gnss is not None:
        gnss['walk_bound_m'] = 0.5
        for k in ('fix_hz', 'noise_std_m', 'bias_xy_m', 'walk_std_m_sqrt_s', 'latency_s'):
            if k not in gnss:
                raise ValueError(f'localization 预设缺少 {k}')
    return {
        'schema': SCHEMA,
        'mode': 'nonideal',
        'ground': {'preset': ground_key, 'preset_label': preset['label'],
                   'model': 'coulomb_friction_partition', 'zones': zones,
                   'geometry': 'coplanar_partition/1' if zones else 'uniform_plane',
                   'support_rect_m': [bounds[0]-30,bounds[1]-30,bounds[2]+30,bounds[3]+30],
                   'declared_as': '低附着地面代理（摩擦分区），非土壤力学；μ 未标定',
                   'base_mu_note': '分区以外地面沿用物理配置的均匀摩擦'},
        'localization': {'preset': loc_key, 'preset_label': LOCALIZATION_PRESETS[loc_key]['label'],
                         'gnss': gnss, 'max_fix_gap_s': MAX_FIX_GAP_S,
                         'slip_gate_m': SLIP_GATE_M, 'slip_sustain_s': SLIP_SUSTAIN_S,
                         'slip_clear_m': SLIP_CLEAR_M, 'slip_clear_s': SLIP_CLEAR_S,
                         'slip_creep_scale': SLIP_CREEP_SCALE, 'slip_creep_max_s': SLIP_CREEP_MAX_S,
                         'protection': protection,
                         'declared_as': '合成误差模型绝对位置观测 + 轮式里程计融合估计；未标定',
                         'odom_noise_note': '轮式里程计不加合成噪声：滑移误差已由物理接触产生'},
        'seed': seed,
        'estimator': {'schema': 'urp.fixed-lag-planar-ekf/1', 'history_s': 3.0,
            'process_covariance': 'diag(0.08^2 + 0.02^2*abs(v), 0.08^2 + 0.02^2*abs(v), 0.03^2 + 0.04^2*abs(w))*dt; SI units',
            'initial_position_std_m': 0.05, 'initial_yaw_std_rad': 0.05,
            'calibration': 'engineering assumptions, uncalibrated'},
        'controller_policy': {'task_target_radius_m': 0.05, 'terminal_target_radius_m': 0.10,
            'stationary_confirmation_s': 1.0,
            'task_acceptance': 'unchanged 0.45 m transit / 0.20 m corner; independent truth stationary dwell',
            'claim': 'tighter estimated targets, not a bound on unknown localization bias'},
        'ground_placement': 'map coordinates fixed before algorithm execution, independent of route',
        'planner_knowledge': '求解与在线控制均不接收地面分区或定位误差参数；真值仅用于独立评价',
        'provenance': {'ground': PROVENANCE['ground'], 'localization': PROVENANCE['localization'],
                       'stress': PROVENANCE['stress']},
    }


def _bounds(boundary):
    xs = [p[0] for p in boundary]
    ys = [p[1] for p in boundary]
    return min(xs), min(ys), max(xs), max(ys)


def zone_sdf(zone, index):
    """One buried static box; top matches the rest of the rigid ground exactly."""
    x0, y0, x1, y1 = zone['rect']
    w, d = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    t = ZONE_THICKNESS_M
    return f"""
      <collision name='zone_{index}'><pose>{cx:.9f} {cy:.9f} {PLANE_Z - t / 2:.9f} 0 0 0</pose>
        <geometry><box><size>{w:.9f} {d:.9f} {t}</size></box></geometry>
        <surface><friction><ode><mu>{zone['mu']}</mu><mu2>{zone['mu']}</mu2></ode></friction></surface></collision>
      <visual name='zone_{index}'><pose>{cx:.3f} {cy:.3f} 0.043 0 0 0</pose>
        <geometry><box><size>{w:.3f} {d:.3f} 0.004</size></box></geometry>
        <material><ambient>0.36 0.22 0.08 0.85</ambient><diffuse>0.36 0.22 0.08 0.85</diffuse></material></visual>"""


def ground_collisions(zones, bounds, mu):
    """Tile the support rectangle minus the zones, in the ground model's frame.

    Axis-aligned cuts make the union exact, with disjoint interiors and identical
    top heights. Each patch retains its own friction collision; no plane remains
    underneath it to compete for contacts. At most (2n+1)^2 cells for n zones.
    """
    minx,miny,maxx,maxy=bounds
    ox,oy=(minx+maxx)/2,(miny+maxy)/2
    xs=sorted({minx-30,maxx+30,*[z['rect'][k] for z in zones for k in (0,2)]})
    ys=sorted({miny-30,maxy+30,*[z['rect'][k] for z in zones for k in (1,3)]})
    cells=[]
    for x0,x1 in zip(xs,xs[1:]):
        for y0,y1 in zip(ys,ys[1:]):
            x,y=(x0+x1)/2,(y0+y1)/2
            if any(z['rect'][0] < x < z['rect'][2] and z['rect'][1] < y < z['rect'][3] for z in zones):
                continue
            cells.append(f"""<collision name='cell_{len(cells)}'>
              <pose>{x-ox:.9f} {y-oy:.9f} {-ZONE_THICKNESS_M/2} 0 0 0</pose>
              <geometry><box><size>{x1-x0:.9f} {y1-y0:.9f} {ZONE_THICKNESS_M}</size></box></geometry>
              <surface><friction><ode><mu>{mu}</mu><mu2>{mu}</mu2></ode></friction></surface></collision>""")
    return ''.join(cells)


def audit_ground_zones(sdf_path, execution):
    """Independently re-check the emitted zone boxes against the frozen execution config."""
    errors = []
    zones = (execution or {}).get('ground', {}).get('zones') or []
    root = ET.parse(sdf_path).getroot()
    world = root.find('world')
    model = world.find("model[@name='ground_zones']") if world is not None else None
    if not zones:
        if model is not None:
            errors.append('理想执行不应包含 ground_zones 模型')
        return {'valid': not errors, 'errors': errors, 'zones': 0}
    if model is None:
        return {'valid': False, 'errors': ['执行配置声明了地面分区，但 SDF 缺少 ground_zones 模型'], 'zones': len(zones)}
    link = model.find('link')
    def vector(element, path, want, tol=1e-6, default=None):
        try:
            values = list(map(float, element.findtext(path, default).split()))
            return len(values) == len(want) and all(math.isfinite(a) and abs(a-b) <= tol for a,b in zip(values,want))
        except (AttributeError, TypeError, ValueError):
            return False
    if not vector(model, 'pose', [0]*6, default='0 0 0 0 0 0') or not vector(link, 'pose', [0]*6, default='0 0 0 0 0 0'):
        errors.append('地面分区模型/链接存在未声明的坐标变换')
    collisions = link.findall('collision') if link is not None else []
    visuals = link.findall('visual') if link is not None else []
    if len(collisions) != len(zones) or len(visuals) != len(zones):
        errors.append('SDF 地面分区数量与执行配置不一致')
    for i, zone in enumerate(zones):
        col = next((c for c in collisions if c.get('name') == f'zone_{i}'), None)
        vis = next((v for v in visuals if v.get('name') == f'zone_{i}'), None)
        if col is None or vis is None:
            errors.append(f'地面分区 {i} 缺少碰撞或可视几何')
            continue
        x0, y0, x1, y1 = zone['rect']
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        want_pose = [cx, cy, PLANE_Z - ZONE_THICKNESS_M / 2]
        want_size = [x1 - x0, y1 - y0, ZONE_THICKNESS_M]
        if not vector(col, 'pose', want_pose + [0]*3, 1e-7) or not vector(col, 'geometry/box/size', want_size, 1e-7):
            errors.append(f'地面分区 {i} 位置或尺寸与冻结配置不一致')
        if not vector(vis, 'pose', [cx,cy,.043,0,0,0], 6e-4) or not vector(vis, 'geometry/box/size', want_size[:2]+[.004], 6e-4):
            errors.append(f'地面分区 {i} 可视几何与冻结配置不一致')
        for k in ('mu', 'mu2'):
            if not vector(col, f'surface/friction/ode/{k}', [zone['mu']]):
                errors.append(f'地面分区 {i} {k} 与冻结配置不一致')
        if model.findtext('static') != 'true':
            errors.append('地面分区模型必须是静态的')
    # Independently verify the complete support, not merely the coloured patch.
    from shapely.geometry import box
    from shapely.ops import unary_union
    try:
        support = execution['ground']['support_rect_m']
        if execution['ground']['geometry'] != 'coplanar_partition/1':
            raise ValueError('未知分区几何版本')
        base = world.find("model[@name='ground']")
        base_link = base.find('link')
        ox,oy=(support[0]+support[2])/2,(support[1]+support[3])/2
        if (base.findtext('static') != 'true'
                or not vector(base,'pose',[ox,oy,PLANE_Z,0,0,0],1e-7)
                or not vector(base_link,'pose',[0]*6,default='0 0 0 0 0 0')):
            raise ValueError('基础地面变换或静态属性不一致')
        pieces=[]
        for c in base_link.findall('collision'):
            pose=list(map(float,c.findtext('pose').split()))
            size=list(map(float,c.findtext('geometry/box/size').split()))
            if (len(pose)!=6 or len(size)!=3 or not all(math.isfinite(x) for x in pose+size)
                    or min(size)<=0 or max(abs(x) for x in pose[3:])>1e-7
                    or abs(pose[2]+size[2]/2)>1e-7):
                raise ValueError('基础地面不是共面正尺寸盒体')
            pieces.append(box(ox+pose[0]-size[0]/2,oy+pose[1]-size[1]/2,
                              ox+pose[0]+size[0]/2,oy+pose[1]+size[1]/2))
        pieces += [box(*z['rect']) for z in zones]
        union=unary_union(pieces)
        if abs(sum(p.area for p in pieces)-union.area)>1e-5 or union.symmetric_difference(box(*support)).area>1e-5:
            raise ValueError('地面分区存在重叠、孔洞或覆盖越界')
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        errors.append(f'共面支撑审计失败：{exc}')
    return {'valid': not errors, 'errors': errors, 'zones': len(zones),
            'scope': '分区盒的位姿/尺寸/摩擦与冻结执行配置核对；不证明摩擦值等于真实土壤'}


class LocalizationModel:
    """Synthetic absolute-position sensor: turns truth into delayed, biased, noisy,
    occasionally-dropped fixes. Deterministic under its seed."""

    def __init__(self, gnss: dict | None, seed: int):
        self.cfg = gnss
        self.rng = random.Random(seed)
        self.next_fix_t = None
        self.previous_truth = None
        self.drift = [0.0, 0.0]
        self.truth_inputs = []
        self.bias = [float(gnss['bias_xy_m'][0]), float(gnss['bias_xy_m'][1])] if gnss else [0.0, 0.0]
        self.pending: list[dict] = []      # generated, waiting for availability time
        self.observations: list[list] = []  # [t_measure, t_available, x, y, delivered(0/1), reason]

    def _in_outage(self, t):
        for w in (self.cfg or {}).get('outages') or []:
            if w['t_start_s'] <= t < w['t_start_s'] + w['duration_s']:
                return True
        return False

    def on_truth(self, x, y, t):
        """Called at truth rate; generates fixes at the scheduled fix times."""
        if self.cfg is None:
            return
        if not all(math.isfinite(v) for v in (x, y, t)):
            raise ValueError('观测生成器真值输入无效')
        self.truth_inputs.append([t, x, y])
        hz = float(self.cfg['fix_hz'])
        if self.previous_truth is None:
            self.previous_truth = (t, x, y)
            self.next_fix_t = (math.floor(t * hz + 1e-8) + 1) / hz
            return
        pt, px, py = self.previous_truth
        if t <= pt:
            raise ValueError('观测生成器源时间必须严格递增')
        while self.next_fix_t <= t + 1e-9:
            t_m = self.next_fix_t
            if t_m > 1e-9:                # no fix exactly at t=0
                dt = 1.0 / hz
                std = float(self.cfg['walk_std_m_sqrt_s'])
                for i in (0, 1):
                    bound = float(self.cfg.get('walk_bound_m', .5))
                    self.drift[i] = max(-bound, min(bound, self.drift[i] + self.rng.gauss(0, std * math.sqrt(dt))))
                u = (t_m - pt) / (t - pt)
                obs_x = px + u * (x - px) + self.bias[0] + self.drift[0] + self.rng.gauss(0, float(self.cfg['noise_std_m']))
                obs_y = py + u * (y - py) + self.bias[1] + self.drift[1] + self.rng.gauss(0, float(self.cfg['noise_std_m']))
                reason = 'truth_gap' if t - pt > .25 + 1e-8 else ('outage' if self._in_outage(t_m) else None)
                drop = reason is not None
                fix = {'t_measure': t_m, 't_available': t_m + float(self.cfg['latency_s']),
                       'x': obs_x, 'y': obs_y, 'dropped': drop, 'reason': reason,
                       '_row': len(self.observations)}
                self.observations.append([round(t_m, 4), round(fix['t_available'], 4), round(obs_x, 4),
                                          round(obs_y, 4), 0, fix['reason'], None, pt, t])
                if not drop:
                    self.pending.append(fix)
            self.next_fix_t += 1.0 / hz
        self.previous_truth = (t, x, y)

    def deliver(self, t):
        """Fixes whose availability time has passed; models transport latency."""
        def available(f):
            return max(f['t_available'], self.observations[f['_row']][8]) <= t + 1e-9
        out = [f for f in self.pending if available(f)]
        if out:
            self.pending = [f for f in self.pending if not available(f)]
            for f in out:
                self.observations[f['_row']][4] = 1
                self.observations[f['_row']][6] = t
        return out

    def finalize(self, t_end):
        """At session end, still-undelivered fixes are labelled; they are neither
        delivered nor outage drops."""
        for f in self.pending:
            row = self.observations[f['_row']]
            if not row[4]:
                row[5] = 'pending_at_end'
        self.pending = []



from .field_localization import Estimator  # shared deterministic replay implementation
