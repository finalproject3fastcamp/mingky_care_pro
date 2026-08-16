"""토픽 주기 감시 — 유닛은 active 인데 데이터가 안 나오는 상태를 잡는다.

monitoring-spec.md §7.2 · 로드맵 9.

## 왜 systemd 만으로는 부족한가

`system` 탭은 유닛 상태만 본다. 그런데 실전 장애 모드는 **유닛은 active 인데
`/scan` 이 안 나오는 것**이다. 라이다 USB 가 죽어도 노드 프로세스는 멀쩡히
살아 있고 systemd 는 초록이며, 로봇만 아무것도 못 한다.

## 구조는 §3.3 의 두절 판정과 같다

도착하는 데이터로는 '안 오는 것' 을 감지할 수 없다. 감시 노드가 콜백에서
마지막 수신 시각만 갱신하고, 게이트웨이가 heartbeat 에 **경과 시간**을 실어
보낸다. 서버는 그 경과를 임계와 대조한다.

`ros2 topic hz` 를 서브프로세스로 돌리지 않는다 — 호출마다 노드를 새로 띄우는
비용이 5초 주기에 안 맞는다.

## 나이만으로는 못 잡는 것

라이다가 USB 대역 부족으로 10Hz → 3Hz 로 떨어지면 마지막 수신은 0.33초 전이라
어떤 나이 임계에도 안 걸린다. 그래서 로봇이 측정 Hz 도 같이 보고하고, 기대치의
`min_hz_ratio` 아래면 늦은 것으로 본다.

## 판정은 서버가 한다

로봇은 "몇 초 전에 받았고 몇 Hz 였다" 는 사실만 보고한다. 임계와 문구는
config/topic_watch.yaml 에 있다 — inventory_rules.py 와 같은 이유로, 임계를
바꾸려고 로봇을 재배포하는 상황을 만들지 않는다.

## 상시 발행이 아닌 토픽

`/cmd_vel` 은 서 있는 로봇에서 안 나오는 것이 정상이고 `/amcl_pose` 는 AMCL 이
파티클을 갱신할 때만 나온다. 이걸 끊김으로 그리면 대기 중인 로봇 2대가 항상
경고 상태가 되고, 그러면 진짜 `/scan` 두절이 그 빨강 속에 묻힌다. 정본의
`always_on: false` 가 그 구분이고, 이 토픽들은 이벤트를 발행하지 않는다.
단 **발행은 되는데 느린 경우**(hz 저하)는 그대로 늦은 것으로 본다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import db, heartbeat, robot_runtime
from .config import TOPIC_WATCH_INTERVAL_SEC
from .ingest import ingest
from .registry import get_registry
from .schemas import EventIn, TopicAgeOut, TopicSampleIn

log = logging.getLogger("mingky")

SOURCE_NODE = "backend.topic_watch"

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "topic_watch.yaml")

# 정본에 비율이 없을 때. 기대의 절반 아래로 떨어지면 늦은 것으로 본다.
_DEFAULT_MIN_HZ_RATIO = 0.5

# 화면 정렬 순서. 조용히 틀리는 쪽이 위로 와야 한다.
_STATE_ORDER = {
    "stale": 0, "slow": 1, "missing": 2, "unwatched": 3,
    "idle": 4, "unrated": 5, "fresh": 6,
}


@dataclass(frozen=True)
class TopicRule:
    expected_hz: float | None = None
    warn_sec: float | None = None
    stale_sec: float | None = None
    min_hz_ratio: float = _DEFAULT_MIN_HZ_RATIO
    always_on: bool = False
    why: str = ""


class TopicWatchRules:
    """토픽별 임계 정본."""

    def __init__(self, topics: dict[str, TopicRule]):
        self._topics = topics

    @classmethod
    def load(cls, explicit: str = "") -> "TopicWatchRules":
        """파일이 없어도 죽지 않는다.

        inventory_rules 와 같은 판단이다. 없으면 판정만 못 하고(전부 unrated)
        나이는 그대로 보인다. 관측성 기능 하나 때문에 관제 전체가 안 뜨는
        쪽이 더 나쁘다.
        """
        path = Path(
            explicit or os.environ.get("TOPIC_WATCH_RULES_FILE") or _DEFAULT_PATH)
        if not path.is_file():
            return cls({})

        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        default_ratio = _as_float(raw.get("default_min_hz_ratio"))
        topics = {}
        for name, entry in (raw.get("topics") or {}).items():
            if not isinstance(entry, dict):
                continue
            topics[name] = TopicRule(
                expected_hz=_as_float(entry.get("expected_hz")),
                warn_sec=_as_float(entry.get("warn_sec")),
                stale_sec=_as_float(entry.get("stale_sec")),
                min_hz_ratio=(
                    _as_float(entry.get("min_hz_ratio"))
                    or default_ratio or _DEFAULT_MIN_HZ_RATIO),
                always_on=bool(entry.get("always_on")),
                why=str(entry.get("why") or ""),
            )
        return cls(topics)

    def get(self, topic: str) -> TopicRule | None:
        return self._topics.get(topic)

    def items(self):
        return self._topics.items()


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_rules: TopicWatchRules | None = None


def load() -> TopicWatchRules:
    global _rules
    _rules = TopicWatchRules.load()
    return _rules


def get_rules() -> TopicWatchRules:
    global _rules
    if _rules is None:
        _rules = TopicWatchRules.load()
    return _rules


def _judge_one(topic: str, sample: TopicSampleIn,
               rule: TopicRule | None) -> TopicAgeOut:
    if rule is None:
        # 정본에 없는 토픽. 사실은 그대로 보여주되 임의 임계로 색을 칠하지
        # 않는다. 근거 없이 빨간 값이 하나 생기면 나머지 판정도 같이 안 믿게 된다.
        return TopicAgeOut(topic=topic, age_sec=sample.age_sec, hz=sample.hz,
                           state="unrated")

    common = dict(topic=topic, age_sec=sample.age_sec, hz=sample.hz,
                  expected_hz=rule.expected_hz, always_on=rule.always_on,
                  why=rule.why)

    if sample.age_sec is None:
        # 구버전 게이트웨이. 정상 경로에서는 감시 시작 시각부터 재므로 항상 값이 있다.
        return TopicAgeOut(**common, state="missing")

    # 발행은 되는데 느린 경우. 나이 임계로는 절대 안 걸린다.
    hz_low = (rule.expected_hz is not None and sample.hz is not None
              and sample.hz > 0
              and sample.hz < rule.expected_hz * rule.min_hz_ratio)

    if rule.stale_sec is not None and sample.age_sec > rule.stale_sec:
        # 상시 발행이 아닌 토픽이 안 나오는 것은 고장이 아니다. 대기 중인
        # 로봇에 /cmd_vel 이 없는 것과 같다.
        return TopicAgeOut(**common, state="stale" if rule.always_on else "idle")

    if rule.warn_sec is not None and sample.age_sec > rule.warn_sec:
        return TopicAgeOut(**common, state="slow" if rule.always_on else "idle")

    return TopicAgeOut(**common, state="slow" if hz_low else "fresh")


def judge(reported: dict[str, TopicSampleIn],
          rules: TopicWatchRules | None = None) -> list[TopicAgeOut]:
    """로봇이 보고한 토픽 나이에 판정을 붙인다.

    아무것도 보고하지 않은 로봇은 빈 목록이다. 정본의 네 토픽을 전부
    `unwatched` 로 채우면 구버전 게이트웨이 하나가 화면을 경고로 덮는다 —
    그건 토픽이 죽은 게 아니라 감시가 아직 안 붙은 것이고, 화면은 그 둘을
    다르게 그려야 한다(호출부가 빈 목록을 '보고 안 함' 으로 그린다).
    """
    if not reported:
        return []

    rules = rules or get_rules()
    judged = [_judge_one(topic, sample, rules.get(topic))
              for topic, sample in reported.items()]

    # 정본에 있는데 보고에 없는 토픽. 감시 노드가 그 토픽을 아예 안 보고 있다는
    # 사실이고, "정상" 과 구분해야 한다.
    for topic, rule in rules.items():
        if topic not in reported:
            judged.append(TopicAgeOut(
                topic=topic, expected_hz=rule.expected_hz,
                always_on=rule.always_on, why=rule.why, state="unwatched"))

    return sorted(judged, key=lambda t: (_STATE_ORDER.get(t.state, 9), t.topic))


# --- 이벤트 발행 --------------------------------------------------------------
#
# 화면에만 있고 아무도 안 보는 지표는 없는 것과 같다(원칙 4). 라이다가 죽은
# 것은 사람이 알아야 하므로 events 로 내보내 타임라인·알림 경로에 올린다.

# (robot_id, topic) — 이미 끊김을 알린 것. 매 주기 반복 발행하지 않기 위한 것이다.
_alerted: set[tuple[str, str]] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(robot_id: str, at: datetime, code: str, level: str,
           payload: dict) -> EventIn:
    return EventIn(
        event_id=uuid.uuid4(),
        robot_id=robot_id,
        session_id=0,
        occurred_at=at,
        level=level,
        event_code=code,
        source_node=SOURCE_NODE,
        payload=payload,
    )


def collect(now: datetime | None = None) -> list[EventIn]:
    """이번 주기에 발행할 토픽 이벤트를 모은다. 상태 전이도 여기서 일어난다.

    두절된 로봇은 건너뛴다. heartbeat 가 안 오면 토픽 나이도 그 시점에 멈춰
    있을 뿐이고, 그건 이미 `robot.comm_lost` 가 말한 사실이다. 여기서 또
    알리면 한 사건에 알림이 두 번 간다.
    """
    now = now or _now()
    events: list[EventIn] = []
    seen = heartbeat.snapshot()

    for robot_id, runtime in robot_runtime.snapshot().items():
        last_seen = seen.get(robot_id)
        if last_seen is None or last_seen[1]:
            continue

        for judged in judge(runtime.topics):
            key = (robot_id, judged.topic)
            # 판정 대상은 상시 발행 토픽뿐이다. /cmd_vel 이 안 나오는 것은
            # 대기 중인 로봇의 정상 상태다.
            if not judged.always_on:
                continue

            if judged.state == "stale" and key not in _alerted:
                _alerted.add(key)
                events.append(_event(
                    robot_id, now, "robot.topic_stale", "error",
                    {"topic": judged.topic, "age_sec": judged.age_sec,
                     "expected_hz": judged.expected_hz}))
            elif judged.state in ("fresh", "slow") and key in _alerted:
                _alerted.discard(key)
                events.append(_event(
                    robot_id, now, "robot.topic_restored", "info",
                    {"topic": judged.topic, "hz": judged.hz}))

    return events


async def _tick() -> None:
    events = collect()
    if not events:
        return
    pool = db.get_pool()
    async with pool.acquire() as conn:
        await ingest(conn, events, get_registry())

    for event in events:
        log.warning("%s robot=%s %s",
                    event.event_code, event.robot_id, event.payload)


async def monitor() -> None:
    """판정 루프. main.py 의 lifespan 이 태스크로 띄운다.

    heartbeat.monitor 와 따로 도는 이유는 다루는 사실이 다르기 때문이다 —
    저쪽은 '로봇이 살아 있는가', 이쪽은 '살아 있는 로봇 안에서 데이터가
    흐르는가'. 한 루프에 합치면 두절 판정 주기를 토픽 사정으로 바꾸게 된다.
    """
    log.info("토픽 주기 감시 시작 (판정 %.0f초 주기)", TOPIC_WATCH_INTERVAL_SEC)
    while True:
        try:
            await asyncio.sleep(TOPIC_WATCH_INTERVAL_SEC)
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("토픽 판정 실패. 다음 주기에 다시 시도한다.")


def reset() -> None:
    """테스트용. 발행 상태만 비운다. 정본은 그대로 둔다."""
    _alerted.clear()
