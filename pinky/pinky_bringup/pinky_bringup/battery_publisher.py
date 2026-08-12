#!/usr/bin/env python3
"""
배터리 전압을 읽어 battery/voltage 와 battery/percent 로 발행한다.

I2C 를 만지는 노드다. 이 노드가 두 개 뜨면 측정값이 조용히 망가진다.

    0x08 장치는 "커맨드 바이트로 채널 선택 -> 변환 -> 2바이트 읽기" 구조이고,
    선택된 채널을 하나만 기억한다. write 와 read 사이에 다른 프로세스가
    채널을 바꾸면 엉뚱한 채널 값을 배터리 값으로 읽는다. 통신은 성공하고
    예외도 안 나므로 호출한 쪽에서 알아챌 방법이 없다.

    실측(2026-08-12, pinky2): 리더가 둘일 때 전압 중앙값이 0.397V 낮아지고
    산포가 37mV -> 3.22V 로 벌어졌다. 리더를 하나로 되돌리자 2mV 오차로
    복귀했다. 오염은 값을 낮추는 방향으로만 작용한다.

그래서 두 가지를 한다.
    1. 기동 직후 같은 토픽의 다른 발행자를 확인하고, 있으면 종료한다
    2. 한 번 읽은 전압으로 퍼센트까지 계산한다 (읽기 횟수 절반)
"""

from pinkylib import Battery
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Float32

# pinkylib/battery.py 의 battery_percentage() 와 같은 식.
# 여기서 직접 계산하는 이유는 get_voltage() 를 두 번 부르지 않기 위해서다.
EMPTY_V = 6.8   # 0%
FULL_V = 7.6    # 100%


def percent_from_voltage(volt):
    """전압 -> 퍼센트. pinkylib 와 같은 식과 같은 반올림을 쓴다."""
    percent = (volt - EMPTY_V) / (FULL_V - EMPTY_V) * 100
    return round(max(0.0, min(100.0, percent)), 2)


class BatteryPublisher(Node):

    def __init__(self, **kwargs):
        # 노드 이름의 오타는 기존 배포와 맞추기 위해 그대로 둔다.
        # 바꾸면 이 이름을 보고 있는 스크립트나 문서가 조용히 어긋난다.
        super().__init__('battery_publihser', **kwargs)

        self.declare_parameter('publish_period_sec', 5.0)
        # 기동 직후에는 DDS 탐색이 끝나지 않아 다른 발행자가 안 보인다.
        # 이만큼 기다린 뒤 한 번 확인한다.
        self.declare_parameter('duplicate_check_sec', 3.0)
        self.declare_parameter('exit_on_duplicate', True)

        self.period = float(self.get_parameter('publish_period_sec').value)
        self.exit_on_duplicate = bool(
            self.get_parameter('exit_on_duplicate').value)

        self.battery = Battery()

        self.percentage_publisher = self.create_publisher(
            Float32, 'battery/percent', 10)
        self.voltage_publisher = self.create_publisher(
            Float32, 'battery/voltage', 10)

        # 타이머 하나로 둘 다 발행한다. 예전에는 타이머가 둘이라
        # battery_percentage() 와 get_voltage() 를 따로 불렀고, 그러면
        # I2C 를 5초마다 40회 읽는 데다 두 값이 서로 다른 표본에서 나왔다.
        # 실제로 한 줄에 "66.33% (8.18V)" 처럼 앞뒤가 안 맞는 출력이 나왔다.
        self.timer = self.create_timer(self.period, self.publish_once)

        check_after = float(self.get_parameter('duplicate_check_sec').value)
        self._dup_timer = self.create_timer(check_after, self._check_duplicate)

        self.get_logger().info(
            f'배터리 발행 시작 | {self.period:.1f}초 주기 '
            f'| 중복 확인 {check_after:.1f}초 후')

    # ------------------------------------------------------------------

    def _check_duplicate(self):
        """같은 토픽에 다른 발행자가 있는지 한 번만 본다."""
        self._dup_timer.cancel()

        others = self.count_publishers('battery/voltage') - 1
        if others <= 0:
            return

        self.get_logger().error(
            f'battery/voltage 에 다른 발행자가 {others}개 있습니다. '
            'I2C 를 동시에 읽으면 전압이 실제보다 낮게 측정됩니다.')
        self.get_logger().error(
            'mingky-battery-pub.service 와 주행 런치 중 하나만 이 노드를 '
            '띄워야 합니다. (주행 런치는 start_battery_publisher:=false)')

        if not self.exit_on_duplicate:
            self.get_logger().warn('exit_on_duplicate=false — 그대로 진행합니다.')
            return

        self.get_logger().error('중복을 피해 종료합니다.')
        raise SystemExit(1)

    def publish_once(self):
        """전압을 한 번 읽어 두 토픽을 모두 채운다."""
        volt = self.battery.get_voltage()
        if volt is None:
            # pinkylib 은 20회 읽기가 전부 실패하면 None 을 준다.
            self.get_logger().warn('배터리 전압을 읽지 못했습니다.')
            return

        volt = float(volt)
        self.voltage_publisher.publish(Float32(data=volt))
        self.percentage_publisher.publish(
            Float32(data=float(percent_from_voltage(volt))))


def main(args=None):
    rclpy.init(args=args)
    publisher = None
    code = 0
    try:
        publisher = BatteryPublisher()
        rclpy.spin(publisher)
    except (KeyboardInterrupt, ExternalShutdownException):
        # systemctl stop 은 SIGTERM 을 보낸다. 잡지 않으면 종료할 때마다
        # traceback 이 서비스 로그에 남는다.
        pass
    except SystemExit as e:
        code = getattr(e, 'code', 0) or 0
    finally:
        if publisher is not None:
            try:
                publisher.battery.close()
            except OSError:
                pass
            publisher.destroy_node()
        # 이미 shutdown 된 컨텍스트에 또 부르면 RCLError 가 난다.
        if rclpy.ok():
            rclpy.shutdown()
    return code


if __name__ == '__main__':
    # ros2 run 은 setuptools 래퍼가 sys.exit(main()) 로 감싸주지만,
    # 스크립트로 직접 실행하면 반환값이 버려져 종료코드가 항상 0이 된다.
    raise SystemExit(main())
