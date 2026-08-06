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
        default_value='mono8',
        description='ROS Image 인코딩. 컬러 확인이 필요하면 bgr8 사용',
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
            },
        ],
    )

    return LaunchDescription([
        video_device_arg,
        camera_frame_id_arg,
        output_encoding_arg,
        camera_node,
    ])
