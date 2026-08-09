#!/usr/bin/env python3
"""배터리 측정값을 필터링해 저전압 상태 변화를 발행한다.

    battery/voltage 구독 → 기준치 이하 → 부저 3번 → battery/low=True
    (battery/percent 는 전압을 한 번도 못 받았을 때 쓰는 예비 경로)

이 노드는 측정과 판정만 담당한다. 세션 종료, 이벤트 발행, Nav2 충전소 복귀는
프로젝트 상태를 소유한 mingky_guide_manager 가 battery/low 를 받아 처리한다.

충전 중이면 울리지 않는다. 이미 충전되고 있는데 경보를 내거나,
충전소에 있는 로봇을 충전소로 또 보내는 것을 막기 위함이다.

퍼센트 ↔ 전압 (pinkylib/battery.py)
    percent = (V - 6.8) / (7.6 - 6.8) * 100
    1% = 8mV 로 촘촘하다. 모터가 돌면 0.1V(=12%p)가 순식간에 빠진다.
      40% = 7.12V     100% = 7.60V
"""

from collections import deque
import statistics
import subprocess
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, Float32

BUSY_EXIT = 3   # battery_buzzer.py 가 "부저 사용 중"일 때 주는 종료코드

# pinkylib/battery.py 와 같은 변환식. 2셀 리튬이온.
EMPTY_V = 6.8   # 0%
FULL_V = 7.6    # 100%


def percent_from_voltage(v):
    """전압 -> 퍼센트. pinkylib 와 같은 식을 쓴다.

    퍼센트를 내는 쪽이 죽어도 전압만으로 감시를 계속하기 위해 필요하다.
    (리튬이온 방전 곡선은 실제로는 선형이 아니라서 이 식은 근사다.
     그래서 기록에는 항상 전압 원본을 함께 남긴다.)
    """
    return max(0.0, min(100.0, (v - EMPTY_V) / (FULL_V - EMPTY_V) * 100.0))


class BatteryGuard(Node):

    def __init__(self, **kwargs):
        # kwargs 는 테스트에서 parameter_overrides 를 넣기 위한 통로다.
        super().__init__('battery_guard', **kwargs)

        # ---- 파라미터 ----
        self.declare_parameter('threshold_percent', 40.0)   # 이 % 이하면 발동
        self.declare_parameter('rearm_percent', 60.0)       # 이 % 이상 회복되면 재무장
        self.declare_parameter('confirm_count', 3)          # 연속 몇 번 낮아야 인정
        self.declare_parameter('trend_samples', 5)          # 추세를 볼 표본 수
        self.declare_parameter('trend_rise', 2.0)           # 이만큼(%p) 올라야 충전 중
        self.declare_parameter('median_samples', 3)         # 중앙값 필터 창 (1이면 끔)

        self.declare_parameter('use_buzzer', True)
        self.declare_parameter('buzzer_script', '/home/pinky/ap/battery_buzzer.py')
        self.declare_parameter('buzzer_level', 'danger')    # danger=784Hz 3번, warn=550Hz 2번

        g = self.get_parameter
        self.threshold = g('threshold_percent').value
        self.rearm = g('rearm_percent').value
        self.confirm = int(g('confirm_count').value)
        self.trend_rise = g('trend_rise').value
        self.use_buzzer = g('use_buzzer').value
        self.buzzer_script = g('buzzer_script').value
        self.buzzer_level = g('buzzer_level').value

        # 재무장 간격이 좁으면 경계에서 껐다 켰다 반복한다.
        if self.rearm - self.threshold < 10.0:
            self.get_logger().warn(
                f'재무장 간격이 {self.rearm - self.threshold:.1f}%p 뿐입니다. '
                '측정 노이즈로 경보가 반복될 수 있습니다. 20%p 이상을 권합니다.')

        # ---- 상태 ----
        self.fired = False                                        # 이미 울렸나
        self.low_count = 0                                        # 연속 저전압 횟수
        self.last_percent = None                                  # 마지막 판단값
        self.history = deque(maxlen=int(g('trend_samples').value))  # 필터 후 측정값

        # battery_publisher 는 필터를 전혀 걸지 않는다. 5초마다 ADC 를 한 번
        # 읽어서 그대로 발행하므로, 샘플 하나가 튀면 그게 그대로 넘어온다.
        # 중앙값은 평균과 달리 튄 값 하나에 끌려가지 않아서 여기에 맞다.
        self.raw = deque(maxlen=max(1, int(g('median_samples').value)))

        # 상태 변경은 늦게 뜬 GuideManager 도 마지막 값을 받도록 latched QoS 를 쓴다.
        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.low_pub = self.create_publisher(Bool, 'battery/low', state_qos)

        # 전압이 1차 소스다. 퍼센트는 전압을 선형 변환한 파생값이라
        # 6.8V 아래·7.6V 위가 전부 0%/100% 로 뭉개진다.
        # 그리고 퍼센트를 내는 쪽이 죽어도 감시는 계속되어야 한다.
        self.voltage = None           # 필터를 거친 값. 판단은 이걸로 한다.
        self.voltage_raw = None       # 방금 받은 원본. 기록용.
        self.saw_voltage = False
        self.create_subscription(Float32, 'battery/voltage', self.on_voltage, 10)
        self.create_subscription(Float32, 'battery/percent', self.on_percent, 10)

        self.get_logger().info(
            f'배터리 감시 시작 | 발동 {self.threshold}% (연속 {self.confirm}회) '
            f'| 재무장 {self.rearm}% | 충전 중이면 건너뜀')

    # ===================================================================

    def is_charging(self):
        """최근 표본이 예전 표본보다 통째로 높으면 충전 중으로 본다.

        별도의 충전 감지 하드웨어가 없어서 전압 추세로 판단한다.
        표본이 다 차기 전에는 판단하지 않는다(False).

        처음과 끝만 비교하면 안 된다. 모터가 멈출 때 전압이 원래대로
        회복되는 것만으로 "오르는 중"이 되어 버리고 (1% = 8mV, 모터 부하는
        12%p 급), 그러면 충전 중으로 오인해 저전압 경보가 영영 안 나간다.
        그래서 최근 표본의 최솟값이 예전 표본의 최댓값보다 높을 것을 요구한다.
        회복은 기준선으로 돌아올 뿐 기준선을 넘지 못하므로 걸러진다.
        """
        if len(self.history) < self.history.maxlen:
            return False
        n = self.history.maxlen // 2
        if n == 0:
            return False
        samples = list(self.history)
        return (min(samples[-n:]) - max(samples[:n])) >= self.trend_rise

    def on_voltage(self, msg: Float32):
        """1차 소스. 중앙값을 거른 뒤 퍼센트로 바꿔 판단한다."""
        self.saw_voltage = True
        self.voltage_raw = msg.data
        self.raw.append(msg.data)
        self.voltage = statistics.median(self.raw)
        self.evaluate(percent_from_voltage(self.voltage))

    def on_percent(self, msg: Float32):
        """전압 토픽이 없을 때만 쓰는 예비 경로. 여기도 똑같이 거른다."""
        if self.saw_voltage:
            return
        self.raw.append(msg.data)
        self.evaluate(statistics.median(self.raw))

    def evaluate(self, pct: float):
        self.last_percent = pct
        self.history.append(pct)
        charging = self.is_charging()

        # 퍼센트는 6.8V 아래·7.6V 위가 전부 0%/100% 로 뭉개지므로
        # 전압을 같이 찍어야 실제 상태를 눈으로 볼 수 있다.
        volt = ''
        if self.voltage is not None:
            volt = f' ({self.voltage:.3f}V)'
            # 원본이 필터값과 벌어졌다면 튄 샘플을 걸러냈다는 뜻이다.
            # 이걸 남겨야 "간혹 낮게 뜬다"를 나중에 근거로 확인할 수 있다.
            if (self.voltage_raw is not None
                    and abs(self.voltage_raw - self.voltage) >= 0.01):
                volt += f' [원본 {self.voltage_raw:.3f}V 걸러냄]'
        self.get_logger().info(
            f'배터리 {pct:.1f}%{volt}' + (' (충전 중)' if charging else ''))

        # 1) 충분히 회복됐으면 재무장. 충전 감지 여부와 무관하게 판단한다.
        #    충전이 끝나 전압이 평평해지면 is_charging() 이 False 로 돌아오는데,
        #    재무장을 그 안에 두면 100% 인 채로 영영 무장 해제로 남는다.
        if self.fired and pct >= self.rearm:
            self.fired = False
            self.low_count = 0
            self.publish_low_state(False)
            self.get_logger().info(f'{pct:.1f}% 회복 — 감시를 다시 시작합니다.')

        # 2) 충전 중이면 경보하지 않는다.
        if charging:
            self.low_count = 0
            return

        # 3) 이미 울렸으면 재무장 전까지 조용히 있는다.
        if self.fired:
            return

        # 4) 기준치 위면 카운터를 지운다.
        if pct > self.threshold:
            self.low_count = 0
            return

        # 5) 모터 부하로 인한 순간 강하를 걸러내기 위해 연속 확인.
        self.low_count += 1
        if self.low_count < self.confirm:
            self.get_logger().warn(
                f'낮음 {self.low_count}/{self.confirm} ({pct:.1f}%)')
            return

        # 6) 충전 중인지 판단할 표본이 아직 부족하면 기다린다.
        #    이게 없으면 confirm_count 가 trend_samples 보다 작을 때
        #    "충전 중"을 알아채기 전에 발동해버린다.
        if len(self.history) < self.history.maxlen:
            self.get_logger().warn(
                f'표본 부족 {len(self.history)}/{self.history.maxlen} — '
                '충전 여부 판단 후 결정합니다.')
            return

        # 7) 발동 — 한 번만.
        self.fired = True
        self.get_logger().warn(f'배터리 {pct:.1f}%{volt} — 기준 도달')

        self.publish_low_state(True)
        self.beep()

    # ===================================================================

    def publish_low_state(self, is_low: bool):
        self.low_pub.publish(Bool(data=is_low))

    def beep(self):
        """로봇의 기존 부저 스크립트를 호출한다 (기본 danger = 784Hz 3번).

        GPIO를 직접 잡지 않는 이유: battery-buzzer.service 데몬이 상시 돌고
        있어서 서로 뺏으면 부저가 깨진다. 저 스크립트는 짧게 울리고 반납하며,
        이미 사용 중이면 종료코드 3으로 비켜준다.
        """
        if not self.use_buzzer:
            return

        def run():
            try:
                rc = subprocess.call(
                    ['python3', self.buzzer_script, 'beep', self.buzzer_level],
                    timeout=15)
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                self.get_logger().warn(f'부저 실행 실패: {e}')
                return
            if rc == BUSY_EXIT:
                self.get_logger().warn('부저 사용 중 — 건너뜀')
            elif rc != 0:
                self.get_logger().warn(f'부저 실패 (종료코드 {rc})')
            else:
                self.get_logger().info(f'부저 울림 ({self.buzzer_level})')

        threading.Thread(target=run, daemon=True).start()   # spin 을 막지 않도록

def main(args=None):
    rclpy.init(args=args)
    node = BatteryGuard()
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
