"""Include the reviewed official TurtleBot 4 reference; no task or cmd_vel publisher."""
import os
import uuid

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, IncludeLaunchDescription,
                            OpaqueFunction, SetEnvironmentVariable)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from ceres_route_sim.scene_core import read_json
from ceres_route_sim.upstream_core import check_upstream, manifest_path


def include_reference(context):
    manifest = read_json(manifest_path())
    preflight = check_upstream(manifest)
    if not preflight['launch_available']:
        raise RuntimeError('TurtleBot 4 reference preflight blocked: ' + '; '.join(preflight['blockers']))
    share = get_package_share_directory(manifest['package'])
    return [IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(share, manifest['launch_file'])),
        launch_arguments={
            'world': manifest['world'], 'model': manifest['model'],
            'namespace': manifest['namespace'], 'rviz': LaunchConfiguration('rviz').perform(context),
            'use_sim_time': 'true', 'localization': 'false', 'slam': 'false', 'nav2': 'false',
            'x': '0.0', 'y': '0.0', 'z': '0.0', 'yaw': '0.0',
        }.items(),
    )]


def generate_launch_description():
    partition = os.environ.get('IGN_PARTITION', 'ceres_tb4_' + uuid.uuid4().hex)
    if not partition.startswith('ceres_tb4_'):
        raise RuntimeError('TurtleBot 4 reference requires a separate ceres_tb4_ transport partition')
    return LaunchDescription([
        SetEnvironmentVariable('ROS_LOCALHOST_ONLY', '1'),
        SetEnvironmentVariable('ROS_DOMAIN_ID', '72'),
        SetEnvironmentVariable('IGN_IP', '127.0.0.1'),
        SetEnvironmentVariable('IGN_PARTITION', partition),
        DeclareLaunchArgument('rviz', default_value='false', choices=['true', 'false']),
        OpaqueFunction(function=include_reference),
    ])
