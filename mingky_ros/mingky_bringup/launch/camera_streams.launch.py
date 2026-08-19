"""Rear camera, QR distance detector, and low-bandwidth MJPEG preview."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    GroupAction,
    IncludeLaunchDescription,
    OpaqueFunction,
    RegisterEventHandler,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution

from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


ROBOT_CAMERA_PROFILES = {
    'pinky-01': 'pinky_6294',
    'pinky-02': 'pinky_15e2',
}


def _rear_camera_actions(context):
    """Create camera and detector with the calibration for this robot."""
    robot_id = LaunchConfiguration('robot_id').perform(context)
    camera_profile = LaunchConfiguration('camera_profile').perform(context)
    camera_profile = camera_profile or ROBOT_CAMERA_PROFILES.get(robot_id, '')

    calibration_file = ''
    camera_info_url = ''
    if camera_profile:
        calibration_path = (
            Path(get_package_share_directory('mingky_bringup'))
            / 'config' / 'camera' / camera_profile / 'rear_camera.yaml'
        )
        calibration_file = str(calibration_path)
        camera_info_url = calibration_path.as_uri()

    rear_camera = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('mingky_bringup'),
            'launch',
            'rear_camera.launch.py',
        ])),
        launch_arguments={
            'camera_info_url': camera_info_url,
        }.items(),
    )
    qr_distance = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('mingky_qr_distance'),
            'launch',
            'qr_distance.launch.py',
        ])),
        condition=IfCondition(LaunchConfiguration('start_qr_distance')),
        launch_arguments={
            'calibration_file': calibration_file,
            'qr_size': LaunchConfiguration('qr_size'),
        }.items(),
    )
    return [rear_camera, qr_distance]


def _rear_stream_action() -> Node:
    return Node(
        package='mingky_camera_streamer',
        executable='image_streamer',
        name='rear_camera_streamer',
        output='screen',
        parameters=[{
            'image_topic': '/rear_camera/image_raw',
            'compressed_topic': LaunchConfiguration(
                'tracking_compressed_topic'),
            'compressed_jpeg_quality': LaunchConfiguration(
                'tracking_jpeg_quality'),
            'port': LaunchConfiguration('rear_preview_port'),
            'max_fps': LaunchConfiguration('preview_max_fps'),
            'max_width': LaunchConfiguration('preview_max_width'),
            'jpeg_quality': LaunchConfiguration('preview_jpeg_quality'),
        }],
        prefix='nice -n 5',
    )


def _rear_actions():
    return [
        OpaqueFunction(function=_rear_camera_actions),
        _rear_stream_action(),
    ]


def generate_launch_description() -> LaunchDescription:
    """Build the integrated rear-camera launch description."""
    front_camera_waiter = ExecuteProcess(
        cmd=[
            'timeout', LaunchConfiguration('front_camera_ready_timeout'),
            'ros2', 'topic', 'echo', '/front_camera/ready',
            'std_msgs/msg/Bool', '--once',
            '--qos-durability', 'transient_local',
        ],
        name='front_camera_ready_waiter',
        output='screen',
        condition=IfCondition(LaunchConfiguration('wait_for_front_camera')),
    )
    return LaunchDescription([
        DeclareLaunchArgument('robot_id', default_value='pinky-01'),
        DeclareLaunchArgument(
            'camera_profile',
            default_value='',
            description=(
                '보정 폴더 이름. 비우면 pinky-01/pinky-02에서 자동 선택'
            ),
        ),
        DeclareLaunchArgument('start_qr_distance', default_value='true'),
        DeclareLaunchArgument(
            'qr_size', default_value='0.028',
            description='Printed QR symbol side length in metres',
        ),
        DeclareLaunchArgument('rear_preview_port', default_value='8092'),
        DeclareLaunchArgument(
            'wait_for_front_camera',
            default_value='false',
            description=(
                '전방 CSI 카메라 준비 신호를 받은 뒤 후방 카메라 시작'
            ),
        ),
        DeclareLaunchArgument(
            'front_camera_ready_timeout',
            default_value='15.0',
            description='전방 준비 신호 최대 대기 시간. 초과 시 후방은 계속 시작',
        ),
        DeclareLaunchArgument('preview_max_fps', default_value='5.0'),
        DeclareLaunchArgument('preview_max_width', default_value='640'),
        DeclareLaunchArgument('preview_jpeg_quality', default_value='60'),
        DeclareLaunchArgument(
            'tracking_compressed_topic',
            default_value='/rear_camera/tracking/image_raw/compressed',
        ),
        DeclareLaunchArgument('tracking_jpeg_quality', default_value='70'),
        GroupAction(
            actions=_rear_actions(),
            condition=UnlessCondition(
                LaunchConfiguration('wait_for_front_camera')),
        ),
        RegisterEventHandler(
            OnProcessExit(
                target_action=front_camera_waiter,
                on_exit=_rear_actions(),
            ),
        ),
        front_camera_waiter,
    ])
