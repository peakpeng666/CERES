"""Portable, self-contained records. Local map geometry is not Earth longitude/latitude."""
import csv
import io
import json

from .result_integrity import without_invalidated_results

def pack(record, fmt, stem):
    if fmt == 'json': return json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False).encode(), 'application/json', stem+'.json'
    raise ValueError('不支持的导出格式')

def rows_csv(rows, fields, stem):
    stream = io.StringIO(); w=csv.DictWriter(stream,fieldnames=fields); w.writeheader(); w.writerows(rows)
    return stream.getvalue().encode('utf-8-sig'),'text/csv',stem+'.csv'

def export_run(sol, fmt):
    sol = without_invalidated_results(sol)
    stem=sol['run_id']
    if fmt=='json': return pack(sol,fmt,stem)
    if fmt=='csv':
        rows=[dict(run_id=stem,vehicle=r['vehicle'],point=i,x=p[0],y=p[1],units=sol['frame']['units'],instance_sha256=sol['instance_sha256']) for r in sol['routes'] for i,p in enumerate(r['polyline'])]
        return rows_csv(rows,['run_id','vehicle','point','x','y','units','instance_sha256'],stem)
    if fmt=='geojson' and sol.get('problem_class')=='fleet_routing':
        from .compose.geo import export_geojson
        return export_geojson(sol.get('scenario_snapshot') or {'instance':sol.get('instance')}, sol),'application/geo+json',stem+'.geojson'
    if fmt=='tasks-csv':
        if sol.get('problem_class') != 'fleet_routing':
            raise ValueError('tasks-csv 仅适用于 fleet_routing；csv 保持几何点格式')
        inst=sol.get('instance') or {}
        tasks={t['id']:t for t in inst.get('tasks',[])}
        rows=[dict(run_id=stem,instance_sha256=sol['instance_sha256'],units=sol['frame']['units'],
                   row_kind='task_service',vehicle=r['vehicle'],vehicle_name=r.get('vehicle_name'),
                   trip=k+1,order=i+1,task=tid,label=tasks[tid].get('label'),
                   x=tasks[tid]['xy'][0],y=tasks[tid]['xy'][1],demand=tasks[tid].get('demand'))
              for r in sol['routes'] for k,trip in enumerate(r.get('trips') or [r['sequence']]) for i,tid in enumerate(trip)]
        return rows_csv(rows,['run_id','instance_sha256','units','row_kind','vehicle','vehicle_name',
                              'trip','order','task','label','x','y','demand'],stem+'-tasks')
    if fmt=='geojson':
        # Explicit local-coordinate extension, never advertise WGS84 / RFC7946 geographic compatibility.
        obj={'type':'FeatureCollection','coordinate_reference':sol['frame'], 'warning':'Local coordinates; NOT WGS84 / RFC7946 geographic coordinates',
             'features':[{'type':'Feature','properties':{'run_id':stem,'vehicle':r['vehicle']},'geometry':{'type':'LineString','coordinates':r['polyline']}} for r in sol['routes'] if len(r['polyline'])>=2]}
        return json.dumps(obj,ensure_ascii=False).encode(),'application/json',stem+'.geojson'
    raise ValueError('格式须为 json/csv/tasks-csv/geojson')

def export_sim_run(run, fmt):
    if fmt=='json': return pack(run,fmt,run['sim_run_id'])
    if fmt=='csv':
        rows=[dict(sim_run_id=run['sim_run_id'],source=k,t_sim=p[0],x_m=p[1],y_m=p[2],yaw_rad=p[3]) for k,pts in run['actual'].items() for p in pts]
        return rows_csv(rows,['sim_run_id','source','t_sim','x_m','y_m','yaw_rad'],run['sim_run_id'])
    raise ValueError('格式须为 json/csv')

def export_compare(solutions, fmt):
    solutions = [without_invalidated_results(s) for s in solutions]
    if fmt=='json': return pack(solutions,fmt,'plan-compare')
    if fmt=='csv':
        rows=[dict(run_id=s['run_id'],scenario=s['scenario_id'],instance_sha256=s['instance_sha256'],algorithm=s['algorithm']['id'],name=s['algorithm'].get('name'),state=s['state'],status=s['status'],cost=s['objective']['value'],unit=s['objective']['unit'],valid=(s.get('check')or{}).get('valid'),
                   length=s['metrics'].get('length'),total_distance_m=s['metrics'].get('total_distance_m'),makespan_s=s['metrics'].get('makespan_s'),vehicles_used=s['metrics'].get('vehicles_used'),trips=s['metrics'].get('trips'),balance_cv=s['metrics'].get('balance_cv'),gap_to_bks_pct=s['metrics'].get('gap_to_bks_pct'),wall_s=s['metrics'].get('wall_s')) for s in solutions]
        return rows_csv(rows,['run_id','scenario','instance_sha256','algorithm','name','state','status','cost','unit','valid','length','total_distance_m','makespan_s','vehicles_used','trips','balance_cv','gap_to_bks_pct','wall_s'],'plan-compare')
    raise ValueError('格式须为 json/csv')
