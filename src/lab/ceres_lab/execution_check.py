"""Independent post-execution metrics in the solver environment, never in system Python."""
import json
import sys
import math
from .solvers import map_check
from .scenarios.maps import densify


def check(plan, execution, world):
    from shapely.geometry import LineString, Polygon
    pts = [p[1:3] for p in execution['tracking_trajectory']]
    result = {'actual_map_check': map_check(world, [{'polyline':pts}]) if len(pts)>1 else None}
    tolerance = plan.get('goal_tolerance_m')
    if tolerance is not None:
        error = math.dist(pts[-1], plan['path'][-1]) if pts else None
        result.update(goal_tolerance_m=tolerance, independent_goal_error_m=error,
                      goal_reached_within_tolerance=error is not None and error <= tolerance,
                      goal_check_basis='last recorded tracking ground-truth sample; XY metres')
    if len(pts)>1:
        from shapely.geometry import Point
        actual=LineString(pts)
        result['planned_to_actual_max_m']=max(Point(p).distance(actual) for p in densify(plan['path'],.025))
    inst = plan.get('coverage_instance')
    if inst and len(pts)>1:
        area=Polygon(inst['field'],inst.get('obstacles',[])); interior=area.buffer(-inst['headland_width'])
        swept=LineString(pts).buffer(inst['swath_width']/2)
        result.update(actual_working_coverage_fraction=swept.intersection(interior).area/interior.area,
            actual_field_coverage_fraction=swept.intersection(area).area/area.area,
            actual_coverage_basis='Gazebo 真值轨迹线段 buffer(作业幅宽/2)，仅统计与作业区交集；近似连续开启作业具',
            actual_coverage_kind='trajectory_swath_proxy', operation_state_observed=False,
            coverage_required_fraction=.90,
            actual_footprint_in_field=area.covers(LineString(pts).buffer(inst['robot_width']/2)))
    return result

if __name__=='__main__':
    data=json.load(sys.stdin)
    print(json.dumps(check(data['plan'],data['execution'],data['world']),ensure_ascii=False,allow_nan=False))
