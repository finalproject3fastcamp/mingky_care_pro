"""YOLO 인형 박스와 QR 보정값으로 안내 환자 거리를 추정한다.

현재 세션의 patient_id와 같은 YOLO 클래스만 추적한다. QR이
없어도 13cm 인형 높이와 카메라 보정값으로 절대거리를 근사하고,
QR이 보이면 그 거리로 YOLO 박스 추정을 다시 보정한다. QR·YOLO가
순간 유실되면 2초까지 직전 주행 상태를 유지한다.
"""

import collections
import json
import math
import threading
import time

from mingky_interfaces.msg import GuideState, QrObservation
from nav_msgs.msg import Odometry
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
from sensor_msgs.msg import CameraInfo, CompressedImage
from std_msgs.msg import Bool, String

from .distance_policy import (
    bbox_is_complete,
    DistancePolicy,
    estimate_bbox_distance,
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
DETECTION_LOG_INTERVAL_SEC = 5.0


class PersonFollowNode(Node):

    def __init__(self, **kwargs):
        super().__init__('person_follow_node', **kwargs)

        self.declare_parameter('robot_id', 'pinky-01')
        self.declare_parameter(
            'image_topic', '/rear_camera/image_raw/compressed')
        self.declare_parameter(
            'camera_info_topic', '/rear_camera/camera_info')
        self.declare_parameter('odom_topic', '/odom')
        self.declare_parameter('frame_max_age_sec', 2.0)
        # YOLO 서버를 비우면 QR 거리 단독으로 안전하게 폴백한다.
        self.declare_parameter('infer_server_url', '')
        self.declare_parameter('infer_timeout_sec', 2.0)
        self.declare_parameter('conf_threshold', 0.25)
        self.declare_parameter('max_jump_px', 200.0)
        self.declare_parameter('target_reacquire_misses', 5)
        self.declare_parameter('slow_distance_m', 0.15)
        self.declare_parameter('stop_distance_m', 0.30)
        self.declare_parameter('distance_hysteresis_m', 0.02)
        self.declare_parameter('distance_window_size', 5)
        self.declare_parameter('qr_stale_sec', 1.0)
        self.declare_parameter('tracking_grace_sec', 2.0)
        self.declare_parameter('initial_acquire_grace_sec', 4.0)
        self.declare_parameter('initial_acquire_max_distance_m', 0.30)
        self.declare_parameter('target_height_m', 0.13)
        self.declare_parameter('bbox_edge_margin_px', 5.0)
        self.declare_parameter('status_period_sec', 0.5)
        # 0.0은 Nav2에서 제한 해제이므로 정지 표현에 쓰지 않는다.
        self.declare_parameter('stop_speed_percent', 0.1)
        self.declare_parameter('slow_speed_percent', 35.0)
        self.declare_parameter('normal_speed_percent', 100.0)

        get = self.get_parameter
        self.robot_id = str(get('robot_id').value)
        self.image_topic = str(get('image_topic').value)
        self.camera_info_topic = str(get('camera_info_topic').value)
        self.odom_topic = str(get('odom_topic').value)
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
        self.tracking_grace_sec = max(
            0.0, float(get('tracking_grace_sec').value))
        self.initial_acquire_grace_sec = max(
            0.0, float(get('initial_acquire_grace_sec').value))
        self.initial_acquire_max_distance_m = max(
            0.0, float(get('initial_acquire_max_distance_m').value))
        self.target_height_m = float(get('target_height_m').value)
        self.bbox_edge_margin_px = max(
            0.0, float(get('bbox_edge_margin_px').value))
        if not math.isfinite(self.target_height_m) or self.target_height_m <= 0:
            raise ValueError('target_height_m은 0보다 큰 유한한 수여야 합니다.')
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
        self._visual_distances = collections.deque(maxlen=distance_window_size)
        self._latest_jpeg: bytes | None = None
        self._latest_frame_at: float | None = None
        self._last_processed_at: float | None = None
        self._inference_available: bool | None = None
        self._last_detection_log_at: float | None = None
        self._last_detection_classes: tuple[str, ...] | None = None
        self._locked_target: dict | None = None
        self._locked_class: str | None = None
        self._target_misses = 0
        self._visual_visible = False
        self._last_visual_at: float | None = None
        self._visual_height_px: float | None = None
        self._visual_anchor_distance_m: float | None = None
        self._visual_anchor_height_px: float | None = None
        self._camera_focal_y_px: float | None = None
        self._last_reliable_distance_m: float | None = None
        self._guidance_started_at: float | None = None
        self._odom_xy: tuple[float, float] | None = None
        self._acquire_traveled_m = 0.0
        self._acquire_odom_seen = False

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
        self.create_subscription(
            CameraInfo, self.camera_info_topic, self._on_camera_info,
            qos_profile_sensor_data)
        self.create_subscription(
            Odometry, self.odom_topic, self._on_odom, qos_profile_sensor_data)
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
                f'YOLO 거리 추정 서버: {self.infer_server_url}')
        else:
            self.get_logger().info('YOLO 추적 비활성; QR 거리만 사용합니다.')
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
        self._visual_distances.clear()
        self._locked_target = None
        self._locked_class = None
        self._target_misses = 0
        self._visual_visible = False
        self._last_visual_at = None
        self._visual_height_px = None
        self._visual_anchor_distance_m = None
        self._visual_anchor_height_px = None
        self._last_reliable_distance_m = None
        self._guidance_started_at = None
        self._acquire_traveled_m = 0.0
        self._acquire_odom_seen = False

    def _on_guide_state(self, msg: GuideState) -> None:
        now = time.monotonic()
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
            starting = active and (changed_session or not self._active)
            self._active = active
            self._session_id = int(msg.session_id) if active else 0
            self._patient_id = str(msg.patient_id) if active else ''
            if starting:
                self._guidance_started_at = now

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

    def _on_camera_info(self, msg: CameraInfo) -> None:
        focal_y = float(msg.k[4])
        if not math.isfinite(focal_y) or focal_y <= 0.0:
            return
        with self._lock:
            self._camera_focal_y_px = focal_y

    def _on_odom(self, msg: Odometry) -> None:
        position = msg.pose.pose.position
        xy = (float(position.x), float(position.y))
        if not all(math.isfinite(value) for value in xy):
            return
        with self._lock:
            previous = self._odom_xy
            self._odom_xy = xy
            if self._active and self._last_reliable_distance_m is None:
                self._acquire_odom_seen = True
                if previous is not None:
                    self._acquire_traveled_m += math.hypot(
                        xy[0] - previous[0], xy[1] - previous[1])

    def _median_qr_distance(self) -> float | None:
        if not self._qr_distances:
            return None
        ordered = sorted(self._qr_distances)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[middle])
        return float((ordered[middle - 1] + ordered[middle]) / 2.0)

    def _median_visual_distance(self) -> float | None:
        if not self._visual_distances:
            return None
        ordered = sorted(self._visual_distances)
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
                patient_id = self._patient_id
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
                required_class=locked_class or patient_id,
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
                        # 위치 잠금만 풀고 세션 환자 클래스는 유지한다.
                        self._locked_target = None
                    continue
                self._locked_target = target
                self._locked_class = str(target['cls'])
                self._target_misses = 0
                if not bbox_is_complete(
                        center_y_px=float(target['y']),
                        height_px=float(target['h']),
                        image_height_px=float(target['image_height']),
                        edge_margin_px=self.bbox_edge_margin_px):
                    self._visual_visible = False
                    continue
                self._visual_height_px = float(target['h'])
                if qr_recent:
                    self._visual_anchor_distance_m = self._median_qr_distance()
                    self._visual_anchor_height_px = self._visual_height_px
                distance = estimate_visual_distance(
                    self._visual_anchor_distance_m,
                    self._visual_anchor_height_px,
                    self._visual_height_px,
                )
                if distance is None:
                    distance = estimate_bbox_distance(
                        self._camera_focal_y_px,
                        self.target_height_m,
                        self._visual_height_px,
                    )
                if distance is None:
                    self._visual_visible = False
                    continue
                self._visual_distances.append(distance)
                self._visual_visible = True
                self._last_visual_at = now

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
                and now - self._last_visual_at <= self.frame_max_age_sec
            )
            if not active:
                mode, distance, source = INACTIVE, None, 'none'
            elif visual_fresh:
                distance = self._median_visual_distance()
                mode = select_mode(distance, previous, self.policy)
                source = 'visual'
                self._last_reliable_distance_m = distance
            elif qr_fresh:
                distance = self._median_qr_distance()
                mode = select_mode(distance, previous, self.policy)
                source = 'qr'
                self._last_reliable_distance_m = distance
            else:
                last_seen = max(
                    (stamp for stamp in (
                        self._last_qr_at, self._last_visual_at)
                     if stamp is not None),
                    default=None,
                )
                acquire_elapsed = (
                    None if self._guidance_started_at is None
                    else now - self._guidance_started_at
                )
                acquire_distance = (
                    self._acquire_traveled_m
                    if self._acquire_odom_seen else None
                )
                acquiring = (
                    self._last_reliable_distance_m is None
                    and acquire_elapsed is not None
                    and acquire_elapsed <= self.initial_acquire_grace_sec
                    and (
                        acquire_distance is None
                        or acquire_distance
                        <= self.initial_acquire_max_distance_m)
                )
                grace_active = (
                    previous in (NORMAL, SLOW)
                    and last_seen is not None
                    and now - last_seen <= self.tracking_grace_sec
                )
                if acquiring:
                    distance = None
                    mode = SLOW
                    source = 'acquiring'
                elif grace_active:
                    distance = self._last_reliable_distance_m
                    mode = previous
                    source = 'grace'
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
            image_width = float(body['image_width'])
            image_height = float(body['image_height'])
            if not (
                    math.isfinite(image_width)
                    and math.isfinite(image_height)
                    and image_width > 0.0
                    and image_height > 0.0):
                raise ValueError('입력 영상 크기가 유효하지 않습니다.')
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
                    'image_width': image_width,
                    'image_height': image_height,
                }
                numeric = [
                    value for key, value in detection.items() if key != 'cls'
                ]
                if not all(math.isfinite(value) for value in numeric):
                    raise ValueError(
                        '검출 좌표와 신뢰도는 유한한 수여야 합니다.')
                detections.append(detection)
            self._log_detections(detections)
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
                    level='warning',
                )
            self._inference_available = False
            self.get_logger().warn(
                f'추론 서버 호출 실패: {exc}', throttle_duration_sec=5.0)
            return []

    def _log_detections(self, detections: list[dict]) -> None:
        """검출 클래스 변화는 즉시, 같은 결과는 5초마다 기록한다."""
        now = time.monotonic()
        classes = tuple(sorted({str(item['cls']) for item in detections}))
        changed = classes != self._last_detection_classes
        periodic = (
            self._last_detection_log_at is None
            or now - self._last_detection_log_at >= DETECTION_LOG_INTERVAL_SEC
        )
        if not changed and not periodic:
            return

        if detections:
            summaries = []
            for item in sorted(
                    detections,
                    key=lambda value: float(value['conf']),
                    reverse=True):
                summaries.append(
                    f"{item['cls']}(conf={float(item['conf']):.2f}, "
                    f"bbox={float(item['w']):.0f}x{float(item['h']):.0f}px)"
                )
            self.get_logger().info(
                f"YOLO 검출: count={len(detections)}, "
                f"targets=[{', '.join(summaries)}]")
        else:
            self.get_logger().info('YOLO 미검출: targets=[]')

        self._last_detection_classes = classes
        self._last_detection_log_at = now


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
