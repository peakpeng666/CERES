"""Starts fixed synthetic world and bridge, never starts motion commands."""
import os
import uuid
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess,
                            RegisterEventHandler, SetEnvironmentVariable)
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('ceres_route_sim')
    partition = os.environ.get('IGN_PARTITION', 'ceres_synthetic_'+uuid.uuid4().hex)
    if not partition.startswith('ceres_synthetic_'):
        raise RuntimeError('Refusing non-synthetic IGN_PARTITION')
    simulator = ExecuteProcess(cmd=['ign','gazebo','-s','-r',
                                   os.path.join(share,'worlds','synthetic_greenhouse.sdf')],output='screen')
    bridge = Node(package='ros_gz_bridge', executable='parameter_bridge', name='synthetic_bridge',
                  parameters=[{'config_file':os.path.join(share,'config','bridge_fortress.yaml')}],output='screen')
    return LaunchDescription([
        SetEnvironmentVariable('ROS_LOCALHOST_ONLY','1'),
        SetEnvironmentVariable('ROS_DOMAIN_ID','71'),
        SetEnvironmentVariable('IGN_IP','127.0.0.1'),
        SetEnvironmentVariable('IGN_PARTITION',partition),
        DeclareLaunchArgument('rviz',default_value='false', choices=['true', 'false']),
        RegisterEventHandler(OnProcessExit(target_action=simulator,
            on_exit=[EmitEvent(event=Shutdown(reason='Synthetic Gazebo process exited'))])),
        RegisterEventHandler(OnProcessExit(target_action=bridge,
            on_exit=[EmitEvent(event=Shutdown(reason='Synthetic bridge process exited'))])),
        simulator,
        bridge,
        Node(package='rviz2',executable='rviz2', parameters=[{'use_sim_time':True}],
             arguments=['-d',os.path.join(share,'config','synthetic_view.rviz')],
             condition=IfCondition(LaunchConfiguration('rviz'))),
    ])
