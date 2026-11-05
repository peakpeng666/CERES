"""Bounded failure evidence only. No identifiers, exception text or paths."""
import json
import math
import urllib.error
PHASES={'unknown','preflight','source_verification','historical_inputs','fuel_restore','resource_check','record_build','record_store','guard','gateway_start','gateway_health','gateway_readback','lifecycle','session_start','session_ready','execution_submit','execution_wait','execution_check','session_stop','cleanup_wait','session_report','physical_check','overlay_identity','gateway_shutdown','complete'}
STATES={'unknown','starting','assets','launching','sensors','undocking','localizing','nav2_starting','ready','executing','failed','stopping','stopped'}
CODES={'none','unknown','lifecycle_timeout','execution_wall_envelope_insufficient','session_not_ready','session_identifier_invalid','session_identity_changed','sim_identifier_invalid','submitted_identity_mismatch','submitted_execution_policy_mismatch','submitted_plan_mismatch','terminal_identity_mismatch','terminal_execution_policy_mismatch','simulation_failed','cleanup_identity_mismatch','session_cleanup_failed','gateway_failed','gateway_import_readback_mismatch','session_identity_missing','session_report_identity_mismatch','historical_resources_mismatch','historical_cache_provenance_changed','current_execution_identity_changed','current_map_identity_changed','raw_resource_identity_changed','current_resources_changed','warehouse_arguments_invalid','wrong_checker_interpreter','runtime_override_forbidden','runtime_not_empty','import_readback_mismatch','interrupted'}
KINDS={'none','timeout','http','network','json_decode','os','value','runtime','interrupted','unknown'}
REPORTS={'unavailable','missing','invalid','identity_mismatch','available'}
def failure(exc):
    code=str(exc) if type(exc).__name__=='Rejected' or isinstance(exc,(ValueError,RuntimeError)) else None
    kind=('http' if isinstance(exc,urllib.error.HTTPError) else 'timeout' if isinstance(exc,TimeoutError) else 'network' if isinstance(exc,urllib.error.URLError) else 'json_decode' if isinstance(exc,json.JSONDecodeError) else 'os' if isinstance(exc,OSError) else 'value' if isinstance(exc,ValueError) else 'interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'runtime' if isinstance(exc,RuntimeError) else 'unknown')
    status=exc.code if isinstance(exc,urllib.error.HTTPError) and type(exc.code) is int and 100<=exc.code<=599 else None
    return {'failure_code':code if code in CODES else 'unknown','exception_kind':kind,'http_status':status}
def enum(value,allowed,default):return value if isinstance(value,str) and value in allowed else default
def number(value):return value if type(value) in (int,float) and 0<=value<=10000 and math.isfinite(value) else None
def section(raw):
    raw=raw if isinstance(raw,dict) else {}
    out={k:enum(raw.get(k),allowed,default) for k,allowed,default in [('phase',PHASES,'unknown'),('failure_phase',PHASES,'unknown'),('failure_code',CODES,'none'),('exception_kind',KINDS,'none'),('last_session_phase',STATES,'unknown'),('cleanup_session_phase',STATES,'unknown'),('cleanup_failure_code',CODES,'none'),('cleanup_exception_kind',KINDS,'none'),('identity_probe_exception_kind',KINDS,'none'),('gateway_shutdown_exception_kind',KINDS,'none')]}
    for key in ('http_status','cleanup_http_status','identity_probe_http_status'):out[key]=raw.get(key) if type(raw.get(key)) is int and 100<=raw[key]<=599 else None
    for key in ('session_id_observed','gateway_health_verified','gateway_record_verified','identity_probe_attempted','forced_cleanup','gateway_wait_timed_out','startup_collection_failed'):out[key]=raw.get(key) if type(raw.get(key)) is bool else None
    out['elapsed_wall_s']=number(raw.get('elapsed_wall_s'));return out
def project(raw):
    raw=raw if isinstance(raw,dict) else {}
    return {'harness':section(raw.get('harness')),'lifecycle':section(raw.get('lifecycle')),'session_report':enum(raw.get('session_report'),REPORTS,'unavailable')}
