"""Session-local profile for deterministic Gazebo odometry; no system files changed."""
import hashlib
from pathlib import Path
import yaml


def create_profile(runtime, localization='amcl'):
    root = Path('/opt/ros/humble/share/turtlebot4_navigation/config')
    changes = {
        'localization': {'amcl': {
            'alpha1': .02, 'alpha2': .02, 'alpha3': .02, 'alpha4': .02,
            'max_beams': 180, 'min_particles': 1000, 'max_particles': 3000,
            'sigma_hit': .08, 'z_hit': .95, 'z_rand': .05,
            'update_min_d': .05, 'update_min_a': .05}},
        'nav2': {'controller_server': {
            'controller_plugins': ['FollowPath','FollowCoverage'],
            'general_goal_checker': {'xy_goal_tolerance': .06, 'yaw_goal_tolerance': .10},
            'progress_checker': {'required_movement_radius': .15, 'movement_time_allowance': 20.0},
            'FollowPath': {'xy_goal_tolerance': .06, 'trans_stopped_velocity': .03},
            'FollowCoverage': {
                'plugin':'nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController',
                'desired_linear_vel':.18,'lookahead_dist':.18,'use_velocity_scaled_lookahead_dist':False,
                'transform_tolerance':.2,'min_approach_linear_velocity':.03,'approach_velocity_scaling_dist':.4,
                'use_collision_detection':True,'max_allowed_time_to_collision_up_to_carrot':1.0,
                'use_regulated_linear_velocity_scaling':True,'use_cost_regulated_linear_velocity_scaling':True,
                'regulated_linear_scaling_min_radius':.3,'regulated_linear_scaling_min_speed':.06,
                'use_rotate_to_heading':True,'rotate_to_heading_min_angle':.35,'rotate_to_heading_angular_vel':.6,
                'max_angular_accel':2.0,'max_robot_pose_search_dist':.6,'use_interpolation':True,
                'cost_scaling_dist':.3,'cost_scaling_gain':1.0,'inflation_cost_scaling_factor':4.0}}}}
    def merge(base, delta):
        for k, v in delta.items():
            if isinstance(v, dict): merge(base.setdefault(k,{}), v)
            else: base[k] = v
    # The stock DWB controller repeatedly stalled when a new waypoint was behind
    # the robot after a completed FollowPath goal. Reuse the already validated
    # RPP controller, whose initial rotate-to-heading supports this transition.
    # Nav2 still replans NavigateToPose; FollowPath still receives the CERES path.
    changes['nav2']['controller_server']['FollowPath'] = dict(changes['nav2']['controller_server']['FollowCoverage'])
    changes['localization']['amcl']['tf_broadcast']=localization=='amcl'
    report = {'id': 'gazebo-precision-rpp-v2', 'scope': '仿真低噪声里程计；不适用于实机',
              'localization': localization,
              'feedback': 'Gazebo 真值 + odom → map TF；AMCL 仅旁路观测' if localization=='ground_truth' else 'AMCL + odometry；真值仅用于初始位姿和独立验收', 'files': {}}
    for name, delta in changes.items():
        source = root / (name + '.yaml')
        data = yaml.safe_load(source.read_text())
        if name == 'nav2':
            data['controller_server']['ros__parameters'].pop('FollowPath', None)
        for node, patch in delta.items(): merge(data[node]['ros__parameters'], patch)
        target = Path(runtime) / (name + '.yaml')
        target.write_text(yaml.safe_dump(data, sort_keys=False))
        report['files'][name] = {'path': str(target), 'source': str(source),
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'overrides': delta}
    return report
