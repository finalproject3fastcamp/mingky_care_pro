"""QR reader 노드 실행 launch.

기본 config는 mingky_bringup/config/qr_reader.yaml 에서 로드하고,
자주 바꾸는 값(source / image_path / backend_url)은 CLI 인자로 오버라이드한다.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    default_image_path = PathJoinSubstitution([
        FindPackageShare('mingky_qr_reader'),
        'samples',
        'p001.png',
    ])
    config_path = PathJoinSubstitution([
        FindPackageShare('mingky_bringup'),
        'config',
        'qr_reader.yaml',
    ])

    source_arg = DeclareLaunchArgument(
        'source',
        default_value='image',
        description='카메라 소스: image / usb / csi',
    )
    image_path_arg = DeclareLaunchArgument(
        'image_path',
        default_value=default_image_path,
        description='source=image 일 때 읽을 QR 이미지 경로',
    )
    backend_url_arg = DeclareLaunchArgument(
        'backend_url',
        default_value='http://localhost:8000',
        description='FastAPI 백엔드 base URL',
    )

    qr_node = Node(
        package='mingky_qr_reader',
        executable='qr_reader_node',
        name='qr_reader_node',
        output='screen',
        parameters=[
            config_path,
            {
                'source': LaunchConfiguration('source'),
                'image_path': LaunchConfiguration('image_path'),
                'backend_url': LaunchConfiguration('backend_url'),
            },
        ],
    )

    return LaunchDescription([
        source_arg,
        image_path_arg,
        backend_url_arg,
        qr_node,
    ])
