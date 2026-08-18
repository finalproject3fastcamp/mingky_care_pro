"""QR 거리를 주 기준으로 안내 환자의 지연을 감지한다.

이 노드는 현재 안내 세션의 patient_id와 후방 QR의 data가 같을
때만 거리를 신뢰한다. QR이 짧게 가려진 구간에서는 직전 QR 거리와
YOLO 박스 크기를 결합해 제한된 시간 동안만 보간한다. 최신 거리를
신뢰할 수 없으면 안전하게 waiting을 발행한다.
"""

import collections
import json
import math
import threading
import time

from mingky_interfaces.msg import GuideState, QrObservation
from nav2_msgs.msg import SpeedLimit
import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)
import requests
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String

from .distance_policy import (
    DistancePolicy,
    estimate_visual_distance,
    INACTIVE,
    NORMAL,
    select_mode,
    SLOW,
    WAITING,
)
from .event_publisher import PersonFollowEventPublisher
from .target_lock import pick_target


SPEED_LIMIT_TOPIC = '/speed_limit'
FOLLOWING_ACTIVE_TOPIC = '/person_follow/following'
FOLLOW_STATE_TOPIC = '/person_follow/state'


class PersonFollowNode(Node):

    def __init__(self, **kwargs):
        super().__init__('person_follow_node', **kwargs)

        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter(
            'image_topic', '/rear_camera/image_raw/compressed')
        self.declare_parameter('frame_max_age_sec', 2.0)
        # QR 거리가 주 센서이므로 YOLO 서버는 선택 기능이다.
        self.declare_parameter('infer_server_url', '')
        self.declare_parameter('infer_timeout_sec', 2.0)
        self.declare_parameter('conf_threshold', 0.25)
        self.declare_parameter('max_jump_px', 200.0)
        self.declare_parameter('target_reacquire_misses', 5)
        self.declare_parameter('slow_distance_m', 1.5)
        self.declare_parameter('stop_distance_m', 2.5)
        self.declare_parameter('distance_hysteresis_m', 0.2)
        self.declare_parameter('distance_window_size', 5)
        self.declare_parameter('qr_stale_sec', 1.0)
        self.declare_parameter('visual_fallback_sec', 2.0)
        self.declare_parameter('status_period_sec', 0.5)
        # 0.0은 Nav2에서 제한 해제이므로 정지 표현에 쓰지 않는다.
        self.declare_parameter('stop_speed_percent', 0.1)
        self.declare_parameter('slow_speed_percent', 35.0)
        self.declare_parameter('normal_speed_percent', 100.0)

        get = self.get_parameter
        self.robot_id = str(get('robot_id').value)
        self.image_topic = str(get('image_topic').value)
        self.frame_max_age_sec = max(
            0.1, float(get('frame_max_age_sec').value))
        self.infer_server_url = str(get('infer_server_url').value).strip()
        self.infer_timeout_sec = max(
            0.1, float(get('infer_timeout_sec').value))
        self.conf_threshold = float(get('conf_threshold').value)
        self.max_jump_px = max(1.0, float(get('max_jump_px').value))
        self.target_reacquire_misses = max(
            1, int(get('target_reacquire_misses').value))
        self.policy = DistancePolicy(
            slow_distance_m=float(get('slow_distance_m').value),
            stop_distance_m=float(get('stop_distance_m').value),
            hysteresis_m=float(get('distance_hysteresis_m').value),
        )
        distance_window_size = max(
            1, int(get('distance_window_size').value))
        self.qr_stale_sec = max(0.1, float(get('qr_stale_sec').value))
        self.visual_fallback_sec = max(
            self.qr_stale_sec,
            float(get('visual_fallback_sec').value),
        )
        self.status_period_sec = max(
            0.1, float(get('status_period_sec').value))
        self.stop_speed_percent = float(get('stop_speed_percent').value)
        self.slow_speed_percent = float(get('slow_speed_percent').value)
        self.normal_speed_percent = float(get('normal_speed_percent').value)
        if not (
                0.0 < self.stop_speed_percent
                <= self.slow_speed_percent
                <= self.normal_speed_percent
                <= 100.0):
            raise ValueError(
                '속도 비율은 0 < stop <= slow <= normal <= 100이어야 합니다.')

        self.events = PersonFollowEventPublisher(self, self.robot_id)
        self._lock = threading.Lock()
        self._stop = False
        self._active = False
        self._session_id = 0
        self._patient_id = ''
        self._mode = INACTIVE
        self._last_status_at = 0.0
        self._qr_visible = False
        self._last_qr_at: float | None = None
        self._qr_center: tuple[float, float] | None = None
        self._qr_distances = collections.deque(maxlen=distance_window_size)
        self._latest_jpeg: bytes | None = None
        self._latest_frame_at: float | None = None
        self._last_processed_at: float | None = None
        self._inference_available: bool | None = None
        self._locked_target: dict | None = None
        self._locked_class: str | None = None
        self._target_misses = 0
        self._visual_visible = False
        self._last_visual_at: float | None = None
        self._visual_height_px: float | None = None
        self._visual_anchor_distance_m: float | None = None
        self._visual_anchor_height_px: float | None = None

        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.speed_limit_pub = self.create_publisher(
            SpeedLimit, SPEED_LIMIT_TOPIC, 10)
        self.following_active_pub = self.create_publisher(
            Bool, FOLLOWING_ACTIVE_TOPIC, state_qos)
        self.follow_state_pub = self.create_publisher(
            String, FOLLOW_STATE_TOPIC, state_qos)
        self.create_subscription(
            GuideState, '/guide_manager/state', self._on_guide_state, state_qos)
        self.create_subscription(
            QrObservation, '/rear_qr/observation', self._on_qr_observation, 10)
        if self.infer_server_url:
            self.create_subscription(
                CompressedImage,
                self.image_topic,
                self._on_image,
                qos_profile_sensor_data,
            )
            threading.Thread(
                target=self._inference_loop,
                daemon=True,
                name='person-follow-inference',
            ).start()
            self.get_logger().info(
                f'YOLO 보완 추론 서버: {self.infer_server_url}')
        else:
            self.get_logger().info('YOLO 보완 추적 비활성; QR 거리만 사용합니다.')
        self.create_timer(0.1, self._control_tick)

        # 안내 세션이 아니므로 시작 즉시 Nav2 속도 제한을 해제한다.
        self._publish_speed_limit(INACTIVE)
        self._publish_status(INACTIVE, None, 'none', force=True)

    def destroy_node(self):
        self._stop = True
        # 정상 종료 시 다음 Nav2 목표에 제한이 남지 않게 한다.
        self._publish_speed_limit(INACTIVE)
        super().destroy_node()

    def _reset_tracking(self) -> None:
        self._qr_visible = False
        self._last_qr_at = None
        self._qr_center = None
        self._qr_distances.clear()
        self._locked_target = None
        self._locked_class = None
        self._target_misses = 0
        self._visual_visible = False
        self._last_visual_at = None
        self._visual_height_px = None
        self._visual_anchor_distance_m = None
        self._visual_anchor_height_px = None

    def _on_guide_state(self, msg: GuideState) -> None:
        active = (
            msg.session_state == GuideState.SESSION_GUIDING
            and msg.session_id > 0
            and bool(msg.patient_id)
        )
        with self._lock:
            changed_session = (
                msg.session_id != self._session_id
                or msg.patient_id != self._patient_id
            )
            if changed_session or (self._active and not active):
                self._reset_tracking()
            self._active = active
            self._session_id = int(msg.session_id) if active else 0
            self._patient_id = str(msg.patient_id) if active else ''

    def _on_qr_observation(self, msg: QrObservation) -> None:
        now = time.monotonic()
        distance = float(msg.distance)
        with self._lock:
            if not self._active:
                return
            matches = (
                msg.visible
                and msg.data == self._patient_id
                and math.isfinite(distance)
                and distance > 0.0
            )
            self._qr_visible = matches
            if not matches:
                return
            self._last_qr_at = now
            self._qr_center = (float(msg.center_x), float(msg.center_y))
            self._qr_distances.append(distance)
            if self._visual_visible and self._visual_height_px:
                self._visual_anchor_distance_m = self._median_qr_distance()
                self._visual_anchor_height_px = self._visual_height_px

    def _on_image(self, msg: CompressedImage) -> None:
        with self._lock:
            self._latest_jpeg = bytes(msg.data)
            self._latest_frame_at = time.monotonic()

    def _median_qr_distance(self) -> float | None:
        if not self._qr_distances:
            return None
        ordered = sorted(self._qr_distances)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[middle])
        return float((ordered[middle - 1] + ordered[middle]) / 2.0)

    def _inference_loop(self) -> None:
        while rclpy.ok() and not self._stop:
            time.sleep(0.05)
            with self._lock:
                active = self._active
                jpeg = self._latest_jpeg
                frame_at = self._latest_frame_at
                processed_at = self._last_processed_at
                locked = self._locked_target
                locked_class = self._locked_class
                last_qr_at = self._last_qr_at
                qr_center = self._qr_center
            now = time.monotonic()
            if not active or jpeg is None or frame_at is None:
                continue
            if now - frame_at > self.frame_max_age_sec:
                with self._lock:
                    self._visual_visible = False
                continue
            if frame_at == processed_at:
                continue
            with self._lock:
                self._last_processed_at = frame_at

            detections = self._detect(jpeg)
            target = pick_target(
                detections,
                locked,
                screen_center=qr_center or (320.0, 240.0),
                max_jump_px=self.max_jump_px,
                required_class=locked_class,
            )
            qr_recent = (
                last_qr_at is not None
                and now - last_qr_at <= self.qr_stale_sec
            )
            with self._lock:
                if target is None:
                    self._target_misses += 1
                    self._visual_visible = False
                    if self._target_misses >= self.target_reacquire_misses:
                        # 위치 잠금만 풀고 QR로 검증한 클래스는 유지한다.
                        self._locked_target = None
                    continue
                if self._locked_class is None and not qr_recent:
                    # QR로 현재 환자를 확인하기 전에는 임의 인형을
                    # 잠그지 않는다.
                    self._visual_visible = False
                    continue
                self._locked_target = target
                self._locked_class = self._locked_class or str(target['cls'])
                self._target_misses = 0
                self._visual_visible = True
                self._last_visual_at = now
                self._visual_height_px = float(target['h'])
                if qr_recent:
                    self._visual_anchor_distance_m = self._median_qr_distance()
                    self._visual_anchor_height_px = self._visual_height_px

    def _control_tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            active = self._active
            previous = self._mode
            qr_fresh = (
                self._qr_visible
                and self._last_qr_at is not None
                and now - self._last_qr_at <= self.qr_stale_sec
            )
            visual_fresh = (
                self._visual_visible
                and self._last_visual_at is not None
                and self._last_qr_at is not None
                and now - self._last_visual_at <= self.frame_max_age_sec
                and now - self._last_qr_at <= self.visual_fallback_sec
            )
            if not active:
                mode, distance, source = INACTIVE, None, 'none'
            elif qr_fresh:
                distance = self._median_qr_distance()
                mode = select_mode(distance, previous, self.policy)
                source = 'qr'
            elif visual_fresh:
                distance = estimate_visual_distance(
                    self._visual_anchor_distance_m,
                    self._visual_anchor_height_px,
                    self._visual_height_px,
                )
                mode = select_mode(distance, previous, self.policy)
                source = 'visual'
            else:
                distance = None
                mode = WAITING
                source = 'stale'
            changed = mode != previous
            self._mode = mode

        if changed:
            self.get_logger().warn(
                f'환자 추종 상태: {previous} -> {mode} '
                f'(distance={distance}, source={source})')
            self.events.publish('person_follow.state_changed', {
                'state': mode,
                'distance': distance,
                'source': source,
            })
            self._publish_speed_limit(mode)
            self.following_active_pub.publish(Bool(
                data=mode in (NORMAL, SLOW)))
        self._publish_status(mode, distance, source, force=changed)

    def _publish_speed_limit(self, mode: str) -> None:
        speed = {
            WAITING: self.stop_speed_percent,
            SLOW: self.slow_speed_percent,
        }.get(mode, self.normal_speed_percent)
        msg = SpeedLimit()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.percentage = True
        msg.speed_limit = float(speed)
        self.speed_limit_pub.publish(msg)
        self.get_logger().info(f'speed_limit -> {speed:.1f}% ({mode})')

    def _publish_status(
            self, mode: str, distance: float | None, source: str,
            *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_status_at < self.status_period_sec:
            return
        self._last_status_at = now
        with self._lock:
            payload = {
                'state': mode,
                'session_id': self._session_id,
                'patient_id': self._patient_id,
                'distance': distance,
                'source': source,
                'qr_visible': self._qr_visible,
                'visual_visible': self._visual_visible,
            }
        self.follow_state_pub.publish(String(data=json.dumps(
            payload, ensure_ascii=False, separators=(',', ':'))))

    def _detect(self, jpeg_bytes: bytes) -> list[dict]:
        """GPU 응답 오류가 추론 스레드를 종료하지 않도록 검증한다."""
        try:
            response = requests.post(
                self.infer_server_url,
                files={'image': ('frame.jpg', jpeg_bytes, 'image/jpeg')},
                data={'conf': str(self.conf_threshold)},
                timeout=self.infer_timeout_sec,
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError('추론 응답은 JSON 객체여야 합니다.')
            payload = body.get('detections', [])
            if not isinstance(payload, list):
                raise ValueError('detections는 배열이어야 합니다.')
            detections = []
            for raw in payload:
                if not isinstance(raw, dict):
                    raise ValueError('검출 항목은 JSON 객체여야 합니다.')
                detection = {
                    'cls': str(raw['class']),
                    'conf': float(raw['conf']),
                    'x': float(raw['x']),
                    'y': float(raw['y']),
                    'w': float(raw['w']),
                    'h': float(raw['h']),
                }
                numeric = [
                    value for key, value in detection.items() if key != 'cls'
                ]
                if not all(math.isfinite(value) for value in numeric):
                    raise ValueError(
                        '검출 좌표와 신뢰도는 유한한 수여야 합니다.')
                detections.append(detection)
            if self._inference_available is False:
                self.events.publish('person_follow.inference_restored')
                self.get_logger().info('추론 서버 연결이 복구됐습니다.')
            self._inference_available = True
            return detections
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            if self._inference_available is not False:
                self.events.publish(
                    'person_follow.inference_unavailable',
                    {'reason': str(exc)},
                    level='error',
                )
            self._inference_available = False
            self.get_logger().warn(
                f'추론 서버 호출 실패: {exc}', throttle_duration_sec=5.0)
            return []


def main() -> None:
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
