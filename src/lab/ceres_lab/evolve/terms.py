"""Canonical bilingual terminology for the evolving-decision studies, shared by the figures and the dashboard.

Each entry is a (Chinese, English) pair. Configuration identifiers from ``plans`` are rendered by
``config_label``; the identifiers themselves (and ``plans.config_label`` used by frozen run records) are unchanged.
"""
from __future__ import annotations

PROFILE = {'robot_s': ('小型机器人', 'Small robot'), 'tractor_m': ('中型拖拉机', 'Mid-size tractor'),
           'sprayer_l': ('大型喷杆喷雾机', 'Large boom sprayer')}
FAMILY = {'cvrp_archive': ('CVRP 基准', 'CVRP benchmark'),
          'fleet:total_distance': ('多车任务分配 · 总里程', 'Multi-vehicle allocation, total distance'),
          'fleet:makespan': ('多车任务分配 · 完工时间', 'Multi-vehicle allocation, makespan'),
          'coverage': ('覆盖路径配置', 'Coverage path configuration')}
VERDICT = {'official': ('原生曲率检验', 'Native curvature check'),
           'fillet': ('圆角可执行性检验', 'Fillet executability check'),
           'strict': ('加密曲率检验', 'Densified curvature check')}
POLICY = {'default': ('默认配置优先', 'Default first'), 'static': ('静态尝试顺序', 'Static try-order'),
          'static-online': ('在线静态顺序', 'Online static order'), 'sbs': ('单一最优配置', 'Single best solver'),
          'oracle': ('虚拟最优配置', 'Virtual best solver'), 'random': ('随机顺序', 'Random order')}
LEARNER = {'knn1': ('1-近邻', '1-NN'), 'knn5': ('5-近邻', '5-NN'), 'logit': ('逻辑回归', 'Logistic')}
FEATURES = {'agri': ('农艺结构特征', 'agronomic features'), 'size': ('规模特征', 'size features')}
DECOMPOSITION = {'none': ('无分解', 'no decomposition'), 'boustrophedon': ('牛耕式分解', 'boustrophedon'),
                 'trapezoidal': ('梯形分解', 'trapezoidal')}
OBJECTIVE = {'n_swath': ('最少作业行数', 'fewest swaths'), 'swath_length': ('最短作业行总长', 'shortest swath length'),
             'field_coverage': ('最大覆盖率', 'maximum coverage')}
SINGLE = {'single:boustrophedon': ('单区往复路径', 'Single-cell boustrophedon'), 'single:snake': ('单区蛇形路径', 'Single-cell snake')}
OPERATION = {'repair': ('局部 Dubins 修复', 'local Dubins repair'), 'repair2': ('两阶段修复', 'two-stage repair'),
             'repairw': ('修复窗口', 'repair window'), 'split': ('分割角', 'split angle'), 'fillet': ('圆角后处理', 'fillet post-processing')}
STRATEGY = {'insert': ('增量插入', 'Incremental insertion'), 'reopt': ('逐事件重优化', 'Event-driven re-optimisation'),
            'periodic3': ('周期重优化', 'Periodic re-optimisation'), 'rollout': ('前瞻采样', 'Rollout'),
            'gate': ('学习型重规划触发', 'Learned re-planning trigger')}
RATE = {'sparse': ('稀疏', 'sparse'), 'medium': ('中等', 'medium'), 'dense': ('密集', 'dense')}
TIME_MODEL = {'constant_speed': ('恒速模型', 'Constant-speed model'), 'constant_scaled': ('恒速模型（速度标定）', 'Constant speed, calibrated'),
              'reference': ('停—转—行解析模型', 'Stop–turn–go analytic model'), 'reference_scaled': ('解析模型（标定）', 'Analytic model, calibrated'),
              'linear': ('线性残差校正', 'Linear residual correction')}
METRIC = {'headroom': ('VBS–SBS 差距', 'VBS–SBS gap'), 'rho': ('惩罚化成本比', 'penalised cost ratio'),
          'first_try': ('首次尝试可行率', 'first-try feasibility'), 'stop_rho': ('终止成本比', 'cost ratio at stop'),
          'mape': ('平均绝对百分比误差', 'mean absolute percentage error'), 'response': ('平均响应时间', 'mean response time'),
          'distance': ('总行驶里程', 'total travel distance'), 'shapley': ('Shapley 贡献', 'Shapley contribution')}


def t(pair: tuple[str, str], lang: str = 'zh') -> str:
    return pair[0] if lang == 'zh' else pair[1]


def config_label(name: str, lang: str = 'zh') -> str:
    """Readable name of a coverage configuration identifier, e.g. ``full:none/n_swath`` → 无分解 · 最少作业行数."""
    sep = ' · ' if lang == 'zh' else ', '
    kind, _, body = name.partition(':')
    if name in SINGLE:
        label = t(SINGLE[name], lang)
    elif kind == 'full':
        d, o = body.split('/')
        label = t(DECOMPOSITION[d], lang) + sep + t(OBJECTIVE[o], lang)
    elif kind in OPERATION:
        base, _, arg = body.partition('@')
        op = t(OPERATION[kind], lang)
        if kind == 'repairw':
            op += f"（{arg.replace('-', '/')} r）" if lang == 'zh' else f" ({arg.replace('-', '/')} r)"
        elif kind == 'split':
            op += f' {arg}π'
        label = config_label('full:' + base, lang) + ' + ' + op
    else:
        return name
    return label[0].upper() + label[1:] if lang == 'en' else label


def policy_label(name: str, lang: str = 'zh') -> str:
    if name in POLICY:
        return t(POLICY[name], lang)
    kind, _, fs = name.partition(':')
    sep = ' · ' if lang == 'zh' else ', '
    return t(LEARNER[kind], lang) + sep + t(FEATURES[fs], lang)
