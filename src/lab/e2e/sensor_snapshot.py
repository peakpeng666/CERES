#!/usr/bin/python3
"""Read-only ROS probe: capture a stationary scan, Gazebo truth, and physical laser mounting TF."""
import argparse,json,math,time
import rclpy
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from tf2_msgs.msg import TFMessage
from tf2_ros import Buffer,TransformListener
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
rclpy.init();n=rclpy.create_node('ceres_scan_map_audit',parameter_overrides=[Parameter('use_sim_time',value=True)])
b=Buffer(node=n);listener=TransformListener(b,n);latest={}
def truth(msg):
 for t in msg.transforms:
  if t.child_frame_id=='turtlebot4':latest['truth']=t
subs=[n.create_subscription(LaserScan,'/scan',lambda m:latest.update(scan=m),qos_profile_sensor_data),n.create_subscription(TFMessage,'/ground_truth/poses',truth,qos_profile_sensor_data)]
end=time.monotonic()+20;tf=None
try:
 while time.monotonic()<end:
  rclpy.spin_once(n,timeout_sec=.1)
  if 'scan' in latest and 'truth' in latest:
   try:tf=b.lookup_transform('base_link',latest['scan'].header.frame_id,rclpy.time.Time());break
   except Exception:pass
 if tf is None:raise RuntimeError('scan/truth/base_link→laser TF 未齐备')
 def yaw(q):return math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
 scan=latest['scan'];gt=latest['truth'];t=gt.transform;m=tf.transform;heading=yaw(t.rotation);sx=t.translation.x+math.cos(heading)*m.translation.x-math.sin(heading)*m.translation.y;sy=t.translation.y+math.sin(heading)*m.translation.x+math.cos(heading)*m.translation.y
 points=[]
 for i,d in enumerate(scan.ranges):
  if math.isfinite(d) and scan.range_min<d<scan.range_max-.1:
   angle=heading+yaw(m.rotation)+scan.angle_min+i*scan.angle_increment;points.append([sx+d*math.cos(angle),sy+d*math.sin(angle)])
 obj=dict(frame='world = map (official pairing)',scan_frame=scan.header.frame_id,scan_time=scan.header.stamp.sec+scan.header.stamp.nanosec/1e9,
          truth_time=gt.header.stamp.sec+gt.header.stamp.nanosec/1e9,truth=[t.translation.x,t.translation.y,heading],
          laser_mount=[m.translation.x,m.translation.y,yaw(m.rotation)],laser_origin=[sx,sy],endpoints=points,finite_count=len(points),total_beams=len(scan.ranges))
 Path(a.out).write_text(json.dumps(obj,indent=2));print(json.dumps({k:v for k,v in obj.items() if k!='endpoints'}))
finally:listener.unregister();n.destroy_node();rclpy.shutdown()
