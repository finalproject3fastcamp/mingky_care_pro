#!/usr/bin/env python3
"""모터 명령의 단일 출구이자 로컬 비상정지 안전 게이트.

Nav2/teleop 은 ``cmd_vel_safety_input`` 으로 명령하고, 이 노드만 실제 모터가
구독하는 ``cmd_vel`` 을 발행한다. 정지 중에는 입력을 버리고 0 속도를 계속
발행한다. 입력 자체가 끊겨도 command_timeout 뒤 0을 발행한다.

비상정지 상태는 파일에 남겨 프로세스가 재시작되어도 명시적 release 전에는
풀리지 않는다. GuideManager 는 latched 상태 토픽을 받아 관제 이벤트와 시스템
상태를 갱신한다.
"""

from pathlib import Path
import subprocess
import threading

from action_msgs.srv import CancelGoal
from geometry_msgs.msg import Twist
from pinky_interfaces.srv import SetLed
import rclpy
from rclpy.node import Node
from rclpy.qos import (QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile,
                       QoSReliabilityPolicy)
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger


class EmergencyStop(Node):

    def __init__(self, **kwargs):
        super().__init__('emergency_stop', **kwargs)

        self.declare_parameter('publish_rate', 50.0)
        self.declare_parameter('command_timeout', 0.5)
        self.declare_parameter('blink_period', 0.5)
        self.declare_parameter('use_led', True)
        self.declare_parameter('use_buzzer', True)
        self.declare_parameter(
            'fire_buzzer_script', '/home/pinky/ap/battery_buzzer.py')
        self.declare_parameter('fire_buzzer_level', 'danger')
        self.declare_parameter('fire_buzzer_repeat_seconds', 5.0)
        self.declare_parameter('cancel_nav2', True)
        self.declare_parameter('input_topic', 'cmd_vel_safety_input')
        self.declare_parameter('output_topic', 'cmd_vel')
        self.declare_parameter(
            'state_file', '~/.mingky/emergency_stop.state')

        get = self.get_parameter
        self.rate = float(get('publish_rate').value)
        self.command_timeout = float(get('command_timeout').value)
        self.use_led = bool(get('use_led').value)
        self.use_buzzer = bool(get('use_buzzer').value)
        self.fire_buzzer_script = str(get('fire_buzzer_script').value)
        self.fire_buzzer_level = str(get('fire_buzzer_level').value)
        self.fire_buzzer_repeat_seconds = max(
            1.0, float(get('fire_buzzer_repeat_seconds').value))
        self.cancel_nav2 = bool(get('cancel_nav2').value)
        self.state_file = Path(get('state_file').value).expanduser()

        self.reason = self._load_reason()
        self.engaged = self.reason is not None
        self.last_input_at = None
        self.blink_on = False
        self.blink_timer = None
        self.fire_alarm_active = None
        self.fire_buzzer_timer = None
        self._fire_buzzer_running = False

        latched = QoSProfile(
            depth=1,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.state_pub = self.create_publisher(Bool, 'emergency_stop/state', latched)
        self.reason_pub = self.create_publisher(
            String, 'emergency_stop/reason', latched)

        self.cmd_pub = self.create_publisher(Twist, get('output_topic').value, 10)
        self.create_subscription(
            Twist, get('input_topic').value, self.on_cmd, 10)
        self.create_subscription(Bool, 'emergency_stop', self.on_operator, 10)
        self.create_subscription(
            Bool, 'emergency_stop/obstacle', self.on_obstacle, 10)
        self.create_subscription(
            Bool, 'emergency_stop/communication', self.on_communication, 10)
        self.create_subscription(
            Bool, '/fire_evac/alarm_active', self.on_fire_alarm, latched)
        self.create_service(Trigger, 'emergency_stop/release', self.on_release)

        self.cancel_client = self.create_client(
            CancelGoal, 'navigate_to_pose/_action/cancel_goal') \
            if self.cancel_nav2 else None
        self.led_client = (
            self.create_client(SetLed, 'set_led') if self.use_led else None)

        # 항상 살아 있는 안전 타이머다. 정지 상태뿐 아니라 입력 두절도 감시한다.
        self.safety_timer = self.create_timer(1.0 / self.rate, self.tick_safety)
        if self.engaged:
            self._start_blink()
            self.get_logger().warn(
                f'저장된 비상정지 상태 복원 ({self.reason}) — '
                '명시적 해제가 필요합니다.')
        self.publish_state()
        self.get_logger().info(
            f'안전 게이트 시작: {get("input_topic").value} -> '
            f'{get("output_topic").value}, timeout={self.command_timeout:.2f}s')

    def _load_reason(self):
        try:
            if self.state_file.is_file():
                saved = self.state_file.read_text(encoding='utf-8').strip()
                return saved or 'recovered'
        except OSError as exc:
            self.get_logger().error(f'비상정지 상태 파일 읽기 실패: {exc}')
            # 상태를 확인할 수 없을 때는 안전하게 정지한다.
            return 'state_file_error'
        return None

    def _persist_reason(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(self.reason or 'unknown', encoding='utf-8')
        except OSError as exc:
            self.get_logger().error(f'비상정지 상태 저장 실패: {exc}')

    def on_cmd(self, msg: Twist):
        self.last_input_at = self.get_clock().now()
        if not self.engaged:
            self.publish_cmd(msg)

    def on_operator(self, msg: Bool):
        if msg.data:
            self.engage('operator')

    def on_obstacle(self, msg: Bool):
        if msg.data:
            self.engage('obstacle')

    def on_communication(self, msg: Bool):
        if msg.data:
            self.engage('communication_loss')

    def on_fire_alarm(self, msg: Bool):
        active = bool(msg.data)
        if active == self.fire_alarm_active:
            return
        self.fire_alarm_active = active
        if active:
            self._stop_blink()
            self.set_led('fill', 255, 0, 0)
            self._start_fire_buzzer()
            self.get_logger().error('화재 경보 표시 시작 — 빨간 LED / 부저')
            return
        self._stop_fire_buzzer()
        if self.engaged:
            self._start_blink()
        else:
            self.set_led('clear')
        self.get_logger().info('화재 경보 표시 해제')

    def on_release(self, request, response):
        if not self.engaged:
            response.success = False
            response.message = '비상정지 상태가 아닙니다.'
            return response
        if not self.release():
            response.success = False
            response.message = '상태 파일을 지우지 못해 안전상 정지를 유지합니다.'
            return response
        response.success = True
        response.message = (
            '비상정지를 해제했습니다. 주행 목표는 다시 내려야 합니다.')
        return response

    def engage(self, reason: str):
        if self.engaged and self.reason == reason:
            return
        self.engaged = True
        self.reason = reason
        self._persist_reason()
        self.publish_cmd(Twist())
        self.cancel_nav2_goals()
        self._start_blink()
        self.publish_state()
        self.get_logger().error(f'*** 비상정지 ({reason}) ***')

    def release(self):
        previous_reason = self.reason
        try:
            self.state_file.unlink(missing_ok=True)
        except OSError as exc:
            self.get_logger().error(f'비상정지 상태 파일 삭제 실패: {exc}')
            # 파일 상태와 메모리 상태가 갈라지지 않도록 정지를 유지한다.
            return False
        self.engaged = False
        self.reason = None
        self._stop_blink()
        if self.fire_alarm_active:
            self.set_led('fill', 255, 0, 0)
        else:
            self.set_led('clear')
        self.publish_cmd(Twist())
        self.publish_state(previous_reason or 'manual_release')
        self.get_logger().info('비상정지 해제 — 새 속도 명령을 기다립니다.')
        return True

    def tick_safety(self):
        if self.engaged or self.last_input_at is None:
            self.publish_cmd(Twist())
            return
        age = (self.get_clock().now() - self.last_input_at).nanoseconds / 1e9
        if age >= self.command_timeout:
            self.publish_cmd(Twist())

    def publish_cmd(self, msg: Twist):
        self.cmd_pub.publish(msg)

    def _start_blink(self):
        if self.fire_alarm_active:
            self.set_led('fill', 255, 0, 0)
            return
        if self.led_client is not None and self.blink_timer is None:
            self.blink_timer = self.create_timer(
                float(self.get_parameter('blink_period').value), self.tick_blink)

    def _stop_blink(self):
        if self.blink_timer is not None:
            self.blink_timer.cancel()
            self.destroy_timer(self.blink_timer)
            self.blink_timer = None

    def tick_blink(self):
        self.blink_on = not self.blink_on
        self.set_led('fill', 255, 0, 0) if self.blink_on else self.set_led('clear')

    def _start_fire_buzzer(self):
        if not self.use_buzzer:
            return
        self._beep_fire_alarm()
        if self.fire_buzzer_timer is None:
            self.fire_buzzer_timer = self.create_timer(
                self.fire_buzzer_repeat_seconds, self._beep_fire_alarm)

    def _stop_fire_buzzer(self):
        if self.fire_buzzer_timer is not None:
            self.fire_buzzer_timer.cancel()
            self.destroy_timer(self.fire_buzzer_timer)
            self.fire_buzzer_timer = None

    def _beep_fire_alarm(self):
        if (not self.fire_alarm_active or not self.use_buzzer
                or self._fire_buzzer_running):
            return
        self._fire_buzzer_running = True

        def run():
            try:
                result = subprocess.call(
                    [
                        'python3', self.fire_buzzer_script, 'beep',
                        self.fire_buzzer_level,
                    ],
                    timeout=15,
                )
                if result not in (0, 3):
                    self.get_logger().warn(
                        f'화재 부저 실패 (종료코드 {result})')
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                self.get_logger().warn(f'화재 부저 실행 실패: {exc}')
            finally:
                self._fire_buzzer_running = False

        threading.Thread(target=run, daemon=True).start()

    def set_led(self, command, red=0, green=0, blue=0):
        if self.led_client is None or not self.led_client.service_is_ready():
            return
        request = SetLed.Request()
        request.command = command
        request.r, request.g, request.b = int(red), int(green), int(blue)
        self.led_client.call_async(request)

    def cancel_nav2_goals(self):
        if self.cancel_client is None:
            return
        if not self.cancel_client.service_is_ready():
            self.get_logger().warn(
                'Nav2 취소 서비스 없음 — 안전 게이트로 정지합니다.')
            return
        self.cancel_client.call_async(CancelGoal.Request())

    def publish_state(self, release_reason=''):
        reason = self.reason if self.engaged else release_reason
        self.reason_pub.publish(String(data=reason or ''))
        self.state_pub.publish(Bool(data=self.engaged))

    def destroy_node(self):
        self._stop_fire_buzzer()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = EmergencyStop()
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
