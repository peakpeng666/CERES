"""Does this machine fit this job? Each check states what was compared and what to change.

Levels: pass, warn (planning continues, the result depends on it), block (no meaningful plan), info.
"""
from __future__ import annotations

from . import catalog as cat
from . import rig as rigs

DRAWBAR_RATIO = .77           # ASAE D497 fig. 1 worked example: 4WD on firm soil, PTO → drawbar
PLATFORM_SLOPE_DEG = {'ackermann': 15, 'diff_drive': 12, 'skid_steer': 15, 'tracked': 25, 'uav': 90, 'rail': 2, 'boat': 0}   # assumption
WHEEL_LOAD_WARN_KG = 3000     # Horn et al. 2003 as cited by Augustin et al. 2022


def _c(check_id, level, title, detail, fix=None, sources=()):
    return {'id': check_id, 'level': level, 'title': title, 'detail': detail, 'fix': fix, 'sources': list(sources)}


def assess(env: dict, crop: dict | None, operation: dict, platform: dict, implement: dict, rig: dict, *,
           pitch: float, headland_m: float, line_max_m: float | None = None) -> list[dict]:
    out = []
    v = cat.value
    if operation.get('family') != 'line':
        out.append(_c('family', 'block', '这项作业不是沿行作业', f"「{operation['name']}」按点或协同任务建模，请用任务地图规划。", '回到任务地图'))
    elif operation['id'] not in env['line_operations']:
        out.append(_c('fit', 'block', '这里不做这项作业', f"{env['name']}没有「{operation['name']}」这项作业。", '换一项作业或环境'))
    if platform['class'] not in env['platforms']:
        names = '、'.join(cat.PLATFORM_CLASSES[c] for c in env['platforms'])
        out.append(_c('environment', 'block', '平台不适用此环境', f"{env['name']}适用：{names}。", '更换平台'))
    if operation['id'] not in implement['operations']:
        out.append(_c('operation', 'block', '机具不做这项作业', f"{implement['name']}不支持{operation['name']}。", '更换机具'))
    if implement['hitch'] not in platform['hitch'] or ('for' in implement and platform['id'] not in implement['for']):
        out.append(_c('hitch', 'block', '连接方式不匹配', f"{platform['name']}不能装{implement['name']}。", '更换机具或平台'))
    if platform['tier'] == 'display':
        out.append(_c('tier', 'block', '只有展示模型', f"{platform.get('note') or '这台平台还没有规划参数'}。", '更换平台'))

    load = v(implement.get('mass_kg'), 0)
    cap = v(platform.get('lift_kg')) if implement['hitch'] == '3pt' else v(platform.get('payload_kg')) if implement['hitch'] == 'deck' else None
    if cap is not None:
        out.append(_c('load', 'block' if load > cap else 'pass', '载重', f'机具 {load:g} kg，平台上限 {cap:g} kg。', '换轻型机具或更大平台' if load > cap else None,
                      [(platform.get('lift_kg') or platform.get('payload_kg') or {}).get('s')]))
    speed_kmh = rig['speed_work_mps'] * 3.6
    spacing = v(crop['row_spacing_m']) if crop and crop.get('row_spacing_m') else None
    need = rigs.draft_kw(implement, speed_kmh, rig['work_width_m'], spacing)
    pto = rigs.pto_kw(implement, rig['work_width_m'])
    have = v(platform.get('pto_kw'))
    if need is not None and have is not None:
        drawbar = have * DRAWBAR_RATIO
        out.append(_c('power', 'block' if need > drawbar else 'pass', '牵引功率', f'需 {need:.0f} kW，牵引可用约 {drawbar:.0f} kW（{speed_kmh:.1f} km/h）。',
                      '减小幅宽或速度' if need > drawbar else None, ['asae_d497_5']))
    if pto is not None and have is not None:
        out.append(_c('pto', 'block' if pto > have else 'pass', 'PTO 功率', f'需 {pto:.0f} kW，平台 {have:.0f} kW。', '减小幅宽' if pto > have else None, [implement['pto'].get('s')]))

    if env['structure'] == 'alley' and spacing:
        alley = spacing - v(crop['row_width_m'], 0)
        fits = rig['clearance_width_m'] + .3 <= alley
        out.append(_c('alley', 'pass' if fits else 'block', '通道宽度', f"净通道 {alley:.2f} m，机组宽 {rig['clearance_width_m']:.2f} m（两侧共留 0.3 m）。",
                      None if fits else '换窄型平台或更宽行距'))
    if crop and crop.get('stand') == 'row' and spacing and operation.get('crop_protected') and rig['track_m'] > 0:
        # wheels must run between crop rows: track a multiple of the row spacing
        k = rig['track_m'] / spacing
        drift = abs(k - round(k)) * spacing
        ok = drift <= spacing * .25 and round(k) >= 1
        out.append(_c('track', 'pass' if ok else 'warn', '轮距与行距', f"轮距 {rig['track_m']:.2f} m，行距 {spacing:.2f} m。",
                      None if ok else '调整轮距或行距，否则轮辙压到作物'))
    if rig['endurance_s'] and line_max_m and line_max_m / rig['speed_work_mps'] > rig['endurance_s']:
        out.append(_c('endurance_line', 'block', '续航不足', '单条作业行超过一次续航。', '缩短行长或换平台'))
    res = rig['resource']
    if res['kind'] != 'none':
        if not res['rate_per_ha']:
            out.append(_c('rate', 'block', '缺少施用量', f"需要{res['name']}的用量（{res['unit']}/ha）。", '填写用量'))
        elif line_max_m:
            line_demand = rigs.demand_per_m(rig, pitch) * line_max_m
            ok = line_demand <= res['capacity']
            out.append(_c('capacity', 'pass' if ok else 'block', '单行需求', f"最长一行 {line_demand:.1f} {res['unit']}，容量 {res['capacity']:g} {res['unit']}。",
                          None if ok else '减小用量或增大容量；行内补给还未建模'))
    r = rig['min_turn_radius_m']
    need_depth = rigs.headland_for(rig, pitch)
    if r > 0 and headland_m < need_depth:
        floor = r + rig['clearance_width_m'] / 2
        blocked = headland_m < floor
        out.append(_c('headland', 'block' if blocked else 'warn', '地头宽度', f'地头 {headland_m:.1f} m，相邻行掉头需 {need_depth:.1f} m。',
                      f'加宽到 {need_depth:.1f} m' if blocked else '求解时用跳行或倒车转弯', ['bochtis2008']))
    limit = PLATFORM_SLOPE_DEG.get(platform['class'], 15)
    if env['slope_deg'] > limit:
        out.append(_c('slope', 'warn', '坡度', f"{env['name']}典型坡度 {env['slope_deg']}°，{cat.PLATFORM_CLASSES[platform['class']]}平台按 {limit}° 考虑。", '坡度未进入路径成本'))
    if rig['wheel_load_kg'] and rig['wheel_load_kg'] > WHEEL_LOAD_WARN_KG:
        out.append(_c('wheel_load', 'warn', '轮载', f"单轮约 {rig['wheel_load_kg'] / 1000:.1f} Mg，超过 3 Mg 参考值。", '考虑履带或更低轴荷', ['augustin2022']))
    if platform['tier'] == 'planning':
        out.append(_c('tier', 'info', '只到规划层', f"{platform['name']}没有执行模型，可比较方案，不能进入物理仿真。", '要跑仿真请用田间差速机器人'))
    elif platform['tier'] == 'simulation':
        out.append(_c('tier', 'pass', '可进入仿真', 'MuJoCo 与 Gazebo 用同一差速模型执行路径，不含机具动力学。'))
    return out


def blocked(checks: list[dict]) -> bool:
    return any(c['level'] == 'block' for c in checks)
