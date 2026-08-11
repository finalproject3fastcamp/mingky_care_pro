"""로봇 이벤트를 관제 서버로 전달하는 게이트웨이.

    /events 토픽 → 로컬 큐 → HTTP POST /events        → 성공 시 큐에서 제거
    (주기)       → 큐 없음 → HTTP POST .../heartbeat  → 실패하면 버림

상태머신(mingky_guide_manager)과 분리한 이유는, 서버가 느리거나 죽었을 때
재시도 루프가 상태 전이를 지연시키면 안 되기 때문이다. 로봇이 서버 때문에
멈추는 구조는 피한다.

같은 이유로 HTTP 호출을 ROS 콜백 안에서 하지 않는다. 콜백은 큐에 쓰기만
하고(수 ms), 별도 스레드가 전송한다.

이벤트와 heartbeat 는 요구가 정반대다.

    이벤트     하나도 잃으면 안 된다 → 큐에 쌓고 될 때까지 재전송
    heartbeat  늦게 도착하면 거짓말이 된다 → 큐를 안 타고 실패하면 버림

그래서 두 경로를 한 노드에 두되 스레드와 큐를 분리했다. 노드를 더 늘리지
않은 것은, 노드가 늘면 그 노드가 살아있는지도 감시해야 하기 때문이다.
"""

import json
import math
import threading
import time
from datetime import datetime, timezone

import rclpy
import requests
from rclpy.node import Node
from std_msgs.msg import Float32, String

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
        # guide_manager 와 같은 기본값을 쓴다. heartbeat 는 이벤트와 달리
        # 메시지에 robot_id 가 실려 오지 않으므로 노드가 알고 있어야 한다.
        self.declare_parameter("robot_id", "pinky-01")
        # 0 이면 heartbeat 를 보내지 않는다. 백엔드 기본 판정이 15초라
        # 5초 주기면 3회 연속 유실에 두절로 잡힌다.
        self.declare_parameter("heartbeat_interval_sec", 5.0)
        # 주기보다 짧아야 한다. 길면 응답을 기다리는 사이 다음 차례가 밀린다.
        self.declare_parameter("heartbeat_timeout_sec", 2.0)
        # 배터리 추이는 이벤트가 아니라 별도 로그에 저빈도로 저장한다.
        # 0 이면 전송하지 않는다. 첫 표본은 즉시, 이후에는 이 주기로 보낸다.
        self.declare_parameter("battery_interval_sec", 120.0)
        # 관제에서 내려오는 명령을 물어보는 주기. 0 이면 물어보지 않는다.
        # 서버가 밀어넣지 않고 로봇이 물어보는 이유는, 로봇이 NAT 안에 있거나
        # 네트워크를 옮겨도 나가는 연결만 만들면 되기 때문이다.
        self.declare_parameter("order_interval_sec", 3.0)
        self.declare_parameter("order_timeout_sec", 2.0)
        # 롱폴링. 명령이 없어도 서버가 이 시간까지 응답을 붙들고 있다가,
        # 걸리는 순간 돌려준다. 0 이면 예전처럼 즉시 응답을 받고 주기마다
        # 다시 묻는다.
        #
        # 폴링 주기(3초)면 명령이 걸린 뒤 로봇이 받기까지 평균 1.5초가 그냥
        # 흐른다. 왕복이 200ms 인 것에 비하면 대기가 지연의 대부분이었다.
        # 서버 상한이 50초이고, 중간 프록시가 먼저 끊지 않도록 그보다 짧게 둔다.
        self.declare_parameter("order_wait_sec", 25.0)

        base = self.get_parameter("backend_url").value.rstrip("/")
        self.url = base + "/events"
        self.robot_id = self.get_parameter("robot_id").value
        self.heartbeat_url = f"{base}/robots/{self.robot_id}/heartbeat"
        self.heartbeat_interval = float(
            self.get_parameter("heartbeat_interval_sec").value)
        self.heartbeat_timeout = float(
            self.get_parameter("heartbeat_timeout_sec").value)
        self.battery_url = f"{base}/robots/{self.robot_id}/battery"
        self.battery_interval = float(
            self.get_parameter("battery_interval_sec").value)
        self.orders_url = f"{base}/robots/{self.robot_id}/orders"
        self.order_interval = float(self.get_parameter("order_interval_sec").value)
        self.order_timeout = float(self.get_parameter("order_timeout_sec").value)
        self.order_wait = float(self.get_parameter("order_wait_sec").value)
        self.batch_size = int(self.get_parameter("batch_size").value)
        self.flush_interval = float(self.get_parameter("flush_interval_sec").value)
        self.timeout = float(self.get_parameter("http_timeout_sec").value)
        self.max_backoff = float(self.get_parameter("max_backoff_sec").value)

        self.queue = QueueStore(
            self.get_parameter("queue_path").value,
            int(self.get_parameter("max_queue_rows").value))

        self.create_subscription(Event, "/events", self._on_event, 100)
        self.create_subscription(
            Float32, "/battery/voltage", self._on_battery_voltage, 10)
        self.create_subscription(
            Float32, "/battery/percent", self._on_battery_percent, 10)

        self._battery_lock = threading.Lock()
        self._battery_voltage = None
        self._battery_percent = None
        self._battery_wake = threading.Event()

        # 관제 명령을 각 책임 노드에 넘기는 통로. 환자 세션은 guide_manager,
        # 비임상 Waypoint 시험은 navigation_manager가 받는다.
        self._order_pubs = {
            "goto": self.create_publisher(
                String, "/navigation_manager/goto", 10),
            "goto_pose": self.create_publisher(
                String, "/navigation_manager/goto_pose", 10),
            "start_session": self.create_publisher(
                String, "/guide_manager/start_session", 10),
            "start_guidance": self.create_publisher(
                String, "/guide_manager/start_guidance", 10),
            # 모드는 mode_manager 가 정본을 들고 있다. 여기서는 요청만 넘긴다.
            "set_mode": self.create_publisher(String, "/mode/set", 10),
        }

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._sender = threading.Thread(target=self._send_loop, daemon=True)
        self._sender.start()

        self._heartbeat = None
        if self.heartbeat_interval > 0:
            self._heartbeat = threading.Thread(
                target=self._heartbeat_loop, daemon=True)
            self._heartbeat.start()

        self._battery = None
        if self.battery_interval > 0:
            self._battery = threading.Thread(
                target=self._battery_loop, daemon=True)
            self._battery.start()
        self._orders = None
        if self.order_interval > 0:
            self._orders = threading.Thread(target=self._order_loop, daemon=True)
            self._orders.start()

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

    def _on_battery_voltage(self, msg: Float32) -> None:
        if not math.isfinite(msg.data) or not 0 <= msg.data <= 12:
            self.get_logger().warn(f"유효하지 않은 배터리 전압 무시: {msg.data}")
            return
        with self._battery_lock:
            self._battery_voltage = float(msg.data)
        self._battery_wake.set()

    def _on_battery_percent(self, msg: Float32) -> None:
        if not math.isfinite(msg.data):
            self.get_logger().warn(f"유효하지 않은 배터리 퍼센트 무시: {msg.data}")
            return
        with self._battery_lock:
            self._battery_percent = int(round(max(0.0, min(100.0, msg.data))))
        self._battery_wake.set()

    # ------------------------------------------------------------------ 전송

    def _send_loop(self) -> None:
        # heartbeat·orders·battery 는 이미 Session 을 쓰는데 이 경로만 빠져
        # 있었다. 매 배치마다 TCP + TLS 를 새로 맺으면 왕복이 세 번이다 —
        # 실측으로 새 연결 287ms, 재사용 92ms 였다. 무선 구간에서 특히 크다.
        #
        # 다른 스레드와 공유하지 않는다. requests.Session 은 스레드 안전이
        # 아니고, 여기는 백오프로 몇 초씩 묶이는 경로라 heartbeat 가 그 뒤에
        # 줄 서면 생존 신호가 늦는다.
        session = requests.Session()
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

            if self._post(session, bodies):
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

    def _post(self, session: requests.Session, bodies: list[dict]) -> bool:
        """성공하면 True. True 를 돌려준 건만 큐에서 지운다."""
        try:
            response = session.post(self.url, json=bodies, timeout=self.timeout)
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

    # -------------------------------------------------------------- heartbeat

    def _heartbeat_loop(self) -> None:
        """생존 신호. 큐를 타지 않고 곧바로 보내고, 실패하면 버린다.

        큐에 넣으면 안 되는 이유가 이 노드의 존재 이유와 정반대다. 이벤트는
        두절 동안 쌓아뒀다 나중에 보내야 기록이 안 사라진다. heartbeat 는
        그러면 안 된다 — 복구 순간 "10분 전 나 살아있었음" 이 한꺼번에
        도착하면 서버가 두절을 판정할 수 없다.

        재시도도 하지 않는다. 다음 주기가 곧 재시도다.

        별도 스레드인 이유는 ROS 타이머로 돌리면 HTTP 대기 동안 실행기가
        묶여 _on_event 콜백이 밀리기 때문이다. 전송 스레드와도 분리한다 —
        큐가 밀려 백오프 중일 때도 생존 신호는 계속 나가야 한다.
        """
        # 커넥션을 재사용한다. 5초마다 TCP 핸드셰이크를 다시 하면 무선
        # 구간에서 낭비가 크다. 전송 스레드와 Session 을 공유하지 않는다.
        session = requests.Session()
        self.get_logger().info(
            f"heartbeat 시작 ({self.heartbeat_url}, {self.heartbeat_interval:.0f}초 주기)")

        failing = False
        while not self._stop.wait(self.heartbeat_interval):
            try:
                response = session.post(
                    self.heartbeat_url, timeout=self.heartbeat_timeout)
            except requests.RequestException as exc:
                if not failing:
                    # 두절 중에는 매 주기 찍히면 로그가 도배된다. 전이할 때만.
                    self.get_logger().warn(f"heartbeat 실패: {exc}")
                    failing = True
                continue

            if response.status_code == 404:
                # robot_id 오타이거나 robots 테이블에 없는 로봇이다.
                # 재시도해도 결과가 같으므로 크게 남긴다.
                self.get_logger().error(
                    f"heartbeat 거부: {self.robot_id} 가 서버에 등록되지 않았습니다. "
                    "robot_id 파라미터와 robots 시드를 확인하세요.")
                failing = True
                continue

            if not response.ok:
                if not failing:
                    self.get_logger().warn(
                        f"heartbeat 실패: HTTP {response.status_code}")
                    failing = True
                continue

            if failing:
                self.get_logger().info("heartbeat 복구")
                failing = False

        session.close()

    # --------------------------------------------------------------- battery

    def _battery_payload(self) -> dict | None:
        with self._battery_lock:
            voltage = self._battery_voltage
            percent = self._battery_percent
        if voltage is None and percent is None:
            return None
        return {"voltage": voltage, "battery_percent": percent}

    def _battery_loop(self) -> None:
        """첫 표본은 즉시, 이후 최신 표본을 저빈도로 전송한다.

        전송 실패를 큐에 쌓지 않는다. 배터리는 최신값만 의미가 있고 다음 주기에
        다시 보내면 되므로, 과거 표본을 몰아서 보내는 것이 오히려 화면을 속인다.
        """
        session = requests.Session()
        last_sent = None
        while not self._stop.is_set():
            timeout = None
            if last_sent is not None:
                elapsed = time.monotonic() - last_sent
                timeout = max(0.0, self.battery_interval - elapsed)
            self._battery_wake.wait(timeout=timeout)
            self._battery_wake.clear()
            if self._stop.is_set():
                break
            if (last_sent is not None
                    and time.monotonic() - last_sent < self.battery_interval):
                continue

            payload = self._battery_payload()
            if payload is None:
                continue
            last_sent = time.monotonic()
            try:
                response = session.post(
                    self.battery_url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                self.get_logger().warn(f"배터리 표본 전송 실패: {exc}")
                continue
            if not response.ok:
                self.get_logger().warn(
                    f"배터리 표본 거부: HTTP {response.status_code}")
        session.close()

    # ----------------------------------------------------------------- 명령 수신

    def _order_loop(self) -> None:
        """관제에서 내려온 명령을 물어보고 상태머신에 넘긴다.

            GET  .../orders/next            대기 명령 조회 (서버가 지우지 않음)
            발행  guide_manager 토픽
            POST .../orders/{id}/ack        받았음을 알림. 서버가 여기서 지움

        조회와 삭제를 나눈 이유는 무선 때문이다. 응답이 유실되면 로봇은 못
        받았는데 서버는 보냈다고 믿게 되고 명령이 증발한다. ack 를 보낸
        것만 지우면 최악의 경우가 '같은 명령을 두 번 받는 것' 이 된다.

        같은 order_id 를 두 번 처리하지 않도록 마지막 처리분을 기억한다.
        ack 가 유실되면 다음 폴링에 같은 명령이 다시 오기 때문이다.
        """
        session = requests.Session()
        if self.order_wait > 0:
            # 붙들려 있는 동안 읽기 타임아웃이 나면 안 된다. 서버가 기다리는
            # 시간보다 넉넉히 길게 준다.
            read_timeout = self.order_wait + 10.0
            params = {"wait": self.order_wait}
            self.get_logger().info(
                f"명령 수신 시작 ({self.orders_url}/next, "
                f"롱폴링 {self.order_wait:.0f}초)")
        else:
            read_timeout = self.order_timeout
            params = None
            self.get_logger().info(
                f"명령 수신 시작 ({self.orders_url}/next, "
                f"{self.order_interval:.0f}초 주기)")

        last_id = None
        failing = False
        self._warned_no_longpoll = False
        while not self._stop.is_set():
            # 롱폴링일 때는 서버가 대기를 대신하므로 여기서 또 쉬지 않는다.
            # 다만 요청이 곧바로 실패하는 상황(서버 다운)에서 재요청을
            # 퍼붓지 않도록, 실패했을 때만 주기만큼 쉰다.
            asked_at = time.monotonic()
            try:
                response = session.get(
                    f"{self.orders_url}/next", params=params,
                    timeout=(self.order_timeout, read_timeout))
                response.raise_for_status()
                order = response.json()
            except (requests.RequestException, ValueError) as exc:
                if not failing:
                    self.get_logger().warn(f"명령 조회 실패: {exc}")
                    failing = True
                if self._stop.wait(self.order_interval):
                    break
                continue

            if failing:
                self.get_logger().info("명령 조회 복구")
                failing = False

            if not order:
                # 롱폴링이면 대기 시간을 채우고 빈손으로 돌아온 것이라 곧바로
                # 다시 건다. 아니면 다음 주기까지 쉰다.
                #
                # 단, 롱폴링인데 즉시 돌아왔다면 서버가 wait 를 모르는
                # 구버전이다(모르는 질의 인자는 그냥 무시된다). 그대로 두면
                # 쉬지 않고 재요청하는 뜨거운 루프가 되므로, 응답이 너무
                # 빨리 오면 폴링처럼 쉰다. 배포 순서가 어긋나도 안전하도록
                # 서버 버전을 묻지 않고 걸린 시간으로 판단한다.
                too_fast = (self.order_wait > 0
                            and time.monotonic() - asked_at < self.order_wait / 2)
                if too_fast and not self._warned_no_longpoll:
                    self.get_logger().warn(
                        "서버가 롱폴링을 지원하지 않는 것 같다 "
                        f"(대기 요청 {self.order_wait:.0f}초, 즉시 응답). "
                        f"{self.order_interval:.0f}초 주기 폴링으로 동작한다.")
                    self._warned_no_longpoll = True
                if (self.order_wait <= 0 or too_fast) and self._stop.wait(
                        self.order_interval):
                    break
                continue

            order_id = order.get("order_id")
            if order_id != last_id:
                if self._dispatch(order):
                    last_id = order_id
                else:
                    # 모르는 명령은 ack 하지 않는다. 서버에 남겨두면
                    # 대시보드에서 미처리 상태가 보인다.
                    continue

            # ack 는 매번 시도한다. 앞서 유실됐을 수 있다.
            acked = True
            try:
                session.post(f"{self.orders_url}/{order_id}/ack",
                             json={"order_id": order_id},
                             timeout=self.order_timeout)
            except requests.RequestException:
                # 실패해도 버린다. 다음 폴링에 같은 명령이 오면 다시 ack 한다.
                acked = False

            # ack 가 안 됐으면 서버에 명령이 그대로 남아, 다음 요청이 기다리지
            # 않고 같은 것을 즉시 돌려준다. 롱폴링에는 주기적인 쉼이 없으므로
            # 그대로 두면 초당 수십 번을 재요청하는 뜨거운 루프가 된다.
            # 폴링 방식일 때는 루프 앞머리에서 이미 쉬므로 해당 없다.
            if self.order_wait > 0 and not acked:
                if self._stop.wait(self.order_interval):
                    break

        session.close()

    def _dispatch(self, order: dict) -> bool:
        """명령을 해당 토픽으로 발행한다. 처리했으면 True.

        guide_manager 의 기존 수동 트리거 토픽을 그대로 쓴다. 상태머신은
        명령이 사람에게서 왔는지 관제에서 왔는지 알 필요가 없다.
        """
        command = order.get("command")
        argument = order.get("argument", "")
        publisher = self._order_pubs.get(command)
        if publisher is None:
            self.get_logger().error(
                f"모르는 명령: {command} (order_id={order.get('order_id')})")
            return False

        publisher.publish(String(data=argument))
        self.get_logger().info(f"명령 실행: {command}({argument})")
        return True

    # ------------------------------------------------------------------ 종료

    def destroy_node(self):
        self._stop.set()
        self._wake.set()
        self._battery_wake.set()
        self._sender.join(timeout=2.0)
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=2.0)
        if self._battery is not None:
            self._battery.join(timeout=2.0)
        if self._orders is not None:
            self._orders.join(timeout=2.0)
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
