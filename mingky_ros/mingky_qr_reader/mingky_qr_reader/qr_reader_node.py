"""Pinky 카메라에서 QR을 읽어 백엔드로 환자·로봇 정보를 POST하는 노드."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import rclpy
import requests
from pyzbar import pyzbar
from pyzbar.pyzbar import ZBarSymbol
from rclpy.node import Node

from mingky_interfaces.msg import SessionStart


@dataclass
class LastScan:
    value: str
    ts: float


class _PreviewServer:
    """노드가 잡은 프레임을 MJPEG 로 송출하는 경량 미리보기 서버.

    카메라는 한 프로세스만 열 수 있어(스캔 중 별도 camera_server 사용 불가) 노드가
    직접 최신 프레임을 공유 버퍼에 넣고, 접속한 뷰어들이 그 버퍼를 나눠 받는다.
    대시보드에서 `<img src=".../stream">` 로 바로 임베드한다.
    """

    def __init__(self, port: int, logger) -> None:
        from flask import Flask, Response  # 미리보기 켤 때만 필요

        self._lock = threading.Lock()
        self._jpeg: bytes | None = None

        app = Flask(__name__)

        @app.route("/")
        def _index():
            return ('<html><body style="margin:0;background:#000">'
                    '<img src="/stream" style="width:100%"></body></html>')

        @app.route("/stream")
        def _stream():
            return Response(self._frames(),
                            mimetype="multipart/x-mixed-replace; boundary=frame")

        self._thread = threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=port,
                                   threaded=True, use_reloader=False),
            daemon=True,
        )
        self._thread.start()
        logger.info(f"미리보기 MJPEG 서버 시작: http://0.0.0.0:{port}/stream")

    def _frames(self):
        while True:
            with self._lock:
                buf = self._jpeg
            if buf is not None:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + buf + b"\r\n")
            time.sleep(0.05)

    def update(self, frame, codes) -> None:
        """최신 프레임에 인식된 QR 박스·라벨을 얹어 JPEG 버퍼로 갱신한다."""
        import numpy as np  # cv2 와 함께 항상 존재

        disp = frame.copy()
        for code in codes:
            label = code.data.decode("utf-8", errors="replace")
            pts = code.polygon
            if pts and len(pts) >= 4:
                poly = np.array([[p.x, p.y] for p in pts], dtype=np.int32)
                cv2.polylines(disp, [poly], True, (0, 255, 0), 3)
            r = code.rect
            cv2.putText(disp, label, (r.left, max(r.top - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        ok, buf = cv2.imencode(".jpg", disp)
        if ok:
            with self._lock:
                self._jpeg = buf.tobytes()


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

        self.source = self.get_parameter("source").value
        self.backend_url = self.get_parameter("backend_url").value.rstrip("/")
        self.robot_id = self.get_parameter("robot_id").value
        self.marker_id = int(self.get_parameter("marker_id").value)
        self.debounce_seconds = float(self.get_parameter("debounce_seconds").value)
        self.http_timeout = float(self.get_parameter("http_timeout_seconds").value)
        fps = float(self.get_parameter("fps").value)

        if not self.robot_id:
            raise RuntimeError("robot_id 파라미터가 필요합니다 (백엔드가 필수로 받음)")

        self._capture: cv2.VideoCapture | None = None
        self._picam2 = None
        self._static_frame = None
        self._last: LastScan | None = None

        self._setup_source()

        self._preview: _PreviewServer | None = None
        preview_port = int(self.get_parameter("preview_port").value)
        if preview_port > 0:
            self._preview = _PreviewServer(preview_port, self.get_logger())

        # 백엔드가 세션을 만들어 주면 로봇 내부에 SessionStart 를 흘려 다른 노드
        # (guide_manager 등)가 session_id 와 진료 순서를 받도록 한다.
        self._session_pub = self.create_publisher(SessionStart, "~/session_start", 10)

        period = 1.0 / max(fps, 0.1)
        self._timer = self.create_timer(period, self._tick)
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
        frame = self._read_frame()
        if frame is None:
            return

        # QR 만 디코드한다. 다른 심볼로지(DataBar 등)까지 돌리면 라이브 카메라
        # 노이즈에서 zbar 가 시끄러운 경고를 쏟고 CPU 도 낭비한다.
        codes = pyzbar.decode(frame, symbols=[ZBarSymbol.QRCODE])
        if self._preview is not None:
            self._preview.update(frame, codes)

        for code in codes:
            value = code.data.decode("utf-8", errors="replace").strip()
            if not value:
                continue
            if self._is_debounced(value):
                continue
            self._last = LastScan(value=value, ts=time.monotonic())
            self._post_scan(value)
            return

    def _is_debounced(self, value: str) -> bool:
        if self._last is None:
            return False
        if self._last.value != value:
            return False
        return (time.monotonic() - self._last.ts) < self.debounce_seconds

    def _post_scan(self, patient_id: str) -> None:
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
            self._publish_session_start(response)
        elif response.status_code == 404:
            self.get_logger().warn(f"QR 인식 실패 (환자 없음): {patient_id}")
        else:
            self.get_logger().error(
                f"백엔드 응답 이상: {response.status_code} {response.text[:200]}"
            )

    def _publish_session_start(self, response: requests.Response) -> None:
        """POST /qr/scan 응답을 파싱해 SessionStart 로 흘린다.

        응답 스키마는 backend/app/schemas.py 의 TodaySchedule 와 1:1 이다.
        파싱이 실패해도 노드는 계속 살아 있어야 한다 — 재스캔으로 회복 가능하고,
        예외로 죽으면 카메라 리소스까지 재초기화해야 해서 손해가 크다.
        """
        try:
            body = response.json()
            steps = body.get("steps") or []
            msg = SessionStart()
            msg.session_id = int(body.get("session_id") or 0)
            msg.patient_id = str(body.get("patient", {}).get("patient_id") or "")
            msg.current_step_order = int(body.get("current_step_order") or 0)
            # 백엔드는 이미 step_order 오름차순으로 보내지만, 명시적으로 정렬한다.
            msg.visit_names = [
                str(s.get("visit_name") or "")
                for s in sorted(steps, key=lambda s: s.get("step_order", 0))
            ]
        except (ValueError, TypeError, AttributeError) as exc:
            self.get_logger().error(f"세션 응답 파싱 실패: {exc}")
            return

        self._session_pub.publish(msg)
        self.get_logger().info(
            f"session_start 발행: session_id={msg.session_id} "
            f"patient={msg.patient_id} step={msg.current_step_order}/"
            f"{len(msg.visit_names)} visits={msg.visit_names}"
        )

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
