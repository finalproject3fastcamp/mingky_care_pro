#!/usr/bin/env python3
"""배터리가 낮으면 부저를 울리고 Nav2로 충전소에 복귀시킨다.

    battery/voltage 구독 → 기준치 이하 → 부저 3번 → 충전소로 이동
    (battery/percent 는 전압을 한 번도 못 받았을 때 쓰는 예비 경로)

경보는 팀 표준대로 /events 에 mingky_interfaces/msg/Event 로 나간다.
event_code 는 config/event_codes.yaml 에 등록된 것만 쓴다.
    robot.battery_low   기준치 도달
    nav.goal_aborted    충전소 복귀 실패 (visit_name='charging_dock')

guide_manager 도 robot.battery_low 를 발행하는 코드를 갖고 있으나, 그쪽은
/batt_state(pinky_sensor_adc) 를 구독하는데 그 노드를 띄우는 launch 가 없어
현재는 동작하지 않는다. 임계값도 6.9V(방전 직전 안전선)로 이 노드의
40%(7.12V, 여유 있게 복귀시키는 운영선)와 계층이 다르다.

충전 중이면 울리지 않는다. 이미 충전되고 있는데 경보를 내거나,
충전소에 있는 로봇을 충전소로 또 보내는 것을 막기 위함이다.

퍼센트 ↔ 전압 (pinkylib/battery.py)
    percent = (V - 6.8) / (7.6 - 6.8) * 100
    1% = 8mV 로 촘촘하다. 모터가 돌면 0.1V(=12%p)가 순식간에 빠진다.
      40% = 7.12V     100% = 7.60V
"""

from collections import deque
import math
import statistics
import subprocess
import threading

from action_msgs.msg import GoalStatus
from mingky_guide_manager.event_publisher import EventPublisher
from nav2_msgs.action import NavigateToPose
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import Float32

BUSY_EXIT = 3   # battery_buzzer.py 가 "부저 사용 중"일 때 주는 종료코드

# nav.goal_aborted 의 visit_name. 안내 목적지들과 구분되는 이름이어야 한다.
DOCK_VISIT_NAME = 'charging_dock'

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

        self.declare_parameter('dock_x', 0.0)               # 충전소 좌표
        self.declare_parameter('dock_y', 0.0)               # (맵마다 다름. 꼭 바꿀 것)
        self.declare_parameter('dock_yaw', 0.0)
        self.declare_parameter('dock_frame', 'map')

        self.declare_parameter('robot_id', 'pinky-01')      # Event.robot_id
        self.declare_parameter('event_codes_file', '')      # 비우면 자동 탐색

        self.declare_parameter('use_nav2', True)
        self.declare_parameter('use_buzzer', True)
        self.declare_parameter('buzzer_script', '/home/pinky/ap/battery_buzzer.py')
        self.declare_parameter('buzzer_level', 'danger')    # danger=784Hz 3번, warn=550Hz 2번

        g = self.get_parameter
        self.threshold = g('threshold_percent').value
        self.rearm = g('rearm_percent').value
        self.confirm = int(g('confirm_count').value)
        self.trend_rise = g('trend_rise').value
        self.use_nav2 = g('use_nav2').value
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

        # ---- 통신 ----
        # 이벤트는 게이트웨이가 받아 관제 서버로 넘긴다. 큐·재시도는 그쪽 몫이라
        # 여기서는 표준 헬퍼로 발행만 한다. 미등록 코드는 헬퍼가 막아준다.
        self.events = EventPublisher(
            self,
            self.get_parameter('robot_id').value,
            self.get_parameter('event_codes_file').value)

        # 전압이 1차 소스다. 퍼센트는 전압을 선형 변환한 파생값이라
        # 6.8V 아래·7.6V 위가 전부 0%/100% 로 뭉개진다.
        # 그리고 퍼센트를 내는 쪽이 죽어도 감시는 계속되어야 한다.
        self.voltage = None           # 필터를 거친 값. 판단은 이걸로 한다.
        self.voltage_raw = None       # 방금 받은 원본. 기록용.
        self.saw_voltage = False
        self.create_subscription(Float32, 'battery/voltage', self.on_voltage, 10)
        self.create_subscription(Float32, 'battery/percent', self.on_percent, 10)

        self.nav = ActionClient(self, NavigateToPose, 'navigate_to_pose') \
            if self.use_nav2 else None

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

        # 퍼센트와 전압을 함께 실어 보낸다. 전압이 원본이고 퍼센트는 파생값이라,
        # 나중에 변환식을 실측으로 보정하면 전압으로 다시 계산할 수 있다.
        # (DB 의 robot_battery_log 는 두 컬럼을 모두 갖고 있고, 전압 컬럼은
        #  값이 없으면 NULL 을 허용한다.)
        # payload 형태는 event_codes.yaml 이 정본이고 percent 는 int 다.
        # 전압을 같이 싣고 싶으면 yaml 부터 고쳐야 한다.
        self.events.publish('robot.battery_low', {'percent': int(round(pct))})

        self.beep()          # Nav2가 없어도 경고는 나가야 하므로 부저가 먼저
        self.go_to_dock()

    # ===================================================================

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

    def dock_failed(self, why, error_code=0):
        """복귀 실패는 반드시 관제로 올린다.

        배터리가 낮은데 스스로 충전소로 못 가는 상황이라, 로그만 남기고 끝내면
        아무도 모르는 채로 로봇이 그 자리에서 방전된다.

        전용 코드가 event_codes.yaml 에 없어서 nav.goal_aborted 를 쓴다.
        실제로 복귀 목표가 중단된 것이라 의미도 맞는다. 충전소로 간 것임은
        visit_name 으로 구분한다. (전용 코드가 필요하면 yaml 부터 고칠 것)
        """
        self.get_logger().error(f'충전소 복귀 실패 — {why}')
        self.events.publish('nav.goal_aborted', {
            'visit_name': DOCK_VISIT_NAME,
            'error_code': int(error_code),
        })

    def go_to_dock(self):
        if self.nav is None:
            self.get_logger().info('use_nav2=false — 복귀 명령 생략')
            return
        if not self.nav.wait_for_server(timeout_sec=5.0):
            self.dock_failed('navigate_to_pose 액션 서버 없음 (Nav2 미실행?)')
            return

        x = float(self.get_parameter('dock_x').value)
        y = float(self.get_parameter('dock_y').value)
        yaw = float(self.get_parameter('dock_yaw').value)

        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = self.get_parameter('dock_frame').value
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        goal.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal.pose.pose.orientation.w = math.cos(yaw / 2.0)

        self.get_logger().info(f'충전소로 복귀: x={x:.2f}, y={y:.2f}')
        self.events.publish('nav.goal_sent', {'visit_name': DOCK_VISIT_NAME})
        self.nav.send_goal_async(goal).add_done_callback(self.on_goal)

    def on_goal(self, future):
        try:
            handle = future.result()
        except Exception as e:                       # noqa: BLE001 - 원인 무관하게 보고
            self.dock_failed(f'목표 전송 중 예외: {e}')
            return
        if not handle.accepted:
            self.dock_failed('Nav2가 복귀 목표를 거부함')
            return
        self.get_logger().info('Nav2가 복귀 목표를 수락했습니다.')
        handle.get_result_async().add_done_callback(self.on_result)

    def on_result(self, future):
        """수락됐다고 도착한 것은 아니다. 끝까지 확인해야 한다."""
        try:
            status = future.result().status
        except Exception as e:                       # noqa: BLE001
            self.dock_failed(f'결과 수신 중 예외: {e}')
            return
        if status == GoalStatus.STATUS_SUCCEEDED:
            self.get_logger().info('충전소 도착.')
            self.events.publish('nav.goal_succeeded',
                                {'visit_name': DOCK_VISIT_NAME})
        else:
            self.dock_failed(f'복귀 중단 (status={status})', error_code=status)


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
