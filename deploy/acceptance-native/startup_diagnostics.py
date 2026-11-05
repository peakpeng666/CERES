"""Private startup evidence -> bounded fixed-schema diagnostics only."""
import json,math,pathlib,re
from process_resource_diagnostics import project as project_resources, sanitize as sanitize_resources
from resource_uri_diagnostics import project as project_resource_uri, sanitize as sanitize_resource_uri, runtime_failure_codes
PHASES={'starting','undocking','localizing','nav2_starting','ready','executing','stopping','stopped','failed'}
COUNTS=('clock','odom','scan','dock','ground_truth','amcl','global_map','local_map','cmd_vel','nav2_plan')
COMPONENTS=('session','sim','truth','localization','nav')
LABELS={'sensors_not_ready':'控制器/传感器未就绪','dock_not_ready':'初始充电座状态不可用','localization_timeout':'AMCL 初始定位超时','initialpose_unavailable':'initialpose 订阅者不可用','nav2_not_active':'Nav2 生命周期未激活','costmap_tf_not_ready':'代价地图/TF 未就绪','sim_launch_error':'官方仿真启动错误','assets_missing':'世界依赖缺失：','required_process_exit':'关键服务 ','required_node_exit':'的子节点意外退出：'}
LABELS.update({'undock_server_unavailable':'/undock 动作服务不可用','undock_goal_rejected':'/undock 拒绝目标','undock_cancel_unconfirmed':'/undock 未确认取消','undock_budget_exhausted':'/undock 执行预算耗尽','action_cancel_not_settled':'动作取消未收敛','ros_response_timeout':'ROS 响应超时','undock_canceled':'/undock 状态 5，未到达','undock_aborted':'/undock 状态 6，未到达'})
UNDOCK_EVENTS={'received':'Received new undock goal','cancel_requested':'Received request to cancel undock goal','runtime_exceeded':'Undock Goal Exceeded Runtime','succeeded':'Undock Goal Succeeded','aborted':'Undock Goal Aborted','start_failed':'Undock behavior failed to start','rejected_not_docked':'Robot already undocked, reject'}
CODES=set(LABELS)|{'launch_process_died','launch_server_failure','launch_uri_missing','segmentation_fault','python_module_missing','python_import_error'}
SID=re.compile(r'sim_[0-9]{8}T[0-9]{6}_[0-9a-f]{6}\Z')

def session_id(report):
    if not isinstance(report,dict):return None
    sid=report.get('session_id')
    if isinstance(sid,str) and SID.fullmatch(sid):return sid
    ids=[]
    for step in report.get('steps',[]) if isinstance(report.get('steps'),list) else []:
        if not isinstance(step,dict) or step.get('name')!='start simulation session' or step.get('ok') is not True:continue
        response=step.get('response');value=response.get('session_id') if isinstance(response,dict) else None
        if isinstance(value,str) and SID.fullmatch(value):ids.append(value)
    return ids[0] if len(set(ids))==1 else None

def num(value,integer=False,high=1e9):
    return value if type(value) in ((int,) if integer else (int,float)) and 0<=value<=high and math.isfinite(value) else None

TIMING_STREAMS=('clock','dock','odom','ground_truth')
TIMING_TRIGGERS={'none','stop','cancel','simulation_budget','wall_watchdog','clock_invalid','clock_backwards','clock_no_progress'}|{prefix+':'+name for prefix in ('stream_missing','stream_gap') for name in TIMING_STREAMS}
def sanitize_undock_timing(raw):
    raw=raw if isinstance(raw,dict) and raw.get('policy')=='undock_dual_clock_v1' else {}
    def bounded(value,low=0,high=1800):
        return value if type(value) in (int,float) and low<=value<=high and math.isfinite(value) else None
    result={'policy':'undock_dual_clock_v1' if raw else 'unavailable'}
    for key,high in [('simulation_budget_s',1200),('minimum_planned_rtf',10),('wall_watchdog_s',1800),('no_progress_wall_s',1200),('stream_gap_wall_s',1200)]:
        value=bounded(raw.get(key),high=high);result[key]=value if value is not None and value>0 else None
    for key in ('elapsed_wall_s','trigger_elapsed_wall_s','total_elapsed_wall_s','max_no_progress_wall_s'):
        result[key]=bounded(raw.get(key))
    for key in ('elapsed_sim_s','trigger_elapsed_sim_s','total_elapsed_sim_s'):
        result[key]=bounded(raw.get(key),low=-86400,high=86400)
    status=raw.get('terminal_status');statuses={4:'succeeded',5:'canceled',6:'aborted'}
    result['terminal_status']=statuses.get(status,'unavailable') if type(status) is int else status if isinstance(status,str) and status in statuses.values() else 'unavailable'
    trigger=raw.get('trigger','unavailable');trigger='none' if trigger is None else trigger
    result['trigger']=trigger if isinstance(trigger,str) and trigger in TIMING_TRIGGERS else 'unavailable'
    gaps=raw.get('max_stream_gap_wall_s');gaps=gaps if isinstance(gaps,dict) else {}
    result['max_stream_gap_wall_s']={key:bounded(gaps.get(key)) for key in TIMING_STREAMS}
    result['cancel_accepted']=raw.get('cancel_accepted') if type(raw.get('cancel_accepted')) is bool else None
    result['samples']=num(raw.get('samples'),True,10000000)
    return result

NAV_NODES={'controller_server','smoother_server','planner_server','behavior_server','bt_navigator','waypoint_follower','velocity_smoother','amcl','map_server','local_costmap','global_costmap','manager'}
NAV_EVENTS={'configure_requested','activate_requested','deactivate_requested','cleanup_requested','shutdown_requested','transition_failed','bond_connected','bond_failed','state_service_wait','bringup_started','bringup_failed','managed_active','shutdown_started','shutdown_finished','preshutdown','tf_wait','change_state_unavailable','get_state_unavailable','bt_load_failed','bt_xml_failed','action_server_unavailable','controller_create_failed'}
NAV_CONTEXTS={'before_shutdown_marker','at_or_after_shutdown_marker','no_shutdown_marker'}
def sanitize_nav_lifecycle(raw):
    raw=raw if isinstance(raw,dict) else {};events=[]
    for row in raw.get('events',[]) if isinstance(raw.get('events'),list) else []:
        if not isinstance(row,dict):continue
        if all(isinstance(row.get(k),str) and row[k] in allowed for k,allowed in [('component',{'nav','localization'}),('node',NAV_NODES),('event',NAV_EVENTS),('context',NAV_CONTEXTS)]) and num(row.get('count'),True,10000) is not None:
            events.append({k:row[k] for k in ('component','node','event','context','count')})
    return {'coverage':'bounded_log_tail','log_window':'tail_64k','failure_time_alignment':'unavailable','events':events[:60]}

def nav_lifecycle(logs):
    events={}
    node_pattern='('+'|'.join(sorted(NAV_NODES-{'manager'},key=len,reverse=True))+r')(?=$|[\s.,:;\x1b])'
    transitions=[('Configuring ','configure_requested'),('Activating ','activate_requested'),('Deactivating ','deactivate_requested'),('Cleaning up ','cleanup_requested'),('Shutting down ','shutdown_requested'),('Failed to change state for node: ','transition_failed'),('Waiting for service /','state_service_wait')]
    markers={'Starting managed nodes bringup...':'bringup_started','Failed to bring up all requested nodes. Aborting bringup.':'bringup_failed','Managed nodes are active':'managed_active','Shutting down managed nodes...':'shutdown_started','Managed nodes have been shut down':'shutdown_finished','Running Nav2 LifecycleManager rcl preshutdown':'preshutdown','change_state service is not available!':'change_state_unavailable','get_state service is not available!':'get_state_unavailable'}
    for component in ('nav','localization'):
        lines=logs.get(component,'').splitlines();shutdown=next((n for n,line in enumerate(lines) if any(marker in line for marker in ('Shutting down managed nodes','Running Nav2 LifecycleManager rcl preshutdown','signal_handler(signum='))),None)
        for index,line in enumerate(lines):
            context='no_shutdown_marker' if shutdown is None else 'before_shutdown_marker' if index<shutdown else 'at_or_after_shutdown_marker'
            found=[]
            for marker,event in transitions:
                # Exact static node name after the known manager phrase, never raw names.
                pattern=re.escape(marker)+node_pattern
                if event=='state_service_wait':pattern=r'Waiting for service /?('+ '|'.join(sorted(NAV_NODES-{'manager'}))+r')/get_state(?:\.\.\.|\s|$)'
                match=re.search(pattern,line)
                if match:found.append((match.group(1),event))
            for marker,event in markers.items():
                if marker in line:found.append(('manager',event))
            match=re.search(r'Server '+node_pattern+r' (connected with bond\.|was unable to be reached after)',line)
            if match:found.append((match.group(1),'bond_connected' if match.group(2).startswith('connected') else 'bond_failed'))
            if 'Timed out waiting for transform from ' in line and ' to become available, tf error:' in line:
                for node in ('global_costmap','local_costmap'):
                    if '['+node+'.'+node+']' in line or '['+node+']' in line:found.append((node,'tf_wait'))
            for node,marker,event in [('bt_navigator','Exception when loading BT:','bt_load_failed'),('bt_navigator','Error loading XML file:','bt_xml_failed'),('bt_navigator','action server not available after waiting','action_server_unavailable'),('controller_server','Failed to create controller.','controller_create_failed')]:
                if '['+node+']' in line and marker in line:found.append((node,event))
            for node,event in found:
                key=(component,node,event,context);events[key]=min(10000,events.get(key,0)+1)
    return sanitize_nav_lifecycle({'events':[dict(zip(('component','node','event','context'),key),count=count) for key,count in events.items()]})

def sanitize(raw):
    raw=raw if isinstance(raw,dict) else {};counts=raw.get('counts');counts=counts if isinstance(counts,dict) else {}
    stages=[]
    for row in raw.get('stages',[]) if isinstance(raw.get('stages'),list) else []:
        if isinstance(row,dict) and isinstance(row.get('phase'),str) and row['phase'] in PHASES and num(row.get('wall_s'),high=7200) is not None:stages.append({'phase':row['phase'],'wall_s':row['wall_s']})
    exits=[]
    for row in raw.get('component_exits',[]) if isinstance(raw.get('component_exits'),list) else []:
        if isinstance(row,dict) and row.get('component') in COMPONENTS and type(row.get('exit_code')) is int and -255<=row['exit_code']<=255:exits.append({'component':row['component'],'exit_code':row['exit_code']})
    events=raw.get('undock_events');events=events if isinstance(events,list) else []
    codes=raw.get('error_codes');codes=codes if isinstance(codes,list) else []
    return {'process_resources':sanitize_resources(raw.get('process_resources')),'launch_evidence_version':'runtime_records_v2' if raw.get('launch_evidence_version')=='runtime_records_v2' else 'legacy_or_unavailable','resource_uri_diagnostics':sanitize_resource_uri(raw.get('resource_uri_diagnostics')),'nav_lifecycle':sanitize_nav_lifecycle(raw.get('nav_lifecycle')),'undock_timing':sanitize_undock_timing(raw.get('undock_timing')),'report_available':raw.get('report_available') is True,'undock_events':sorted({e for e in events if isinstance(e,str) and e in UNDOCK_EVENTS}),'dock_state_at_undock_entry':raw.get('dock_state_at_undock_entry') if raw.get('dock_state_at_undock_entry') == 'docked' else 'unavailable','final_dock_state':'unavailable','final_sim_time_s':num(raw.get('final_sim_time_s'),high=86400),'final_speed_m_s':num(raw.get('final_speed_m_s'),high=20),'phase':raw.get('phase') if isinstance(raw.get('phase'),str) and raw['phase'] in PHASES else 'unknown','stages':stages[-20:],'counts':{k:num(counts.get(k),True) for k in COUNTS},'assets_complete':raw.get('assets_complete') if type(raw.get('assets_complete')) is bool else None,'assets_missing_count':num(raw.get('assets_missing_count'),True,100000),'error_codes':sorted({c for c in codes if isinstance(c,str) and c in CODES})[:16],'component_exits':exits[:20]}

def project(report,logs,*,log_availability=None):
    report=report if isinstance(report,dict) else {};final=report.get('final_state') or {};final=final if isinstance(final,dict) else {}
    stages=[{'phase':r.get('name'),'wall_s':r.get('wall_s')} for r in (report.get('stages',[]) if isinstance(report.get('stages'),list) else []) if isinstance(r,dict)]
    assets=report.get('assets') or {};assets=assets if isinstance(assets,dict) else {}
    errors=report.get('errors',[]);errors=list(errors) if isinstance(errors,list) else []
    errors+=final.get('errors',[]) if isinstance(final.get('errors'),list) else []
    text='\n'.join(x[:65536] for x in errors[:20] if isinstance(x,str))
    codes=[code for code,label in LABELS.items() if label in text]
    codes.extend(runtime_failure_codes(logs))
    exits=[];events=[]
    for component in COMPONENTS:
        text=logs.get(component,'')
        events.extend(event for event,label in UNDOCK_EVENTS.items() if label in text)
        codes.extend(code for code,label in LABELS.items() if label in text)
        for marker,code in [('CRITICAL FAILURE: SERVER','launch_server_failure'),('Segmentation fault','segmentation_fault'),('ModuleNotFoundError:','python_module_missing'),('ImportError:','python_import_error')]:
            if marker in text:codes.append(code)
        for value in re.findall(r'\bexit code (-?\d{1,3})(?:\D|$)',text):exits.append({'component':component,'exit_code':int(value)})
    robot=final.get('robot');robot=robot if isinstance(robot,dict) else {}
    # The pinned session gates undocking on latest dock.is_docked; final dock state is not persisted.
    return sanitize({'process_resources':project_resources(report),'launch_evidence_version':'runtime_records_v2','resource_uri_diagnostics':project_resource_uri(logs,log_availability),'nav_lifecycle':nav_lifecycle(logs),'undock_timing':report.get('undock_timing'),'report_available':bool(report),'undock_events':events,'dock_state_at_undock_entry':'docked' if any(r.get('phase')=='undocking' for r in stages) else 'unavailable','final_sim_time_s':robot.get('t_sim'),'final_speed_m_s':robot.get('speed'),'phase':final.get('phase') or (stages[-1]['phase'] if stages else 'unknown'),'stages':stages,'counts':{k:report['counts'].get(k,0) for k in COUNTS} if isinstance(report.get('counts'),dict) else {},'assets_complete':assets.get('complete'),'assets_missing_count':len(assets['missing']) if isinstance(assets.get('missing'),list) else None,'error_codes':codes,'component_exits':exits})

def collect(runtime,sid):
    if not isinstance(sid,str) or not SID.fullmatch(sid):return sanitize({})
    root=pathlib.Path(runtime)/'sim'/sid
    if root.is_symlink() or root.parent.is_symlink():return sanitize({})
    def bounded(path,limit):
        if path.is_symlink() or path.parent.is_symlink():return b'','rejected'
        if not path.is_file():return b'','rejected' if path.exists() else 'missing'
        with path.open('rb') as stream:
            stream.seek(0,2);size=stream.tell()
            if path.suffix=='.json' and size>limit:return b'','rejected'
            stream.seek(max(0,size-limit));data=stream.read(limit)
            # An incomplete first line in a truncated tail is not a log event.
            data=data.split(b'\n',1)[1] if size>limit and b'\n' in data else b'' if size>limit else data
            return data,'tail_truncated' if size>limit else 'empty' if not data else 'tail_read'
    try:report=json.loads(bounded(root/'session-report.json',2097152)[0])
    except (ValueError,OSError):report={}
    logs={};availability={}
    for component in COMPONENTS:
        try:
            path=pathlib.Path(runtime)/'logs'/('sim-'+sid+'.log') if component=='session' else root/'logs'/(component+'.log')
            data,availability[component]=bounded(path,65536)
            logs[component]=data.decode('utf-8',errors='replace')
        except OSError:logs[component]='';availability[component]='unreadable'
    return project(report,logs,log_availability=availability)
