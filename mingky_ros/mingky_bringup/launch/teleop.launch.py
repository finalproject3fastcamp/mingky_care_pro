"""사람이 로봇을 모는 경로를 연다.

    Foxglove Teleop 패널 → cmd_vel_teleop_raw
                            → teleop_limiter (상한) → cmd_vel_teleop
                            → twist_mux (우선순위 100) → cmd_vel → 모터

**이 launch 를 띄우지 않으면 텔레옵이 동작하지 않는다.** `cmd_vel_teleop` 에
발행자가 없어 twist_mux 가 그 입력을 후보로 올리지 않기 때문이다. 조작을
쓰려는 사람이 명시적으로 켜는 구조를 의도한 것이다. 늘 켜 두면 같은 망에
있는 누구나 언제든 로봇을 몰 수 있다.

twist_mux 는 여기 포함하지 않는다. 중재기는 자율주행에도 필요해서 수명이
다르다. `twist_mux.launch.py` 를 따로 띄운다.

    ros2 launch mingky_bringup teleop.launch.py
    ros2 launch mingky_bringup teleop.launch.py max_linear:=0.1

상한은 로봇 쪽에서 자른다. Foxglove 패널의 속도 설정은 조작자가 바꿀 수
있으므로 그것만으로는 상한이 아니다.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    max_linear = LaunchConfiguration("max_linear")
    max_angular = LaunchConfiguration("max_angular")

    return LaunchDescription([
        DeclareLaunchArgument(
            "max_linear", default_value="0.15",
            description="텔레옵 직진 속도 상한 [m/s]"),
        DeclareLaunchArgument(
            "max_angular", default_value="0.6",
            description="텔레옵 회전 속도 상한 [rad/s]"),

        DeclareLaunchArgument(
            "robot_id", default_value="pinky-01",
            description="모드 변경 이벤트에 실을 로봇 ID"),
        DeclareLaunchArgument(
            "initial_mode", default_value="auto",
            description="기동 시 모드. 사람이 켜지 않는 한 auto 로 시작한다"),
        DeclareLaunchArgument(
            "mode_publish_interval_sec", default_value="1.0",
            description="현재 모드를 반복 발행하는 주기 [초]"),
        DeclareLaunchArgument(
            "mode_mismatch_grace_sec", default_value="3.0",
            description="limiter 적용 불일치를 장애로 판단하기 전 유예 [초]"),
        DeclareLaunchArgument(
            "applied_mode_timeout_sec", default_value="3.0",
            description="limiter 상태를 최신으로 인정하는 시간 [초]"),

        # 제어권의 정본. 서버가 아니라 로봇이 들고 있어야 통신이 끊겨도
        # 안전한 상태가 유지된다.
        Node(
            package="mingky_teleop",
            executable="mode_manager",
            name="mode_manager",
            output="screen",
            parameters=[{
                "robot_id": LaunchConfiguration("robot_id"),
                "initial_mode": LaunchConfiguration("initial_mode"),
                "mode_publish_interval_sec": LaunchConfiguration(
                    "mode_publish_interval_sec"),
                "mode_mismatch_grace_sec": LaunchConfiguration(
                    "mode_mismatch_grace_sec"),
                "applied_mode_timeout_sec": LaunchConfiguration(
                    "applied_mode_timeout_sec"),
            }],
        ),

        Node(
            package="mingky_teleop",
            executable="teleop_limiter",
            name="teleop_limiter",
            output="screen",
            parameters=[{
                "max_linear": max_linear,
                "max_angular": max_angular,
                "state_publish_interval_sec": LaunchConfiguration(
                    "mode_publish_interval_sec"),
            }],
        ),
    ])
