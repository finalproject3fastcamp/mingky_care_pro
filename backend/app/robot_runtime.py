"""로봇 게이트웨이가 보고한 비영속 실행 상태.

3~5초마다 덮어쓰는 값은 DB 에 쌓지 않는다(003 의 규칙). 화면은 실시간,
DB 는 추이다. 이 경계를 흐리면 "이 숫자가 언제 것인가" 를 나중에 아무도
답하지 못한다.

## 왜 heartbeat.py 가 아니라 여기인가

heartbeat.py 는 '언제 마지막으로 들었는가' 만 다룬다(생존 판정). 로봇이
보고한 내용은 성격이 달라 따로 둔다 — 전자는 두절을 판정하려고 서버가
재는 값이고, 후자는 로봇이 말해준 값이다.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .schemas import TopicSampleIn


@dataclass(frozen=True)
class RuntimeState:
    system_state: str
    localization_active: bool
    fire_alarm_active: bool | None
    reported_at: datetime
    state_since: datetime
    returning_to_dock: bool = False
    navigation_speed_mps: float | None = None
    low_obstacle_mode: str | None = None
    guide_robot_state: str | None = None

    # heartbeat 층 1 — 작고 자주 바뀌는 값들.
    #
    # 전부 기본값이 있다. 구버전 게이트웨이가 안 보내도 None 으로 남을 뿐
    # heartbeat 가 거부되면 안 된다. 생존 신호가 끊기면 멀쩡한 로봇에
    # comm_lost 가 찍힌다.
    inventory_hash: str | None = None
    cpu_total_pct: float | None = None
    queue_pending: int | None = None
    max_node_cpu_pct: float | None = None
    max_node_cpu_name: str | None = None
    # 토픽별 마지막 수신 경과와 최근 주기 (§7.2). 판정은 여기서 하지 않는다 —
    # 로봇이 보고한 사실만 들고 있고, 임계 대조는 topic_watch 가 한다.
    topics: dict[str, TopicSampleIn] = field(default_factory=dict)


_states: dict[str, RuntimeState] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def update(
    robot_id: str,
    system_state: str,
    localization_active: bool,
    fire_alarm_active: bool | None = None,
    returning_to_dock: bool | None = None,
    navigation_speed_mps: float | None = None,
    low_obstacle_mode: str | None = None,
    *,
    guide_robot_state: str | None = None,
    inventory_hash: str | None = None,
    cpu_total_pct: float | None = None,
    queue_pending: int | None = None,
    max_node_cpu_pct: float | None = None,
    max_node_cpu_name: str | None = None,
    topics: dict[str, TopicSampleIn] | None = None,
) -> None:
    now = _now()
    previous = _states.get(robot_id)
    _states[robot_id] = RuntimeState(
        system_state=system_state,
        localization_active=localization_active,
        fire_alarm_active=fire_alarm_active,
        returning_to_dock=bool(returning_to_dock),
        navigation_speed_mps=navigation_speed_mps,
        low_obstacle_mode=low_obstacle_mode,
        guide_robot_state=guide_robot_state,
        reported_at=now,
        state_since=(
            previous.state_since
            if previous is not None and previous.system_state == system_state
            else now
        ),
        inventory_hash=inventory_hash,
        cpu_total_pct=cpu_total_pct,
        queue_pending=queue_pending,
        max_node_cpu_pct=max_node_cpu_pct,
        max_node_cpu_name=max_node_cpu_name,
        topics=dict(topics or {}),
    )


def snapshot() -> dict[str, RuntimeState]:
    return dict(_states)


def reset() -> None:
    _states.clear()
