"""Launch rear-camera QR distance estimation."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    """Build the rear QR detector launch description."""
    return LaunchDescription([
        DeclareLaunchArgument('calibration_file', default_value=''),
        DeclareLaunchArgument('qr_size', default_value='0.028'),
        DeclareLaunchArgument('process_every_n', default_value='1'),
        DeclareLaunchArgument('max_process_fps', default_value='5.0'),
        Node(
            package='mingky_qr_distance',
            executable='qr_distance',
            namespace='rear_qr',
            name='qr_distance',
            output='screen',
            parameters=[{
                'calibration_file': LaunchConfiguration('calibration_file'),
                'qr_size': LaunchConfiguration('qr_size'),
                'process_every_n': LaunchConfiguration('process_every_n'),
                'max_process_fps': LaunchConfiguration('max_process_fps'),
            }],
            prefix=['nice -n 5'],
        ),
    ])
