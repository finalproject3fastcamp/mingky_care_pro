"""Rear camera, ArUco detector, and low-bandwidth MJPEG preview."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    OpaqueFunction,
)
from launch.conditions import IfCondition
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
    aruco_detector = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('mingky_aruco_detector'),
            'launch',
            'aruco_detector.launch.py',
        ])),
        condition=IfCondition(LaunchConfiguration('start_aruco_detector')),
        launch_arguments={
            'calibration_file': calibration_file,
        }.items(),
    )
    return [rear_camera, aruco_detector]


def generate_launch_description() -> LaunchDescription:
    """Build the integrated rear-camera launch description."""
    rear_stream = Node(
        package='mingky_camera_streamer',
        executable='image_streamer',
        name='rear_camera_streamer',
        output='screen',
        parameters=[{
            'image_topic': '/rear_camera/image_raw',
            'port': LaunchConfiguration('rear_preview_port'),
            'max_fps': LaunchConfiguration('preview_max_fps'),
            'max_width': LaunchConfiguration('preview_max_width'),
            'jpeg_quality': LaunchConfiguration('preview_jpeg_quality'),
        }],
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
        DeclareLaunchArgument('start_aruco_detector', default_value='true'),
        DeclareLaunchArgument('rear_preview_port', default_value='8092'),
        DeclareLaunchArgument('preview_max_fps', default_value='10.0'),
        DeclareLaunchArgument('preview_max_width', default_value='640'),
        DeclareLaunchArgument('preview_jpeg_quality', default_value='60'),
        OpaqueFunction(function=_rear_camera_actions),
        rear_stream,
    ])
