"""로봇 이벤트를 관제 서버로 전달하는 게이트웨이.

    /events 토픽 → 로컬 큐 → HTTP POST /events → 성공 시 큐에서 제거

상태머신(mingky_guide_manager)과 분리한 이유는, 서버가 느리거나 죽었을 때
재시도 루프가 상태 전이를 지연시키면 안 되기 때문이다. 로봇이 서버 때문에
멈추는 구조는 피한다.

같은 이유로 HTTP 호출을 ROS 콜백 안에서 하지 않는다. 콜백은 큐에 쓰기만
하고(수 ms), 별도 스레드가 전송한다.
"""

import json
import threading
import time
from datetime import datetime, timezone

import rclpy
import requests
from rclpy.node import Node

from mingky_interfaces.msg import Event

from .queue_store import QueueStore

_LEVEL_NAME = {
    Event.LEVEL_INFO: "info",
    Event.LEVEL_WARNING: "warning",
    Event.LEVEL_ERROR: "error",
}


def _iso(stamp) -> str:
    """builtin_interfaces/Time → ISO8601 UTC.

    발행 측이 벽시계로 채운 값이다. 여기서 다시 시각을 찍지 않는다.
    """
    seconds = stamp.sec + stamp.nanosec / 1e9
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


class EventGateway(Node):

    def __init__(self):
        super().__init__("event_gateway")

        self.declare_parameter("backend_url", "http://192.168.0.10:8000")
        self.declare_parameter("queue_path", "~/.mingky/event_queue.db")
        self.declare_parameter("batch_size", 100)
        self.declare_parameter("flush_interval_sec", 2.0)
        self.declare_parameter("http_timeout_sec", 5.0)
        self.declare_parameter("max_queue_rows", 50_000)
        self.declare_parameter("max_backoff_sec", 60.0)

        self.url = self.get_parameter("backend_url").value.rstrip("/") + "/events"
        self.batch_size = int(self.get_parameter("batch_size").value)
        self.flush_interval = float(self.get_parameter("flush_interval_sec").value)
        self.timeout = float(self.get_parameter("http_timeout_sec").value)
        self.max_backoff = float(self.get_parameter("max_backoff_sec").value)

        self.queue = QueueStore(
            self.get_parameter("queue_path").value,
            int(self.get_parameter("max_queue_rows").value))

        self.create_subscription(Event, "/events", self._on_event, 100)

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._sender = threading.Thread(target=self._send_loop, daemon=True)
        self._sender.start()

        self.get_logger().info(
            f"event_gateway 시작 (대상={self.url}, 대기 {self.queue.count()}건)")

    # ------------------------------------------------------------------ 수신

    def _on_event(self, msg: Event) -> None:
        """ROS 콜백. 큐에 쓰기만 하고 즉시 반환한다."""
        self.queue.put({
            "event_id": msg.event_id,
            "robot_id": msg.robot_id,
            "session_id": msg.session_id,
            "occurred_at": _iso(msg.occurred_at),
            "level": _LEVEL_NAME.get(msg.level, "info"),
            "event_code": msg.event_code,
            "source_node": msg.source_node,
            "payload": json.loads(msg.payload) if msg.payload else {},
        })
        self._wake.set()

    # ------------------------------------------------------------------ 전송

    def _send_loop(self) -> None:
        backoff = self.flush_interval
        while not self._stop.is_set():
            self._wake.wait(timeout=backoff)
            self._wake.clear()

            batch = self.queue.take(self.batch_size)
            if not batch:
                backoff = self.flush_interval
                continue

            ids = [row_id for row_id, _ in batch]
            bodies = [body for _, body in batch]

            if self._post(bodies):
                self.queue.drop(ids)
                backoff = self.flush_interval
                # 남은 게 있으면 곧바로 다음 배치를 보낸다.
                if self.queue.count():
                    self._wake.set()
            else:
                # 지수 백오프. 서버가 죽어 있는 동안 요청을 퍼붓지 않는다.
                backoff = min(backoff * 2, self.max_backoff)
                self.get_logger().warn(
                    f"전송 실패, {backoff:.0f}초 뒤 재시도 "
                    f"(대기 {self.queue.count()}건)")

    def _post(self, bodies: list[dict]) -> bool:
        """성공하면 True. True 를 돌려준 건만 큐에서 지운다."""
        try:
            response = requests.post(self.url, json=bodies, timeout=self.timeout)
        except requests.RequestException as exc:
            self.get_logger().debug(f"HTTP 실패: {exc}")
            return False

        if response.ok:
            result = response.json()
            if result.get("unknown_codes"):
                self.get_logger().error(
                    f"미등록 event_code: {result['unknown_codes']} "
                    "— config/event_codes.yaml 을 갱신하세요.")
            if result.get("rejected_updates"):
                self.get_logger().warn(
                    f"상태 갱신 거부: {result['rejected_updates']} "
                    "— 로봇 시계를 확인하세요.")
            return True

        # 4xx 는 재시도해도 결과가 같다. 잘못된 이벤트 하나가 큐를 영원히
        # 막으면 그 뒤 이벤트가 전부 못 나간다. 버리되 크게 남긴다.
        if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
            self.get_logger().error(
                f"서버가 거부해 폐기함 ({response.status_code}): "
                f"{response.text[:200]}")
            return True

        self.get_logger().debug(f"서버 오류 {response.status_code}, 재시도")
        return False

    # ------------------------------------------------------------------ 종료

    def destroy_node(self):
        self._stop.set()
        self._wake.set()
        self._sender.join(timeout=2.0)
        self.queue.close()
        return super().destroy_node()


def main():
    rclpy.init()
    node = EventGateway()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
