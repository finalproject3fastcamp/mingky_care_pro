"""Rear camera publisher and low-bandwidth MJPEG preview."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    rear_camera = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare('mingky_bringup'), 'launch', 'rear_camera.launch.py',
        ])),
    )
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
        DeclareLaunchArgument('rear_preview_port', default_value='8092'),
        DeclareLaunchArgument('preview_max_fps', default_value='3.0'),
        DeclareLaunchArgument('preview_max_width', default_value='640'),
        DeclareLaunchArgument('preview_jpeg_quality', default_value='60'),
        rear_camera,
        rear_stream,
    ])
