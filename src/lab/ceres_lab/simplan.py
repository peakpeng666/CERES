"""Transfer checked map-frame geometry or task order; never silently scale benchmark coordinates."""
import math
from .scenarios.maps import densify

class NotSimulatable(ValueError): pass

def to_sim_plan(solution, scenario, mode):
    if mode not in ('follow_path', 'nav2_waypoints'): raise NotSimulatable('执行方式须为 follow_path 或 nav2_waypoints')
    if solution.get('state', 'done') != 'done' or solution.get('status') in ('no_solution', 'infeasible'):
        raise NotSimulatable('只有完成求解且具有可行路线的结果才能执行')
    if not solution.get('routes'): raise NotSimulatable('没有可执行路线')
    if solution['frame']['kind']!='map' or solution['frame']['units']!='m': raise NotSimulatable('仅支持 map 米坐标')
    if not (solution.get('check') or {}).get('valid'): raise NotSimulatable('没有通过独立检查的路线')
    if mode=='follow_path' and not (solution.get('map_check') or {}).get('collision_free'): raise NotSimulatable('整段路径与机器人占用的地图检查未通过')
    inst=solution.get('instance') or scenario['instance']
    from .planning import requires_coverage_recheck
    if requires_coverage_recheck(solution):
        raise NotSimulatable('旧 Fields2Cover 全流程记录未包含完整末段与新版独立检查；请重新求解，原始记录保留')
    if scenario['problem_class']=='point_to_point' and inst.get('motion_model','holonomic')!='holonomic':
        raise NotSimulatable('当前 TB4 只适配全向 XY 几何路径跟踪，尚未适配 Dubins/Reeds–Shepp 的朝向、前进/倒车约束；请保留该方案用于规划比较')
    if scenario['problem_class']=='coverage' and inst['robot_width']<.35:
        raise NotSimulatable('田块机器人宽度小于当前 TB4 仿真足迹直径 0.35 m')
    if scenario['problem_class']=='coverage' and (solution.get('metrics') or {}).get('reverse_states', 0)>0:
        raise NotSimulatable('覆盖路径含倒车段；当前 TB4 适配未保留前进/倒车控制语义，请保留该结果用于规划研究')
    tasks=[]; route_paths=[]; legs=[]
    for r in solution['routes']:
        pts=[list(p) for p in densify(r['polyline'],.05)]
        pts=[p for i,p in enumerate(pts) if i==0 or math.dist(p,pts[i-1])>1e-8]
        if len(pts)<2: raise NotSimulatable('路线缺少可执行几何')
        route_paths.append(pts)
        if r.get('execution_legs'):
            parts=r['execution_legs']
            flattened=[p for i,part in enumerate(parts) for p in (part if not i else part[1:])]
            if any(len(part)<2 for part in parts) or any(math.dist(a[-1],b[0])>1e-8 for a,b in zip(parts,parts[1:])) or flattened!=r['polyline']:
                raise NotSimulatable('执行分段与已检查的几何路径不一致')
            legs.extend(r['execution_legs'])
        else:
            legs.extend([list(p) for p in densify([a,b],.05)] for a,b in zip(r['polyline'],r['polyline'][1:]) if math.dist(a,b)>1e-8)
        if scenario['problem_class']=='point_to_point':
            tasks.append(dict(task=0,label='终点',xy=list(inst['goal'][:2]))); continue
        for t in r['sequence']:
            if scenario['problem_class']=='turn_routing':
                c=next(c for c in inst['customers'] if c['id']==t); xy=inst['coordinates'][str(c['node'])]; label=c.get('label',str(t))
            else: xy=inst['coordinates'][t]; label=(inst.get('labels') or [str(i) for i in range(len(inst['coordinates']))])[t]
            tasks.append(dict(task=t,label=label,xy=xy))
    if mode=='nav2_waypoints' and not tasks: raise NotSimulatable('覆盖任务没有逐点访问语义，请使用路径跟踪')
    if any(math.dist(a[-1],b[0])>1e-6 for a,b in zip(route_paths,route_paths[1:])):
        raise NotSimulatable('多条路线端点不连续，需要显式连接规划')
    points=[p for r in route_paths for p in r]
    length=sum(math.dist(a,b) for r in route_paths for a,b in zip(r,r[1:]))
    goal_tolerance = inst.get('goal_tolerance', .05) if scenario['problem_class']=='point_to_point' else None
    return dict(path=points,route_paths=route_paths,legs=legs,tasks=tasks,
                waypoints=[dict(task=t['task'],label=t['label'],x=t['xy'][0],y=t['xy'][1],yaw=None) for t in tasks],
                task_radius_m=goal_tolerance if goal_tolerance is not None else .35, goal_tolerance_m=goal_tolerance,
                planned_length_m=length,budget_s=max(90,min(1800,length/.10+60)),
                problem_class=scenario['problem_class'], coverage_instance=inst if scenario['problem_class']=='coverage' else None,
                sources=dict(route_plan=solution['algorithm']['origin'],approach='Nav2 NavigateToPose (NavFn + RPP)，单独记录',
                             tracking='求解器几何路径 / Nav2 FollowPath 控制器' if mode=='follow_path' else '求解器访问顺序 / Nav2 NavigateToPose 重新规划'))
