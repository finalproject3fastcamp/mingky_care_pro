#!/usr/bin/env python3
"""배터리 실측 기록기. 전압을 신뢰할 수 있는 지표로 바꾸기 위한 근거를 모은다.

퍼센트를 못 믿는 이유는 배터리가 여러 센서에 직결돼 부하가 계속 변하기
때문이다. 하드웨어를 바꿀 수 없으므로 대신 **측정 조건을 고정**한다.
같은 조건에서 잰 값끼리는 비교할 수 있다.

이 노드는 조건을 나눠서 기록만 한다. 판정은 하지 않는다.

    휴지(rest)  모터가 rest_settle_sec 이상 멈춰 있던 구간
                -> 이 값만 잔량 비교에 쓴다
    부하(load)  모터가 도는 구간
                -> 이 값은 '팩이 힘을 낼 수 있나' 를 본다

두 값의 차이(ΔV)가 팩 건강도의 직접 지표다. 리튬이온은 열화·방전이
진행될수록 내부저항이 올라가 같은 전류에도 더 크게 처진다.

    ros2 run mingky_battery_guard battery_logger
    ros2 run mingky_battery_guard battery_logger --ros-args -p out_dir:=~/logs

CSV 한 줄이 표본 하나다. 나중에 표계산으로 그대로 열린다.
"""

from collections import deque
import csv
from datetime import datetime
import math
from pathlib import Path

from geometry_msgs.msg import Twist
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

# pinkylib/battery.py 와 같은 변환식. 비교용으로 같이 기록한다.
EMPTY_V = 6.8
FULL_V = 7.6


def percent_from_voltage(v):
    return max(0.0, min(100.0, (v - EMPTY_V) / (FULL_V - EMPTY_V) * 100.0))


class BatteryLogger(Node):

    def __init__(self, **kwargs):
        super().__init__('battery_logger', **kwargs)

        self.declare_parameter('out_dir', '~')
        # 모터를 멈춘 뒤 전압이 회복되는 데 시간이 걸린다. 멈추자마자 잰 값은
        # 아직 처져 있어서 휴지 전압이 아니다. 이만큼 지나야 인정한다.
        self.declare_parameter('rest_settle_sec', 60.0)
        # 이 이상이면 움직이는 것으로 본다. 노이즈를 걸러내기 위한 값.
        self.declare_parameter('motion_epsilon', 0.01)
        self.declare_parameter('summary_period_sec', 60.0)

        g = self.get_parameter
        self.rest_settle = float(g('rest_settle_sec').value)
        self.motion_eps = float(g('motion_epsilon').value)

        out_dir = Path(g('out_dir').value).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.path = out_dir / f'battery_{stamp}.csv'
        self.fh = open(self.path, 'w', newline='', encoding='utf-8')
        self.csv = csv.writer(self.fh)
        self.csv.writerow([
            'time', 'elapsed_sec', 'voltage', 'percent_linear',
            'percent_published', 'state', 'idle_sec',
        ])
        self.fh.flush()

        self.started = self.now()
        self.last_motion_at = None      # None = 시작 후 한 번도 안 움직임
        self.published_percent = None

        # 조건별로 따로 모은다. 섞으면 비교가 무의미해진다.
        self.rest_v = deque(maxlen=200)
        self.load_v = deque(maxlen=200)
        self.samples = 0

        self.create_subscription(Float32, 'battery/voltage', self.on_voltage, 10)
        self.create_subscription(Float32, 'battery/percent', self.on_percent, 10)
        # 모터가 도는지는 실제 명령으로 판단한다. 안전 게이트 출력이 최종이므로
        # 그쪽을 본다. 게이트를 안 쓰는 환경이면 그냥 cmd_vel 이 들어온다.
        self.create_subscription(Twist, 'cmd_vel', self.on_cmd, 10)

        self.create_timer(float(g('summary_period_sec').value), self.summary)
        self.get_logger().info(
            f'기록 시작 → {self.path}\n'
            f'  휴지 인정: 정지 후 {self.rest_settle:.0f}초 경과\n'
            '  완충 직후 / 방치 / 주행 을 각각 충분히 담아 주세요.')

    # ------------------------------------------------------------------

    def now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def idle_sec(self):
        """마지막으로 움직인 뒤 지난 시간. 한 번도 안 움직였으면 시작부터."""
        base = self.last_motion_at if self.last_motion_at is not None else self.started
        return self.now() - base

    def state_now(self):
        if self.idle_sec() < 1.0:
            return 'load'
        if self.idle_sec() >= self.rest_settle:
            return 'rest'
        return 'settling'      # 회복 중. 어느 쪽에도 넣지 않는다.

    def on_cmd(self, msg: Twist):
        moving = (abs(msg.linear.x) > self.motion_eps
                  or abs(msg.angular.z) > self.motion_eps)
        if moving:
            self.last_motion_at = self.now()

    def on_percent(self, msg: Float32):
        self.published_percent = msg.data

    def on_voltage(self, msg: Float32):
        v = float(msg.data)
        state = self.state_now()
        elapsed = self.now() - self.started

        self.csv.writerow([
            datetime.now().isoformat(timespec='seconds'),
            f'{elapsed:.1f}', f'{v:.3f}', f'{percent_from_voltage(v):.1f}',
            '' if self.published_percent is None else f'{self.published_percent:.1f}',
            state, f'{self.idle_sec():.1f}',
        ])
        self.fh.flush()     # 로봇 전원이 갑자기 꺼져도 여기까지는 남는다

        if state == 'rest':
            self.rest_v.append(v)
        elif state == 'load':
            self.load_v.append(v)
        self.samples += 1

    # ------------------------------------------------------------------

    def summary(self):
        if not self.samples:
            self.get_logger().warn(
                'battery/voltage 를 아직 못 받았습니다. '
                'bringup 또는 battery_publisher 가 도는지 확인하세요.')
            return

        parts = [f'표본 {self.samples}개']
        if self.rest_v:
            parts.append(
                f'휴지 {statmin(self.rest_v):.3f}~{max(self.rest_v):.3f}V '
                f'(최근 {self.rest_v[-1]:.3f}V)')
        if self.load_v:
            parts.append(f'부하 최저 {min(self.load_v):.3f}V')
        if self.rest_v and self.load_v:
            # 이 값이 팩 건강도다. 커질수록 힘을 못 낸다.
            parts.append(f'ΔV {max(self.rest_v) - min(self.load_v):.3f}V')
        self.get_logger().info(' | '.join(parts))

    def destroy_node(self):
        try:
            self.fh.close()
            self.get_logger().info(f'기록 저장 완료 → {self.path}')
        except OSError:
            pass
        return super().destroy_node()


def statmin(seq):
    """빈 수열이 와도 죽지 않게 감싼다."""
    return min(seq) if seq else math.nan


def main(args=None):
    rclpy.init(args=args)
    node = BatteryLogger()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
