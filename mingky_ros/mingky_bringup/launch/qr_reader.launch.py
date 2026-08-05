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
    robot_id_arg = DeclareLaunchArgument(
        'robot_id',
        default_value='pinky-01',
        description='스캔한 로봇 ID (백엔드 /qr/scan 필수값)',
    )
    marker_id_arg = DeclareLaunchArgument(
        'marker_id',
        default_value='-1',
        description='도킹 마커 ID (0~49). -1 이면 미지정으로 전송에서 제외',
    )
    csi_width_arg = DeclareLaunchArgument(
        'csi_width',
        default_value='1280',
        description='source=csi 캡처 가로 해상도',
    )
    csi_height_arg = DeclareLaunchArgument(
        'csi_height',
        default_value='720',
        description='source=csi 캡처 세로 해상도',
    )
    preview_port_arg = DeclareLaunchArgument(
        'preview_port',
        default_value='0',
        description='>0 이면 그 포트로 MJPEG 미리보기 송출 (대시보드 임베드용)',
    )
    csi_hflip_arg = DeclareLaunchArgument(
        'csi_hflip',
        default_value='false',
        description='source=csi 좌우 반전 (뒤집혀 장착된 카메라 보정)',
    )
    csi_vflip_arg = DeclareLaunchArgument(
        'csi_vflip',
        default_value='false',
        description='source=csi 상하 반전 (뒤집혀 장착된 카메라 보정)',
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
                'robot_id': LaunchConfiguration('robot_id'),
                'marker_id': LaunchConfiguration('marker_id'),
                'csi_width': LaunchConfiguration('csi_width'),
                'csi_height': LaunchConfiguration('csi_height'),
                'csi_hflip': LaunchConfiguration('csi_hflip'),
                'csi_vflip': LaunchConfiguration('csi_vflip'),
                'preview_port': LaunchConfiguration('preview_port'),
            },
        ],
    )

    return LaunchDescription([
        source_arg,
        image_path_arg,
        backend_url_arg,
        robot_id_arg,
        marker_id_arg,
        csi_width_arg,
        csi_height_arg,
        csi_hflip_arg,
        csi_vflip_arg,
        preview_port_arg,
        qr_node,
    ])
