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
    # Pinky 의 CSI 카메라는 180° 뒤집혀 장착돼 있다. 좌우·상하를 함께 뒤집어야
    # 화면이 바로 선다 (한쪽만 켜면 글자가 거울처럼 보인다).
    # 정방향으로 단 기체에서는 둘 다 false 로 넘기면 된다.
    csi_hflip_arg = DeclareLaunchArgument(
        'csi_hflip',
        default_value='true',
        description='source=csi 좌우 반전 (뒤집혀 장착된 카메라 보정)',
    )
    csi_vflip_arg = DeclareLaunchArgument(
        'csi_vflip',
        default_value='true',
        description='source=csi 상하 반전 (뒤집혀 장착된 카메라 보정)',
    )
    preview_max_width_arg = DeclareLaunchArgument(
        'preview_max_width',
        default_value='640',
        description='미리보기 전송 전 축소할 가로 폭 (0 이면 원본 그대로)',
    )
    preview_jpeg_quality_arg = DeclareLaunchArgument(
        'preview_jpeg_quality',
        default_value='60',
        description='미리보기 JPEG 품질 (낮출수록 지연·대역폭 감소)',
    )
    preview_max_fps_arg = DeclareLaunchArgument(
        'preview_max_fps',
        default_value='5.0',
        description='관제 미리보기 최대 FPS',
    )
    arming_poll_seconds_arg = DeclareLaunchArgument(
        'arming_poll_seconds',
        default_value='2.0',
        description='백엔드에 arming 여부를 물어보는 주기(초)',
    )
    arming_fail_disarm_after_arg = DeclareLaunchArgument(
        'arming_fail_disarm_after',
        default_value='5',
        description='폴링이 이 횟수 연속 실패하면 disarmed 로 떨어뜨린다 (페일세이프)',
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
                'preview_max_width': LaunchConfiguration('preview_max_width'),
                'preview_jpeg_quality': LaunchConfiguration('preview_jpeg_quality'),
                'preview_max_fps': LaunchConfiguration('preview_max_fps'),
                'arming_poll_seconds': LaunchConfiguration('arming_poll_seconds'),
                'arming_fail_disarm_after': LaunchConfiguration(
                    'arming_fail_disarm_after'),
            },
        ],
        prefix=['nice', '-n', '5'],
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
        preview_max_width_arg,
        preview_jpeg_quality_arg,
        preview_max_fps_arg,
        arming_poll_seconds_arg,
        arming_fail_disarm_after_arg,
        qr_node,
    ])
