"""Bounded relative-time projection; no absolute stamps or arbitrary objects."""
import math
STREAMS=('clock','scan','odom','ground_truth')
KINDS={'header','clock_stamp','clock_at_receive_zero_stamp'}
FAULTS={'missing','invalid','backwards'}
def obj(value):return value if isinstance(value,dict) else {}
def finite(value,low,high,integer=False):
    return value if type(value) in ((int,) if integer else (int,float)) and low<=value<=high and math.isfinite(value) else None
def enum(value,values):return value if isinstance(value,str) and value in values else None

def observation(raw):
    if not isinstance(raw,dict) or not any(k in raw for k in ('elapsed_wall_s','elapsed_sim_s','observation_gap_wall_s','clock_advanced','clock_progress_gap_wall_s','streams')):return None
    result={k:finite(raw.get(k),-10000,10000) for k in ('elapsed_wall_s','observation_gap_wall_s','clock_progress_gap_wall_s')}
    result['elapsed_sim_s']=finite(raw.get('elapsed_sim_s'),-86400,86400)
    result['clock_advanced']=raw.get('clock_advanced') if type(raw.get('clock_advanced')) is bool else None
    streams=obj(raw.get('streams'));result['streams']={}
    for name in STREAMS:
        row=obj(streams.get(name))
        clean={k:finite(row.get(k),-10000,10000) for k in ('receipt_age_wall_s','progress_age_wall_s')}
        clean.update({k:finite(row.get(k),-86400,86400) for k in ('receipt_age_sim_s','source_age_sim_s','freshness_age_sim_s')})
        clean.update(source_kind=enum(row.get('source_kind'),KINDS),source_fault=enum(row.get('source_fault'),FAULTS),count=finite(row.get('count'),0,1000000000,True))
        # A fallback is a receipt-clock proxy, never a measured source timestamp.
        if name=='ground_truth' and clean['source_kind']=='clock_at_receive_zero_stamp':
            clean['source_age_sim_s']=None
        result['streams'][name]=clean
    return result

def project(raw):
    raw=obj(raw);history=raw.get('recent_observations');history=history if isinstance(history,list) else []
    result={'diagnostics':enum(raw.get('diagnostics'),{'execution_observation_v1'}),
       'max_observation_gap_wall_s':finite(raw.get('max_observation_gap_wall_s'),0,10000),
       'last_observation':observation(raw.get('last_observation')),
       'trigger_observation':observation(raw.get('trigger_observation')),
       'recent_observations':[clean for row in history[-8:] if (clean:=observation(row)) is not None]}
    for field,high in [('max_stream_age_sim_s',86400),('max_stream_progress_gap_wall_s',10000)]:
        rows=obj(raw.get(field));result[field]={name:finite(rows.get(name),0,high) for name in STREAMS[1:]}
    return result
