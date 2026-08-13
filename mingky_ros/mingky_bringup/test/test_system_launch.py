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
ROBOT_INSTALL_SCRIPT = (
    Path(__file__).resolve().parents[3] / 'deploy' / 'robot' / 'install.sh'
)
ROBOT_SYSTEMD_UNIT = (
    Path(__file__).resolve().parents[3]
    / 'deploy' / 'robot' / 'systemd' / 'mingky-system.service'
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
        'preview_max_fps': '$(var camera_preview_max_fps)',
        'preview_max_width': '$(var camera_preview_max_width)',
        'preview_jpeg_quality': '$(var camera_preview_jpeg_quality)',
    }


def test_low_bandwidth_camera_streams_are_integrated() -> None:
    root = _root()

    assert _argument(root, 'qr_preview_port').get('default') == '8091'
    assert _argument(root, 'rear_preview_port').get('default') == '8092'
    assert _argument(root, 'front_camera_ready_timeout').get('default') == '15.0'
    assert _argument(root, 'camera_preview_max_fps').get('default') == '10.0'
    assert _argument(root, 'start_rear_camera_stream').get('default') == 'true'
    qr_distance_arg = _argument(root, 'start_rear_qr_distance')
    assert qr_distance_arg.get('default') == 'true'
    assert _argument(root, 'rear_qr_size').get('default') == '0.028'

    rear = next(
        item for item in root.findall('include')
        if item.get('file', '').endswith('/launch/camera_streams.launch.py')
    )
    assert rear.get('if') == '$(var start_rear_camera_stream)'
    forwarded = {
        item.get('name'): item.get('value') for item in rear.findall('arg')
    }
    assert forwarded['robot_id'] == '$(var robot_id)'
    assert forwarded['camera_profile'] == '$(var camera_profile)'
    assert forwarded['wait_for_front_camera'] == '$(var start_qr_reader)'
    assert (
        forwarded['front_camera_ready_timeout']
        == '$(var front_camera_ready_timeout)'
    )
    assert (
        forwarded['start_qr_distance']
        == '$(var start_rear_qr_distance)'
    )
    assert forwarded['qr_size'] == '$(var rear_qr_size)'


def test_aruco_detector_is_not_started_by_integrated_launch() -> None:
    system_text = LAUNCH_FILE.read_text(encoding='utf-8')
    camera_text = (
        LAUNCH_FILE.parent / 'camera_streams.launch.py'
    ).read_text(encoding='utf-8')

    assert 'mingky_aruco_detector' not in system_text
    assert 'start_rear_aruco_detector' not in system_text
    assert 'mingky_aruco_detector' not in camera_text
    assert 'start_aruco_detector' not in camera_text


def test_adaptive_recovery_is_the_integrated_default() -> None:
    root = _root()

    assert _argument(root, 'recovery_mode').get('default') == 'adaptive'
    assert _argument(root, 'planner_mode').get('default') == 'navfn'


def test_non_clinical_navigation_has_a_separate_manager() -> None:
    root = _root()
    managers = {
        item.get('name'): item
        for item in root.findall('node')
        if item.get('name') in ('guide_manager', 'navigation_manager')
    }

    assert set(managers) == {'guide_manager', 'navigation_manager'}
    assert managers['guide_manager'].get('pkg') == 'mingky_guide_manager'
    assert managers['navigation_manager'].get('pkg') == 'mingky_navigation_manager'


def test_lcd_status_is_the_only_integrated_lcd_owner() -> None:
    root = _root()

    assert _argument(root, 'start_lcd_status').get('default') == 'true'
    lcd_nodes = [
        item for item in root.findall('node')
        if item.get('name') == 'lcd_status'
    ]
    assert len(lcd_nodes) == 1
    assert lcd_nodes[0].get('pkg') == 'mingky_lcd_status'
    assert lcd_nodes[0].get('if') == '$(var start_lcd_status)'
    assert all(item.get('pkg') != 'pinky_emotion' for item in root.findall('node'))


def test_fire_evacuation_is_configurable_in_integrated_launch() -> None:
    root = _root()

    assert _argument(root, 'start_fire_evac').get('default') == 'false'
    assert _argument(root, 'fire_infer_server_url').get('default') == ''
    fire_node = next(
        item for item in root.findall('node')
        if item.get('name') == 'fire_evac_node'
    )
    assert fire_node.get('pkg') == 'mingky_fire_evac'
    assert fire_node.get('if') == '$(var start_fire_evac)'
    params = {
        item.get('name'): item.get('value')
        for item in fire_node.findall('param')
    }
    assert params == {
        'robot_id': '$(var robot_id)',
        'infer_server_url': '$(var fire_infer_server_url)',
    }

    unit = ROBOT_SYSTEMD_UNIT.read_text(encoding='utf-8')
    assert 'start_fire_evac:=${MINGKY_FIRE_EVAC_ENABLED:-false}' in unit
    assert 'fire_infer_server_url:=${MINGKY_FIRE_INFER_SERVER_URL:-}' in unit


def test_systemd_uses_a_writable_working_directory() -> None:
    unit = ROBOT_SYSTEMD_UNIT.read_text(encoding='utf-8')

    # lgpio creates notification files relative to the process directory.
    assert 'WorkingDirectory=/home/pinky' in unit


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
    assert 'teleop.launch.py' not in includes

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


def test_systemd_starts_the_teleop_control_nodes() -> None:
    install_script = ROBOT_INSTALL_SCRIPT.read_text(encoding='utf-8')

    enable_block = install_script.split('systemctl enable --now', 1)[1].split(
        'cat <<EOF', 1)[0]
    assert 'mingky-teleop-bridge' in enable_block
    assert 'fg-teleop' in enable_block
    assert 'mingky-system' in enable_block
