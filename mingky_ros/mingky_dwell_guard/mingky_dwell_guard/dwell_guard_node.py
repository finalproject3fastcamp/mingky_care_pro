"""환자를 놓친 채 너무 오래 서 있으면 안내를 접고 충전소로 보낸다.

## 왜 별도 노드인가

로봇은 환자를 놓치면 그 자리에서 **무기한** 기다린다(`mingky_person_follow`
가 `waiting` 을 내고 `mingky_guide_manager` 가 주행을 멈춘다). 자리를 지키는
것 자체는 옳다 -- 옮기면 돌아온 환자가 로봇을 못 찾는다. 다만 아무도 안 오는
경우가 있고, 그러면 로봇이 복도를 계속 막고 서 있는다.

그 마지막 한 걸음만 여기서 더한다. **기존 노드는 한 줄도 고치지 않는다.**

## 어떻게

이미 열려 있는 문을 두드린다. 의료진이 관제에서 안내를 취소할 때 쓰는
`~/cancel_session` 토픽에 같은 요청을 보낼 뿐이다. 취소 뒤 충전소로 가는
것은 `mingky_guide_manager` 가 원래 하던 일이다.

    /person_follow/state 가 waiting 으로 N초 유지
        -> /guide_manager/cancel_session 에 {"reason":"aborted", ...}
        -> guide_manager 가 세션을 끝내고 충전소로 복귀

## 알아 둘 것

취소는 **세션을 중단시킨다.** 그 세션은 `session.ended{end_reason:aborted}`
로 기록되고 안내 완주율에 실패로 잡힌다. 로봇이 포기하고 돌아간 것은 실제로
완주 실패이므로 그게 맞다고 보지만, 지표가 움직이는 일이라 팀이 알아야 한다.

사유를 따로 만들 수는 없다. 받는 쪽이 `aborted`/`robot_offline`/
`system_failure` 만 받고 나머지는 버린다.

## 끄고 켜기

`enabled` 를 false 로 두면 아무것도 하지 않는다. 노드를 아예 안 띄워도
기존 동작은 그대로다 -- 무기한 기다리던 예전 모습으로 돌아갈 뿐이다.
"""

import json

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from mingky_interfaces.msg import GuideState

from .dwell_policy import WAITING, DwellPolicy, DwellTimer


class DwellGuardNode(Node):

    def __init__(self) -> None:
        super().__init__('dwell_guard')

        self.declare_parameter('enabled', True)
        self.declare_parameter('timeout_sec', 180.0)
        # 남은 시간을 로그로 알려 주는 간격. 0 이면 안 알린다.
        self.declare_parameter('notice_every_sec', 30.0)

        self._enabled = bool(self.get_parameter('enabled').value)
        timeout = float(self.get_parameter('timeout_sec').value)
        self._notice_every = float(self.get_parameter('notice_every_sec').value)

        self._timer_state = DwellTimer(DwellPolicy(timeout_sec=timeout))
        self._follow_state: str | None = None
        self._session_id = 0
        self._session_state = GuideState.SESSION_NONE
        self._last_notice = 0.0

        # 상태 토픽은 늦게 붙는 구독자도 마지막 값을 받아야 한다.
        state_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.create_subscription(
            String, '/person_follow/state', self._on_follow_state, 10)
        self.create_subscription(
            GuideState, '/guide_manager/state', self._on_guide_state, state_qos)
        self._cancel_pub = self.create_publisher(
            String, '/guide_manager/cancel_session', 10)

        # 2 Hz. 초 단위 판정이라 이보다 자주 볼 이유가 없다.
        self.create_timer(0.5, self._tick)

        if self._enabled:
            self.get_logger().info(
                f'환자를 놓친 채 {timeout:.0f}초가 지나면 안내를 접고 충전소로 보냅니다.')
        else:
            self.get_logger().warn('꺼져 있습니다. 시간이 지나도 아무것도 하지 않습니다.')

    # ---------------------------------------------------------------- 입력
    def _on_follow_state(self, msg: String) -> None:
        self._follow_state = (msg.data or '').strip()

    def _on_guide_state(self, msg: GuideState) -> None:
        self._session_id = int(msg.session_id)
        self._session_state = msg.session_state

    # ---------------------------------------------------------------- 판정
    def _guiding(self) -> bool:
        """안내 중일 때만 개입한다.

        세션이 없거나 이미 끝났으면 기다릴 환자도 없다. 그때 취소를 보내면
        받는 쪽이 무시하지만, 애초에 보내지 않는 편이 로그가 깨끗하다.
        """
        return self._session_id > 0 and self._session_state == GuideState.SESSION_GUIDING

    def _now(self) -> float:
        """지금 시각(초).

        따로 뺀 이유는 시험 때문이다. 3분을 기다리는 동작을 실제로 3분
        기다려 확인할 수는 없으므로, 시험에서는 이 함수만 갈아끼운다.
        """
        return self.get_clock().now().nanoseconds / 1e9

    def _tick(self) -> None:
        now = self._now()

        if not self._enabled or not self._guiding():
            # 안내가 아니면 시계를 지운다. 다음 세션에서 이전 대기가 이어지면 안 된다.
            self._timer_state.update(None, now)
            return

        expired = self._timer_state.update(self._follow_state, now)

        if self._follow_state == WAITING and self._notice_every > 0 and not expired:
            left = self._timer_state.remaining(now)
            if left > 0 and now - self._last_notice >= self._notice_every:
                self._last_notice = now
                self.get_logger().info(f'환자를 기다리는 중 -- {left:.0f}초 뒤 복귀합니다.')

        if not expired:
            return

        self._last_notice = 0.0
        self.get_logger().warn(
            f'환자를 {self._timer_state.elapsed(now):.0f}초 동안 기다렸습니다. '
            f'안내 세션 {self._session_id} 을 접고 충전소로 복귀합니다.')
        self._cancel_pub.publish(String(data=json.dumps(
            {'reason': 'aborted', 'session_id': self._session_id},
            ensure_ascii=False,
        )))


def main(args=None) -> None:
    rclpy.init(args=args)
    node = DwellGuardNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except ExternalShutdownException:
        # systemd 는 SIGTERM 으로 멈춘다. 이걸 안 잡으면 서비스를 정지할
        # 때마다 로그에 예외 흔적이 남아, 진짜 고장과 구분이 안 된다.
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
