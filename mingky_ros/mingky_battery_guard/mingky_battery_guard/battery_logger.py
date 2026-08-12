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

import csv
from datetime import datetime
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
        # 어떤 부하 조건에서 잰 기록인지 표시한다. 조건이 다른 기록을 나중에
        # 한 축에 섞으면 비교가 무의미해지므로, 파일 이름과 칼럼 양쪽에 남긴다.
        #   예: idle_only / full_system / full_system_driving
        self.declare_parameter('session_label', '')
        # 모터를 멈춘 뒤 전압이 회복되는 데 시간이 걸린다. 멈추자마자 잰 값은
        # 아직 처져 있어서 휴지 전압이 아니다. 이만큼 지나야 인정한다.
        self.declare_parameter('rest_settle_sec', 60.0)
        # 이 이상이면 움직이는 것으로 본다. 노이즈를 걸러내기 위한 값.
        self.declare_parameter('motion_epsilon', 0.01)
        self.declare_parameter('summary_period_sec', 60.0)

        g = self.get_parameter
        self.rest_settle = float(g('rest_settle_sec').value)
        self.motion_eps = float(g('motion_epsilon').value)

        self.label = str(g('session_label').value).strip()

        out_dir = Path(g('out_dir').value).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        suffix = f'_{self.label}' if self.label else ''
        self.path = out_dir / f'battery_{stamp}{suffix}.csv'
        self.fh = open(self.path, 'w', newline='', encoding='utf-8')
        self.csv = csv.writer(self.fh)
        self.csv.writerow([
            'time', 'elapsed_sec', 'voltage', 'percent_linear',
            'percent_published', 'state', 'idle_sec', 'label',
        ])
        self.fh.flush()

        self.started = self.now()
        self.last_motion_at = None      # None = 시작 후 한 번도 안 움직임
        self.published_percent = None

        # 요약은 덱이 아니라 누적 집계로 낸다.
        # 5초 주기에 덱 200 이면 약 17분치뿐이라, 30분 방치를 재는 동안 초기
        # 고전압 표본이 밀려나간다. 그러면 요약의 '휴지 최대' 가 완충 전압이
        # 아니라 '최근 17분 중 최고' 가 되고 ΔV 도 같이 어긋난다.
        # CSV 는 전부 남으니 사후 계산은 정확하지만, 측정 중에 보는 건 요약이라
        # 그걸 믿고 판단하면 틀린다.
        self.rest_max = None
        self.rest_min = None
        self.rest_last = None
        self.rest_n = 0
        self.load_min = None
        self.load_n = 0
        self.samples = 0

        # settling 구간(정지 후 자연 회복)의 상승폭과 소요 시간.
        # 이 값 자체가 데이터다. battery_guard 의 trend_rise 가 이보다 작으면
        # 모터 부하 해제를 충전으로 오인해 저전압 경보가 영영 막힌다.
        self.settle_from_v = None
        self.settle_from_t = None
        self.settle_rises = []      # (상승폭 V, 소요 초)
        self.prev_state = None

        self.create_subscription(Float32, 'battery/voltage', self.on_voltage, 10)
        self.create_subscription(Float32, 'battery/percent', self.on_percent, 10)
        # 모터가 도는지는 실제 명령으로 판단한다. 안전 게이트 출력이 최종이므로
        # 그쪽을 본다. 게이트를 안 쓰는 환경이면 그냥 cmd_vel 이 들어온다.
        self.create_subscription(Twist, 'cmd_vel', self.on_cmd, 10)

        self.create_timer(float(g('summary_period_sec').value), self.summary)
        self.get_logger().info(
            f'기록 시작 → {self.path}\n'
            f'  부하 조건: {self.label or "(라벨 없음)"}\n'
            f'  휴지 인정: 정지 후 {self.rest_settle:.0f}초 경과\n'
            '  조건이 다르면 session_label 을 바꿔 따로 기록하세요.')

    # ------------------------------------------------------------------

    def now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def idle_sec(self):
        """마지막으로 움직인 뒤 지난 시간. 한 번도 안 움직였으면 시작부터."""
        base = self.last_motion_at if self.last_motion_at is not None else self.started
        return self.now() - base

    def state_now(self):
        """표본의 부하 조건. 분류가 틀리면 수집한 데이터 전체가 무의미해진다."""
        # 움직이는 걸 아직 못 봤다는 건 '정지' 가 아니라 '모름' 이다.
        # 기동 직후를 load 로 넣으면 부하 최저 전압이 오염된다.
        if self.last_motion_at is None:
            if self.now() - self.started >= self.rest_settle:
                return 'rest'       # 이만큼 지나도록 명령이 없었으면 정지로 본다
            return 'unknown'        # 판단 보류. 어느 통계에도 넣지 않는다
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
            state, f'{self.idle_sec():.1f}', self.label,
        ])
        self.fh.flush()     # 로봇 전원이 갑자기 꺼져도 여기까지는 남는다

        self.track_settling(state, v)

        if state == 'rest':
            self.rest_max = v if self.rest_max is None else max(self.rest_max, v)
            self.rest_min = v if self.rest_min is None else min(self.rest_min, v)
            self.rest_last = v
            self.rest_n += 1
        elif state == 'load':
            self.load_min = v if self.load_min is None else min(self.load_min, v)
            self.load_n += 1
        # unknown / settling 은 어느 통계에도 넣지 않는다.
        self.samples += 1

    def track_settling(self, state, v):
        """정지 후 자연 회복 구간의 상승폭과 소요 시간을 모은다.

        휴지 전압 비교에서 settling 을 빼는 것과는 별개로, 이 구간 자체가
        정해야 할 값이다. battery_guard 의 trend_rise 가 이 상승폭보다 작으면
        모터 부하 해제를 충전으로 오인해 저전압 경보가 영영 막힌다.
        """
        prev, self.prev_state = self.prev_state, state

        if state == 'settling':
            if prev != 'settling':          # 회복 시작
                self.settle_from_v = v
                self.settle_from_t = self.now()
            return

        if prev == 'settling' and self.settle_from_v is not None:
            # 회복이 끝났다. rest 로 갔든 다시 load 로 갔든 구간은 닫는다.
            self.settle_rises.append(
                (v - self.settle_from_v, self.now() - self.settle_from_t))
            self.settle_from_v = None
            self.settle_from_t = None

    # ------------------------------------------------------------------

    def summary(self):
        if not self.samples:
            self.get_logger().warn(
                'battery/voltage 를 아직 못 받았습니다. '
                'bringup 또는 battery_publisher 가 도는지 확인하세요.')
            return

        parts = [f'표본 {self.samples}개']
        if self.rest_n:
            parts.append(
                f'휴지 {self.rest_min:.3f}~{self.rest_max:.3f}V '
                f'(최근 {self.rest_last:.3f}V, {self.rest_n}개)')
        if self.load_n:
            parts.append(f'부하 최저 {self.load_min:.3f}V ({self.load_n}개)')
        if self.rest_n and self.load_n:
            # 이 값이 팩 건강도다. 커질수록 힘을 못 낸다.
            parts.append(f'ΔV {self.rest_max - self.load_min:.3f}V')
        self.get_logger().info(' | '.join(parts))

        if self.settle_rises:
            rises = [r for r, _ in self.settle_rises]
            durs = [d for _, d in self.settle_rises]
            self.get_logger().info(
                f'  정지 후 회복 {len(rises)}회 | '
                f'상승 평균 +{sum(rises) / len(rises):.3f}V '
                f'최대 +{max(rises):.3f}V | '
                f'소요 평균 {sum(durs) / len(durs):.0f}초')

    def destroy_node(self):
        try:
            self.fh.close()
            self.get_logger().info(f'기록 저장 완료 → {self.path}')
        except OSError:
            pass
        return super().destroy_node()


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
