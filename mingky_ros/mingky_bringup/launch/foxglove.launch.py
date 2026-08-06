"""Foxglove Bridge 실행.

Nav2 디버깅에 쓴다. 코스트맵·TF·경로·라이다를 실시간으로 본다.
로그만 보고 '왜 안 가지' 를 푸는 것보다 훨씬 빠르다.

로봇마다 ROS 도메인이 달라(pinky1=21, pinky2=20) 관제 한 곳에서 두 대를
동시에 볼 수 없다. **로봇마다 하나씩 띄우고 Foxglove Studio 에서 접속을
갈아탄다.**

    # pinky1 에서
    ros2 launch mingky_bringup foxglove.launch.py

    # 같은 포트를 쓰는 다른 브리지가 있으면
    ros2 launch mingky_bringup foxglove.launch.py port:=8766

Foxglove Studio 접속 주소

    pinky1  ws://192.168.0.21:8765
    pinky2  ws://192.168.0.22:8765

주행 디버깅에 먼저 띄울 패널

    Map            /map
    Costmap        /global_costmap/costmap, /local_costmap/costmap
    Path           /plan, /local_plan
    LaserScan      /scan
    Pose           /amcl_pose, /particlecloud
    Plot           /cmd_vel  linear.x, angular.z
    Parameters     controller_server, global_costmap, local_costmap
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    port = LaunchConfiguration("port")
    address = LaunchConfiguration("address")
    topic_whitelist = LaunchConfiguration("topic_whitelist")

    return LaunchDescription([
        DeclareLaunchArgument(
            "port", default_value="8765",
            description="WebSocket 포트"),
        DeclareLaunchArgument(
            "address", default_value="0.0.0.0",
            description="바인드 주소. 0.0.0.0 이어야 다른 PC 에서 붙는다"),
        DeclareLaunchArgument(
            # 기본값은 전부 허용이다. 무선이 좁으면 필요한 것만 남겨라.
            # 카메라와 코스트맵이 대역폭을 가장 많이 먹는다.
            "topic_whitelist", default_value="['.*']",
            description="공개할 토픽 정규식 목록"),

        Node(
            package="foxglove_bridge",
            executable="foxglove_bridge",
            name="foxglove_bridge",
            output="screen",
            parameters=[{
                "port": port,
                "address": address,
                "topic_whitelist": topic_whitelist,
                # 파라미터 읽기/쓰기를 허용한다.
                # Studio 의 Parameters 패널에서 튜닝할 수 있다.
                "capabilities": [
                    "clientPublish", "parameters", "parametersSubscribe",
                    "services", "connectionGraph", "assets",
                ],
                # 2.4GHz 무선이라 대역폭이 넉넉하지 않다.
                # 큰 메시지는 압축해서 보낸다.
                "send_buffer_limit": 10_000_000,
                "use_compression": True,
                # 서비스 호출은 필요할 때만.
                "service_type_retrieval_timeout_ms": 250,
            }],
        ),
    ])
