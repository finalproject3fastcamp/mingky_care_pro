"""후방 카메라로 안내 대상(손님) 유무를 보고 주행 속도를 조절한다.

핑키는 이미 정해진 경로로 안내 주행 중이다(Nav2, `guide_manager`/
`navigation_manager`가 목표를 보낸다). 이 노드는 그 경로나 조향에는 전혀
관여하지 않는다 -- 후방 카메라에 손님이 보이면 정상 속도로 계속 가게 두고,
손님이 안 보이면 `/speed_limit`을 낮춰서 로봇이 제자리에서 기다리게 할
뿐이다. "어디로 갈지"는 이미 있는 시스템의 몫이고, 이 노드는 "지금 가도
되는지"만 결정한다.

## 왜 조향이 아니라 속도만 건드리는가

손님을 카메라로 보고 로봇이 직접 방향을 트는 것도 생각해봤지만, 병원
안내로봇은 이미 검증된 경로(Nav2 웨이포인트)를 타야 한다 -- 손님 위치를
따라 로봇이 스스로 진로를 바꾸면 오히려 정해진 동선을 벗어나 위험하다.
그래서 손님이 "따라오고 있는지"만 확인하고, 못 따라오고 있으면 Nav2 의
`/speed_limit` (nav2_msgs/SpeedLimit, velocity_smoother 가 구독)에 낮은
값을 걸어 로봇을 제자리에 세워 기다리게 한다. Nav2 goal 자체는 안 건드리므로
손님이 다시 나타나면 원래 경로를 그대로 이어서 간다.

## YOLO 추론은 별도 HTTP 서버에 위탁 (mingky_fire_evac 과 같은 이유)

핑키에는 GPU가 없고, 이 프로젝트 와이파이는 기기 간 순수 UDP(ROS2/DDS
디스커버리가 쓰는 것)를 막아놔서 두 기기를 ROS2로 직접 못 붙인다. 그래서
`mingky_fire_evac/infer_server.py`와 같은 패턴을 그대로 재사용한다: 이
노드는 핑키 위에서 도는 평범한 ROS2 노드로 남고, 프레임(JPEG)만 HTTP로 GPU
컴퓨터의 `infer_server.py`에 보내 검출 결과(박스 좌표+클래스+신뢰도)를
받는다.

## 여러 손님을 구분해야 한다 -- 왜 위치만으로 안 되는가

인형(손님 역할)이 한 종류가 아니라 p001/p002/p003 세 클래스로 나뉘어 있다
-- 안내 도중 다른 손님이 비슷한 화면 위치로 끼어들었을 때 그 손님을 원래
안내받던 손님인 척 계속 따라가면 안 되기 때문이다. 그래서 대상 잠금은
위치만 보지 않고 **클래스가 같은 검출만** 후보로 삼는다
(`target_lock.pick_target` 참고) -- 실제로 위치만으로 잠갔을 때 다른
인형으로 잠금이 넘어가는 문제를 겪고 나서 넣은 조건이다.

## `/speed_limit` 을 0.0 으로 보내면 안 된다

nav2_msgs/SpeedLimit 은 `speed_limit=0.0`을 "제한 없음(무제한)"의 특수값으로
정의한다 (메시지 정의 주석: "When no-limit it is set to 0.0"). 그래서
"정지"를 표현하려면 0.0이 아니라 아주 작은 양수를 써야 한다 -- 실기에서
0.0을 그대로 보냈다가 오히려 무제한으로 해석돼 로봇이 손님 없이 그냥
출발해버리는 걸 직접 확인했다 (`stop_speed_percent` 파라미터 참고, 기본값
0.1).

## 정지 상태가 길어지면 Nav2 recovery(제자리 회전)가 끼어든다

Nav2 controller_server 의 progress_checker(`movement_time_allowance`)가
"이 시간 안에 최소 이 거리는 움직여야 한다"를 감시한다. 이 노드가 손님을
오래 못 찾아 속도를 계속 낮게 걸어두면, Nav2 입장에서는 "제자리에 멈춰서
못 움직이는 상태"로 보여 자체 recovery(제자리 회전 등)를 시작해버린다 --
이건 이 노드가 의도한 "기다림"과 Nav2 가 오해한 "고장"이 충돌하는
것이므로, 이 기능을 실제로 켜는 로봇에서는 `controller_server`의
`progress_checker.movement_time_allowance`를 손님이 없어질 수 있는 최대
시간보다 넉넉하게(예: 60초 이상) 잡아둬야 한다. 이 노드 자체는 그 파라미터를
건드리지 않는다 -- nav2_params.yaml 은 팀 공용 설정이라 이 패키지가
임의로 덮어쓰지 않는다.
"""

import collections
import threading
import time

from nav2_msgs.msg import SpeedLimit
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
import requests
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool

from .event_publisher import PersonFollowEventPublisher
from .follow_state import next_following_state
from .target_lock import pick_target

SPEED_LIMIT_TOPIC = '/speed_limit'
FOLLOWING_ACTIVE_TOPIC = '/person_follow/following'


class PersonFollowNode(Node):

    def __init__(self, **kwargs):
        super().__init__('person_follow_node', **kwargs)

        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter('image_topic', '/rear_camera/image_raw/compressed')
        # 이 시간(초)보다 오래된 프레임은 판단에 안 쓴다 (카메라/네트워크 끊김 방지,
        # mingky_fire_evac 과 같은 이유).
        self.declare_parameter('frame_max_age_sec', 2.0)
        # AI 컴퓨터에서 도는 추론 서버 주소. 필수값이라 기본값을 비워두고,
        # 없으면 바로 에러를 낸다 (mingky_fire_evac 과 같은 패턴).
        self.declare_parameter('infer_server_url', '')
        self.declare_parameter('infer_timeout_sec', 2.0)
        # 검증 당시(흑백 카메라, 뒤에서 본 각도) p002 인형의 실측 신뢰도가
        # 0.3 안팎까지 낮게 나온 적이 있어, fire_evac 의 기본값(0.3)보다
        # 낮춰뒀다. 오탐이 늘면 window_size/required_detections 로 걸러진다.
        self.declare_parameter('conf_threshold', 0.25)
        self.declare_parameter('window_size', 7)
        self.declare_parameter('required_detections', 5)
        # 직전 잠금 위치에서 이 픽셀 이상 벗어난 같은 클래스 검출은 다른
        # 개체로 보고 버린다 (예: 같은 p002 인형이 화면 반대편에 하나 더
        # 있는 경우).
        self.declare_parameter('max_jump_px', 200.0)
        # SpeedLimit.speed_limit=0.0 은 "무제한" 특수값이라 정지 표현에
        # 못 쓴다 (모듈 docstring 참고). 0보다 큰 값을 강제한다.
        self.declare_parameter('stop_speed_percent', 0.1)
        self.declare_parameter('follow_speed_percent', 100.0)

        get = self.get_parameter
        self.robot_id = str(get('robot_id').value)
        self.image_topic = str(get('image_topic').value)
        self.frame_max_age_sec = float(get('frame_max_age_sec').value)
        self.infer_server_url = str(get('infer_server_url').value)
        if not self.infer_server_url:
            raise RuntimeError(
                'infer_server_url 파라미터가 필요합니다 (GPU 컴퓨터의 추론 서버 주소).')
        self.infer_timeout_sec = float(get('infer_timeout_sec').value)
        self.conf_threshold = float(get('conf_threshold').value)
        self.window_size = int(get('window_size').value)
        self.required_detections = int(get('required_detections').value)
        if self.window_size <= 0:
            raise ValueError('window_size는 1 이상이어야 합니다.')
        if not 1 <= self.required_detections <= self.window_size:
            raise ValueError(
                'required_detections는 1 이상 window_size 이하여야 합니다.')
        self.max_jump_px = float(get('max_jump_px').value)
        self.stop_speed_percent = float(get('stop_speed_percent').value)
        if self.stop_speed_percent <= 0.0:
            raise ValueError(
                'stop_speed_percent는 0보다 커야 합니다 '
                '(0.0은 SpeedLimit 에서 "무제한" 특수값이라 정지에 못 씀).')
        self.follow_speed_percent = float(get('follow_speed_percent').value)

        self.events = PersonFollowEventPublisher(self, self.robot_id)

        self._stop = False
        self._latest_jpeg: bytes | None = None
        self._latest_frame_at: float | None = None
        self._last_processed_at: float | None = None
        self._inference_available: bool | None = None
        self._recent: collections.deque = collections.deque(maxlen=self.window_size)
        self._locked_target: dict | None = None
        self._following = False

        self.create_subscription(
            CompressedImage, self.image_topic, self._on_image,
            qos_profile_sensor_data)
        self.speed_limit_pub = self.create_publisher(SpeedLimit, SPEED_LIMIT_TOPIC, 10)
        self.following_active_pub = self.create_publisher(
            Bool, FOLLOWING_ACTIVE_TOPIC, 10)

        self.get_logger().info(
            f'추론 서버: {self.infer_server_url}. image_topic={self.image_topic}. '
            f'감지 스레드 시작.')
        # 시작 직후(아직 상태 전환이 없는 상태)엔 Nav2가 speed_limit을 한 번도
        # 못 받아 기본값(무제한)으로 안다 -- 전환이 있을 때만 발행하면 이
        # 틈을 놓친다. 시작하자마자 현재(정지) 상태를 명시적으로 한 번 보낸다.
        self._publish_speed_limit()
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def destroy_node(self):
        self._stop = True
        super().destroy_node()

    # ------------------------------------------------------------ 구독 콜백

    def _on_image(self, msg: CompressedImage):
        # 디코드하지 않고 압축된 바이트 그대로 들고 있는다 -- 다시 볼 사람은
        # 여기가 아니라 GPU 컴퓨터(HTTP로 그대로 전달)라, 여기서 디코드했다가
        # 다시 인코드하는 건 낭비다.
        self._latest_jpeg = bytes(msg.data)
        self._latest_frame_at = time.monotonic()

    # ------------------------------------------------------------ 감지 루프
    #
    # HTTP 요청(GPU 컴퓨터 추론 서버 호출)은 블로킹이라 rclpy.spin(self)을
    # 도는 메인 스레드에서 하면 다른 콜백까지 같이 밀린다. mingky_fire_evac과
    # 같은 이유로 별도 스레드에서 폴링한다.

    def _watch_loop(self):
        while rclpy.ok() and not self._stop:
            time.sleep(0.1)
            jpeg, frame_at = self._latest_jpeg, self._latest_frame_at
            if jpeg is None or frame_at is None:
                continue
            if time.monotonic() - frame_at > self.frame_max_age_sec:
                continue
            if frame_at == self._last_processed_at:
                continue
            self._last_processed_at = frame_at

            detections = self._detect(jpeg)
            target = pick_target(
                detections, self._locked_target,
                screen_center=(320.0, 240.0), max_jump_px=self.max_jump_px)
            detected = target is not None
            if detected:
                self._locked_target = target

            was_following = self._following
            self._following = next_following_state(
                self._recent, detected, was_following,
                required=self.required_detections)

            if was_following != self._following:
                state = 'FOLLOWING' if self._following else 'STOPPED'
                self.get_logger().warn(f'>>> 상태 전환: {state} <<<')
                self.events.publish(
                    'person_follow.state_changed',
                    {
                        'following': self._following,
                        'target_class': target['cls'] if target else None,
                    })
                self._publish_speed_limit()
                self.following_active_pub.publish(Bool(data=self._following))

    def _detect(self, jpeg_bytes: bytes) -> list[dict]:
        """GPU 컴퓨터의 추론 서버에 프레임을 보내고 검출 목록을 받는다.

        네트워크/서버 문제로 요청이 실패해도 이 노드를 죽이면 안 된다 --
        이번 프레임만 '검출 없음'으로 취급하고 다음 프레임에서 다시 시도한다.
        """
        try:
            resp = requests.post(
                self.infer_server_url,
                files={'image': ('frame.jpg', jpeg_bytes, 'image/jpeg')},
                data={'conf': str(self.conf_threshold)},
                timeout=self.infer_timeout_sec,
            )
            resp.raise_for_status()
            if self._inference_available is False:
                self.events.publish('person_follow.inference_restored')
                self.get_logger().info('추론 서버 연결이 복구됐습니다.')
            self._inference_available = True
            payload = resp.json().get('detections', [])
            return [
                {
                    'cls': d['class'],
                    'conf': float(d['conf']),
                    'x': float(d['x']),
                    'y': float(d['y']),
                    'w': float(d['w']),
                    'h': float(d['h']),
                }
                for d in payload
            ]
        except (requests.RequestException, ValueError, KeyError) as exc:
            if self._inference_available is not False:
                self.events.publish(
                    'person_follow.inference_unavailable',
                    {'reason': str(exc)}, level='error')
            self._inference_available = False
            self.get_logger().warn(f'추론 서버 호출 실패: {exc}', throttle_duration_sec=5.0)
            return []

    def _publish_speed_limit(self):
        msg = SpeedLimit()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.percentage = True
        msg.speed_limit = (
            self.follow_speed_percent if self._following
            else self.stop_speed_percent)
        self.speed_limit_pub.publish(msg)
        self.get_logger().info(f'speed_limit -> {msg.speed_limit:.1f}%')


def main():
    rclpy.init()
    node = PersonFollowNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
