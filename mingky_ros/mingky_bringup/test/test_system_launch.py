"""통합 launch에서 안내 핵심 노드가 빠지는 회귀를 막는다."""

from pathlib import Path
import xml.etree.ElementTree as ET


LAUNCH_FILE = (
    Path(__file__).resolve().parents[1] / 'launch' / 'mingky_system.launch.xml'
)
ROBOT_LAUNCH_FILE = (
    Path(__file__).resolve().parents[3]
    / 'pinky' / 'pinky_bringup' / 'launch' / 'bringup_robot.launch.xml'
)


def _root():
    return ET.parse(LAUNCH_FILE).getroot()


def _argument(root, name: str):
    return next(item for item in root.findall('arg') if item.get('name') == name)


def test_qr_reader_is_enabled_for_robot_operation() -> None:
    root = _root()

    assert _argument(root, 'start_qr_reader').get('default') == 'true'
    assert _argument(root, 'qr_source').get('default') == 'csi'

    include = next(
        item for item in root.findall('include')
        if item.get('file', '').endswith('/launch/qr_reader.launch.py')
    )
    assert include.get('if') == '$(var start_qr_reader)'
    forwarded = {
        item.get('name'): item.get('value') for item in include.findall('arg')
    }
    assert forwarded == {
        'source': '$(var qr_source)',
        'robot_id': '$(var robot_id)',
        'backend_url': '$(var backend_url)',
        'preview_port': '$(var qr_preview_port)',
    }


def test_adaptive_recovery_is_the_integrated_default() -> None:
    root = _root()

    assert _argument(root, 'recovery_mode').get('default') == 'adaptive'
    assert _argument(root, 'planner_mode').get('default') == 'navfn'


def test_systemd_owned_publishers_are_not_duplicated() -> None:
    root = _root()

    assert _argument(root, 'start_event_gateway').get('default') == 'false'

    robot_include = next(
        item for item in root.findall('include')
        if item.get('file', '').endswith('/launch/bringup_robot.launch.xml')
    )
    forwarded = {
        item.get('name'): item.get('value')
        for item in robot_include.findall('arg')
    }
    assert forwarded['start_battery_publisher'] == 'false'

    robot_root = ET.parse(ROBOT_LAUNCH_FILE).getroot()
    assert _argument(robot_root, 'start_battery_publisher').get('default') == 'true'
    battery = next(
        item for item in robot_root.findall('node')
        if item.get('exec') == 'battery_publisher'
    )
    assert battery.get('if') == '$(var start_battery_publisher)'


def test_nav2_and_teleop_are_arbitrated_before_safety_gate() -> None:
    root = _root()

    navigation = next(
        item for item in root.findall('include')
        if item.get('file', '').endswith('/launch/bringup_launch.xml')
    )
    navigation_args = {
        item.get('name'): item.get('value')
        for item in navigation.findall('arg')
    }
    assert navigation_args['cmd_vel_output'] == 'cmd_vel_smoothed'

    includes = {
        item.get('file', '').rsplit('/', 1)[-1]: item
        for item in root.findall('include')
    }
    assert 'twist_mux.launch.py' in includes
    assert includes['teleop.launch.py'].get('if') == '$(var start_teleop_control)'
    teleop_args = {
        item.get('name'): item.get('value')
        for item in includes['teleop.launch.py'].findall('arg')
    }
    assert teleop_args['robot_id'] == '$(var robot_id)'

    emergency_stop = next(
        item for item in root.findall('node')
        if item.get('name') == 'emergency_stop'
    )
    params = {
        item.get('name'): item.get('value')
        for item in emergency_stop.findall('param')
    }
    assert params['input_topic'] == 'cmd_vel_safety_input'
    assert params['output_topic'] == 'cmd_vel'
