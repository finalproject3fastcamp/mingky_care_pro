"""cmd_vel 안전 게이트와 비상정지 지속성 회귀 테스트."""

from geometry_msgs.msg import Twist
from mingky_battery_guard.emergency_stop import EmergencyStop
import pytest
import rclpy
from rclpy.parameter import Parameter
from std_msgs.msg import Bool


@pytest.fixture(scope='module', autouse=True)
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


def make_gate(state_file):
    return EmergencyStop(parameter_overrides=[
        Parameter('use_led', value=False),
        Parameter('cancel_nav2', value=False),
        Parameter('state_file', value=str(state_file)),
    ])


def test_gate_forwards_normally_and_blocks_while_engaged(tmp_path):
    node = make_gate(tmp_path / 'emergency.state')
    commands = []
    node.publish_cmd = lambda msg: commands.append((msg.linear.x, msg.angular.z))

    moving = Twist()
    moving.linear.x = 0.2
    moving.angular.z = 0.1
    node.on_cmd(moving)
    node.engage('obstacle')
    blocked = Twist()
    blocked.linear.x = 0.3
    node.on_cmd(blocked)
    node.tick_safety()

    assert commands == [(0.2, 0.1), (0.0, 0.0), (0.0, 0.0)]
    node.release()
    node.destroy_node()


def test_engaged_state_survives_node_restart(tmp_path):
    state_file = tmp_path / 'emergency.state'
    first = make_gate(state_file)
    first.engage('operator')
    first.destroy_node()

    second = make_gate(state_file)
    assert second.engaged is True
    assert second.reason == 'operator'
    second.release()
    second.destroy_node()


def test_communication_loss_engages_persistent_stop(tmp_path):
    node = make_gate(tmp_path / 'emergency.state')

    node.on_communication(Bool(data=True))

    assert node.engaged is True
    assert node.reason == 'communication_loss'
    node.release()
    node.destroy_node()
