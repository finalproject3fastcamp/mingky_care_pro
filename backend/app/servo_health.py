"""서보 온도·전류 — 팔에만 있는 예지보전 신호 (§4.4 · 로드맵 11).

Dynamixel 은 온도·전류·전압·하드웨어 에러 비트를 스스로 보고한다. mobile 에
대응물이 없는 신호다. 배터리처럼 "지금 몇 %" 를 보는 값이 아니라 **회차마다
어떻게 변하는가**를 보는 값이라, 여기서 하는 일의 절반은 추세 판정이다.

## 지금 뜨거운 것과 오르는 중인 것은 다른 사실이다

40℃ 인데 사이클마다 오르는 조인트가, 55℃ 에서 평평한 조인트보다 나쁜
신호다 — 전자는 그리퍼 마모나 과부하 자세이고 후자는 그냥 그런 축이다.
`state`(지금)와 `rising`(추세)을 따로 내려보내는 이유다. 하나로 뭉개면
예지보전 신호가 사라지고 온도계 하나만 남는다.

## 추세를 함부로 내지 않는다

battery_forecast 와 같은 규칙이다. 표본이 모자라거나 시간 폭이 짧거나 적합이
나쁘면 기울기를 내지 않는다. 조제는 사이클마다 부하가 출렁여서, 짧은 창의
기울기로 "시간당 12℃ 상승" 같은 숫자를 만들면 다음 표본에 뒤집힌다.
**틀린 추세는 없는 추세보다 나쁘다.**

## 판정은 서버가 한다

임계는 `config/servo_limits.yaml` 이다. 로봇은 "shoulder_lift 가 57℃ 였다"
까지만 말한다. 조인트별로 임계가 다른 것도 여기서 정한다 — 그리퍼는 쥔 채
버티는 시간이 길어 뜨겁게 도는 것이 정상이고, 같은 선을 쓰면 정상 동작이
매 사이클 경고가 된다.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .battery_forecast import linear_fit
from .schemas import EventIn, ServoHealthOut, ServoReadingOut

log = logging.getLogger("mingky")

SOURCE_NODE = "backend.servo_health"

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "servo_limits.yaml")

# 추세를 보는 창. 사이클 하나가 10~15초라 6시간이면 수백 회차다. 하루를 보면
# 어제 식은 구간이 오늘 오르는 구간을 상쇄해 기울기가 0 에 가까워진다.
DEFAULT_WINDOW_MIN = 360

# 최소 요건. 이보다 적거나 짧으면 기울기가 조제 부하 변동에 묻힌다.
MIN_SAMPLES = 5
MIN_SPAN_SEC = 900.0
MIN_R_SQUARED = 0.5

# 임계가 없을 때. 정본 파일이 없어도 판정 자체는 돌아야 한다.
_FALLBACK = {
    "warn_temp_c": 55.0,
    "hot_temp_c": 65.0,
    "rising_c_per_hour": 4.0,
    "warn_current_ma": 1200.0,
}

# 화면 정렬 순서. 사람을 불러야 하는 쪽이 위로 온다.
_STATE_ORDER = {"fault": 0, "hot": 1, "warm": 2, "unknown": 3, "ok": 4}

# 조인트별 최신 표본. DISTINCT ON 이 인덱스(robot_id, joint, recorded_at DESC)를
# 그대로 탄다.
LATEST_SQL = """
    SELECT DISTINCT ON (joint)
           joint, recorded_at, temp_c, current_ma, voltage_v, hardware_error
    FROM robot_servo_log
    WHERE robot_id = $1
    ORDER BY joint, recorded_at DESC
"""

# 추세용 표본. 온도만 읽는다 — 전류는 사이클 위상에 따라 출렁여서 시간
# 회귀가 의미를 갖지 않는다.
TREND_SQL = """
    SELECT joint, recorded_at, temp_c
    FROM robot_servo_log
    WHERE robot_id = $1
      AND temp_c IS NOT NULL
      AND recorded_at >= now() - ($2 || ' minutes')::interval
    ORDER BY joint, recorded_at
"""

INSERT_SQL = """
    INSERT INTO robot_servo_log (
        robot_id, joint, temp_c, current_ma, voltage_v, hardware_error)
    VALUES ($1, $2, $3, $4, $5, $6)
"""


@dataclass(frozen=True)
class ServoLimits:
    warn_temp_c: float
    hot_temp_c: float
    rising_c_per_hour: float
    warn_current_ma: float


class ServoLimitRules:
    """조인트별 임계 정본."""

    def __init__(self, default: dict, joints: dict):
        self._default = {**_FALLBACK, **(default or {})}
        self._joints = joints or {}

    @classmethod
    def load(cls, explicit: str = "") -> "ServoLimitRules":
        """파일이 없어도 죽지 않는다. inventory_rules 와 같은 판단이다."""
        path = Path(
            explicit or os.environ.get("SERVO_LIMITS_FILE") or _DEFAULT_PATH)
        if not path.is_file():
            return cls({}, {})
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls(raw.get("default") or {}, raw.get("joints") or {})

    def for_joint(self, joint: str) -> ServoLimits:
        merged = {**self._default, **(self._joints.get(joint) or {})}
        return ServoLimits(
            warn_temp_c=float(merged["warn_temp_c"]),
            hot_temp_c=float(merged["hot_temp_c"]),
            rising_c_per_hour=float(merged["rising_c_per_hour"]),
            warn_current_ma=float(merged["warn_current_ma"]),
        )


_rules: ServoLimitRules | None = None


def load() -> ServoLimitRules:
    global _rules
    _rules = ServoLimitRules.load()
    return _rules


def get_rules() -> ServoLimitRules:
    global _rules
    if _rules is None:
        _rules = ServoLimitRules.load()
    return _rules


def _state(temp_c: float | None, hardware_error: int | None,
           limits: ServoLimits) -> str:
    # 에러 비트가 가장 먼저다. 온도가 멀쩡해도 서보가 이미 토크를 끊었을 수 있다.
    if hardware_error:
        return "fault"
    if temp_c is None:
        # 에러 비트를 0 으로 읽었어도 온도가 없으면 뜨거운지는 모른다.
        return "unknown"
    if temp_c >= limits.hot_temp_c:
        return "hot"
    if temp_c >= limits.warn_temp_c:
        return "warm"
    return "ok"


def _trend(samples: list[tuple[datetime, float]]) -> tuple[float | None, int]:
    """(시간당 상승, 표본 수). 신뢰할 수 없으면 기울기는 None 이다."""
    if len(samples) < MIN_SAMPLES:
        return None, len(samples)

    origin = samples[0][0]
    span = (samples[-1][0] - origin).total_seconds()
    if span < MIN_SPAN_SEC:
        return None, len(samples)

    slope_per_sec, _intercept, r_squared = linear_fit(
        [((at - origin).total_seconds(), temp) for at, temp in samples])
    if r_squared < MIN_R_SQUARED:
        # 조제 부하가 출렁이는 중이다. 방향을 말하지 않는다.
        return None, len(samples)
    return round(slope_per_sec * 3600.0, 2), len(samples)


def summarize(robot_id: str, latest_rows, trend_rows,
              window_min: int = DEFAULT_WINDOW_MIN,
              rules: ServoLimitRules | None = None) -> ServoHealthOut:
    """최신 표본과 추세 표본을 조인트별로 접는다. DB 를 모른다 — 행만 받는다."""
    rules = rules or get_rules()

    series: dict[str, list[tuple[datetime, float]]] = {}
    for row in trend_rows:
        series.setdefault(row["joint"], []).append(
            (row["recorded_at"], float(row["temp_c"])))

    servos = []
    for row in latest_rows:
        joint = row["joint"]
        limits = rules.for_joint(joint)
        temp = row["temp_c"]
        slope, samples = _trend(series.get(joint, []))
        servos.append(ServoReadingOut(
            joint=joint,
            recorded_at=row["recorded_at"],
            temp_c=temp,
            current_ma=row["current_ma"],
            voltage_v=row["voltage_v"],
            hardware_error=row["hardware_error"],
            state=_state(temp, row["hardware_error"], limits),
            warn_temp_c=limits.warn_temp_c,
            hot_temp_c=limits.hot_temp_c,
            slope_c_per_hour=slope,
            # 지금 온도와 무관하게 판정한다. 40℃ 인데 오르는 조인트가
            # 55℃ 에서 평평한 조인트보다 나쁜 신호다.
            rising=slope is not None and slope >= limits.rising_c_per_hour,
            sample_count=samples,
        ))

    servos.sort(key=lambda s: (_STATE_ORDER.get(s.state, 9),
                               -(s.temp_c or 0), s.joint))
    return ServoHealthOut(
        robot_id=robot_id, window_min=window_min, servos=servos)


# --- 이벤트 발행 --------------------------------------------------------------
#
# 화면에만 있고 아무도 안 보는 지표는 없는 것과 같다(원칙 4). 다만 §8.4 대로
# 등급을 아껴 쓴다 — 과열은 warning 이다. error 는 팔이 이미 멈춘 상태
# (manipulator.cycle_aborted · servo_fault)에만 쓴다.

# (robot_id, joint) — 이미 과열을 알린 조인트. 매 표본 반복 발행하지 않는다.
_hot: set[tuple[str, str]] = set()


def _event(robot_id: str, code: str, level: str, payload: dict) -> EventIn:
    return EventIn(
        event_id=uuid.uuid4(),
        robot_id=robot_id,
        session_id=0,
        occurred_at=datetime.now(timezone.utc),
        level=level,
        event_code=code,
        source_node=SOURCE_NODE,
        payload=payload,
    )


def crossings(robot_id: str, readings, rules: ServoLimitRules | None = None,
              ) -> list[EventIn]:
    """이번 표본에서 임계를 넘거나 되돌아온 조인트의 이벤트.

    되돌아옴은 경고선(warn)까지 내려와야 인정한다. 임계 바로 아래에서
    흔들리는 조인트가 과열/해제를 반복 발행하면 그 알림을 아무도 안 본다 —
    히스테리시스가 없으면 알림 자체가 잡음이 된다.
    """
    rules = rules or get_rules()
    events = []

    for reading in readings:
        if reading.temp_c is None:
            continue
        limits = rules.for_joint(reading.joint)
        key = (robot_id, reading.joint)

        if reading.temp_c >= limits.hot_temp_c and key not in _hot:
            _hot.add(key)
            events.append(_event(
                robot_id, "manipulator.servo_overheat", "warning",
                {"joint": reading.joint, "temp_c": reading.temp_c,
                 "limit_c": limits.hot_temp_c}))
        elif reading.temp_c <= limits.warn_temp_c and key in _hot:
            _hot.discard(key)
            events.append(_event(
                robot_id, "manipulator.servo_cooled", "info",
                {"joint": reading.joint, "temp_c": reading.temp_c}))

    return events


def reset() -> None:
    """테스트용. 발행 상태만 비운다. 정본은 그대로 둔다."""
    _hot.clear()
