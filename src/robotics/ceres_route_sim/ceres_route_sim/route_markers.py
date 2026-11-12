"""Display-only MarkerArray publisher. Deliberately has no cmd_vel/Path/TF code."""
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy
from geometry_msgs.msg import Point
from visualization_msgs.msg import Marker, MarkerArray
from .scene_core import prepare_scene, read_json

PALETTE = [(0.1,0.7,1.0),(1.0,0.5,0.1),(0.7,0.4,1.0),(0.0,0.8,0.6)]


class RouteMarkers(Node):
    def __init__(self):
        super().__init__('route_scene_markers')
        self.declare_parameter('scene_file', '')
        self.declare_parameter('calibration_file', '')
        path = self.get_parameter('scene_file').value
        calibration = self.get_parameter('calibration_file').value
        self.scene = prepare_scene(read_json(path), read_json(calibration) if calibration else None)
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.publisher = self.create_publisher(MarkerArray, '/ceres_routes/markers', qos)
        self.create_timer(1.0, self.publish_scene)
        self.get_logger().warn(self.scene.label)
        self.get_logger().info(self.scene.identity_label + '; ' + self.scene.status_label)
        self.publish_scene()

    def marker(self, namespace, mid, kind, color):
        m = Marker()
        m.header.frame_id = self.scene.frame
        m.header.stamp = self.get_clock().now().to_msg()
        m.ns, m.id, m.type, m.action = namespace, mid, kind, Marker.ADD
        m.pose.orientation.w = 1.0
        m.color.r, m.color.g, m.color.b, m.color.a = (*color, 1.0)
        return m

    def publish_scene(self):
        result = MarkerArray()
        clear = Marker()
        clear.header.frame_id = self.scene.frame
        clear.header.stamp = self.get_clock().now().to_msg()
        clear.action = Marker.DELETEALL
        result.markers.append(clear)
        size = self.scene.point_size
        for i, (nid, (x, y)) in enumerate(self.scene.points.items()):
            color = (1.0, 0.2, 0.2) if nid in self.scene.unserved else (0.9, 0.9, 0.9)
            kind = Marker.CUBE if nid == self.scene.depot_id else Marker.SPHERE
            m = self.marker('nodes', i, kind, color)
            m.pose.position.x, m.pose.position.y = x, y
            m.scale.x = m.scale.y = m.scale.z = size
            result.markers.append(m)
            label = self.marker('labels', i, Marker.TEXT_VIEW_FACING, color)
            label.pose.position.x, label.pose.position.y, label.pose.position.z = x, y, size * 2
            label.scale.z, label.text = size, str(nid)
            result.markers.append(label)
        for i, (vehicle, visits, points) in enumerate(self.scene.routes):
            m = self.marker(f'visits_vehicle_{vehicle}', i, Marker.LINE_STRIP, PALETTE[i % len(PALETTE)])
            m.scale.x = size * 0.2
            m.points = [Point(x=x, y=y, z=size*0.1) for x, y in points]
            result.markers.append(m)
        text = self.marker('semantics', 0, Marker.TEXT_VIEW_FACING, (1.0, 0.8, 0.1))
        text.pose.position.y = max(p[1] for p in self.scene.points.values()) + size*5
        text.pose.position.x = sum(p[0] for p in self.scene.points.values()) / len(self.scene.points)
        text.scale.z = size * 1.3
        text.text = '\n'.join((self.scene.label, self.scene.identity_label, self.scene.status_label))
        result.markers.append(text)
        self.publisher.publish(result)


def main():
    rclpy.init()
    node = None
    try:
        node = RouteMarkers()
        rclpy.spin(node)
    finally:
        if node:
            node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
