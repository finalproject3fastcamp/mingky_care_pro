"""Pinky 카메라에서 QR을 읽어 백엔드로 환자·로봇 정보를 POST하는 노드."""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import rclpy
import requests
from pyzbar import pyzbar
from pyzbar.pyzbar import ZBarSymbol
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool

from mingky_interfaces.msg import GuideState, SessionStart
from mingky_camera_streamer.mjpeg_server import MjpegServer

from .scan_policy import completion_scan_enabled


@dataclass
class LastScan:
    value: str
    ts: float


class QrReaderNode(Node):
    def __init__(self) -> None:
        super().__init__("qr_reader_node")

        self.declare_parameter("source", "image")
        self.declare_parameter("image_path", "")
        self.declare_parameter("usb_device_index", 0)
        # source=csi 캡처 해상도. QR 인식엔 720p 면 충분하고 CPU 도 여유롭다.
        self.declare_parameter("csi_width", 1280)
        self.declare_parameter("csi_height", 720)
        # 카메라가 뒤집혀 장착된 경우 좌우/상하 반전으로 바로잡는다 (180° 장착이면 둘 다 true).
        self.declare_parameter("csi_hflip", False)
        self.declare_parameter("csi_vflip", False)
        self.declare_parameter("backend_url", "http://localhost:8000")
        # 스캔한 로봇. guidance_sessions.robot_id 가 NOT NULL 이라 백엔드가 필수로 받는다.
        self.declare_parameter("robot_id", "")
        # 도킹 마커. 선택값이라 -1 을 미지정으로 보고 payload 에서 뺀다.
        self.declare_parameter("marker_id", -1)
        self.declare_parameter("fps", 10.0)
        self.declare_parameter("debounce_seconds", 5.0)
        self.declare_parameter("http_timeout_seconds", 3.0)
        # >0 이면 그 포트로 MJPEG 미리보기 송출 (대시보드 <img> 임베드용). 0 이면 끔.
        self.declare_parameter("preview_port", 0)
        # 미리보기 전송용 축소 폭·JPEG 품질. 디코드는 원본 해상도로 하고 여기만
        # 줄인다. 무선 링크가 좁아 원본을 그대로 흘리면 지연이 쌓인다.
        # 0 이면 축소하지 않는다.
        self.declare_parameter("preview_max_width", 640)
        self.declare_parameter("preview_jpeg_quality", 60)
        self.declare_parameter("preview_max_fps", 10.0)
        # arming 폴링 주기. 백엔드에 GET /robots/<id>/arming 을 주기로 던져
        # 의료진이 이 로봇을 활성화했는지 확인한다. armed 가 아니면 QR 을
        # 디코드/전송하지 않는다.
        self.declare_parameter("arming_poll_seconds", 2.0)
        # 폴링 실패가 이 횟수 연속되면 disarmed 로 취급한다 (페일세이프).
        # 백엔드 두절 상태에서 오래된 arming 이 유효한 것처럼 남아 있으면
        # 통신 회복 순간 스캔이 튀는 위험이 있다.
        self.declare_parameter("arming_fail_disarm_after", 5)
        # 최초 세션 전달은 관제 출발을 여는 핵심 신호다. DDS reliable 이어도
        # publisher/subscriber discovery 경계에서 한 번만 발행하면 유실될 수
        # 있으므로 GuideState 에 같은 session_id 가 보일 때까지 짧게 재전송한다.
        self.declare_parameter("session_retry_seconds", 1.0)
        self.declare_parameter("session_retry_limit", 10)

        self.source = self.get_parameter("source").value
        self.backend_url = self.get_parameter("backend_url").value.rstrip("/")
        self.robot_id = self.get_parameter("robot_id").value
        self.marker_id = int(self.get_parameter("marker_id").value)
        self.debounce_seconds = float(self.get_parameter("debounce_seconds").value)
        self.http_timeout = float(self.get_parameter("http_timeout_seconds").value)
        fps = float(self.get_parameter("fps").value)
        self.arming_poll_seconds = float(
            self.get_parameter("arming_poll_seconds").value)
        self.arming_fail_disarm_after = int(
            self.get_parameter("arming_fail_disarm_after").value)
        self.session_retry_limit = max(
            1, int(self.get_parameter("session_retry_limit").value))

        if not self.robot_id:
            raise RuntimeError("robot_id 파라미터가 필요합니다 (백엔드가 필수로 받음)")

        self._capture: cv2.VideoCapture | None = None
        self._picam2 = None
        self._static_frame = None
        self._last: LastScan | None = None

        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self._camera_ready_pub = self.create_publisher(
            Bool, '/front_camera/ready', state_qos)
        # 후방 카메라(/rear_camera/image_raw)와 같은 패턴 -- 원본 프레임을
        # ROS2 토픽으로 공식 발행해서, 이 노드 내부(QR 디코드) 말고 다른
        # 노드도 정식으로 구독할 수 있게 한다. 첫 사용처는 mingky_fire_evac
        # (화재 감지, AI PC에서 구독). Raw 대신 CompressedImage 인 이유는
        # 와이파이로 매 프레임 수 MB 씩 나가면 안 되기 때문 (관제 미리보기와
        # 같은 이유).
        self._image_pub = self.create_publisher(
            CompressedImage, '/front_camera/image_raw/compressed',
            qos_profile_sensor_data)
        try:
            self._setup_source()
        except Exception:  # noqa: BLE001
            # 후방 카메라는 전방 초기화의 성공 여부가 아니라 완료 시점만
            # 기다린다. 실패 상태도 남겨 후방이 무기한 대기하지 않게 한다.
            self._camera_ready_pub.publish(Bool(data=False))
            raise
        self._camera_ready_pub.publish(Bool(data=True))

        self._preview: MjpegServer | None = None
        preview_port = int(self.get_parameter("preview_port").value)
        if preview_port > 0:
            self._preview = MjpegServer(
                preview_port,
                self.get_logger(),
                max_width=int(self.get_parameter("preview_max_width").value),
                quality=int(self.get_parameter("preview_jpeg_quality").value),
                max_fps=float(self.get_parameter("preview_max_fps").value),
            )

        # 백엔드가 세션을 만들어 주면 로봇 내부에 SessionStart 를 흘려 다른 노드
        # (guide_manager 등)가 session_id 와 진료 순서를 받도록 한다.
        self._session_pub = self.create_publisher(SessionStart, "~/session_start", 10)

        # arming 상태. 백엔드 폴링 결과의 캐시다.
        # 기동 직후는 disarmed 로 시작해, 첫 폴링이 armed 를 확인해야 스캔이 켜진다.
        self._armed = False
        self._completion_scan_enabled = False
        self._arming_fails = 0
        self._pending_session: SessionStart | None = None
        self._session_publish_attempts = 0
        self._guide_state_seen = False
        self._guide_session_id = 0
        # Guide Manager가 재기동해 SESSION_NONE 으로 돌아오면 백엔드 정본에서
        # 활성 세션을 한 번 복원한다. 평상시에는 계속 폴링하지 않는다.
        self._session_recovery_requested = True

        self.create_subscription(
            GuideState, '/guide_manager/state', self._on_guide_state, state_qos)

        period = 1.0 / max(fps, 0.1)
        self._timer = self.create_timer(period, self._tick)
        self._arming_timer = self.create_timer(
            self.arming_poll_seconds, self._poll_arming)
        self._session_retry_timer = self.create_timer(
            max(0.2, float(self.get_parameter("session_retry_seconds").value)),
            self._retry_session_start)
        self._session_recovery_timer = self.create_timer(
            max(2.0, self.arming_poll_seconds), self._recover_active_session)
        self.get_logger().info(
            f"qr_reader_node started (source={self.source}, backend={self.backend_url})"
        )

    def _setup_source(self) -> None:
        if self.source == "image":
            path = self.get_parameter("image_path").value
            if not path:
                raise RuntimeError("source=image 이면 image_path 파라미터가 필요합니다")
            frame = cv2.imread(path)
            if frame is None:
                raise RuntimeError(f"이미지를 읽을 수 없습니다: {path}")
            self._static_frame = frame
        elif self.source == "usb":
            index = int(self.get_parameter("usb_device_index").value)
            capture = cv2.VideoCapture(index)
            if not capture.isOpened():
                raise RuntimeError(f"USB 카메라를 열 수 없습니다 (index={index})")
            self._capture = capture
        elif self.source == "csi":
            # Pi 5 + libcamera 스택에서는 cv2.VideoCapture 로 CSI 카메라를 못 연다
            # (배포된 cv2 가 GStreamer 미지원). Pinky 표준인 Picamera2 를 쓴다.
            # picamera2 는 시스템 python 에만 있으므로 csi 일 때만 지연 임포트한다.
            try:
                from picamera2 import Picamera2
            except ImportError as exc:
                raise RuntimeError(
                    "source=csi 는 picamera2 가 필요합니다: "
                    "sudo apt install -y python3-picamera2"
                ) from exc

            width = int(self.get_parameter("csi_width").value)
            height = int(self.get_parameter("csi_height").value)
            hflip = bool(self.get_parameter("csi_hflip").value)
            vflip = bool(self.get_parameter("csi_vflip").value)
            # 장착 방향 보정은 ISP 레벨에서(Transform) 처리해 디코드·미리보기에 함께 적용한다.
            from libcamera import Transform

            picam2 = Picamera2()
            # RGB888 3채널. pyzbar 는 채널 순서와 무관하게 휘도로 디코드한다.
            picam2.configure(picam2.create_preview_configuration(
                main={"format": "RGB888", "size": (width, height)},
                transform=Transform(hflip=hflip, vflip=vflip),
            ))
            try:
                picam2.start()
            except Exception as exc:  # noqa: BLE001
                # camera_server 서비스나 pinkylib Camera() 가 점유 중이면 busy 로 실패한다.
                raise RuntimeError(
                    "CSI 카메라를 열 수 없습니다. 다른 프로세스가 점유 중일 수 있습니다 "
                    "(camera_server 서비스 · pinkylib Camera 등 종료 후 재시도): "
                    f"{exc}"
                ) from exc
            self._picam2 = picam2
        else:
            raise RuntimeError(f"알 수 없는 source: {self.source}")

    def _read_frame(self):
        if self._static_frame is not None:
            return self._static_frame
        if self._picam2 is not None:
            # 최신 프레임을 즉시 반환한다 (내부적으로 카메라가 자체 fps 로 돈다).
            return self._picam2.capture_array()
        assert self._capture is not None
        ok, frame = self._capture.read()
        if not ok:
            return None
        return frame

    def _tick(self) -> None:
        # QR 디코드는 arming/waiting 동안만 한다. 관제 카메라 화면을 실제로 연
        # 사람이 있거나, /front_camera/image_raw/compressed 구독자(예: 화재
        # 감지 노드)가 있으면 스캔 상태와 무관하게 프레임을 계속 공유한다.
        # 이 셋 중 아무것도 아니면(대기 중 + 아무도 안 봄) 카메라를 안 읽어서
        # CPU 를 아낀다 -- 원래도 그랬던 최적화라 조건만 하나 더 붙였다.
        scan_enabled = self._armed or self._completion_scan_enabled
        preview_enabled = self._preview is not None and self._preview.has_viewers
        image_topic_enabled = self._image_pub.get_subscription_count() > 0
        if not (scan_enabled or preview_enabled or image_topic_enabled):
            return

        frame = self._read_frame()
        if frame is None:
            return

        if image_topic_enabled:
            self._publish_compressed(frame)

        # 관제 미리보기만 보는 동안에는 QR 디코드를 돌리지 않는다.
        codes = (pyzbar.decode(frame, symbols=[ZBarSymbol.QRCODE])
                 if scan_enabled else [])
        if self._preview is not None:
            self._preview.update(self._annotate_preview(frame, codes))

        if not scan_enabled:
            return

        for code in codes:
            value = code.data.decode("utf-8", errors="replace").strip()
            if not value:
                continue
            if self._is_debounced(value):
                continue
            self._last = LastScan(value=value, ts=time.monotonic())
            self._post_scan(value)
            return

    def _publish_compressed(self, frame) -> None:
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.format = 'jpeg'
        msg.data = encoded.tobytes()
        self._image_pub.publish(msg)

    @staticmethod
    def _annotate_preview(frame, codes):
        """Draw QR results without coupling the generic streamer to QR types."""
        if not codes:
            return frame
        import numpy as np

        display = frame.copy()
        for code in codes:
            label = code.data.decode('utf-8', errors='replace')
            points = code.polygon
            if points and len(points) >= 4:
                polygon = np.array(
                    [[point.x, point.y] for point in points], dtype=np.int32)
                cv2.polylines(display, [polygon], True, (0, 255, 0), 3)
            rect = code.rect
            cv2.putText(
                display, label, (rect.left, max(rect.top - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        return display

    def _poll_arming(self) -> None:
        """백엔드에 이 로봇이 armed 인지 물어본다.

        성공 응답의 armed 값을 그대로 캐시한다. 실패가 연속되면 페일세이프로
        disarmed 처리 — 통신 두절 상태에서 헛돌지 않게.
        """
        url = f"{self.backend_url}/robots/{self.robot_id}/arming"
        try:
            response = requests.get(url, timeout=self.http_timeout)
        except requests.RequestException as exc:
            self._arming_fails += 1
            if self._armed and self._arming_fails >= self.arming_fail_disarm_after:
                self.get_logger().warn(
                    f"arming 폴링 {self._arming_fails}회 연속 실패 → disarmed (페일세이프): {exc}")
                self._disarm()
            return

        if response.status_code != 200:
            self._arming_fails += 1
            if self._armed and self._arming_fails >= self.arming_fail_disarm_after:
                self.get_logger().warn(
                    f"arming 응답 이상 {self._arming_fails}회 → disarmed: "
                    f"{response.status_code} {response.text[:120]}")
                self._disarm()
            return

        self._arming_fails = 0
        try:
            armed = bool(response.json().get("armed"))
        except (ValueError, AttributeError):
            self.get_logger().error("arming 응답 파싱 실패")
            return

        if armed != self._armed:
            self.get_logger().info(f"arming 상태 변경: {self._armed} → {armed}")
            if armed:
                self._armed = True
            else:
                self._disarm()

    def _on_guide_state(self, msg: GuideState) -> None:
        previous_session_id = self._guide_session_id
        self._guide_state_seen = True
        self._guide_session_id = int(msg.session_id)
        if (self._pending_session is not None
                and self._guide_session_id == int(self._pending_session.session_id)
                and msg.session_state != GuideState.SESSION_NONE):
            self.get_logger().info(
                f'session_start 수신 확인: session_id={self._guide_session_id} '
                f'attempts={self._session_publish_attempts}')
            self._pending_session = None
            self._session_publish_attempts = 0
        if previous_session_id > 0 and self._guide_session_id == 0:
            self._session_recovery_requested = True

        enabled = completion_scan_enabled(msg, self.robot_id)
        if enabled == self._completion_scan_enabled:
            return
        self.get_logger().info(
            f'검사 완료 QR 스캔 창: {self._completion_scan_enabled} → {enabled}')
        self._completion_scan_enabled = enabled
        if enabled:
            # 이전(최초 확인 또는 직전 검사실)에서 읽은 같은
            # 환자 QR이라도 새 waiting 단계에서는 즉시 받아야 한다.
            self._last = None
        if not enabled and not self._armed and self._preview is not None:
            self._preview.clear()

    def _disarm(self) -> None:
        """스캔을 끄고 미리보기에 남은 프레임을 버린다.

        정상 해제(스캔 성공·의료진 취소)와 페일세이프 해제가 같은 뒷정리를
        해야 해서 한곳에 모은다.
        """
        self._armed = False
        if not self._completion_scan_enabled and self._preview is not None:
            self._preview.clear()

    def _is_debounced(self, value: str) -> bool:
        if self._last is None:
            return False
        if self._last.value != value:
            return False
        return (time.monotonic() - self._last.ts) < self.debounce_seconds

    def _post_scan(self, patient_id: str) -> None:
        # completion QR은 이미 같은 session_id의 GuideState가 있으므로 그 값을
        # ACK로 사용할 수 없다. 이번 재전송은 최초 세션 전달에만 적용한다.
        initial_scan = self._armed and not self._completion_scan_enabled
        url = f"{self.backend_url}/qr/scan"
        body = {"patient_id": patient_id, "robot_id": self.robot_id}
        if self.marker_id >= 0:
            body["marker_id"] = self.marker_id
        try:
            response = requests.post(
                url,
                json=body,
                timeout=self.http_timeout,
            )
        except requests.RequestException as exc:
            self.get_logger().error(f"백엔드 호출 실패: {exc}")
            return

        if response.status_code == 200:
            self.get_logger().info(f"QR 확인 성공: {patient_id}")
            self._publish_session_start(response, require_ack=initial_scan)
        elif response.status_code == 404:
            self.get_logger().warn(f"QR 인식 실패 (환자 없음): {patient_id}")
        else:
            self.get_logger().error(
                f"백엔드 응답 이상: {response.status_code} {response.text[:200]}"
            )

    def _publish_session_start(
        self,
        response: requests.Response,
        *,
        require_ack: bool = False,
    ) -> None:
        """POST /qr/scan 응답을 파싱해 SessionStart 로 흘린다.

        응답 스키마는 backend/app/schemas.py 의 TodaySchedule 와 1:1 이다.
        파싱이 실패해도 노드는 계속 살아 있어야 한다 — 재스캔으로 회복 가능하고,
        예외로 죽으면 카메라 리소스까지 재초기화해야 해서 손해가 크다.
        """
        try:
            msg = self._session_message(response.json())
        except (ValueError, TypeError, AttributeError) as exc:
            self.get_logger().error(f"세션 응답 파싱 실패: {exc}")
            return

        self._session_pub.publish(msg)
        if require_ack:
            self._pending_session = msg
            self._session_publish_attempts = 1
        self.get_logger().info(
            f"session_start 발행: session_id={msg.session_id} "
            f"patient={msg.patient_id} step={msg.current_step_order}/"
            f"{len(msg.visit_names)} visits={msg.visit_names}"
        )

    @staticmethod
    def _session_message(body: dict) -> SessionStart:
        steps = body.get("steps") or []
        msg = SessionStart()
        msg.session_id = int(body.get("session_id") or 0)
        msg.patient_id = str(body.get("patient", {}).get("patient_id") or "")
        msg.current_step_order = int(body.get("current_step_order") or 0)
        # 백엔드는 이미 step_order 오름차순으로 보내지만 복구 응답도 같은
        # 규칙으로 다루도록 여기서 명시적으로 정렬한다.
        msg.visit_names = [
            str(step.get("visit_name") or "")
            for step in sorted(steps, key=lambda step: step.get("step_order", 0))
        ]
        return msg

    def _retry_session_start(self) -> None:
        msg = self._pending_session
        if msg is None:
            return
        if self._session_publish_attempts >= self.session_retry_limit:
            self.get_logger().error(
                f'session_start 수신 확인 실패: session_id={msg.session_id} '
                f'attempts={self._session_publish_attempts}')
            self._pending_session = None
            self._session_publish_attempts = 0
            return
        self._session_pub.publish(msg)
        self._session_publish_attempts += 1
        self.get_logger().warn(
            f'session_start 재전송: session_id={msg.session_id} '
            f'attempt={self._session_publish_attempts}/{self.session_retry_limit}')

    def _recover_active_session(self) -> None:
        """Guide Manager 재기동 시 백엔드의 활성 세션을 한 번 복원한다."""
        if not self._session_recovery_requested or not self._guide_state_seen:
            return
        if self._guide_session_id > 0 or self._armed:
            self._session_recovery_requested = False
            return
        try:
            response = requests.get(
                f'{self.backend_url}/sessions/active', timeout=self.http_timeout)
            response.raise_for_status()
            sessions = response.json()
            active = next(
                (item for item in sessions
                 if str(item.get('robot_id') or '') == self.robot_id),
                None,
            )
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            self.get_logger().warn(f'활성 세션 복구 조회 실패: {exc}')
            return

        self._session_recovery_requested = False
        if active is None:
            return
        try:
            msg = self._session_message(active)
        except (ValueError, TypeError, AttributeError) as exc:
            self.get_logger().error(f'활성 세션 복구 응답 파싱 실패: {exc}')
            return
        if msg.session_id <= 0 or not msg.patient_id:
            self.get_logger().error('활성 세션 복구 응답에 세션 정보가 없습니다.')
            return
        self._session_pub.publish(msg)
        self._pending_session = msg
        self._session_publish_attempts = 1
        self.get_logger().warn(
            f'백엔드 활성 세션 복구 발행: session_id={msg.session_id} '
            f'patient={msg.patient_id}')

    def destroy_node(self) -> bool:
        if self._capture is not None:
            self._capture.release()
        if self._picam2 is not None:
            self._picam2.stop()
            self._picam2.close()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = QrReaderNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
