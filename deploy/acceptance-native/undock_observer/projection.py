"""Strict second-stage public reducer; never forward an observer object as-is."""
import math
FIELDS={'cleanup_destroy_failed','cleanup_shutdown_failed','schema','status','interval','entered','closed','wall_s','clock_delta_s','clock_regressions','counts','dock_first','dock_final','wheel_enabled_final','relative_rotation_rad','relative_displacement_m','motor_nonzero_count','invalid_samples'}
def project(raw):
    def scalar(v,high):return v is None or type(v) in (int,float) and 0<=v<=high and math.isfinite(v)
    def count(v):return type(v) is int and 0<=v<=1000000
    def require(condition):
        if not condition:raise ValueError()
    try:
        require(isinstance(raw,dict) and set(raw)==FIELDS)
        require(type(raw['schema']) is int and raw['schema']==1)
        require(raw['status'] in ('none','interrupted','deadline','observer_error','cleanup_error') and raw['interval']=='observed_undocking')
        require(all(type(raw[k]) is bool for k in ('entered','closed','cleanup_destroy_failed','cleanup_shutdown_failed')))
        require(all(type(raw[k]) is bool or raw[k] is None for k in ('dock_first','dock_final','wheel_enabled_final')))
        require(all(count(raw[k]) for k in ('clock_regressions','motor_nonzero_count','invalid_samples')))
        require(isinstance(raw['counts'],dict) and set(raw['counts'])=={'clock','dock','pose','motor','wheel'} and all(count(v) for v in raw['counts'].values()))
        require(all(scalar(raw[k],limit) for k,limit in [('wall_s',1201),('clock_delta_s',1000000),('relative_displacement_m',1000),('relative_rotation_rad',math.pi)]))
        require(raw['motor_nonzero_count']<=raw['counts']['motor'])
        require(not (raw['cleanup_destroy_failed'] or raw['cleanup_shutdown_failed']) or raw['status']=='cleanup_error')
        return {k:(raw[k].copy() if k=='counts' else raw[k]) for k in sorted(FIELDS)}
    except (ValueError,TypeError,KeyError):return {'schema':1,'status':'invalid_schema'}
