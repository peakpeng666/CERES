"""Optional diagnostic only. No publishers, actions, service calls, or raw persistence."""
import argparse,json,math,os,pathlib,re,signal,time,urllib.request
SID=re.compile(r'sim_[0-9]{8}T[0-9]{6}_[0-9a-f]{6}\Z')
KEYS=('clock','dock','pose','motor','wheel')
def number(x,limit=1e6):return type(x) in (int,float) and -limit<=x<=limit and math.isfinite(x)
class Accumulator:
    def __init__(self):
        self.active=False;self.entered=False;self.closed=False;self.first={};self.last={};self.counts=dict.fromkeys(KEYS,0);self.invalid=0;self.regressions=0;self.motor_nonzero=0;self.start=None;self.end=None
    def phase(self,value,wall):
        if value=='undocking' and not self.entered:self.active=True;self.entered=True;self.start=wall
        elif self.active and value!='undocking':self.active=False;self.closed=True;self.end=wall
    def sample(self,key,value):
        if not self.active:return
        valid=(key=='clock' and number(value) and value>=0) or (key in ('dock','wheel') and type(value) is bool) or (key in ('pose','motor') and isinstance(value,tuple) and len(value)==(3 if key=='pose' else 2) and all(number(v) for v in value))
        if not valid:self.invalid+=1;return
        if key=='clock' and key in self.last and value<self.last[key]:self.regressions+=1
        if key=='motor' and any(abs(v)>1e-6 for v in value):self.motor_nonzero+=1
        self.counts[key]=min(1000000,self.counts[key]+1);self.first.setdefault(key,value);self.last[key]=value
    def result(self):
        delta=self.last['clock']-self.first['clock'] if self.counts['clock']>=2 and not self.regressions else None
        displacement=math.dist(self.first['pose'][:2],self.last['pose'][:2]) if self.counts['pose']>=2 else None
        rotation=abs(math.atan2(math.sin(self.last['pose'][2]-self.first['pose'][2]),math.cos(self.last['pose'][2]-self.first['pose'][2]))) if self.counts['pose']>=2 else None
        if displacement is not None and displacement>1000:displacement=None
        return {'cleanup_destroy_failed':False,'cleanup_shutdown_failed':False,'schema':1,'interval':'observed_undocking','entered':self.entered,'closed':self.closed,'wall_s':round(self.end-self.start,4) if self.closed else None,'clock_delta_s':delta,'clock_regressions':min(self.regressions,1000000),'counts':self.counts.copy(),'dock_first':self.first.get('dock'),'dock_final':self.last.get('dock'),'wheel_enabled_final':self.last.get('wheel'),'relative_displacement_m':displacement,'relative_rotation_rad':rotation,'motor_nonzero_count':min(self.motor_nonzero,1000000),'invalid_samples':min(self.invalid,1000000)}
def accept_state(state,runtime,session):
    if not isinstance(state,dict):raise ValueError('state_invalid')
    sid=state.get('session_id')
    if sid is None:return session,None
    if not isinstance(sid,str) or not SID.fullmatch(sid):raise ValueError('session_invalid')
    directory=runtime/'sim'/sid
    if directory.is_symlink() or directory.parent.is_symlink() or not directory.is_dir():raise ValueError('session_not_owned')
    if session is not None and sid!=session:raise ValueError('session_changed')
    phase=state.get('phase')
    if phase not in ('starting','undocking','localizing','nav2_starting','ready','failed','stopping','stopped','executing'):raise ValueError('phase_invalid')
    return sid,phase

def ros_dependencies():
    import rclpy
    from rclpy.signals import SignalHandlerOptions
    from rclpy.qos import qos_profile_sensor_data
    from rosgraph_msgs.msg import Clock
    from nav_msgs.msg import Odometry
    from geometry_msgs.msg import Twist
    from irobot_create_msgs.msg import DockStatus,WheelStatus
    return rclpy,SignalHandlerOptions,qos_profile_sensor_data,Clock,Odometry,Twist,DockStatus,WheelStatus

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,required=True);parser.add_argument('--output',type=pathlib.Path,required=True);args=parser.parse_args(argv)
    if not (1<=args.port<=65535 and os.environ.get('ROS_DOMAIN_ID')=='146' and os.environ.get('RMW_IMPLEMENTATION')=='rmw_cyclonedds_cpp' and os.environ.get('ROS_LOCALHOST_ONLY')=='1' and re.fullmatch('[0-9a-f]{48}',os.environ.get('URP_ACCEPTANCE_OWNER',''))):raise ValueError('scope_invalid')
    runtime=pathlib.Path(os.environ['URP_LAB_RUNTIME'])
    if not runtime.is_absolute() or runtime.is_symlink() or not runtime.is_dir():raise ValueError('runtime_invalid')
    # Output must be a new file inside the already-private invocation directory.
    if args.output.parent.resolve()!=runtime.parent.resolve() or args.output.exists() or args.output.is_symlink():raise ValueError('output_invalid')
    a=Accumulator();stop=False;node=None;session=None;error='none';initialized=False;rclpy=None;destroy_failed=False;shutdown_failed=False
    def interrupt(*_):
        nonlocal stop;stop=True
    signal.signal(signal.SIGTERM,interrupt);signal.signal(signal.SIGINT,interrupt)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        rclpy,SignalHandlerOptions,qos_profile_sensor_data,Clock,Odometry,Twist,DockStatus,WheelStatus=ros_dependencies()
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO);initialized=True;node=rclpy.create_node('urp_undock_readonly_observer',enable_rosout=False)
        bindings=[(Clock,'/clock',lambda m:a.sample('clock',m.clock.sec+m.clock.nanosec/1e9)),(DockStatus,'/dock_status',lambda m:a.sample('dock',m.is_docked)),(Odometry,'/sim_ground_truth_pose',lambda m:a.sample('pose',(m.pose.pose.position.x,m.pose.pose.position.y,math.atan2(2*(m.pose.pose.orientation.w*m.pose.pose.orientation.z+m.pose.pose.orientation.x*m.pose.pose.orientation.y),1-2*(m.pose.pose.orientation.y**2+m.pose.pose.orientation.z**2))))),(Twist,'/diffdrive_controller/cmd_vel_unstamped',lambda m:a.sample('motor',(m.linear.x,m.angular.z))),(WheelStatus,'/wheel_status',lambda m:a.sample('wheel',m.wheels_enabled))]
        subscriptions=[node.create_subscription(typ,topic,callback,qos_profile_sensor_data) for typ,topic,callback in bindings]
        with args.output.with_suffix('.ready').open('x') as stream:json.dump({'ready':True},stream)
        deadline=time.monotonic()+1200;poll=0
        while not stop and not a.closed and time.monotonic()<deadline:
            if time.monotonic()>=poll:
                with opener.open('http://127.0.0.1:'+str(args.port)+'/lab/api/sim/state',timeout=.5) as response:
                    data=response.read(1048577)
                if len(data)>1048576:raise ValueError('state_oversize')
                session,phase=accept_state(json.loads(data),runtime,session)
                if phase:a.phase(phase,time.monotonic())
                poll=time.monotonic()+.2
            rclpy.spin_once(node,timeout_sec=.02)
        if not a.closed:error='interrupted' if stop else 'deadline'
    except Exception:error='observer_error'
    finally:
        if node is not None:
            try:node.destroy_node()
            except Exception:destroy_failed=True;error='cleanup_error'
        if initialized:
            try:rclpy.shutdown()
            except Exception:shutdown_failed=True;error='cleanup_error'
        result=a.result();result['status']=error;result['cleanup_destroy_failed']=destroy_failed;result['cleanup_shutdown_failed']=shutdown_failed
        with args.output.open('x') as stream:json.dump(result,stream,allow_nan=False)
    return 0 if error=='none' else 1
if __name__=='__main__':raise SystemExit(main())
