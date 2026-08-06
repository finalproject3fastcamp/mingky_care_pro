"""Launch a calibrated ArUco detector for any ROS Image topic."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration

from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    """Build the generic ArUco detector launch description."""
    return LaunchDescription([
        DeclareLaunchArgument(
            'image_topic',
            default_value='/rear_camera/image_raw',
            description='ArUco detection input image topic',
        ),
        DeclareLaunchArgument(
            'camera_info_topic',
            default_value='/rear_camera/camera_info',
            description='CameraInfo fallback topic when no YAML is supplied',
        ),
        DeclareLaunchArgument(
            'calibration_file',
            default_value='',
            description='ROS camera calibration YAML path',
        ),
        DeclareLaunchArgument(
            'namespace',
            default_value='rear_aruco',
            description='Output topic namespace',
        ),
        DeclareLaunchArgument(
            'dictionary',
            default_value='DICT_4X4_50',
            description='OpenCV predefined ArUco dictionary',
        ),
        DeclareLaunchArgument(
            'marker_length',
            default_value='0.026',
            description='Printed marker black-square side length in metres',
        ),
        DeclareLaunchArgument(
            'target_marker_id',
            default_value='-1',
            description='Target marker ID; -1 selects the nearest marker',
        ),
        DeclareLaunchArgument(
            'process_every_n',
            default_value='1',
            description='Process one out of every N input frames',
        ),
        Node(
            package='mingky_aruco_detector',
            executable='aruco_detector',
            namespace=LaunchConfiguration('namespace'),
            name='aruco_detector',
            output='screen',
            parameters=[{
                'image_topic': LaunchConfiguration('image_topic'),
                'camera_info_topic': LaunchConfiguration('camera_info_topic'),
                'calibration_file': LaunchConfiguration('calibration_file'),
                'dictionary': LaunchConfiguration('dictionary'),
                'marker_length': LaunchConfiguration('marker_length'),
                'target_marker_id': LaunchConfiguration('target_marker_id'),
                'process_every_n': LaunchConfiguration('process_every_n'),
            }],
        ),
    ])
