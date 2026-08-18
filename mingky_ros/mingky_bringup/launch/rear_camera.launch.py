"""후방 USB 카메라를 로봇 내부 ROS 토픽으로 발행한다."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution

from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


DEFAULT_VIDEO_DEVICE = (
    '/dev/v4l/by-id/'
    'usb-Jieli_Technology_USB_Composite_Device-video-index0'
)


def generate_launch_description() -> LaunchDescription:
    """Build the rear V4L2 camera launch description."""
    config_path = PathJoinSubstitution([
        FindPackageShare('mingky_bringup'),
        'config',
        'rear_camera.yaml',
    ])

    video_device_arg = DeclareLaunchArgument(
        'video_device',
        default_value=DEFAULT_VIDEO_DEVICE,
        description='후방 V4L2 카메라 장치 경로',
    )
    camera_frame_id_arg = DeclareLaunchArgument(
        'camera_frame_id',
        default_value='rear_camera_optical_frame',
        description='Image와 CameraInfo에 기록할 optical frame',
    )
    output_encoding_arg = DeclareLaunchArgument(
        'output_encoding',
        default_value='bgr8',
        description='ROS Image 인코딩. YOLO 입력과 관제 미리보기는 컬러 사용',
    )
    camera_info_url_arg = DeclareLaunchArgument(
        'camera_info_url',
        default_value='',
        description='로봇별 후방 카메라 보정 YAML의 file:// URL',
    )

    camera_node = Node(
        package='v4l2_camera',
        executable='v4l2_camera_node',
        namespace='rear_camera',
        name='rear_camera',
        output='screen',
        parameters=[
            config_path,
            {
                'video_device': LaunchConfiguration('video_device'),
                'camera_frame_id': LaunchConfiguration('camera_frame_id'),
                'output_encoding': LaunchConfiguration('output_encoding'),
                'camera_info_url': LaunchConfiguration('camera_info_url'),
            },
        ],
    )

    return LaunchDescription([
        video_device_arg,
        camera_frame_id_arg,
        output_encoding_arg,
        camera_info_url_arg,
        camera_node,
    ])
