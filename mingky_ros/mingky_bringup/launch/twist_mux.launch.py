"""cmd_vel 중재기 실행.

모터로 가는 명령에 쓰는 주체가 여럿이다. Nav2 가 자율주행 중인데 텔레옵이
같은 토픽에 발행하면 두 명령이 번갈아 전달된다. 우선순위도 없고 어느 쪽이
반영됐는지 알 수 없다.

    cmd_vel_teleop    (사람)   우선순위 100 ─┐
    cmd_vel_smoothed  (Nav2)  우선순위  10 ─┴→ twist_mux → cmd_vel_safety_input
                                                              │
                                              emergency_stop ─┘ → cmd_vel → 모터

**정지는 여기서 하지 않는다.** 뒤에 있는 mingky_battery_guard 의
emergency_stop 이 단일 게이트로 막는다. 그쪽은 정지 상태를 파일에 남겨
재부팅해도 유지되지만, twist_mux 의 lock 은 노드가 사는 동안만이라 재시작하면
풀린다. 이 노드는 **누가 이기는가만** 정한다.

Nav2 쪽 리맵을 함께 맞춰야 한다. navigation_launch.xml 의 velocity_smoother 가
cmd_vel_smoothed 를 그대로 내보내야 여기로 들어온다.

    ros2 launch pinky_navigation bringup_launch.xml cmd_vel_output:=cmd_vel_smoothed
    ros2 launch mingky_bringup twist_mux.launch.py

입력이 전부 timeout 되면 twist_mux 는 발행을 멈춘다. 그때 모터를 세우는 것은
pinky_bringup 의 command_timeout 워치독이다. 중재기만으로는 정지가 보장되지
않는다.
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

DEFAULT_CONFIG = str(
    Path(get_package_share_directory("mingky_bringup")) / "config" / "twist_mux.yaml"
)


def generate_launch_description() -> LaunchDescription:
    config_file = LaunchConfiguration("config_file")
    output_topic = LaunchConfiguration("output_topic")

    return LaunchDescription([
        DeclareLaunchArgument(
            "config_file", default_value=DEFAULT_CONFIG,
            description="우선순위와 timeout 을 담은 twist_mux 설정"),
        DeclareLaunchArgument(
            # 안전 게이트(emergency_stop)의 입력으로 보낸다. 게이트를 쓰지
            # 않는 구성이면 cmd_vel 로 바꾼다.
            "output_topic", default_value="cmd_vel_safety_input",
            description="중재 결과를 내보낼 토픽"),

        Node(
            package="twist_mux",
            executable="twist_mux",
            name="twist_mux",
            output="screen",
            parameters=[config_file],
            remappings=[("cmd_vel_out", output_topic)],
        ),
    ])
