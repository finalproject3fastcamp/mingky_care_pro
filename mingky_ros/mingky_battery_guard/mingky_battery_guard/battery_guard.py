#!/usr/bin/env python3
"""배터리 측정값을 필터링해 저전압 상태 변화를 발행한다.

    battery/voltage 구독 → 기준치 이하 → 부저 3번 → battery/low=True
    (battery/percent 는 전압을 한 번도 못 받았을 때 쓰는 예비 경로)

이 노드는 측정과 판정만 담당한다. 세션 종료, 이벤트 발행, Nav2 충전소 복귀는
프로젝트 상태를 소유한 mingky_guide_manager 가 battery/low 를 받아 처리한다.

충전 중이면 울리지 않는다. 이미 충전되고 있는데 경보를 내거나,
충전소에 있는 로봇을 충전소로 또 보내는 것을 막기 위함이다.

판정은 전압으로 한다. 퍼센트는 표시·기록용 파생값일 뿐이다.
    percent = (V - 6.8) / (7.6 - 6.8) * 100      (pinkylib/battery.py)

이 식은 단조 선형이라 6.8~7.6V 안에서는 전압 판정과 결과가 같다. 문제는
그 밖을 전부 0%/100% 로 뭉갠다는 것(클램프)이다. 상수가 되면 차이가
사라지고, 차이로 만든 판정이 전부 죽는다.
    - 충전 감지: 7.9→8.3V 상승이 100→100 으로 들어와 영영 '충전 아님'
    - 위험 판정: 6.7V 와 6.3V 가 똑같이 0%
그래서 임계값을 전압에 둔다. 나중에 실측 방전 곡선으로 변환식을 고쳐도
현장에서 맞춰 둔 판정 기준이 흔들리지 않는다는 이점도 같이 온다.

전압 감각 (2셀 리튬이온)
    7.60V 만충 표기   7.28V 재무장   7.12V 발동   6.80V 위험선
    모터가 돌면 0.1V 가 순식간에 빠진다. 히스테리시스는 그보다 커야 한다.
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


def voltage_from_percent(p):
    """퍼센트 -> 전압. percent_from_voltage() 의 역함수.

    예비 경로 전용이다. 클램프된 0%/100% 는 되돌려도 6.8V/7.6V 로만 돌아온다.
    잃어버린 정보를 되찾는 함수가 아니라, 판정 창의 단위를 전압 하나로
    유지하기 위한 어댑터다.
    """
    return EMPTY_V + (max(0.0, min(100.0, p)) / 100.0) * (FULL_V - EMPTY_V)


class BatteryGuard(Node):

    def __init__(self, **kwargs):
        # kwargs 는 테스트에서 parameter_overrides 를 넣기 위한 통로다.
        super().__init__('battery_guard', **kwargs)

        # ---- 파라미터 ----
        # 임계값은 전압이다. 퍼센트는 클램프 구간에서 뭉개져 판정에 못 쓴다
        # (모듈 docstring 참고). 아래 기본값은 기존 40%/60% 의 등가 전압이다.
        self.declare_parameter('low_voltage', 7.12)         # 이 전압 이하면 발동
        self.declare_parameter('rearm_voltage', 7.28)       # 이 전압 이상 회복되면 재무장
        self.declare_parameter('confirm_count', 3)          # 창 안에서 몇 번 낮아야 인정
        self.declare_parameter('confirm_window', 6)         # 그 '창'의 크기
        self.declare_parameter('trend_samples', 5)          # 추세를 볼 표본 수
        # 이만큼(V) 올라야 충전 중. 기존 2.0%p 의 등가는 0.016V 인데 그건
        # ADC 1LSB(약 2mV)의 7배뿐이라 노이즈를 충전으로 오인한다.
        self.declare_parameter('trend_rise_volt', 0.05)
        self.declare_parameter('median_samples', 3)         # 중앙값 필터 창 (1이면 끔)
        # 로봇 기본 부저(battery-buzzer.service)의 danger 선과 같은 값.
        # 여기까지 내려간 전압은 확인 절차 없이 즉시 알린다.
        self.declare_parameter('critical_voltage', 6.80)
        # 위험선을 몇 번 봐야 인정하나. 발행 주기(현재 5초)에 따라 실제
        # 소요 시간이 달라지므로 상수로 묻어두면 안 된다.
        self.declare_parameter('critical_count', 2)

        self.declare_parameter('use_buzzer', True)
        self.declare_parameter('buzzer_script', '/home/pinky/ap/battery_buzzer.py')
        self.declare_parameter('buzzer_level', 'danger')    # danger=784Hz 3번, warn=550Hz 2번

        g = self.get_parameter
        self.low_v = float(g('low_voltage').value)
        self.rearm_v = float(g('rearm_voltage').value)
        self.confirm = int(g('confirm_count').value)
        self.confirm_window = max(self.confirm, int(g('confirm_window').value))
        self.critical_v = float(g('critical_voltage').value)
        self.critical_count = max(1, int(g('critical_count').value))
        self.trend_rise_v = float(g('trend_rise_volt').value)
        self.use_buzzer = g('use_buzzer').value
        self.buzzer_script = g('buzzer_script').value
        self.buzzer_level = g('buzzer_level').value

        # 재무장 간격이 좁으면 경계에서 껐다 켰다 반복한다.
        # 모터 부하만으로 0.1V 가 빠지므로 히스테리시스는 그보다 넉넉해야 한다.
        gap = self.rearm_v - self.low_v
        if gap < 0.10:
            self.get_logger().warn(
                f'재무장 간격이 {gap:.3f}V 뿐입니다. 모터 부하 강하(약 0.1V)에 '
                '묻혀 경보가 반복될 수 있습니다. 0.15V 이상을 권합니다.')

        # ---- 상태 ----
        self.fired = False                                        # 이미 울렸나
        self.last_voltage = None                                  # 마지막 판단값(V)

        # 저전압 여부를 '연속'이 아니라 '최근 창 안의 횟수'로 센다.
        # 연속 카운터는 기준치 위를 한 번만 봐도 0 으로 초기화되는데, 방전이
        # 진행된 배터리는 주행하면 처지고 멈추면 회복하기를 반복하므로 연속이
        # 절대 쌓이지 않는다. 실제로 7.36V <-> 6.86V 를 왕복하는 동안 경보가
        # 한 번도 나가지 않는 현상이 관제에서 보고됐다.
        self.low_window = deque(maxlen=self.confirm_window)
        self.history = deque(maxlen=int(g('trend_samples').value))  # 필터 후 전압

        # battery_publisher 는 필터를 전혀 걸지 않는다. 5초마다 ADC 를 한 번
        # 읽어서 그대로 발행하므로, 샘플 하나가 튀면 그게 그대로 넘어온다.
        # 중앙값은 평균과 달리 튄 값 하나에 끌려가지 않아서 여기에 맞다.
        #
        # 이 덱은 '최근 원본 전압' 하나만 뜻한다. 중앙값 필터와 위험 판정이
        # 각자 필요한 만큼만 잘라 쓴다. 크기를 median_samples 에만 맞추면
        # median_samples=1(필터 끔)일 때 표본이 하나뿐이라 위험 판정이
        # critical_count 를 영영 못 채우고 조용히 죽는다.
        self.median_samples = max(1, int(g('median_samples').value))
        self.raw = deque(maxlen=max(self.median_samples, self.critical_count))

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
            f'배터리 감시 시작 | 발동 {self.low_v:.2f}V '
            f'(최근 {self.confirm_window}회 중 {self.confirm}회) '
            f'| 위험선 {self.critical_v:.2f}V {self.critical_count}회 즉시 '
            f'| 재무장 {self.rearm_v:.2f}V')

    # ===================================================================

    def is_charging(self):
        """최근 표본이 예전 표본보다 통째로 높으면 충전 중으로 본다.

        별도의 충전 감지 하드웨어가 없어서 전압 추세로 판단한다.
        표본이 다 차기 전에는 판단하지 않는다(False).

        처음과 끝만 비교하면 안 된다. 모터가 멈출 때 전압이 원래대로
        회복되는 것만으로 "오르는 중"이 되어 버리고 (모터 부하 강하는 0.1V
        급이다), 그러면 충전 중으로 오인해 저전압 경보가 영영 안 나간다.
        그래서 최근 표본의 최솟값이 예전 표본의 최댓값보다 높을 것을 요구한다.
        회복은 기준선으로 돌아올 뿐 기준선을 넘지 못하므로 걸러진다.
        """
        if len(self.history) < self.history.maxlen:
            return False
        n = self.history.maxlen // 2
        if n == 0:
            return False
        samples = list(self.history)
        return (min(samples[-n:]) - max(samples[:n])) >= self.trend_rise_v

    @property
    def low_count(self):
        """최근 창 안에서 기준치 이하였던 표본 수."""
        return sum(self.low_window)

    def filtered(self):
        """중앙값은 가장 최근 median_samples 개로만 낸다.

        덱은 위험 판정 때문에 그보다 클 수 있다. 덱 전체로 중앙값을 내면
        median_samples=1 이 두 값의 평균이 되어 '필터 끔'이 꺼지지 않는다.
        """
        return statistics.median(list(self.raw)[-self.median_samples:])

    def critical_now(self):
        """원본 전압이 위험선까지 내려갔나.

        중앙값이 아니라 원본을 본다. 중앙값은 최저값을 버리기 때문에 위험한
        강하를 그대로 감춘다. 다만 ADC 단발 오류로 복귀까지 시키면 곤란하므로
        창 안에서 critical_count 회 이상일 때만 인정한다.
        """
        if not self.saw_voltage or not self.raw:
            return False
        return (sum(1 for v in self.raw if v <= self.critical_v)
                >= self.critical_count)

    def on_voltage(self, msg: Float32):
        """1차 소스. 중앙값을 거른 뒤 그대로 판단한다."""
        self.saw_voltage = True
        self.voltage_raw = msg.data
        self.raw.append(msg.data)
        self.voltage = self.filtered()
        self.vmin_pub.publish(Float32(data=float(min(self.raw))))
        self.evaluate(self.voltage)

    def on_percent(self, msg: Float32):
        """전압 토픽이 없을 때만 쓰는 예비 경로.

        퍼센트를 전압으로 되돌려 같은 창에 넣는다. 클램프 때문에 잃은 정보는
        돌아오지 않지만, 한 창에 퍼센트와 전압이 섞이면 중앙값도 최저값도
        전부 무의미해진다. 판정 파이프라인의 단위는 하나여야 한다.
        """
        if self.saw_voltage:
            return
        self.raw.append(voltage_from_percent(msg.data))
        self.voltage = self.filtered()
        self.evaluate(self.voltage)

    def evaluate(self, volt: float):
        self.last_voltage = volt
        self.history.append(volt)
        charging = self.is_charging()

        # 전압이 주(主)다. 퍼센트는 사람이 읽기 편하라고 괄호에 곁들일 뿐,
        # 클램프 구간에서는 0%/100% 로 뭉개져 아무 정보도 주지 않는다.
        line = f'배터리 {volt:.3f}V ({percent_from_voltage(volt):.0f}%)'
        # 최저값을 같이 찍는다. 중앙값만 보면 위험한 강하가 숨는다.
        vmin = min(self.raw) if self.raw else None
        if vmin is not None and abs(vmin - volt) >= 0.01:
            line += f' [최저 {vmin:.3f}V]'
        self.get_logger().info(line + (' (충전 중)' if charging else ''))

        # 저전압 여부를 창에 먼저 기록한다. 기준치 위여도 지우지 않고 밀어낸다.
        # 연속 카운터를 쓰면 왕복하는 배터리에서 영영 쌓이지 않는다.
        self.low_window.append(volt <= self.low_v)

        # 1) 회복은 '지속'되어야 인정한다. 충전 감지 여부와는 무관하다.
        #    (충전이 끝나 전압이 평평해지면 is_charging() 이 False 로 돌아오므로,
        #     재무장을 그 안에 두면 만충인 채로 영영 무장 해제로 남는다)
        #
        #    높은 값 하나로 풀어주면 안 된다. 방전된 배터리는 멈출 때마다
        #    기준치 위로 회복하므로, 발동과 해제를 반복하며 관제에 이벤트
        #    폭풍을 만들고 충전소 복귀도 계속 취소·재시도된다.
        #    창이 전부 기준치 위일 때만 푼다.
        window_full = len(self.low_window) == self.low_window.maxlen
        if (self.fired and volt >= self.rearm_v and window_full
                and self.low_count == 0 and not self.critical_now()):
            self.fired = False
            self.low_window.clear()
            self.publish_low_state(False)
            self.get_logger().info(
                f'{volt:.3f}V 회복 (최근 {self.confirm_window}회 모두 정상) — '
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
            vmin = min(self.raw) if self.raw else float('nan')
            self.get_logger().error(
                f'위험 전압 {vmin:.3f}V (기준 {self.critical_v:.2f}V) — 즉시 발동')
        else:
            # 6) 창 안에 충분히 쌓이지 않았으면 기다린다.
            if self.low_count < self.confirm:
                if volt <= self.low_v:
                    self.get_logger().warn(
                        f'낮음 {self.low_count}/{self.confirm} '
                        f'(최근 {len(self.low_window)}회 중) — {volt:.3f}V')
                return

            # 7) 충전 중인지 판단할 표본이 아직 부족하면 기다린다.
            if len(self.history) < self.history.maxlen:
                self.get_logger().warn(
                    f'표본 부족 {len(self.history)}/{self.history.maxlen} — '
                    '충전 여부 판단 후 결정합니다.')
                return

            self.get_logger().warn(
                f'배터리 {volt:.3f}V (기준 {self.low_v:.2f}V) — 기준 도달 '
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
