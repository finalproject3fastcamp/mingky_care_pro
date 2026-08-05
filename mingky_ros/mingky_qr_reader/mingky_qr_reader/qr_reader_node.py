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
        self.declare_parameter("backend_url", "http://localhost:8000")
        # 스캔한 로봇. guidance_sessions.robot_id 가 NOT NULL 이라 백엔드가 필수로 받는다.
        self.declare_parameter("robot_id", "")
        # 도킹 마커. 선택값이라 -1 을 미지정으로 보고 payload 에서 뺀다.
        self.declare_parameter("marker_id", -1)
        self.declare_parameter("fps", 10.0)
        self.declare_parameter("debounce_seconds", 5.0)
        self.declare_parameter("http_timeout_seconds", 3.0)

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
            picam2 = Picamera2()
            # RGB888 3채널. pyzbar 는 채널 순서와 무관하게 휘도로 디코드한다.
            picam2.configure(picam2.create_preview_configuration(
                main={"format": "RGB888", "size": (width, height)}
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
        for code in pyzbar.decode(frame, symbols=[ZBarSymbol.QRCODE]):
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
        elif response.status_code == 404:
            self.get_logger().warn(f"QR 인식 실패 (환자 없음): {patient_id}")
        else:
            self.get_logger().error(
                f"백엔드 응답 이상: {response.status_code} {response.text[:200]}"
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
