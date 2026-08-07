#!/usr/bin/env python3
"""비상정지. 세워놓고 관제가 풀어줄 때까지 유지한다.

두 가지 입구가 있고 멈추는 방식이 다르다.

    /emergency_stop          (Bool=true)  → 부드러운 감속 (관제 빨간 버튼)
    /emergency_stop/obstacle (Bool=true)  → 급정지       (장애물 감지)

사람이 누른 정지는 서서히 세우는 편이 안전하다. 급정지는 짐이 쏠리거나
로봇이 기울 수 있기 때문이다. 반면 앞에 장애물이 있으면 감속할 여유가
없으므로 즉시 0 을 때린다.

공통 동작:
    → Nav2 목표 취소
    → cmd_vel 을 0 으로 (감속 방식은 위 참조)
    → 0 을 계속 발행해 아무도 못 움직이게 함
    → LED 빨강 깜빡임
    → 상태를 관제로 발행

    /emergency_stop/release (Trigger) 호출 → 해제

관제로는 팀 표준대로 /events 에 robot.paused 로 나간다 (payload: reason).
해제는 별도 코드가 event_codes.yaml 에 없어서 이벤트를 내지 않고,
emergency_stop/state (Bool, latched) 로만 알린다. 관제 UI 는 이 토픽을
구독하면 현재 정지 여부를 즉시 알 수 있다.

해제는 명시적 호출로만 된다. 조건이 사라졌다고 저절로 풀리지 않는다.
원인을 확인하기 전에 로봇이 다시 움직이면 위험하기 때문이다.

왜 0 을 계속 보내야 하는가
    pinky_bringup 의 twist_callback 은 메시지를 받는 즉시 모터 RPM 을 세팅하고
    워치독이 없다. 즉 "명령을 끊는 것"으로는 안 멈추고 마지막 속도로 계속 간다.
    게다가 Nav2 의 velocity_smoother 가 /cmd_vel 로 20Hz 로 쏘고 있으므로,
    목표를 취소해 발행자를 없애고 그 위에 0 을 덮어써야 확실히 선다.
"""

from action_msgs.srv import CancelGoal
from geometry_msgs.msg import Twist
from mingky_guide_manager.event_publisher import EventPublisher
from pinky_interfaces.srv import SetLed
import rclpy
from rclpy.node import Node
from rclpy.qos import (QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile,
                       QoSReliabilityPolicy)
from std_msgs.msg import Bool
from std_srvs.srv import Trigger


class EmergencyStop(Node):

    def __init__(self):
        super().__init__('emergency_stop')

        self.declare_parameter('decel_time', 1.0)      # 0 까지 줄이는 데 걸리는 초
        self.declare_parameter('publish_rate', 50.0)   # Nav2(20Hz) 보다 빨라야 한다
        self.declare_parameter('blink_period', 0.5)    # LED 깜빡임 주기
        self.declare_parameter('use_led', True)
        self.declare_parameter('cancel_nav2', True)
        self.declare_parameter('robot_id', 'pinky-01')      # Event.robot_id
        self.declare_parameter('event_codes_file', '')      # 비우면 자동 탐색

        g = self.get_parameter
        self.decel_time = float(g('decel_time').value)
        self.rate = float(g('publish_rate').value)
        self.use_led = g('use_led').value
        self.cancel_nav2 = g('cancel_nav2').value

        # ---- 상태 ----
        self.engaged = False
        self.reason = None          # 'operator' 또는 'obstacle'
        self.last_cmd = Twist()     # 정지 직전 속도. 여기서부터 줄인다.
        self.ramp_step = 0          # 감속 진행 횟수
        self.ramp_total = 1         # engage 시점에 정해진다 (급정지면 1)
        self.blink_on = False

        # ---- 통신 ----
        latched = QoSProfile(
            depth=1,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        # 관제 UI 가 바로 묶어 쓰기 좋은 단순 상태
        self.state_pub = self.create_publisher(Bool, 'emergency_stop/state', latched)

        # 이벤트는 팀 표준 경로(/events → 게이트웨이 → 관제)로 나간다.
        self.events = EventPublisher(
            self,
            self.get_parameter('robot_id').value,
            self.get_parameter('event_codes_file').value)

        self.cmd_pub = self.create_publisher(Twist, 'cmd_vel', 10)
        self.create_subscription(Twist, 'cmd_vel', self.on_cmd, 10)
        # 사람이 누른 정지 — 서서히 세운다
        self.create_subscription(Bool, 'emergency_stop', self.on_operator, 10)
        # 장애물 감지 — 감속할 여유가 없으므로 즉시 0
        self.create_subscription(Bool, 'emergency_stop/obstacle', self.on_obstacle, 10)

        self.create_service(Trigger, 'emergency_stop/release', self.on_release)

        self.cancel_client = self.create_client(
            CancelGoal, 'navigate_to_pose/_action/cancel_goal') if self.cancel_nav2 else None
        self.led_client = self.create_client(SetLed, 'set_led') if self.use_led else None

        self.stop_timer = None
        self.blink_timer = None

        self.publish_state()
        self.get_logger().info(
            f'비상정지 대기 중 | 감속 {self.decel_time}초 | 발행 {self.rate}Hz | '
            '해제는 emergency_stop/release 서비스로만')

    # ===================================================================

    def on_cmd(self, msg: Twist):
        """정지 중이 아닐 때만 기록한다. 정지 중엔 우리가 쏜 0 이 되돌아오므로."""
        if not self.engaged:
            self.last_cmd = msg

    def on_operator(self, msg: Bool):
        # false 로는 풀지 않는다. 해제는 서비스로만.
        if msg.data and not self.engaged:
            self.engage('operator', hard=False)

    def on_obstacle(self, msg: Bool):
        if not msg.data:
            return
        if self.engaged and self.reason == 'obstacle':
            return
        if self.engaged:
            # 이미 부드럽게 감속하던 중에 장애물이 나타났다면 즉시 0 으로 전환한다.
            self.get_logger().warn('감속 중 장애물 감지 — 급정지로 전환')
            self.reason = 'obstacle'
            self.ramp_total = 1
            self.ramp_step = 1
            self.publish_state()
            return
        self.engage('obstacle', hard=True)

    def on_release(self, request, response):
        if not self.engaged:
            response.success = False
            response.message = '비상정지 상태가 아닙니다.'
            return response
        self.release()
        response.success = True
        response.message = '비상정지를 해제했습니다.'
        return response

    # ===================================================================

    def engage(self, reason, hard):
        self.engaged = True
        self.reason = reason
        self.ramp_step = 0
        # 급정지면 한 번에 0. 아니면 decel_time 동안 나눠서 줄인다.
        self.ramp_total = 1 if hard else max(1, int(self.decel_time * self.rate))

        self.get_logger().warn(
            f'*** 비상정지 ({reason}) *** {"급정지" if hard else f"{self.decel_time}초 감속"} | '
            f'직전 속도 linear={self.last_cmd.linear.x:.2f} '
            f'angular={self.last_cmd.angular.z:.2f}')

        # 1) 명령을 내리는 쪽을 먼저 끊는다. 이게 없으면 0 을 쏴도 서로 싸운다.
        self.cancel_nav2_goals()

        # 2) 마지막 속도에서 0 까지 서서히. 이후에도 0 을 계속 발행한다.
        self.stop_timer = self.create_timer(1.0 / self.rate, self.tick_stop)

        # 3) LED 빨강 깜빡임
        if self.led_client is not None:
            self.blink_timer = self.create_timer(
                float(self.get_parameter('blink_period').value), self.tick_blink)

        self.publish_state()

    def release(self):
        self.engaged = False
        self.reason = None

        # cancel() 만 하면 타이머가 executor 에 그대로 남는다. engage() 는 매번
        # create_timer() 로 새로 만들므로, 정지/해제를 반복하면 죽은 타이머가
        # 계속 쌓인다. 반드시 destroy_timer() 로 떼어낸다.
        for timer in (self.stop_timer, self.blink_timer):
            if timer is not None:
                timer.cancel()
                self.destroy_timer(timer)
        self.stop_timer = None
        self.blink_timer = None

        self.set_led('clear')
        self.last_cmd = Twist()     # 해제 직후 옛 속도로 튀지 않도록 초기화
        self.get_logger().info('비상정지 해제됨')
        self.publish_state()

    # ===================================================================

    def tick_stop(self):
        """마지막 속도 → 0 으로 선형 감속. 다 줄인 뒤에도 0 을 계속 발행한다."""
        cmd = Twist()
        if self.ramp_step < self.ramp_total:
            self.ramp_step += 1
            scale = 1.0 - (self.ramp_step / self.ramp_total)
            cmd.linear.x = self.last_cmd.linear.x * scale
            cmd.angular.z = self.last_cmd.angular.z * scale
        # ramp 가 끝나면 cmd 는 0 인 채로 계속 나간다
        self.cmd_pub.publish(cmd)

    def tick_blink(self):
        self.blink_on = not self.blink_on
        if self.blink_on:
            self.set_led('fill', 255, 0, 0)
        else:
            self.set_led('clear')

    def set_led(self, command, r=0, g=0, b=0):
        if self.led_client is None or not self.led_client.service_is_ready():
            return
        req = SetLed.Request()
        req.command = command
        req.r, req.g, req.b = int(r), int(g), int(b)
        self.led_client.call_async(req)

    def cancel_nav2_goals(self):
        if self.cancel_client is None:
            return
        if not self.cancel_client.service_is_ready():
            self.get_logger().warn('Nav2 취소 서비스 없음 — cmd_vel 만으로 정지합니다.')
            return
        # 빈 goal_info → 출처와 무관하게 모든 활성 goal 취소
        self.cancel_client.call_async(CancelGoal.Request())
        self.get_logger().info('Nav2 목표를 취소했습니다.')

    def publish_state(self):
        """상태 토픽은 항상 갱신한다. 이벤트는 걸릴 때만 낸다.

        해제까지 이벤트로 내면 robot.paused 가 쌍 없이 쌓여 타임라인이
        읽기 나빠진다. 해제 여부는 state 토픽(latched)이 알려준다.
        """
        self.state_pub.publish(Bool(data=self.engaged))
        if self.engaged:
            self.events.publish('robot.paused', {'reason': self.reason})


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
