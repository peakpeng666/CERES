from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory
from ceres_route_sim.scene_core import prepare_scene, read_json
import os


def launch_nodes(context):
    share = get_package_share_directory('ceres_route_sim')
    scene_path = LaunchConfiguration('scene_file').perform(context)
    calibration_path = LaunchConfiguration('calibration_file').perform(context)
    view = prepare_scene(read_json(scene_path), read_json(calibration_path) if calibration_path else None)
    return [
        Node(package='ceres_route_sim', executable='route_markers', output='screen',
             parameters=[{'scene_file': ParameterValue(scene_path, value_type=str),
                          'calibration_file': ParameterValue(calibration_path, value_type=str)}]),
        Node(package='rviz2', executable='rviz2',
             arguments=['-d',os.path.join(share,'config','route_view.rviz'), '-f', view.frame],
             condition=IfCondition(LaunchConfiguration('rviz'))),
    ]


def generate_launch_description():
    return LaunchDescription([
        SetEnvironmentVariable('ROS_LOCALHOST_ONLY', '1'),
        DeclareLaunchArgument('scene_file', description='Local exported route-scene v1 JSON'),
        DeclareLaunchArgument('calibration_file', default_value=''),
        DeclareLaunchArgument('rviz', default_value='true', choices=['true', 'false']),
        OpaqueFunction(function=launch_nodes),
    ])
