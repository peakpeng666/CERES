import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from ceres_route_sim.scene_core import SceneError, prepare_scene, read_json
from ceres_route_sim.control_core import command, WAYPOINTS, yaw_from_quaternion

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'config' / 'route_scene_synthetic_fixture.json'


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.scene = read_json(FIXTURE)

    def test_fixture_preserves_nonmetric_frame_and_visits(self):
        view = prepare_scene(self.scene)
        self.assertFalse(view.metric)
        self.assertEqual(view.frame, 'benchmark_xy')
        self.assertIn('NOT metres', view.label)
        self.assertEqual(view.routes[0][1], (0,1,2,0))
        self.assertEqual(view.points[2], (2.,0.))

    def test_physical_mode_rejects_arbitrary_units(self):
        with self.assertRaisesRegex(SceneError, 'cannot be treated as metres'):
            prepare_scene(self.scene, require_metric=True)

    def test_explicit_calibration_rotation_scale_translation(self):
        calibration = dict(source_frame='benchmark_xy',source_units='arbitrary_distance_units',
                           target_frame='survey_map',meters_per_unit=2, yaw_radians=math.pi/2,
                           translation_m=[3,4],calibration_evidence='TEST ONLY known fixture',calibrated=True)
        view = prepare_scene(self.scene, calibration, require_metric=True)
        self.assertTrue(view.metric)
        self.assertEqual(view.frame,'survey_map')
        self.assertAlmostEqual(view.points[2][0],3)
        self.assertAlmostEqual(view.points[2][1],8)
        self.assertIn('NOT navigation',view.label)

    def test_bad_calibrations_rejected(self):
        good = dict(source_frame='benchmark_xy',source_units='arbitrary_distance_units',
                    target_frame='survey_map',meters_per_unit=2,yaw_radians=0,
                    translation_m=[0,0],calibration_evidence='test',calibrated=True)
        for key, value in [('calibrated',False),('calibration_evidence',''),('meters_per_unit',0),
                           ('meters_per_unit',-1),('meters_per_unit',float('inf')),
                           ('translation_m',[1]),('source_frame','other'),('source_units','m'),
                           ('target_frame','benchmark_xy'),('target_frame','/map')]:
            with self.subTest(key=key,value=value):
                with self.assertRaises(SceneError):
                    prepare_scene(self.scene,{**good,key:value})

    def test_fake_metric_scene_rejected(self):
        self.scene['units']['coordinate'] = 'meters'
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)

    def test_fake_simulation_readiness_rejected(self):
        self.scene['simulation_ready'] = True
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)

    def test_implicit_physical_mapping_rejected(self):
        self.scene['frame']['meters_per_coordinate_unit'] = 1
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)

    def test_nonfinite_and_boolean_coordinates_rejected(self):
        for bad in [float('inf'),float('-inf'),float('nan'),True,'1',1e10,10**1000]:
            scene = copy.deepcopy(self.scene)
            scene['depot']['x'] = bad
            with self.subTest(bad=bad), self.assertRaises(SceneError):
                prepare_scene(scene)

    def test_strict_json_rejects_nan(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.json'
            p.write_text('{"x":NaN}')
            with self.assertRaises(SceneError):
                read_json(p)

    def test_duplicate_nodes_rejected(self):
        self.scene['task_nodes'][0]['node_id'] = 0
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)

    def test_unknown_visits_rejected(self):
        self.scene['vehicles'][0]['visit_node_ids'][1] = 999
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)

    def test_mismatched_polyline_rejected(self):
        self.scene['vehicles'][0]['polyline'][1] = [3,3]
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)

    def test_repeated_visits_are_preserved(self):
        self.scene['vehicles'][0]['vehicle_id']=37
        self.scene['vehicles'][0]['visit_node_ids']=[0,1,1,2,0]
        self.scene['vehicles'][0]['polyline']=[[0,0],[1,0],[1,0],[2,0],[0,0]]
        view=prepare_scene(self.scene)
        self.assertEqual(view.routes[0][0],37)
        self.assertEqual(view.routes[0][1],(0,1,1,2,0))

    def test_unserved_depot_rejected(self):
        self.scene['unserved_task_ids']=[0]
        with self.assertRaises(SceneError):
            prepare_scene(self.scene)

    def test_geographic_and_axis_swap_rejected(self):
        for key,value in [('is_geographic',True),('axis_order',['y','x']),('id','/map')]:
            scene=copy.deepcopy(self.scene)
            scene['frame'][key]=value
            with self.assertRaises(SceneError):
                prepare_scene(scene)

    def test_empty_vehicle_list_valid_display(self):
        self.scene['vehicles']=[]
        self.assertEqual(prepare_scene(self.scene).routes,())

    def test_malformed_schema_rejected(self):
        for scene in [None,{},[],{**self.scene,'schema_version':True}]:
            with self.assertRaises(SceneError):
                prepare_scene(scene)


class ControlTests(unittest.TestCase):
    def test_zero_command_on_arrival(self):
        self.assertEqual(command(1,0,0,(1,0)),(0.,0.,True))

    def test_limits_and_rotate_before_drive(self):
        linear,angular,reached=command(0,0,math.pi,(2,0))
        self.assertEqual(linear,0)
        self.assertLessEqual(abs(angular),0.7)
        self.assertFalse(reached)
        self.assertEqual(command(0,0,0,(2,0))[0],0.25)

    def test_corridor_guard(self):
        with self.assertRaises(ValueError):
            command(0,0.9,0,(1,0))

    def test_invalid_pose_rejected(self):
        with self.assertRaises(ValueError):
            command(float('nan'),0,0,(1,0))

    def test_quaternion_validation(self):
        self.assertAlmostEqual(yaw_from_quaternion(0,0,1,0),math.pi)
        with self.assertRaises(ValueError):
            yaw_from_quaternion(0,0,0,0)

    def test_ideal_kinematic_model_converges_not_physics_evidence(self):
        # A pure control unit test. Never label this Gazebo/ROS or physical execution.
        x=y=yaw=0.
        index=0
        for _ in range(2000):
            v,w,reached=command(x,y,yaw,WAYPOINTS[index])
            if reached:
                index+=1
                if index==len(WAYPOINTS):
                    break
            x+=v*math.cos(yaw)*0.05
            y+=v*math.sin(yaw)*0.05
            yaw+=w*0.05
        self.assertEqual(index,len(WAYPOINTS))
        self.assertLess(math.hypot(x,y),0.11)


class StaticIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.world=ET.parse(ROOT/'worlds'/'synthetic_greenhouse.sdf').getroot()

    def test_world_contains_no_external_assets(self):
        self.assertEqual(self.world.findall('.//uri'),[])
        self.assertEqual(self.world.find('world').get('name'),'ceres_synthetic_greenhouse')
        self.assertEqual(len(self.world.findall('.//sensor[@type="imu"]')),1)

    def test_robot_topic_and_frame_isolation(self):
        robot=self.world.find(".//model[@name='ceres_synthetic_robot']")
        drive=robot.find("plugin[@name='ignition::gazebo::systems::DiffDrive']")
        self.assertEqual(drive.findtext('topic'),'/ceres_synthetic/cmd_vel')
        self.assertEqual(drive.findtext('frame_id'),'synthetic_odom')
        self.assertEqual(drive.findtext('child_frame_id'),'synthetic_base_footprint')
        self.assertEqual(drive.findtext('wheel_radius'),'0.10')
        for side in ['left','right']:
            self.assertIsNotNone(robot.find(f"joint[@name='{drive.findtext(side+'_joint')}']"))

    def test_independent_ground_truth_plugin(self):
        truth=self.world.find(".//plugin[@name='ignition::gazebo::systems::OdometryPublisher']")
        self.assertEqual(truth.findtext('odom_topic'),'/ceres_synthetic/ground_truth')
        self.assertEqual(truth.findtext('odom_frame'),'synthetic_world')
        self.assertEqual(truth.findtext('robot_base_frame'),'synthetic_body')

    def test_route_publisher_cannot_command_robot(self):
        source=(ROOT/'ceres_route_sim'/'route_markers.py').read_text()
        self.assertNotIn('import Twist',source)
        self.assertNotIn('import Path',source)
        self.assertNotIn('TransformBroadcaster',source)
        self.assertEqual(source.count('create_publisher('),1)
        self.assertIn("'/ceres_routes/markers'",source)

    def test_fortress_bridge_has_single_command_direction(self):
        source=(ROOT/'config'/'bridge_fortress.yaml').read_text()
        self.assertEqual(source.count('direction: ROS_TO_GZ'),1)
        self.assertEqual(source.count('direction: GZ_TO_ROS'),5)
        self.assertNotIn('BIDIRECTIONAL',source)
        self.assertNotIn('gz.msgs.',source)

    def test_package_xml(self):
        package=ET.parse(ROOT/'package.xml').getroot()
        self.assertEqual(package.findtext('name'),'ceres_route_sim')
        self.assertEqual(package.findtext('./export/build_type'),'ament_python')

if __name__ == '__main__':
    unittest.main()
