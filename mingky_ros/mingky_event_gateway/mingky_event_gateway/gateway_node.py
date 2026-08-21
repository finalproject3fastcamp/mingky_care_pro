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
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import rclpy
import requests
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.parameter_client import AsyncParameterClient
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from nav_msgs.msg import OccupancyGrid
from rosidl_runtime_py.utilities import get_message
from std_msgs.msg import Bool, Float32, String
from std_srvs.srv import Trigger

from mingky_interfaces.msg import Event, GuideState, QrObservation

from . import inventory, topic_watch
from .queue_store import QueueStore

_LEVEL_NAME = {
    Event.LEVEL_INFO: "info",
    Event.LEVEL_WARNING: "warning",
    Event.LEVEL_ERROR: "error",
}
SYSTEM_COMMANDS = {
    "system_start": "start",
    "system_stop": "stop",
    "system_restart": "restart",
}
ACTIVE_GUIDE_SESSION_STATES = (
    GuideState.SESSION_CONFIRMED,
    GuideState.SESSION_GUIDING,
    GuideState.SESSION_ARRIVED,
    GuideState.SESSION_IN_ROOM,
)

SEND_OK = "ok"          # 적재됐다. 큐에서 지운다.
SEND_RETRY = "retry"    # 지금 안 될 뿐이다. 큐에 남긴다.
SEND_REJECT = "reject"  # 이 본문으로는 영영 안 된다. 문제 건을 가려낸다.

# 본문이 잘못돼 거부된 상태들. 같은 배치를 다시 보내도 결과가 같다.
CONTENT_REJECT_STATUSES = frozenset({400, 409, 413, 422})

# 4xx 지만 본문 탓이 아니다. 서버가 밀리거나 조르지 말라는 뜻이다.
TRANSIENT_STATUSES = frozenset({408, 429})

# 전송 성공 없이 연속으로 폐기할 수 있는 상한. 이걸 넘으면 나쁜 이벤트가
# 섞인 게 아니라 서버 계약이 어긋난 것이다. 계속 가려내면 큐 전체를 지운다.
MAX_CONSECUTIVE_REJECTS = 5

# 배터리 표본을 이 시간보다 오래 못 받았으면 낡은 것으로 본다.
#
# 발행 주기는 5초다. 세 번 연속 놓칠 때까지는 정상으로 보고, 그 뒤로는
# 보내지 않는다. heartbeat 의 두절 판정(15초)과 같은 계산이다.
BATTERY_STALE_AFTER_SEC = 20.0
# 토픽 주기 감시 대상 (§7.2). `토픽:타입` 형식이고 파라미터로 덮어쓴다.
#
# 타입을 같이 적는 이유는 구독을 만들려면 타입이 필요하기 때문이다. 콜백은
# 시각만 갱신하고 내용은 안 본다.
#
# 상시 발행 여부(그래서 끊기면 알릴지)는 여기서 정하지 않는다 — 서버의
# config/topic_watch.yaml 이 정본이다. 로봇은 사실만 보고한다.
DEFAULT_WATCHED_TOPICS = [
    "/scan:sensor_msgs/msg/LaserScan",
    "/odom:nav_msgs/msg/Odometry",
    "/amcl_pose:geometry_msgs/msg/PoseWithCovarianceStamped",
    "/cmd_vel:geometry_msgs/msg/Twist",
]

# 감시 구독의 QoS. 요구를 가장 낮게 잡아야 한다 — BEST_EFFORT 구독자는
# RELIABLE 발행자(/odom, /amcl_pose)에도 붙지만, RELIABLE 구독자는 라이다처럼
# BEST_EFFORT 로 내보내는 발행자에 **아예 안 붙는다.** 그러면 화면에는 라이다가
# 죽은 것과 똑같이 보인다.
WATCH_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.BEST_EFFORT,
    durability=DurabilityPolicy.VOLATILE,
)

DEFAULT_NAVIGATION_SPEED_MPS = 0.20
MIN_NAVIGATION_SPEED_MPS = 0.05
MAX_NAVIGATION_SPEED_MPS = 0.25
NAVIGATION_SPEED_STEP_MPS = 0.01
LOW_OBSTACLE_MODES = frozenset({"disabled", "sidestep"})
LOW_OBSTACLE_STATES = frozenset({
    "STARTING", "DISABLED", "CLEAR", "UNCERTAIN", "CONFIRMED", "SLOW",
    "FORWARD_BLOCKED", "STALE_RANGE", "STALE_LIDAR",
})


def parse_navigation_speed(argument: str) -> float | None:
    """관제 속도 문자열을 검증한다. UI 값을 신뢰하지 않고 로봇에서 다시 막는다."""
    try:
        speed = float(argument)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(speed):
        return None
    ticks = round(speed / NAVIGATION_SPEED_STEP_MPS)
    normalized = round(ticks * NAVIGATION_SPEED_STEP_MPS, 2)
    if abs(speed - normalized) > 1e-9:
        return None
    if not MIN_NAVIGATION_SPEED_MPS <= normalized <= MAX_NAVIGATION_SPEED_MPS:
        return None
    return normalized


def parse_low_obstacle_mode(argument: str) -> str | None:
    """관제에서 선택 가능한 구현된 전략만 통과시킨다."""
    return argument if argument in LOW_OBSTACLE_MODES else None


def parse_low_obstacle_state(argument: str) -> str | None:
    """융합 노드가 정의한 상태만 heartbeat에 싣는다."""
    normalized = argument.strip().upper()
    return normalized if normalized in LOW_OBSTACLE_STATES else None


def matches_guided_patient(
    observation: QrObservation, session_state: str, patient_id: str,
) -> bool:
    """현재 안내 주행 중인 환자의 QR인지 판정한다."""
    return (
        observation.visible
        and session_state == GuideState.SESSION_GUIDING
        and bool(patient_id)
        and observation.data == patient_id
    )


def parse_patient_follow_status(
    raw: str, session_id: int, patient_id: str,
) -> dict | None:
    """현재 안내 세션에 속한 환자 추적 heartbeat를 관제 계약으로 바꾼다."""
    try:
        payload = json.loads(raw)
        state = str(payload['state'])
        reported_session = int(payload.get('session_id', 0))
        reported_patient = str(payload.get('patient_id') or '')
        source = str(payload.get('source') or 'unknown')
        distance = payload.get('distance')
        distance = None if distance is None else float(distance)
        qr_visible = payload.get('qr_visible', False)
        visual_visible = payload.get('visual_visible', False)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if state not in ('inactive', 'normal', 'slow', 'waiting'):
        return None
    if source not in (
            'none', 'qr', 'visual', 'partial_near', 'acquiring', 'grace',
            'stale', 'unknown'):
        return None
    if distance is not None and (
            not math.isfinite(distance) or distance <= 0.0):
        return None
    if not isinstance(qr_visible, bool) or not isinstance(visual_visible, bool):
        return None
    if state != 'inactive' and (
            reported_session != session_id
            or not patient_id
            or reported_patient != patient_id):
        return None
    return {
        'follow_state': state,
        'follow_distance': distance,
        'follow_source': source,
        'qr_visible': qr_visible,
        'visual_visible': visual_visible,
    }


class HeartbeatFailureGuard:
    """연속 heartbeat 실패가 세션 안전 임계값을 넘었는지 한 번만 알린다."""

    def __init__(self, timeout_sec: float):
        self.timeout_sec = max(0.0, timeout_sec)
        self.failed_since: float | None = None
        self.triggered = False

    def failure(self, now: float, clinical_active: bool) -> bool:
        if not clinical_active:
            self.success()
            return False
        if self.failed_since is None:
            self.failed_since = now
        if self.triggered:
            return False
        if now - self.failed_since < self.timeout_sec:
            return False
        self.triggered = True
        return True

    def success(self) -> None:
        self.failed_since = None
        self.triggered = False


class IntervalGate:
    """반복 작업이 지정 주기보다 자주 실행되지 않게 한다.

    작업할 payload 가 없거나 이전 값과 같아 실제 HTTP 전송을 생략해도 실행
    시각은 소비한다. 그렇지 않으면 남은 대기 시간이 계속 0이 되어 스레드가
    쉬지 않고 반복한다.
    """

    def __init__(self, interval_sec: float, now: float):
        self.interval_sec = max(0.0, interval_sec)
        self.last_attempt = now

    def remaining(self, now: float) -> float:
        return max(0.0, self.interval_sec - (now - self.last_attempt))

    def consume(self, now: float) -> bool:
        if self.remaining(now) > 0:
            return False
        self.last_attempt = now
        return True


def battery_is_stale(sample_at, now, max_age_sec=BATTERY_STALE_AFTER_SEC) -> bool:
    """마지막 표본이 너무 오래됐는가.

    이 판정이 없으면 구독이 끊긴 뒤에도 캐시된 마지막 값을 계속 보낸다.
    서버는 수신 시각으로 recorded_at 을 찍으므로 화면에는 '방금 들어온
    최신값' 으로 보인다 — 9시간 전 값이 25초 전 것으로 표시됐다.

    값과 신선도의 출처가 다르면 신선도 배지는 아무것도 보장하지 못한다.
    낡은 값은 보내지 않는 쪽이 맞다. 화면에 '정보 없음' 이 뜨는 편이
    틀린 숫자가 떠 있는 것보다 낫다.

    한 번도 못 받았으면(None) 낡은 것으로 본다.
    """
    if sample_at is None:
        return True
    return (now - sample_at) > max_age_sec


def send_outcome(status_code: int) -> str:
    """HTTP 상태 하나로 배치를 어떻게 처리할지 정한다.

    4xx 를 전부 '재시도해도 같으니 버린다' 로 묶으면 안 된다. 401·404 는
    본문이 아니라 URL·인증이 틀린 것이라, 버리면 이벤트가 통째로 조용히
    사라진다. 큐가 쌓이더라도 보존하고 사람이 설정을 고쳐야 한다.
    """
    if status_code < 400:
        return SEND_OK
    if status_code in CONTENT_REJECT_STATUSES:
        return SEND_REJECT
    return SEND_RETRY


class RejectBudget:
    """전송 성공 없이 연속으로 폐기할 수 있는 양.

    예산을 주기마다 새로 주면 안 된다. 서버가 배치를 계속 거부하는 동안
    매 주기 몇 건씩 버리게 되고, 백오프 상한이 60초라 시간당 수백 건씩
    영구히 사라진다. 느려질 뿐 결국 큐가 비는 것은 같다.

    그래서 예산은 전송이 **한 번이라도 성공했을 때만** 되돌아온다. 성공은
    서버가 정상이고 거부가 진짜 개별 이벤트 문제라는 뜻이기 때문이다.
    거부만 이어지는 동안에는 채워지지 않으므로, 재시도를 아무리 반복해도
    폐기 총량이 이 상한을 넘지 않는다.
    """

    def __init__(self, limit: int = MAX_CONSECUTIVE_REJECTS):
        self.limit = limit
        self.remaining = limit

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def spend(self) -> None:
        self.remaining -= 1

    def restore(self) -> None:
        self.remaining = self.limit


def isolate_rejected(batch, send, drop, on_reject, budget):
    """거부된 배치에서 실제 문제 건만 가려내 버린다.

    배치를 반씩 갈라 다시 보낸다. 통과하는 절반은 그대로 적재되고, 혼자
    남아서도 거부되는 건만 버린다. 요청이 log2(n) 번 더 나가지만 거부가
    일어난 배치에서만이다.

    한 건이 스키마 검증에 걸렸다고 같이 묶인 99건을 버리면, 하필 그때
    비상정지 이력이 섞여 있어도 아무 흔적이 남지 않는다.

    일시적 실패를 만나면 거기서 멈춘다. 남은 건은 큐에 두고 다음 주기에
    다시 본다 — 서버가 흔들리는 동안 멀쩡한 이벤트를 버리면 안 된다.

    가르는 도중 절반이 통과하면 예산을 되돌린다. 그 절반이 들어갔다는 것은
    서버가 살아 있다는 뜻이라, 나쁜 이벤트가 여럿 섞인 배치도 끝까지
    가려낼 수 있다. 반대로 아무것도 안 통과한 채 예산이 바닥나면 계약이
    어긋난 것이므로 거기서 멈추고 나머지는 큐에 남긴다.

    돌려주는 값은 (큐가 줄었는가, 예산을 소진했는가) 다.
    """
    progressed = False
    exhausted = False

    def walk(part) -> bool:
        """이 조각을 끝까지 처리했으면 True. 멈춰야 하면 False."""
        nonlocal progressed, exhausted

        if len(part) == 1:
            if budget.exhausted:
                exhausted = True
                return False
            row_id, body = part[0]
            on_reject(body)
            drop([row_id])
            budget.spend()
            progressed = True
            return True

        mid = len(part) // 2
        for half in (part[:mid], part[mid:]):
            outcome = send([body for _, body in half])
            if outcome == SEND_OK:
                drop([row_id for row_id, _ in half])
                # 서버가 살아 있다. 남은 거부는 개별 이벤트 문제로 본다.
                budget.restore()
                progressed = True
            elif outcome == SEND_REJECT:
                if not walk(half):
                    return False
            else:
                return False
        return True

    walk(batch)
    return progressed, exhausted


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
        # 서버의 세션 장애 취소 임계값과 맞춘다. 이 시간 동안 관제 연결이
        # 계속 끊기면 로봇 안에서도 안내를 종료하고 Nav2를 정지한다.
        self.declare_parameter("heartbeat_session_cancel_after_sec", 30.0)
        # 배터리 추이는 이벤트가 아니라 별도 로그에 저빈도로 저장한다.
        # 0 이면 전송하지 않는다. 첫 표본은 즉시, 이후에는 이 주기로 보낸다.
        self.declare_parameter("battery_interval_sec", 120.0)
        # 후방 QR 거리는 DB 이력이 아니라 관제에 보여줄 현재값이다. 최신 관측만
        # 짧은 주기로 보내며 실패분은 쌓지 않는다.
        self.declare_parameter("qr_observation_interval_sec", 0.5)
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
        # guide_manager와 같은 시작값을 알아야 heartbeat가 관제 화면에 실제
        # 적용 상태를 보고할 수 있다. 운영 환경에서는 robot.env 값을 넘긴다.
        self.declare_parameter("low_obstacle_mode", "disabled")
        # 인벤토리(실행 중인 코드 버전·노드 목록) 수집 주기. 0 이면 끈다.
        # heartbeat 와 달리 느려도 된다 — 노드 목록과 커밋은 몇 시간에 한 번
        # 바뀐다. 내용이 바뀌었을 때만 전송하므로 이 주기는 '확인 주기' 다.
        self.declare_parameter("inventory_interval_sec", 30.0)
        # 주기를 감시할 토픽 (§7.2). 빈 목록이면 감시하지 않는다 — 팔처럼
        # ROS 가 없는 구성이나, 토픽 구성이 다른 로봇을 위해 열어둔다.
        self.declare_parameter("watched_topics", DEFAULT_WATCHED_TOPICS)
        # 비우면 호환용 Python 직접 구독을 사용한다. 운영에서는 C++ 모니터가
        # 고주기 메시지를 집계해 이 저주기 토픽으로 전달한다.
        self.declare_parameter(
            "topic_health_topic", "/mingky/topic_health")
        # 형상 보고용 맵 이름 (§7.2 형상 패널). 지문은 /map 에서 직접 뜨고
        # 이 값은 사람이 읽을 이름일 뿐이다 — 이름이 같아도 내용이 다를 수 있고,
        # 판정은 지문으로 한다.
        self.declare_parameter("map_name", "")

        base = self.get_parameter("backend_url").value.rstrip("/")
        self.url = base + "/events"
        self.robot_id = self.get_parameter("robot_id").value
        self.heartbeat_url = f"{base}/robots/{self.robot_id}/heartbeat"
        self.heartbeat_interval = float(
            self.get_parameter("heartbeat_interval_sec").value)
        self.heartbeat_timeout = float(
            self.get_parameter("heartbeat_timeout_sec").value)
        self.heartbeat_session_cancel_after = float(
            self.get_parameter("heartbeat_session_cancel_after_sec").value)
        self.battery_url = f"{base}/robots/{self.robot_id}/battery"
        self.battery_interval = float(
            self.get_parameter("battery_interval_sec").value)
        self.qr_observation_url = (
            f"{base}/robots/{self.robot_id}/qr-observation")
        self.qr_observation_interval = float(
            self.get_parameter("qr_observation_interval_sec").value)
        self.inventory_url = f"{base}/robots/{self.robot_id}/inventory"
        self.inventory_interval = float(
            self.get_parameter("inventory_interval_sec").value)
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
        self.create_subscription(
            QrObservation, "/rear_qr/observation",
            self._on_qr_observation, 10)
        self.create_subscription(
            String, "/person_follow/state",
            self._on_patient_follow_state, 10)
        state_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(
            GuideState, "/guide_manager/state", self._on_guide_state, state_qos)
        self.create_subscription(
            Bool, "/auto_localize/active", self._on_localization_active, state_qos)
        self.create_subscription(
            Bool, "/fire_evac/alarm_active", self._on_fire_alarm_active, state_qos)
        self.create_subscription(String, "/mode", self._on_mode, state_qos)
        self.create_subscription(
            String, "/low_obstacle/state", self._on_low_obstacle_state,
            state_qos)

        # 토픽 주기 감시 (§7.2). 구독 콜백은 시각만 갱신하고 메시지는 안 본다.
        watched, malformed = topic_watch.parse_watch_spec(
            self.get_parameter("watched_topics").value)
        if malformed:
            # 조용히 빠지면 감시가 없는 채로 화면이 정상으로 보인다.
            self.get_logger().error(
                f"watched_topics 형식 오류 (토픽:타입) — 감시에서 빠집니다: {malformed}")
        self._topic_health_lock = threading.Lock()
        self._topic_health_snapshot: dict[str, dict] = {}
        health_topic = str(
            self.get_parameter("topic_health_topic").value).strip()
        self._topic_ages = None
        if health_topic:
            self.create_subscription(
                DiagnosticArray, health_topic, self._on_topic_health, 1)
        else:
            self._topic_ages = topic_watch.TopicAges(
                [name for name, _ in watched])
            self._subscribe_watched(watched)

        # 지금 돌고 있는 맵 (§7.2 형상 패널). map_server 가 latch 해두므로
        # 구독하는 즉시 한 번 오고, 그 뒤로는 맵을 다시 실을 때만 온다.
        # 디스크의 yaml 을 읽지 않는 이유는, 그건 런치 인자가 가리키는 파일이지
        # map_server 가 실제로 실은 것이 아니기 때문이다.
        self._map_name = self.get_parameter("map_name").value or None
        self._map_hash = None
        self.create_subscription(
            OccupancyGrid, "/map", self._on_map,
            QoSProfile(depth=1,
                       reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL))

        self._battery_lock = threading.Lock()
        self._battery_voltage = None
        self._battery_percent = None
        # 마지막으로 표본을 받은 시각. 값과 함께 봐야 한다 — 값만 들고 있으면
        # 구독이 끊겨도 마지막 값을 영원히 재전송하게 된다.
        self._battery_at = None
        # 끊김 로그를 매 주기 찍지 않기 위한 전이 표시.
        self._battery_stale_logged = False
        self._battery_wake = threading.Event()
        self._qr_lock = threading.Lock()
        self._qr_observation = None
        self._qr_wake = threading.Event()
        self._clinical_active = False
        self._guide_robot_state = GuideState.ROBOT_IDLE
        self._guide_session_state = GuideState.SESSION_NONE
        self._guide_session_id = 0
        self._guide_patient_id = ''
        self._returning_to_dock = False
        self._localization_active = False
        self._fire_alarm_active = None
        self._mode = None
        # Nav2 설정의 기본 목표속도다. 통합 시스템이 재시작되면 동적 설정도
        # 이 값으로 돌아가므로 게이트웨이 상태 역시 함께 초기화한다.
        self._navigation_speed_mps = DEFAULT_NAVIGATION_SPEED_MPS
        configured_low_obstacle_mode = str(
            self.get_parameter("low_obstacle_mode").value)
        self._default_low_obstacle_mode = (
            parse_low_obstacle_mode(configured_low_obstacle_mode) or "disabled")
        self._low_obstacle_mode = self._default_low_obstacle_mode
        self._low_obstacle_state = None

        # 인벤토리 수집 상태.
        #
        # 그래프 조회는 ROS 타이머(실행기 스레드)에서 하고, /proc·git 은
        # 별도 스레드에서 한다. rclpy 그래프 API 를 실행기 밖에서 부르지
        # 않기 위해서다. 대신 그래프 결과만 락으로 건네준다.
        self._inventory_lock = threading.Lock()
        self._graph_snapshot: list[tuple[str, str]] = []
        self._inventory_hash: str | None = None
        self._cpu_total_pct: float | None = None
        self._max_node_cpu_pct: float | None = None
        self._max_node_cpu_name: str | None = None
        # 서버가 "그 해시 모른다" 고 답하면 다음 주기에 다시 보낸다.
        self._need_inventory = threading.Event()
        self._git_cache = inventory.GitCache()

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
        self._session_cancel_pub = self.create_publisher(
            String, "/guide_manager/cancel_session", 10)
        self._navigation_cancel_pub = self.create_publisher(
            Bool, "/navigation_manager/cancel", 10)
        self._communication_stop_pub = self.create_publisher(
            Bool, "/emergency_stop/communication", 10)
        self._localize_client = self.create_client(
            Trigger, "/auto_localize/trigger")
        self._fire_alarm_reset_client = self.create_client(
            Trigger, "/fire_evac/reset_alarm")
        self._fire_evac_cancel_client = self.create_client(
            Trigger, "/fire_evac/cancel")
        self._controller_parameters = AsyncParameterClient(
            self, "controller_server")
        self._guide_parameters = AsyncParameterClient(self, "guide_manager")
        self._navigation_parameters = AsyncParameterClient(
            self, "navigation_manager")

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
        self._qr_sender = None
        if self.qr_observation_interval > 0:
            self._qr_sender = threading.Thread(
                target=self._qr_observation_loop, daemon=True)
            self._qr_sender.start()
        self._orders = None
        if self.order_interval > 0:
            self._orders = threading.Thread(target=self._order_loop, daemon=True)
            self._orders.start()

        self._inventory = None
        if self.inventory_interval > 0:
            # 그래프 조회만 실행기 스레드에서. 로컬 캐시를 읽는 것이라 빠르고
            # I/O 가 없어 콜백을 붙들지 않는다.
            self.create_timer(self.inventory_interval, self._sample_graph)
            self._inventory = threading.Thread(
                target=self._inventory_loop, daemon=True)
            self._inventory.start()

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
            self._battery_at = time.monotonic()
        self._battery_wake.set()

    def _on_battery_percent(self, msg: Float32) -> None:
        if not math.isfinite(msg.data):
            self.get_logger().warn(f"유효하지 않은 배터리 퍼센트 무시: {msg.data}")
            return
        with self._battery_lock:
            self._battery_percent = int(round(max(0.0, min(100.0, msg.data))))
            self._battery_at = time.monotonic()
        self._battery_wake.set()

    def _on_qr_observation(self, msg: QrObservation) -> None:
        matches_patient = matches_guided_patient(
            msg, self._guide_session_state, self._guide_patient_id)
        distance = float(msg.distance) if matches_patient else None
        if matches_patient and (not math.isfinite(distance) or distance <= 0):
            self.get_logger().warn(f"유효하지 않은 QR 거리 무시: {distance}")
            return
        with self._qr_lock:
            current = dict(self._qr_observation or {
                "visible": False,
                "distance": None,
                "follow_state": None,
                "follow_distance": None,
                "follow_source": None,
                "qr_visible": False,
                "visual_visible": False,
            })
            current.update({
                "visible": matches_patient,
                "distance": distance,
            })
            self._qr_observation = current
        self._qr_wake.set()

    def _on_patient_follow_state(self, msg: String) -> None:
        status = parse_patient_follow_status(
            msg.data, self._guide_session_id, self._guide_patient_id)
        if status is None:
            self.get_logger().warn(
                '현재 안내와 맞지 않거나 잘못된 환자 추적 상태를 무시합니다.')
            return
        with self._qr_lock:
            current = dict(self._qr_observation or {
                "visible": False,
                "distance": None,
                "follow_state": None,
                "follow_distance": None,
                "follow_source": None,
                "qr_visible": False,
                "visual_visible": False,
            })
            current.update(status)
            self._qr_observation = current
        self._qr_wake.set()

    def _on_map(self, msg: OccupancyGrid) -> None:
        """맵 지문. 격자 해싱은 몇 ms 지만 맵을 다시 실을 때만 일어난다."""
        digest = inventory.map_hash(msg.info, msg.data)
        with self._inventory_lock:
            changed = digest != self._map_hash
            self._map_hash = digest
        if changed:
            # 4대의 맵이 갈리는 사고는 대개 여기서 시작한다. 언제 바뀌었는지가
            # 로그에 없으면 나중에 되짚을 수 없다.
            self.get_logger().info(f"맵 지문 {digest} ({self._map_name or '이름 없음'})")

    def _on_guide_state(self, msg: GuideState) -> None:
        self._guide_robot_state = msg.robot_state
        self._guide_session_state = msg.session_state
        self._guide_session_id = int(msg.session_id)
        self._guide_patient_id = msg.patient_id
        self._returning_to_dock = bool(msg.returning_to_dock)
        self._clinical_active = (
            msg.session_id > 0
            and msg.session_state in ACTIVE_GUIDE_SESSION_STATES
        )
        # 화면이 자체적으로 대기 시작 시각을 추측하면 새로고침할 때 20초부터
        # 다시 센다. Guide Manager가 계산한 실제 남은 시간을 QR 상태와 함께
        # 보내 0.5초 폴링 화면에서도 같은 시계를 보게 한다.
        with self._qr_lock:
            current = dict(self._qr_observation or {
                "visible": False,
                "distance": None,
                "follow_state": None,
                "follow_distance": None,
                "follow_source": None,
                "qr_visible": False,
                "visual_visible": False,
            })
            remaining = float(msg.patient_wait_remaining_sec)
            current["patient_wait_remaining_sec"] = (
                remaining if remaining >= 0.0 else None)
            self._qr_observation = current
        self._qr_wake.set()

    def _on_localization_active(self, msg: Bool) -> None:
        self._localization_active = bool(msg.data)

    def _on_mode(self, msg: String) -> None:
        self._mode = msg.data.strip().lower()

    def _on_low_obstacle_state(self, msg: String) -> None:
        state = parse_low_obstacle_state(msg.data)
        if state is not None:
            self._low_obstacle_state = state

    def _on_fire_alarm_active(self, msg: Bool) -> None:
        self._fire_alarm_active = bool(msg.data)

    def _system_state(self) -> str:
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "is-active", "mingky-system.service"],
                check=False, capture_output=True, text=True, timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        state = result.stdout.strip()
        return state if state in {
            "active", "activating", "deactivating", "inactive", "failed"
        } else "unknown"

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
        # 루프 밖에 둔다. 주기마다 새로 만들면 예산이 리셋돼, 서버가 계속
        # 거부하는 동안 매 주기 몇 건씩 영구히 사라진다.
        budget = RejectBudget()
        while not self._stop.is_set():
            self._wake.wait(timeout=backoff)
            self._wake.clear()

            batch = self.queue.take(self.batch_size)
            if not batch:
                backoff = self.flush_interval
                continue

            ids = [row_id for row_id, _ in batch]
            bodies = [body for _, body in batch]

            outcome = self._post(session, bodies)
            if outcome == SEND_REJECT and budget.exhausted:
                # 예산이 바닥난 상태다. 갈라 봐야 또 버리기만 하므로 큐를
                # 그대로 두고 재시도한다. 사람이 고칠 때까지 보존한다.
                progressed = False
            elif outcome == SEND_REJECT:
                self.get_logger().warn(
                    f"서버가 배치를 거부했다 ({len(batch)}건). 문제 건을 가려낸다.")
                progressed, exhausted = isolate_rejected(
                    batch,
                    lambda halved: self._post(session, halved),
                    self.queue.drop,
                    self._log_rejected,
                    budget)
                if exhausted:
                    # 전송이 한 번도 성공하지 않은 채 예산을 다 썼다.
                    # 나쁜 이벤트가 섞인 게 아니라 계약이 어긋난 것이므로
                    # 여기서 멈춘다. 남은 큐는 지우지 않는다.
                    self.get_logger().error(
                        f"전송 성공 없이 {budget.limit}건이 거부돼 가려내기를 "
                        "중단한다. 남은 큐는 보존하고 재시도만 한다 — 서버 "
                        "스키마와 event_codes 를 확인하세요 "
                        f"(대기 {self.queue.count()}건).")
                    progressed = False
            else:
                progressed = outcome == SEND_OK
                if progressed:
                    self.queue.drop(ids)
                    # 서버가 정상으로 돌아왔다. 다음 거부는 개별 이벤트
                    # 문제로 보고 다시 가려낼 수 있게 예산을 되돌린다.
                    budget.restore()

            if progressed:
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

    def _post(self, session: requests.Session, bodies: list[dict]) -> str:
        """배치 하나를 보내고 SEND_* 중 하나를 돌려준다.

        SEND_OK 를 돌려준 건만 큐에서 지운다. 폐기 판단은 여기서 하지 않는다 —
        배치 단위로 버리면 문제 없는 이벤트까지 같이 사라지기 때문이다.
        """
        try:
            response = session.post(self.url, json=bodies, timeout=self.timeout)
        except requests.RequestException as exc:
            self.get_logger().debug(f"HTTP 실패: {exc}")
            return SEND_RETRY

        outcome = send_outcome(response.status_code)
        if outcome == SEND_OK:
            result = response.json()
            if result.get("unknown_codes"):
                self.get_logger().error(
                    f"미등록 event_code: {result['unknown_codes']} "
                    "— config/event_codes.yaml 을 갱신하세요.")
            if result.get("rejected_updates"):
                self.get_logger().warn(
                    f"상태 갱신 거부: {result['rejected_updates']} "
                    "— 로봇 시계를 확인하세요.")
            return SEND_OK

        if outcome == SEND_REJECT:
            self.get_logger().debug(
                f"서버가 거부 ({response.status_code}): {response.text[:200]}")
            return SEND_REJECT

        if (400 <= response.status_code < 500
                and response.status_code not in TRANSIENT_STATUSES):
            # URL 이나 인증이 틀렸다. 본문을 아무리 갈라 봐야 통과하지 않으므로
            # 큐가 계속 쌓이지만, 버리면 이벤트가 조용히 전부 사라진다.
            # 큐 상한에 닿기 전에 사람이 봐야 한다. 백오프가 도배를 막는다.
            self.get_logger().error(
                f"서버가 {response.status_code} 로 거부한다 "
                f"(대상={self.url}). backend_url 과 인증 설정을 확인하세요. "
                f"큐는 보존하고 재시도한다: {response.text[:200]}")
            return SEND_RETRY

        self.get_logger().debug(f"서버 오류 {response.status_code}, 재시도")
        return SEND_RETRY

    def _log_rejected(self, body: dict) -> None:
        """혼자 보내도 거부된 한 건. 버리기 전에 식별자를 남긴다."""
        self.get_logger().error(
            "서버가 거부해 폐기함: "
            f"code={body.get('event_code')} robot={body.get('robot_id')} "
            f"event_id={body.get('event_id')} "
            f"occurred_at={body.get('occurred_at')}")

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
        guard = HeartbeatFailureGuard(self.heartbeat_session_cancel_after)

        def failed() -> None:
            nonlocal failing
            if guard.failure(time.monotonic(), self._clinical_active):
                self.get_logger().error(
                    "heartbeat가 계속 실패해 안내 세션을 취소하고 정지합니다.")
                self._session_cancel_pub.publish(String(data="robot_offline"))
                self._communication_stop_pub.publish(Bool(data=True))
            failing = True

        while not self._stop.wait(self.heartbeat_interval):
            try:
                response = session.post(
                    self.heartbeat_url,
                    json=self._heartbeat_payload(),
                    timeout=self.heartbeat_timeout)
            except requests.RequestException as exc:
                if not failing:
                    # 두절 중에는 매 주기 찍히면 로그가 도배된다. 전이할 때만.
                    self.get_logger().warn(f"heartbeat 실패: {exc}")
                failed()
                continue

            if response.status_code == 404:
                # robot_id 오타이거나 robots 테이블에 없는 로봇이다.
                # 재시도해도 결과가 같으므로 크게 남긴다.
                self.get_logger().error(
                    f"heartbeat 거부: {self.robot_id} 가 서버에 등록되지 않았습니다. "
                    "robot_id 파라미터와 robots 시드를 확인하세요.")
                failed()
                continue

            if not response.ok:
                if not failing:
                    self.get_logger().warn(
                        f"heartbeat 실패: HTTP {response.status_code}")
                failed()
                continue

            if failing:
                self.get_logger().info("heartbeat 복구")
                failing = False
            guard.success()
            self._consume_heartbeat_response(response)

        session.close()

    def _subscribe_watched(self, watched) -> None:
        """감시 대상 토픽을 구독한다. 콜백은 수신 시각만 남긴다.

        타입 해석에 실패해도 게이트웨이는 계속 떠야 한다. 관측 기능 하나가
        빠지는 것과 로봇이 관제에 안 붙는 것은 무게가 다르다. 대신 조용히
        빠지지 않게 error 로 남긴다.
        """
        for topic, type_name in watched:
            try:
                msg_type = get_message(type_name)
            except Exception as exc:
                self.get_logger().error(
                    f"토픽 감시 제외 {topic}: 메시지 타입 '{type_name}' 을 "
                    f"불러오지 못했습니다 ({exc})")
                continue
            self.create_subscription(
                msg_type, topic,
                # 기본 인자로 묶지 않으면 마지막 토픽 이름이 전부에 잡힌다.
                lambda _msg, name=topic: self._topic_ages.record(name),
                WATCH_QOS)

    def _on_topic_health(self, message: DiagnosticArray) -> None:
        """Receive the C++ monitor's low-rate topic timing summary."""
        snapshot = {}
        for status in message.status:
            if not status.name:
                continue
            values = {item.key: item.value for item in status.values}
            try:
                age = float(values['age_sec'])
                hz = float(values['hz']) if 'hz' in values else None
            except (KeyError, TypeError, ValueError):
                continue
            snapshot[status.name] = {
                'age_sec': round(max(0.0, age), 3),
                'hz': round(max(0.0, hz), 2) if hz is not None else None,
            }
        with self._topic_health_lock:
            self._topic_health_snapshot = snapshot

    def _heartbeat_payload(self) -> dict:
        """5초마다 나가는 본문. 작고 자주 바뀌는 것만 싣는다.

        인벤토리 본문(노드 목록·커밋)은 여기 넣지 않는다. 로봇 수 × 12회/분
        이라 payload 를 키우면 안 되고, 그 값들은 몇 시간에 한 번 바뀐다.
        해시만 실어 서버가 자기가 아는 것과 다른지 판단하게 한다.
        """
        with self._inventory_lock:
            payload = {
                "system_state": self._system_state(),
                "localization_active": self._localization_active,
                "fire_alarm_active": self._fire_alarm_active,
                "returning_to_dock": self._returning_to_dock,
                "navigation_speed_mps": self._navigation_speed_mps,
                "low_obstacle_mode": self._low_obstacle_mode,
                "low_obstacle_state": self._low_obstacle_state,
                "guide_robot_state": self._guide_robot_state,
                "guide_session_state": self._guide_session_state,
                "inventory_hash": self._inventory_hash,
                "cpu_total_pct": self._cpu_total_pct,
                "max_node_cpu_pct": self._max_node_cpu_pct,
                "max_node_cpu_name": self._max_node_cpu_name,
            }
        # 큐 적체는 통신 두절이나 서버 거부가 진행 중이라는 신호다.
        # 상한(max_queue_rows)에 가까우면 이미 데이터가 버려지는 중이다.
        payload["queue_pending"] = self.queue.count()
        # 토픽 나이·주기 (§7.2). 감시 대상이 없으면 빈 객체이고, 서버는 그걸
        # '감시 안 함' 으로 그린다 — 토픽이 죽은 것과 구분해야 한다.
        if self._topic_ages is not None:
            payload["topics"] = self._topic_ages.snapshot()
        else:
            with self._topic_health_lock:
                payload["topics"] = dict(self._topic_health_snapshot)
        return payload

    def _consume_heartbeat_response(self, response) -> None:
        """서버가 인벤토리를 다시 달라고 하면 표시해둔다.

        서버가 재시작해 메모리를 잃었거나 우리 전송이 유실된 경우다.
        본문이 JSON 이 아니어도 heartbeat 는 계속돼야 하므로 조용히 넘긴다.
        """
        if response.status_code == 204 or not response.content:
            return
        try:
            body = response.json()
        except ValueError:
            return
        if isinstance(body, dict) and body.get("need_inventory"):
            self._need_inventory.set()

    # ------------------------------------------------------------- inventory

    def _sample_graph(self) -> None:
        """ROS 타이머. 그래프만 읽어 넘긴다.

        `ros2 node list` 를 subprocess 로 부르지 않는다 — 호출마다 노드를
        새로 띄우고, 그 자체가 CPU 포화에 기여한다. rclpy 는 이미 로컬에
        들고 있는 그래프 캐시를 돌려주므로 I/O 가 없다.
        """
        try:
            names = self.get_node_names_and_namespaces()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().debug(f"노드 그래프 조회 실패: {exc}")
            return
        with self._inventory_lock:
            self._graph_snapshot = [(name, ns) for name, ns in names]

    def _collect_inventory(self, previous_cpu: dict, elapsed: float) -> dict:
        """이번 표본. 사실만 담는다 — 심각도 판정은 서버가 한다."""
        with self._inventory_lock:
            graph = list(self._graph_snapshot)
            map_hash = self._map_hash

        processes = inventory.scan_processes()
        for process in processes:
            process["cpu_pct"] = inventory.cpu_percent(
                previous_cpu.get(process["pid"]),
                process["cpu_seconds_total"],
                elapsed)

        return {
            "node_graph": inventory.parse_node_graph(graph),
            "processes": processes,
            "workspaces": inventory.build_workspaces(processes, self._git_cache),
            "ros_domain_id": int(os.environ.get("ROS_DOMAIN_ID", 0)),
            # 아직 /map 을 못 받았으면 None 이다. '맵이 없다' 가 아니라
            # '아직 모른다' 이고, 서버가 그 둘을 구분해서 그린다.
            "map_name": self._map_name,
            "map_hash": map_hash,
        }

    def _inventory_loop(self) -> None:
        """인벤토리 수집과 전송.

        heartbeat 에는 해시만 싣고, 본문은 **바뀌었을 때만** 보낸다. 노드
        목록과 커밋은 몇 시간에 한 번 바뀌는데 5초마다 보낼 이유가 없다.

        서버가 heartbeat 응답으로 need_inventory 를 주면 다시 보낸다.
        서버가 재시작해 메모리를 잃었거나 우리 전송이 유실된 경우다.
        """
        session = requests.Session()
        previous_cpu: dict[int, float] = {}
        previous_total = None
        last_at = time.monotonic()
        sent_hash = None

        while not self._stop.wait(self.inventory_interval):
            now = time.monotonic()
            elapsed = now - last_at
            last_at = now

            try:
                payload = self._collect_inventory(previous_cpu, elapsed)
            except OSError as exc:
                self.get_logger().warn(f"인벤토리 수집 실패: {exc}")
                continue

            previous_cpu = {
                p["pid"]: p["cpu_seconds_total"] for p in payload["processes"]}

            # 전체 CPU 는 /proc/stat 에서 따로 읽는다. 노드별 합이 아니다 —
            # 노드 하나가 100% 여도 코어가 4개면 여유가 있다.
            try:
                current_total = inventory.parse_total_cpu(
                    Path("/proc/stat").read_text())
            except OSError:
                current_total = None
            total_pct = inventory.total_cpu_percent(previous_total, current_total)
            previous_total = current_total

            busiest = inventory.busiest_process(payload["processes"])
            digest = inventory.inventory_hash(payload)

            with self._inventory_lock:
                self._inventory_hash = digest
                self._cpu_total_pct = total_pct
                self._max_node_cpu_pct = busiest["cpu_pct"] if busiest else None
                self._max_node_cpu_name = (
                    (busiest["matched_node_names"] or [None])[0]
                    if busiest else None)

            if digest == sent_hash and not self._need_inventory.is_set():
                continue

            payload["inventory_hash"] = digest
            payload["reported_at"] = datetime.now(timezone.utc).isoformat()
            if self._post_inventory(session, payload):
                sent_hash = digest
                self._need_inventory.clear()

        session.close()

    def _post_inventory(self, session: requests.Session, payload: dict) -> bool:
        """실패해도 큐에 쌓지 않는다. 다음 주기가 곧 재시도다."""
        try:
            response = session.post(
                self.inventory_url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            self.get_logger().debug(f"인벤토리 전송 실패: {exc}")
            return False
        if not response.ok:
            self.get_logger().warn(
                f"인벤토리 거부: HTTP {response.status_code}")
            return False
        return True

    # --------------------------------------------------------------- battery

    def _battery_payload(self) -> dict | None:
        """보낼 표본. 낡았으면 None 을 돌려 전송 자체를 건너뛴다."""
        with self._battery_lock:
            voltage = self._battery_voltage
            percent = self._battery_percent
            sample_at = self._battery_at
        if voltage is None and percent is None:
            return None
        if battery_is_stale(sample_at, time.monotonic()):
            # 구독이 끊겼거나 발행 노드가 죽었다. 마지막 값을 계속 보내면
            # 서버가 그것을 최신값으로 기록해 화면이 조용히 거짓말을 한다.
            if not self._battery_stale_logged:
                age = time.monotonic() - sample_at if sample_at else None
                self.get_logger().error(
                    "배터리 표본이 끊겼습니다"
                    + (f" ({age:.0f}초 경과)" if age else "")
                    + ". 낡은 값을 보내지 않고 멈춥니다 — "
                    "battery/voltage 발행 노드를 확인하세요.")
                self._battery_stale_logged = True
            return None
        if self._battery_stale_logged:
            self.get_logger().info("배터리 표본 복구")
            self._battery_stale_logged = False
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
                # 표본이 없거나 낡았다. 이때도 실행 시각은 소비한다 —
                # 안 그러면 남은 대기가 계속 0이 되어 스레드가 쉬지 않고
                # 돈다(IntervalGate 와 같은 이유). 한 번이라도 보낸 뒤에만
                # 해당한다. 첫 표본 전에는 last_sent 가 None 이라 wake 를
                # 무기한 기다리므로 원래 돌지 않는다.
                if last_sent is not None:
                    last_sent = time.monotonic()
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

    # ---------------------------------------------------------- QR observation

    def _qr_observation_loop(self) -> None:
        """최신 QR 거리만 전송하고 실패한 과거 관측은 버린다."""
        session = requests.Session()
        gate = IntervalGate(self.qr_observation_interval, time.monotonic())
        last_payload = None
        while not self._stop.is_set():
            self._qr_wake.wait(timeout=gate.remaining(time.monotonic()))
            self._qr_wake.clear()
            if self._stop.is_set():
                break
            if not gate.consume(time.monotonic()):
                continue
            with self._qr_lock:
                payload = self._qr_observation
            if payload is None:
                continue
            # 새 추적 노드는 heartbeat 자체가 생존 신호다. 상태가 같아도 서버의
            # observed_at 을 갱신한다. 구버전 QR-only payload의 미인식 중복만
            # 기존처럼 생략한다.
            if (payload.get("follow_state") is None
                    and not payload["visible"] and payload == last_payload):
                continue
            last_payload = dict(payload)
            try:
                response = session.post(
                    self.qr_observation_url, json=payload, timeout=self.timeout)
            except requests.RequestException:
                continue
            if not response.ok:
                self.get_logger().debug(
                    f"QR 관측값 거부: HTTP {response.status_code}")
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
        if command in SYSTEM_COMMANDS:
            if argument != "run":
                self.get_logger().error(
                    f"잘못된 시스템 제어 인자: {argument!r} "
                    f"(order_id={order.get('order_id')})")
                return True
            if command != "system_start" and (
                    self._clinical_active or self._localization_active):
                # 서버 검증 뒤 세션이 생기는 경쟁 상황도 로봇에서 다시 막는다.
                # 소비하지 않으면 세션 종료 후 낡은 정지 명령이 실행되므로 ack한다.
                self.get_logger().error(
                    f"안내 또는 재탐색 중이라 {command} 명령을 거부했습니다.")
                return True
            return self._control_system(SYSTEM_COMMANDS[command])

        if command == "localize":
            if argument != "run":
                self.get_logger().error(
                    f"잘못된 재탐색 인자: {argument!r} "
                    f"(order_id={order.get('order_id')})")
                return False
            if self._system_state() != "active":
                # 서비스가 나중에 생길 때까지 낡은 명령을 보관하면 시스템을
                # 다시 켠 순간 예고 없이 로봇이 움직인다. 지금 거부하고 소비한다.
                self.get_logger().error(
                    "통합 시스템이 가동 중이 아니라 재탐색 명령을 거부했습니다.")
                return True
            if not self._localize_client.service_is_ready():
                self.get_logger().warn(
                    "자동 재탐색 서비스가 아직 준비되지 않았습니다. 명령을 유지합니다.")
                return False
            future = self._localize_client.call_async(Trigger.Request())
            future.add_done_callback(self._on_localize_response)
            self.get_logger().info("명령 실행: localize(run)")
            return True

        if command == "fire_alarm_reset":
            if argument != "run":
                self.get_logger().error(
                    f"잘못된 화재 경보 해제 인자: {argument!r} "
                    f"(order_id={order.get('order_id')})")
                return True
            if self._system_state() != "active":
                self.get_logger().error(
                    "통합 시스템이 가동 중이 아니라 화재 경보 해제를 거부했습니다.")
                return True
            if not self._fire_alarm_reset_client.service_is_ready():
                self.get_logger().warn(
                    "화재 경보 해제 서비스가 아직 준비되지 않았습니다. 명령을 유지합니다.")
                return False
            future = self._fire_alarm_reset_client.call_async(Trigger.Request())
            future.add_done_callback(self._on_fire_alarm_reset_response)
            self.get_logger().info("명령 실행: fire_alarm_reset(run)")
            return True

        if command == "cancel_fire_evacuation":
            if argument != "run":
                self.get_logger().error(
                    f"잘못된 비상대피 취소 인자: {argument!r} "
                    f"(order_id={order.get('order_id')})")
                return True
            if self._system_state() != "active":
                self.get_logger().error(
                    "통합 시스템이 가동 중이 아니라 비상대피 취소를 거부했습니다.")
                return True
            if not self._fire_evac_cancel_client.service_is_ready():
                self.get_logger().warn(
                    "비상대피 취소 서비스가 아직 준비되지 않았습니다. 명령을 유지합니다.")
                return False
            future = self._fire_evac_cancel_client.call_async(Trigger.Request())
            future.add_done_callback(self._on_fire_evac_cancel_response)
            self.get_logger().info("명령 실행: cancel_fire_evacuation(run)")
            return True

        if command == "cancel_navigation":
            if argument != "run":
                self.get_logger().error(
                    f"잘못된 시험주행 취소 인자: {argument!r} "
                    f"(order_id={order.get('order_id')})")
                return True
            self._navigation_cancel_pub.publish(Bool(data=True))
            self.get_logger().info("명령 실행: cancel_navigation(run)")
            return True

        if command == "set_navigation_speed":
            speed = parse_navigation_speed(argument)
            if speed is None:
                self.get_logger().error(
                    f"잘못된 주행 속도: {argument!r} "
                    f"(order_id={order.get('order_id')})")
                return True
            if self._system_state() != "active":
                self.get_logger().error(
                    "통합 시스템이 가동 중이 아니라 속도 변경을 거부했습니다.")
                return True
            if (self._clinical_active or self._localization_active
                    or self._fire_alarm_active is True or self._mode != "auto"):
                self.get_logger().error(
                    "안내·안전 동작 중이거나 자동 모드가 아니라 속도 변경을 거부했습니다.")
                return True
            if not self._controller_parameters.services_are_ready():
                self.get_logger().warn(
                    "Nav2 controller_server 파라미터 서비스가 아직 준비되지 "
                    "않았습니다. 명령을 유지합니다.")
                return False
            future = self._controller_parameters.set_parameters([
                Parameter("FollowPath.desired_linear_vel", value=speed),
            ])
            future.add_done_callback(
                lambda result, requested=speed: self._on_navigation_speed_response(
                    result, requested))
            self.get_logger().info(f"주행 속도 변경 요청: {speed:.2f} m/s")
            return True

        if command == "set_low_obstacle_mode":
            mode = parse_low_obstacle_mode(argument)
            if mode is None:
                self.get_logger().error(
                    f"잘못된 저상 장애물 회피 모드: {argument!r} "
                    f"(order_id={order.get('order_id')})")
                return True
            if self._system_state() != "active":
                self.get_logger().error(
                    "통합 시스템이 가동 중이 아니라 회피 모드 변경을 거부했습니다.")
                return True
            if (self._clinical_active or self._localization_active
                    or self._fire_alarm_active is True):
                self.get_logger().error(
                    "안내 또는 안전 동작 중이라 회피 모드 변경을 거부했습니다.")
                return True
            if (not self._guide_parameters.services_are_ready()
                    or not self._navigation_parameters.services_are_ready()):
                self.get_logger().warn(
                    "안내/Waypoint 주행 관리자 파라미터 서비스가 아직 "
                    "준비되지 않았습니다. "
                    "명령을 유지합니다.")
                return False
            previous = self._low_obstacle_mode
            future = self._navigation_parameters.set_parameters([
                Parameter("low_obstacle_mode", value=mode),
            ])
            future.add_done_callback(
                lambda result, requested=mode, old=previous:
                self._on_navigation_low_obstacle_mode_response(
                    result, requested, old))
            self.get_logger().info(
                f"Waypoint 시험 저상 장애물 회피 모드 변경 요청: {mode}")
            return True

        if command == "cancel_guidance":
            try:
                session_id = int(argument)
            except (TypeError, ValueError):
                self.get_logger().error(
                    f"잘못된 안내 취소 세션 ID: {argument!r} "
                    f"(order_id={order.get('order_id')})")
                return True
            if session_id <= 0:
                self.get_logger().error(
                    f"잘못된 안내 취소 세션 ID: {session_id}")
                return True
            if self._guide_session_id not in (0, session_id):
                self.get_logger().warn(
                    f"현재 세션과 다른 안내 취소 명령을 폐기합니다: "
                    f"requested={session_id}, current={self._guide_session_id}")
                return True
            self._session_cancel_pub.publish(String(data=json.dumps({
                "reason": "aborted",
                "session_id": session_id,
            })))
            self.get_logger().info(
                f"명령 실행: cancel_guidance(session_id={session_id})")
            return True
        publisher = self._order_pubs.get(command)
        if publisher is None:
            self.get_logger().error(
                f"모르는 명령: {command} (order_id={order.get('order_id')})")
            return False

        publisher.publish(String(data=argument))
        self.get_logger().info(f"명령 실행: {command}({argument})")
        return True

    def _control_system(self, action: str) -> bool:
        """설치 시 sudoers로 허용한 통합 launch 유닛 하나만 제어한다."""
        try:
            result = subprocess.run(
                ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", action,
                 "mingky-system.service"],
                check=False, capture_output=True, text=True, timeout=15.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.get_logger().error(f"통합 시스템 {action} 실행 실패: {exc}")
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:200]
            self.get_logger().error(f"통합 시스템 {action} 거부: {detail}")
            return False
        self.get_logger().info(f"통합 시스템 제어 완료: {action}")
        if action in ("start", "stop", "restart"):
            self._navigation_speed_mps = DEFAULT_NAVIGATION_SPEED_MPS
            self._low_obstacle_mode = self._default_low_obstacle_mode
        return True

    def _on_localize_response(self, future) -> None:
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"자동 재탐색 요청 실패: {exc}")
            return
        if response.success:
            self.get_logger().info(f"자동 재탐색 요청 승인: {response.message}")
        else:
            self.get_logger().warn(f"자동 재탐색 요청 거부: {response.message}")

    def _on_fire_alarm_reset_response(self, future) -> None:
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"화재 경보 해제 요청 실패: {exc}")
            return
        if response.success:
            self.get_logger().info(f"화재 경보 해제 완료: {response.message}")
        else:
            self.get_logger().warn(f"화재 경보 해제 거부: {response.message}")

    def _on_fire_evac_cancel_response(self, future) -> None:
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"비상대피 취소 요청 실패: {exc}")
            return
        if response.success:
            self.get_logger().warn(f"비상대피 취소 완료: {response.message}")
        else:
            self.get_logger().warn(f"비상대피 취소 거부: {response.message}")

    def _on_navigation_speed_response(self, future, requested: float) -> None:
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"주행 속도 변경 실패: {exc}")
            return
        results = response.results if response is not None else []
        if not results or not all(result.successful for result in results):
            reasons = ", ".join(
                result.reason for result in (results or []) if result.reason)
            self.get_logger().error(
                f"주행 속도 변경 거부: {reasons or '원인 확인 불가'}")
            return
        self._navigation_speed_mps = requested
        self.get_logger().info(f"주행 속도 적용 완료: {requested:.2f} m/s")

    def _on_navigation_low_obstacle_mode_response(
            self, future, requested: str, previous: str) -> None:
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(
                f"Waypoint 시험 저상 장애물 회피 모드 변경 실패: {exc}")
            return
        results = response.results if response is not None else []
        if not results or not all(result.successful for result in results):
            reasons = ", ".join(
                result.reason for result in (results or []) if result.reason)
            self.get_logger().error(
                "Waypoint 시험 저상 장애물 회피 모드 변경 거부: "
                f"{reasons or '원인 확인 불가'}")
            return
        future = self._guide_parameters.set_parameters([
            Parameter("low_obstacle_mode", value=requested),
        ])
        future.add_done_callback(
            lambda result, mode=requested, old=previous:
            self._on_low_obstacle_mode_response(result, mode, old))
        self.get_logger().info(
            f"안내 주행 저상 장애물 회피 모드 변경 요청: {requested}")

    def _on_low_obstacle_mode_response(
            self, future, requested: str, previous: str | None = None) -> None:
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"저상 장애물 회피 모드 변경 실패: {exc}")
            if previous is not None:
                self._rollback_navigation_low_obstacle_mode(previous)
            return
        results = response.results if response is not None else []
        if not results or not all(result.successful for result in results):
            reasons = ", ".join(
                result.reason for result in (results or []) if result.reason)
            self.get_logger().error(
                f"저상 장애물 회피 모드 변경 거부: {reasons or '원인 확인 불가'}")
            if previous is not None:
                self._rollback_navigation_low_obstacle_mode(previous)
            return
        self._low_obstacle_mode = requested
        self.get_logger().info(
            f"안내/Waypoint 시험 저상 장애물 회피 모드 적용 완료: {requested}")

    def _rollback_navigation_low_obstacle_mode(self, previous: str) -> None:
        self._navigation_parameters.set_parameters([
            Parameter("low_obstacle_mode", value=previous),
        ])
        self.get_logger().warn(
            "안내 주행 모드 적용 실패로 Waypoint 시험 모드를 "
            f"{previous}(으)로 되돌립니다.")

    # ------------------------------------------------------------------ 종료

    def destroy_node(self):
        self._stop.set()
        self._wake.set()
        self._battery_wake.set()
        self._qr_wake.set()
        self._sender.join(timeout=2.0)
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=2.0)
        if self._battery is not None:
            self._battery.join(timeout=2.0)
        if self._qr_sender is not None:
            self._qr_sender.join(timeout=2.0)
        if self._inventory is not None:
            self._inventory.join(timeout=2.0)
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
