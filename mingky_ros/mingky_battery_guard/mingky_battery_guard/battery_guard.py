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
        self.declare_parameter('confirm_count', 3)          # 창 안에서 몇 번 낮아야 인정
        self.declare_parameter('confirm_window', 6)         # 그 '창'의 크기
        self.declare_parameter('trend_samples', 5)          # 추세를 볼 표본 수
        self.declare_parameter('trend_rise', 2.0)           # 이만큼(%p) 올라야 충전 중
        self.declare_parameter('median_samples', 3)         # 중앙값 필터 창 (1이면 끔)
        # 로봇 기본 부저(battery-buzzer.service)의 danger 선과 같은 값.
        # 여기까지 내려간 전압은 확인 절차 없이 즉시 알린다.
        self.declare_parameter('critical_voltage', 6.80)
        # 위험 판정 전용 창. 중앙값 필터 창과 겸용하면 안 된다.
        # median_samples=1 로 필터를 끄면 창 크기가 1 이 되어 'N회 이상' 을
        # 영영 만족하지 못하고, 안전 경로가 조용히 죽는다.
        self.declare_parameter('critical_confirm', 2)
        self.declare_parameter('critical_window', 3)

        self.declare_parameter('use_buzzer', True)
        self.declare_parameter('buzzer_script', '/home/pinky/ap/battery_buzzer.py')
        self.declare_parameter('buzzer_level', 'danger')    # danger=784Hz 3번, warn=550Hz 2번

        g = self.get_parameter
        self.threshold = g('threshold_percent').value
        self.rearm = g('rearm_percent').value
        self.confirm = int(g('confirm_count').value)
        self.confirm_window = max(self.confirm, int(g('confirm_window').value))
        self.critical_v = float(g('critical_voltage').value)
        self.critical_confirm = max(1, int(g('critical_confirm').value))
        self.critical_window = max(
            self.critical_confirm, int(g('critical_window').value))
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
        self.last_percent = None                                  # 마지막 판단값

        # 저전압 여부를 '연속'이 아니라 '최근 창 안의 횟수'로 센다.
        # 연속 카운터는 기준치 위를 한 번만 봐도 0 으로 초기화되는데, 방전이
        # 진행된 배터리는 주행하면 처지고 멈추면 회복하기를 반복하므로 연속이
        # 절대 쌓이지 않는다. 실제로 70% <-> 8% 를 왕복하는 동안 경보가 한 번도
        # 나가지 않는 현상이 관제에서 보고됐다.
        self.low_window = deque(maxlen=self.confirm_window)
        self.history = deque(maxlen=int(g('trend_samples').value))  # 필터 후 측정값

        # battery_publisher 는 필터를 전혀 걸지 않는다. 5초마다 ADC 를 한 번
        # 읽어서 그대로 발행하므로, 샘플 하나가 튀면 그게 그대로 넘어온다.
        # 중앙값은 평균과 달리 튄 값 하나에 끌려가지 않아서 여기에 맞다.
        self.raw = deque(maxlen=max(1, int(g('median_samples').value)))

        # 위험 판정은 원본을 따로 모아서 본다. 중앙값 창을 쓰면 필터를 껐을 때
        # 창이 1 이 되어 판정이 성립하지 않는다.
        self.crit_raw = deque(maxlen=self.critical_window)

        # 상태 변경은 늦게 뜬 GuideManager 도 마지막 값을 받도록 latched QoS 를 쓴다.
        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.low_pub = self.create_publisher(Bool, 'battery/low', state_qos)

        # 판단에 쓰는 값은 중앙값이라 최저 전압이 가려진다. 실제로 6.75V 까지
        # 처져 로봇 기본 부저가 울리는데 관제 화면은 31% 로 보이는 상황이
        # 발생했다. 최저값을 따로 내보내 그 간극을 없앤다.
        self.vmin_pub = self.create_publisher(Float32, 'battery/voltage_min', state_qos)

        # 전압이 1차 소스다. 퍼센트는 전압을 선형 변환한 파생값이라
        # 6.8V 아래·7.6V 위가 전부 0%/100% 로 뭉개진다.
        # 그리고 퍼센트를 내는 쪽이 죽어도 감시는 계속되어야 한다.
        self.voltage = None           # 필터를 거친 값. 판단은 이걸로 한다.
        self.voltage_raw = None       # 방금 받은 원본. 기록용.
        self.saw_voltage = False
        self.create_subscription(Float32, 'battery/voltage', self.on_voltage, 10)
        self.create_subscription(Float32, 'battery/percent', self.on_percent, 10)

        self.get_logger().info(
            f'배터리 감시 시작 | 발동 {self.threshold}% '
            f'(최근 {self.confirm_window}회 중 {self.confirm}회) '
            f'| 위험선 {self.critical_v:.2f}V 즉시 | 재무장 {self.rearm}%')

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

    @property
    def low_count(self):
        """최근 창 안에서 기준치 이하였던 표본 수."""
        return sum(self.low_window)

    def critical_now(self):
        """원본 전압이 위험선까지 내려갔나.

        중앙값이 아니라 원본을 본다. 중앙값은 최저값을 버리기 때문에 위험한
        강하를 그대로 감춘다. 다만 ADC 단발 오류로 복귀까지 시키면 곤란하므로
        critical_window 안에서 critical_confirm 회 이상일 때만 인정한다.

        전용 창을 쓴다. 중앙값 창을 겸용하면 median_samples=1 일 때 창 크기가
        1 이 되어 'N회 이상' 이 영영 성립하지 않고 안전 경로가 조용히 죽는다.
        """
        if not self.saw_voltage or not self.crit_raw:
            return False
        below = sum(1 for v in self.crit_raw if v <= self.critical_v)
        return below >= self.critical_confirm

    def on_voltage(self, msg: Float32):
        """1차 소스. 중앙값을 거른 뒤 퍼센트로 바꿔 판단한다."""
        self.saw_voltage = True
        self.voltage_raw = msg.data
        self.raw.append(msg.data)
        self.crit_raw.append(msg.data)
        self.voltage = statistics.median(self.raw)
        self.vmin_pub.publish(Float32(data=float(min(self.raw))))
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
            # 최저값을 같이 찍는다. 중앙값만 보면 위험한 강하가 숨는다.
            vmin = min(self.raw) if self.raw else None
            if vmin is not None and abs(vmin - self.voltage) >= 0.01:
                volt += f' [최저 {vmin:.3f}V]'
        self.get_logger().info(
            f'배터리 {pct:.1f}%{volt}' + (' (충전 중)' if charging else ''))

        # 저전압 여부를 창에 먼저 기록한다. 기준치 위여도 지우지 않고 밀어낸다.
        # 연속 카운터를 쓰면 왕복하는 배터리에서 영영 쌓이지 않는다.
        self.low_window.append(pct <= self.threshold)

        # 1) 회복은 '지속'되어야 인정한다. 충전 감지 여부와는 무관하다.
        #    (충전이 끝나 전압이 평평해지면 is_charging() 이 False 로 돌아오므로,
        #     재무장을 그 안에 두면 100% 인 채로 영영 무장 해제로 남는다)
        #
        #    높은 값 하나로 풀어주면 안 된다. 방전된 배터리는 멈출 때마다
        #    기준치 위로 회복하므로, 발동과 해제를 반복하며 관제에 이벤트
        #    폭풍을 만들고 충전소 복귀도 계속 취소·재시도된다.
        #    창이 전부 기준치 위일 때만 푼다.
        window_full = len(self.low_window) == self.low_window.maxlen
        if (self.fired and pct >= self.rearm and window_full
                and self.low_count == 0 and not self.critical_now()):
            self.fired = False
            self.low_window.clear()
            self.publish_low_state(False)
            self.get_logger().info(
                f'{pct:.1f}% 회복 (최근 {self.confirm_window}회 모두 정상) — '
                '감시를 다시 시작합니다.')

        # 2) 위험선 도달은 확인 절차를 건너뛴다.
        #    로봇 기본 부저가 이미 울리고 있는 구간이다. 충전 중이든 표본이
        #    부족하든, 여기까지 내려갔으면 알려야 한다.
        critical = self.critical_now()

        # 3) 충전 중이면 경보하지 않는다. 단 위험선은 예외.
        if charging and not critical:
            return

        # 5) 이미 울렸으면 재무장 전까지 조용히 있는다.
        if self.fired:
            return

        if critical:
            vmin = min(self.crit_raw)
            below = sum(1 for v in self.crit_raw if v <= self.critical_v)
            self.get_logger().error(
                f'위험 전압 {vmin:.3f}V (기준 {self.critical_v:.2f}V, '
                f'최근 {len(self.crit_raw)}회 중 {below}회) — 즉시 발동')
        else:
            # 6) 창 안에 충분히 쌓이지 않았으면 기다린다.
            if self.low_count < self.confirm:
                if pct <= self.threshold:
                    self.get_logger().warn(
                        f'낮음 {self.low_count}/{self.confirm} '
                        f'(최근 {len(self.low_window)}회 중) — {pct:.1f}%')
                return

            # 7) 충전 중인지 판단할 표본이 아직 부족하면 기다린다.
            if len(self.history) < self.history.maxlen:
                self.get_logger().warn(
                    f'표본 부족 {len(self.history)}/{self.history.maxlen} — '
                    '충전 여부 판단 후 결정합니다.')
                return

            self.get_logger().warn(
                f'배터리 {pct:.1f}%{volt} — 기준 도달 '
                f'(최근 {len(self.low_window)}회 중 {self.low_count}회 낮음)')

        # 8) 발동 — 재무장 전까지 한 번만.
        self.fired = True
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
