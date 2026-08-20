"""통합 launch에서 안내 핵심 노드가 빠지는 회귀를 막는다."""

import xml.etree.ElementTree as ET
from pathlib import Path

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
ROBOT_BATTERY_SYSTEMD_UNIT = (
    Path(__file__).resolve().parents[3]
    / 'deploy' / 'robot' / 'systemd' / 'mingky-battery-pub.service'
)
ROBOT_ENV_EXAMPLE = (
    Path(__file__).resolve().parents[3] / 'deploy' / 'robot' / 'robot.env.example'
)
REAR_CAMERA_LAUNCH_FILE = LAUNCH_FILE.parent / 'rear_camera.launch.py'
CAMERA_STREAMS_LAUNCH_FILE = LAUNCH_FILE.parent / 'camera_streams.launch.py'
REAR_CAMERA_CONFIG_FILE = (
    LAUNCH_FILE.parents[1] / 'config' / 'rear_camera.yaml'
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
    assert _argument(root, 'camera_preview_max_fps').get('default') == '5.0'
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


def test_rear_camera_defaults_to_color_for_yolo() -> None:
    launch_text = REAR_CAMERA_LAUNCH_FILE.read_text(encoding='utf-8')
    config_text = REAR_CAMERA_CONFIG_FILE.read_text(encoding='utf-8')

    assert "default_value='bgr8'" in launch_text
    assert 'output_encoding: bgr8' in config_text


def test_rear_tracking_uses_rate_limited_compressed_stream() -> None:
    root = _root()
    camera_text = CAMERA_STREAMS_LAUNCH_FILE.read_text(encoding='utf-8')
    follower = next(
        item for item in root.findall('node')
        if item.get('name') == 'person_follow_node'
    )
    follower_params = {
        item.get('name'): item.get('value')
        for item in follower.findall('param')
    }

    assert '/rear_camera/tracking/image_raw/compressed' in camera_text
    assert "prefix=['nice -n 5']" in camera_text
    assert follower_params['image_topic'] == (
        '/rear_camera/tracking/image_raw/compressed')
    assert follower.get('launch-prefix') == 'nice -n 5'


def test_high_rate_topic_health_is_aggregated_outside_gateway() -> None:
    root = _root()
    monitor = next(
        item for item in root.findall('node')
        if item.get('name') == 'topic_health_monitor'
    )

    assert monitor.get('pkg') == 'mingky_bringup'
    assert monitor.get('exec') == 'topic_health_monitor'


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
    assert _argument(root, 'planner_mode').get('default') == 'smac2d'
    assert _argument(
        root, 'recovery_retry_delay_sec').get('default') == '0.3'
    managers = {
        item.get('name'): item for item in root.findall('node')
        if item.get('name') in ('guide_manager', 'navigation_manager')
    }
    for manager in managers.values():
        params = {
            item.get('name'): item.get('value')
            for item in manager.findall('param')
        }
        assert params['recovery_mode'] == '$(var recovery_mode)'
        assert params['planner_mode'] == '$(var planner_mode)'
        assert params['recovery_retry_delay_sec'] == (
            '$(var recovery_retry_delay_sec)')


def test_low_obstacle_sidestep_is_opt_in() -> None:
    root = _root()

    assert _argument(root, 'low_obstacle_mode').get('default') == 'disabled'
    guide_manager = next(
        item for item in root.findall('node')
        if item.get('name') == 'guide_manager'
    )
    params = {
        item.get('name'): item.get('value')
        for item in guide_manager.findall('param')
    }
    assert params['low_obstacle_mode'] == '$(var low_obstacle_mode)'

    navigation_manager = next(
        item for item in root.findall('node')
        if item.get('name') == 'navigation_manager'
    )
    navigation_params = {
        item.get('name'): item.get('value')
        for item in navigation_manager.findall('param')
    }
    assert navigation_params['low_obstacle_mode'] == '$(var low_obstacle_mode)'

    event_gateway = next(
        item for item in root.findall('node')
        if item.get('name') == 'event_gateway'
    )
    gateway_params = {
        item.get('name'): item.get('value')
        for item in event_gateway.findall('param')
    }
    assert gateway_params['low_obstacle_mode'] == '$(var low_obstacle_mode)'

    unit = ROBOT_SYSTEMD_UNIT.read_text(encoding='utf-8')
    env_example = ROBOT_ENV_EXAMPLE.read_text(encoding='utf-8')
    assert 'low_obstacle_mode:=${MINGKY_LOW_OBSTACLE_MODE:-disabled}' in unit
    assert 'MINGKY_LOW_OBSTACLE_MODE=disabled' in env_example


def test_patient_distance_guidance_is_enabled_for_test() -> None:
    root = _root()

    assert _argument(root, 'start_patient_follow').get('default') == 'true'
    assert _argument(root, 'patient_follow_slow_distance').get('default') == '0.15'
    assert _argument(root, 'patient_follow_stop_distance').get('default') == '0.30'
    assert _argument(root, 'patient_follow_tracking_grace').get('default') == '2.0'
    assert _argument(root, 'patient_follow_initial_acquire_grace').get('default') == '4.0'
    assert _argument(root, 'patient_follow_initial_acquire_distance').get('default') == '0.30'
    assert _argument(root, 'patient_follow_target_height').get('default') == '0.13'
    assert _argument(
        root, 'patient_follow_target_min_confidence'
    ).get('default') == '0.55'
    assert _argument(
        root, 'patient_follow_target_class_overlap_iou'
    ).get('default') == '0.50'
    assert _argument(
        root, 'patient_follow_target_class_confidence_margin'
    ).get('default') == '0.15'
    assert _argument(
        root, 'patient_follow_target_confirm_frames'
    ).get('default') == '3'
    assert _argument(
        root, 'patient_follow_target_confirm_max_jump_px'
    ).get('default') == '80.0'
    assert _argument(
        root, 'patient_follow_partial_bbox_max_distance'
    ).get('default') == '0.35'
    assert _argument(
        root, 'patient_follow_partial_bbox_conf_threshold'
    ).get('default') == '0.70'
    assert _argument(root, 'patient_follow_wait_limit').get('default') == '20.0'
    follower = next(
        item for item in root.findall('node')
        if item.get('name') == 'person_follow_node'
    )
    assert follower.get('if') == '$(var start_patient_follow)'
    follower_params = {
        item.get('name'): item.get('value')
        for item in follower.findall('param')
    }
    assert follower_params['infer_server_url'] == (
        '$(var patient_follow_infer_server_url)')
    assert follower_params['slow_distance_m'] == (
        '$(var patient_follow_slow_distance)')
    assert follower_params['stop_distance_m'] == (
        '$(var patient_follow_stop_distance)')
    assert follower_params['tracking_grace_sec'] == (
        '$(var patient_follow_tracking_grace)')
    assert follower_params['initial_acquire_grace_sec'] == (
        '$(var patient_follow_initial_acquire_grace)')
    assert follower_params['initial_acquire_max_distance_m'] == (
        '$(var patient_follow_initial_acquire_distance)')
    assert follower_params['target_height_m'] == (
        '$(var patient_follow_target_height)')
    assert follower_params['target_min_confidence'] == (
        '$(var patient_follow_target_min_confidence)')
    assert follower_params['target_class_overlap_iou'] == (
        '$(var patient_follow_target_class_overlap_iou)')
    assert follower_params['target_class_confidence_margin'] == (
        '$(var patient_follow_target_class_confidence_margin)')
    assert follower_params['target_confirm_frames'] == (
        '$(var patient_follow_target_confirm_frames)')
    assert follower_params['target_confirm_max_jump_px'] == (
        '$(var patient_follow_target_confirm_max_jump_px)')
    assert follower_params['partial_bbox_max_distance_m'] == (
        '$(var patient_follow_partial_bbox_max_distance)')
    assert follower_params['partial_bbox_conf_threshold'] == (
        '$(var patient_follow_partial_bbox_conf_threshold)')

    guide = next(
        item for item in root.findall('node')
        if item.get('name') == 'guide_manager'
    )
    guide_params = {
        item.get('name'): item.get('value')
        for item in guide.findall('param')
    }
    assert guide_params['patient_follow_enabled'] == (
        '$(var start_patient_follow)')
    assert guide_params['patient_follow_wait_limit_sec'] == (
        '$(var patient_follow_wait_limit)')

    unit = ROBOT_SYSTEMD_UNIT.read_text(encoding='utf-8')
    env_example = ROBOT_ENV_EXAMPLE.read_text(encoding='utf-8')
    assert 'start_patient_follow:=${MINGKY_PATIENT_FOLLOW_ENABLED:-true}' in unit
    assert (
        '${MINGKY_PATIENT_FOLLOW_INFER_SERVER_URL:+'
        'patient_follow_infer_server_url:='
        '${MINGKY_PATIENT_FOLLOW_INFER_SERVER_URL}}'
    ) in unit
    assert 'patient_follow_infer_server_url:=${MINGKY_PATIENT_FOLLOW_INFER_SERVER_URL:-}' not in unit
    assert 'MINGKY_PATIENT_FOLLOW_ENABLED=true' in env_example
    assert 'MINGKY_PATIENT_FOLLOW_STOP_DISTANCE_M=0.30' in env_example
    assert 'MINGKY_PATIENT_FOLLOW_TRACKING_GRACE_SEC=2.0' in env_example
    assert 'MINGKY_PATIENT_FOLLOW_INITIAL_ACQUIRE_GRACE_SEC=4.0' in env_example
    assert 'MINGKY_PATIENT_FOLLOW_INITIAL_ACQUIRE_DISTANCE_M=0.30' in env_example
    assert 'MINGKY_PATIENT_FOLLOW_TARGET_HEIGHT_M=0.13' in env_example


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


def test_battery_service_enables_ultrasonic_sensor_data() -> None:
    unit = ROBOT_BATTERY_SYSTEMD_UNIT.read_text(encoding='utf-8')

    assert 'sensor_rate_hz:=10.0' in unit
