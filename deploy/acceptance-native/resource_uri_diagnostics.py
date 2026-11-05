"""Finite public resource / launch observations from already-private log tails.

No file or network operations. These are observed markers, not causal diagnoses.
The URI catalogue comes only from the pinned public warehouse world/model graph.
"""
import re

LIMIT=65536
COMPONENTS=('session','sim','truth','localization','nav')
AVAILABILITY={'tail_read','tail_truncated','empty','missing','rejected','unreadable','provided','provided_empty','not_provided','invalid_input'}
CONTEXTS={'before_shutdown_marker','at_or_after_shutdown_marker','no_shutdown_marker'}
MODELS=(('warehouse','OpenRobotics','Warehouse','5'),('shelf_big','MovAi','shelf_big','2'),
        ('shelf','MovAi','shelf','1'),('chair','OpenRobotics','Chair','3'),
        ('jersey_barrier','OpenRobotics','Jersey Barrier','6'),
        ('foldable_chair','OpenRobotics','foldable_chair','3'),
        ('table','OpenRobotics','Table','4'),
        ('male_visitor','OpenRobotics','MaleVisitorOnPhone','2'),
        ('casual_female','plateau','Casual female','1'))

def catalogue():
    out={}
    for key,owner,name,version in MODELS:
        # Fuel model owner/name matching is case-insensitive. Enumerate only the
        # public original and lowercase spellings, not arbitrary URL normalization.
        for own,model in ((owner,name),(owner.lower(),name.lower())):
            for api in ('1.0/',''):
                for suffix,kind in (('', 'model'),('/'+version,'versioned_model')):
                    value='https://fuel.gazebosim.org/'+api+own+'/models/'+model+suffix
                    out[value]=key+'_'+kind
                    out[value.replace(' ','%20')]=key+'_'+kind
    # Four distinct original historical Fuel file URIs (five SDF occurrences).
    files={
        'chair_mesh':'https://fuel.gazebosim.org/1.0/openrobotics/models/chair/3/files/meshes/Chair.obj',
        'male_visitor_mesh':'https://fuel.gazebosim.org/1.0/openrobotics/models/malevisitoronphone/2/files/meshes/MaleVisitorStatic.obj',
        'male_visitor_collision':'https://fuel.gazebosim.org/1.0/openrobotics/models/malevisitoronphone/2/files/meshes/MaleVisitorStatic_Col.obj',
        'table_albedo':'https://fuel.gazebosim.org/1.0/openrobotics/models/table/4/files/Table_Diffuse.jpg',
        'shelf_relative_mesh':'meshes/shelf_big_movai.dae',
        'warehouse_relative_mesh':'meshes/warehouse.dae',
        'warehouse_relative_collision':'meshes/warehouse_colision.stl',
        'jersey_relative_mesh':'meshes/jersey_barrier.dae',
        'foldable_chair_relative_mesh':'mesh/foldable_chair.dae',
        'casual_female_relative_mesh':'meshes/casual_female.dae',
        'classic_material_script':'file://media/materials/scripts/gazebo.material',
    }
    out.update({uri:key for key,uri in files.items()})
    return out

CATALOGUE=catalogue()
REFERENCES=set(CATALOGUE.values())|{'unknown','not_applicable'}
PROCESSES={'gazebo','entity_create','robot_state_publisher','parameter_bridge','image_bridge',
           'turtlebot4_node','create3_nodes','unknown','not_applicable'}
PROCESS_NAMES={'ruby $(which ign) gazebo':'gazebo','ign gazebo':'gazebo','gz sim':'gazebo',
               'create':'entity_create','robot_state_publisher':'robot_state_publisher',
               'parameter_bridge':'parameter_bridge','image_bridge':'image_bridge',
               'turtlebot4_node':'turtlebot4_node','create3_nodes':'create3_nodes'}
# Exact executable labels from the public Humble launch graph.
# Classification only: it does not assert which installed process failed.
LAUNCH_GRAPH_PROCESSES = ('hazards_vector_publisher', 'interface_buttons_node', 'ir_intensity_vector_publisher', 'kidnap_estimator_publisher', 'mock_publisher', 'motion_control', 'pose_republisher_node', 'robot_state', 'sensors_node', 'spawner', 'static_transform_publisher', 'turtlebot4_ignition_hmi_node', 'ui_mgr', 'wheel_status_publisher')
PROCESS_NAMES.update({name: name for name in LAUNCH_GRAPH_PROCESSES})
PROCESSES.update(LAUNCH_GRAPH_PROCESSES)
MARKERS={
    'Configured and activated':'controller_activated_marker',
    'Loading SDF world file[':'world_load_marker',
    'Unable to load Ogre Resources':'ogre_resources_failure_marker',
    'Failed to load render-engine':'render_engine_load_failure_marker',
    'Failed to initialize render-engine':'render_engine_init_failure_marker',
    'Unable to open display:':'display_open_failure_marker',
    'Unable to create glx fbconfig':'glx_config_failure_marker',
    'Unable to create glx context':'glx_context_failure_marker',
    'Unable to create Ogre root':'ogre_root_failure_marker',
    'Unable to setup EGL (headless mode)':'egl_setup_failure_marker',
    'Failed to create dummy render window':'render_window_failure_marker',
    'Unable to create the rendering window':'render_window_failure_marker',
    'OGRE EXCEPTION':'ogre_exception_marker',
}
EVENTS=set(MARKERS.values())|{'uri_missing_marker','fuel_download_marker','process_died_marker'}
SHUTDOWN=('sending signal ', 'shutting down launched system','signal_handler(signum=', 'Received signal[')
URI=re.compile(r'Unable to find uri\[([^\]]{0,2048})\]')
DOWNLOAD=re.compile(r'Downloading model \[([^\]\r\n]{0,2048})\]')
DIED=re.compile(r'\[([^\]\r\n]{1,120})-\d{1,5}\]: process has died \[pid \d{1,12}, exit code (-?\d{1,3})(?=,)')

def sanitize(raw):
    raw=raw if isinstance(raw,dict) else {};events=[]
    for row in raw.get('events',[])[:64] if isinstance(raw.get('events'),list) else []:
        if not isinstance(row,dict):continue
        if not all(isinstance(row.get(k),str) and row[k] in allowed for k,allowed in (
            ('component',set(COMPONENTS)),('event',EVENTS),('reference',REFERENCES),
            ('process',PROCESSES),('context',CONTEXTS))):continue
        count=row.get('count');code=row.get('exit_code')
        if type(count) is not int or not 1<=count<=10000:continue
        if code is not None and (type(code) is not int or not -255<=code<=255):continue
        if row['event']!='process_died_marker' and code is not None:continue
        if row['event']=='process_died_marker':
            if code is None or row['reference']!='not_applicable' or row['process']=='not_applicable':continue
        elif row['event'] in ('uri_missing_marker','fuel_download_marker'):
            if row['reference']=='not_applicable' or row['process']!='not_applicable':continue
        elif row['reference']!='not_applicable' or row['process']!='not_applicable':continue
        events.append({k:row.get(k) for k in ('component','event','reference','process','context','count','exit_code')})
    availability=raw.get('log_availability');availability=availability if isinstance(availability,dict) else {}
    return {'schema_version':1,'log_availability':{c:availability.get(c) if isinstance(availability.get(c),str) and availability[c] in AVAILABILITY else 'not_provided' for c in COMPONENTS},'coverage':'bounded_log_tail','log_window':'tail_64k','failure_time_alignment':'unavailable',
            'events':events,'events_truncated':raw.get('events_truncated') is True or (isinstance(raw.get('events'),list) and len(raw['events'])>64),
            'additional_input_truncation':raw.get('additional_input_truncation') is True}

def runtime_failure_codes(logs):
    logs=logs if isinstance(logs,dict) else {};codes=set()
    for component in COMPONENTS[1:]:
        text=logs.get(component);text=text if isinstance(text,str) else ''
        if len(text)>LIMIT:
            text=text[-LIMIT:];text=text.split('\n',1)[1] if '\n' in text else ''
        if any(m.group(1).strip() for m in URI.finditer(text)):codes.add('launch_uri_missing')
        if any(-255<=int(m.group(2))<=255 for m in DIED.finditer(text)):codes.add('launch_process_died')
    return codes

def project(logs,availability=None):
    logs=logs if isinstance(logs,dict) else {};events={};truncated=False
    if availability is None:
        availability={c:('provided' if logs[c] else 'provided_empty') if isinstance(logs.get(c),str) else 'invalid_input' if c in logs else 'not_provided' for c in COMPONENTS}
    def add(component,event,reference,process,context,code=None):
        key=(component,event,reference,process,context,code)
        events[key]=min(10000,events.get(key,0)+1)
    for component in COMPONENTS:
        text=logs.get(component,'');text=text if isinstance(text,str) else ''
        if len(text)>LIMIT:
            truncated=True;text=text[-LIMIT:]
            text=text.split('\n',1)[1] if '\n' in text else ''
        shutdown=min((text.find(m) for m in SHUTDOWN if m in text),default=None)
        def context(offset):
            return 'no_shutdown_marker' if shutdown is None else 'before_shutdown_marker' if offset<shutdown else 'at_or_after_shutdown_marker'
        # Every marker is counted, including malformed/unrecognized URIs. Only
        # exact public catalogue matches can emit an identifying reference enum.
        for marker,pattern,event in (('Unable to find uri[',URI,'uri_missing_marker'),('Downloading model [',DOWNLOAD,'fuel_download_marker')):
            matches={m.start():m for m in pattern.finditer(text)}
            for m in re.finditer(re.escape(marker),text):
                capture=matches.get(m.start())
                ref=CATALOGUE.get(capture.group(1).strip(),'unknown') if capture else 'unknown'
                add(component,event,ref,'not_applicable',context(m.start()))
        for marker,event in MARKERS.items():
            for m in re.finditer(re.escape(marker),text):
                add(component,event,'not_applicable','not_applicable',context(m.start()))
        for m in DIED.finditer(text):
            code=int(m.group(2))
            if -255<=code<=255:
                add(component,'process_died_marker','not_applicable',PROCESS_NAMES.get(m.group(1),'unknown'),context(m.start()),code)
    rows=[dict(zip(('component','event','reference','process','context','exit_code'),key),count=count)
          for key,count in sorted(events.items(),key=lambda item:repr(item[0]))]
    return sanitize({'log_availability':availability,'events':rows[:64],'events_truncated':len(rows)>64,'additional_input_truncation':truncated})
