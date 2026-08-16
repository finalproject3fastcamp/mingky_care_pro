"""조제 사이클 지표 — 팔의 `detail` 한 칸 (§7.3 · §7.2).

mobile 의 배터리·주행속도 자리에 오는 것이 팔에는 조제 사이클·pick 성공률·
정책 체크포인트다. 겹치는 것이 거의 없다.

## 왜 컬럼이 아니라 계산인가

§7.3 이 정한 규칙은 "공통 필드는 평평하게, 타입별 지표는 `detail` 한 칸에"
다. 서보 온도와 사이클 타임을 `robots` 의 컬럼으로 만들면 4행 중 2행이 항상
NULL 이고, 세 번째 로봇 종류가 오면 또 마이그레이션한다.

그래서 저장하지 않는다. 재료가 이미 `events` 에 다 있다 — §6.2 의 9개 코드가
사이클의 시작·끝·pick 결과·정책을 전부 싣고 온다. 같은 사실을 컬럼으로 한 번
더 들고 있으면 둘이 갈라지는 날이 오고, 그때 어느 쪽이 맞는지 판단할 근거가
없다. events 가 정본이다.

## 창은 시간이 아니라 사이클 수다

24시간 창을 쓰면 오전에 5건 돌린 팔과 야간에 40건 돌린 팔의 pick 성공률이
같은 신뢰도로 나란히 표시된다. §1.2 가 완주율에 이동창을 쓴 것과 같은
이유로 여기도 **직전 N 사이클**이다. 표본이 창을 못 채우면 그 사실을
`window_cycles` 로 같이 내려보내 화면이 "아직 모자란다" 를 말할 수 있게 한다.

## 판정하지 않는다

이 모듈은 숫자만 만든다. "pick 성공률 82% 는 나쁜가" 는 여기서 정하지 않는다.
§7.2 는 pick 성공률을 SLO 가 아니라 **선행 지표**로 뒀다 — 목표선이 없는 값에
서버가 임의로 임계값을 박으면, 그 숫자가 근거 없이 빨갛게 보이기 시작한다.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from fractions import Fraction

from .schemas import ManipulatorDetail, ServoFault

# 이동창. 조제 2대가 하루에 도는 사이클 수를 생각하면 50 은 며칠치라
# "지금 팔이 어떤가" 를 못 보여주고, 5 는 한 건에 20% 씩 흔들린다.
DEFAULT_WINDOW = 20

# 로봇당 거슬러 올라갈 이벤트 수. 사이클 한 건이 보통 6~10개(policy_loaded 는
# 기동 시 한 번)라 20 사이클이면 200 근처다. 재시도가 붙은 사이클을 감안해
# 두 배를 잡는다. 이 수를 키우는 대신 창을 줄이는 쪽이 항상 낫다 — 여기는
# 로봇 수 × LIMIT 만큼 행을 읽는 자리다.
EVENT_LIMIT = 400

# §7.2 의 "사이클 타임 p50/p95".
P50 = Fraction(1, 2)
P95 = Fraction(19, 20)

# 팔 이벤트만, 팔에서만 읽는다. LIKE 가 인덱스를 타지는 못하지만
# idx_events_robot_time (robot_id, occurred_at DESC) 이 정렬과 로봇 필터를
# 주고, 조제 로봇의 이벤트는 거의 전부 manipulator.* 라 LIMIT 이 금방 찬다.
DETAIL_SQL = """
    SELECT r.robot_id, e.event_code, e.occurred_at, e.payload
    FROM robots r
    JOIN LATERAL (
        SELECT event_code, occurred_at, payload
        FROM events
        WHERE robot_id = r.robot_id
          AND event_code LIKE 'manipulator.%'
        ORDER BY occurred_at DESC
        LIMIT $1
    ) e ON TRUE
    WHERE r.robot_type = 'manipulator'
    -- 접기는 오래된 것부터 한다. 최신순으로 받아 뒤집으면 '지금 도는
    -- 사이클' 판정이 거꾸로 선다.
    ORDER BY r.robot_id, e.occurred_at
"""

CYCLE_STARTED = "manipulator.cycle_started"
CYCLE_COMPLETED = "manipulator.cycle_completed"
CYCLE_ABORTED = "manipulator.cycle_aborted"
PICK_SUCCEEDED = "manipulator.pick_succeeded"
PICK_FAILED = "manipulator.pick_failed"
SERVO_FAULT = "manipulator.servo_fault"
POLICY_LOADED = "manipulator.policy_loaded"
HOMING_REQUIRED = "manipulator.homing_required"


def _payload(row) -> dict:
    """asyncpg 는 JSONB 를 문자열로 돌려준다 (routers/events.py 와 같은 이유).

    테스트는 dict 를 그대로 넘긴다. 둘 다 받는 이유는 접기 함수를 DB 없이
    돌릴 수 있게 하려는 것이다 — slo.judge 와 같은 구조다.
    """
    raw = row["payload"]
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 정본을 벗어난 payload 하나가 팔 전체 지표를 죽이면 안 된다.
            return {}
    return dict(raw)


def _int(value) -> int | None:
    """payload 값은 로봇이 채운다. 문자열로 와도 화면이 깨지지 않게 한다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _Cycle:
    """접는 도중의 사이클 하나. 닫히면 집계로 넘어간다."""

    def __init__(self, dispense_id: str | None = None,
                 started_at: datetime | None = None) -> None:
        self.dispense_id = dispense_id
        self.started_at = started_at
        self.pick_ok = 0
        self.pick_failed = 0
        # 재시도로 성공한 건. attempt 를 안 보면 한 번에 집은 것과 세 번 만에
        # 집은 것이 같은 성공으로 뭉개져 정책 품질이 안 보인다 (§6.2).
        self.pick_retried = 0
        self.ended_at: datetime | None = None
        self.completed = False
        self.duration_ms: int | None = None

    @property
    def touched(self) -> bool:
        return (self.started_at is not None or self.pick_ok > 0
                or self.pick_failed > 0)


def _percentile(values: list[int], ratio: Fraction) -> int | None:
    """nearest-rank. 보간하지 않는다.

    실제로 돈 사이클 하나의 시간을 그대로 보여준다. 표본이 20건이면 p95 는
    사실상 최댓값인데, 그것이 정직하다 — 없는 표본을 보간해서 만든 숫자가
    p95 라는 이름을 달고 나가는 것보다 낫다.

    ratio 가 float 이 아니라 Fraction 인 이유는 slo.budget_total 과 같다.
    ceil(20 * 0.95) 는 0.95 가 이진수로 정확하지 않아 19 가 아니라 다른 답이
    나올 수 있고, 그 차이는 화면에서 안 보인다.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = min(len(ordered), max(1, math.ceil(len(ordered) * ratio)))
    return ordered[rank - 1]


def _fold(events) -> ManipulatorDetail:
    """로봇 한 대의 이벤트를 오래된 것부터 접는다."""
    policy_checkpoint = policy_revision = None
    policy_loaded_at = None
    servo_fault: ServoFault | None = None
    homing_reason: str | None = None
    homing_at: datetime | None = None

    closed: list[_Cycle] = []
    current = _Cycle()

    for row in events:
        code = row["event_code"]
        at = row["occurred_at"]
        payload = _payload(row)

        if code == CYCLE_STARTED:
            # 앞 사이클이 끝나지 않은 채 새 사이클이 시작됐다면 그 사이클은
            # 끝을 못 봤다는 뜻이다(로봇 재기동 등). 종료 이벤트가 없으므로
            # 완주로도 포기로도 셀 수 없어 버린다.
            current = _Cycle(payload.get("dispense_id"), at)
            # 새 사이클이 시작됐다는 것은 홈 복귀가 끝났다는 뜻이다.
            # 팔이 별도의 해제 이벤트를 내지 않으므로 여기가 유일한 해소점이다.
            homing_reason = None
            homing_at = None
        elif code == PICK_SUCCEEDED:
            current.pick_ok += 1
            attempt = _int(payload.get("attempt"))
            if attempt is not None and attempt > 1:
                current.pick_retried += 1
        elif code == PICK_FAILED:
            current.pick_failed += 1
        elif code in (CYCLE_COMPLETED, CYCLE_ABORTED):
            # started 를 못 본 사이클도 닫는다. 창 끝에서 잘렸을 뿐이고,
            # 그 사이클의 pick 결과는 여기 모여 있다.
            current.ended_at = at
            current.completed = code == CYCLE_COMPLETED
            current.duration_ms = _int(payload.get("duration_ms"))
            if current.dispense_id is None:
                current.dispense_id = payload.get("dispense_id")
            closed.append(current)
            current = _Cycle()
        elif code == SERVO_FAULT:
            servo_fault = ServoFault(
                joint=str(payload.get("joint") or ""),
                fault_bits=_int(payload.get("fault_bits")),
                temp_c=_float(payload.get("temp_c")),
                occurred_at=at,
            )
        elif code == POLICY_LOADED:
            policy_checkpoint = payload.get("checkpoint_id")
            policy_revision = payload.get("dataset_revision")
            policy_loaded_at = at
        elif code == HOMING_REQUIRED:
            homing_reason = str(payload.get("reason") or "")
            homing_at = at

    window_cycles = closed[-DEFAULT_WINDOW:]
    pick_ok = sum(c.pick_ok for c in window_cycles)
    pick_failed = sum(c.pick_failed for c in window_cycles)
    picks = pick_ok + pick_failed
    durations = [c.duration_ms for c in window_cycles
                 if c.completed and c.duration_ms is not None]

    return ManipulatorDetail(
        policy_checkpoint_id=policy_checkpoint,
        policy_dataset_revision=policy_revision,
        policy_loaded_at=policy_loaded_at,
        active_dispense_id=current.dispense_id if current.touched else None,
        active_started_at=current.started_at if current.touched else None,
        homing_required=homing_at is not None,
        homing_reason=homing_reason,
        last_servo_fault=servo_fault,
        window=DEFAULT_WINDOW,
        window_cycles=len(window_cycles),
        sample_complete=len(window_cycles) >= DEFAULT_WINDOW,
        cycles_completed=sum(1 for c in window_cycles if c.completed),
        cycles_aborted=sum(1 for c in window_cycles if not c.completed),
        last_cycle_ended_at=window_cycles[-1].ended_at if window_cycles else None,
        pick_succeeded=pick_ok,
        pick_failed=pick_failed,
        pick_retried=sum(c.pick_retried for c in window_cycles),
        # 표본이 없으면 성공률이 없다. 0.0 으로 돌려주면 "한 번도 못 집었다"
        # 와 구분되지 않고, 그 둘은 화면에서 아주 다르게 그려야 한다.
        pick_success_rate=(pick_ok / picks) if picks else None,
        cycle_time_p50_ms=_percentile(durations, P50),
        cycle_time_p95_ms=_percentile(durations, P95),
    )


def summarize(rows) -> dict[str, ManipulatorDetail]:
    """DETAIL_SQL 의 행을 로봇별 detail 로 접는다. DB 를 모른다 — 행만 받는다.

    행이 하나도 없는 팔(아직 아무것도 보고하지 않은 로봇)은 여기 안 담긴다.
    호출부가 빈 detail 을 채워야 한다 — "보고가 없다" 는 팔의 정상 초기
    상태이고, 그것 때문에 로봇이 목록에서 빠지면 안 된다.
    """
    by_robot: dict[str, list] = {}
    for row in rows:
        by_robot.setdefault(row["robot_id"], []).append(row)
    return {robot_id: _fold(events) for robot_id, events in by_robot.items()}
