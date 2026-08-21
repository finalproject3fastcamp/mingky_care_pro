"""로봇과 조정층을 잇는 링크 — 목표를 받고 판정을 돌려준다.

    로봇 ──ws──→ /robots/{id}/fleet   "나 xray_room_goal 로 간다"
    로봇 ←─ws─── /robots/{id}/fleet   "가라" / "서라 (seg-4 를 pinky-02 가 쥐었다)"

`fleet_pose` 가 어디 있는지를, 이 모듈이 어디로 가는지를 갖는다. 둘이 모이면
`fleet_reserve` 가 판정할 수 있다 — 지금까지는 목표를 아무도 몰라 예약층이
있어도 아무 판정을 못 했다.

## 왜 orders 를 안 쓰나

`routers/orders.py` 는 모든 명령을 `control_audit` 에 남긴다. 조정층은 사람이
아니라 기계라, 그 기록이 쌓이면 감사 로그가 오염되고 fleet 탭의 '익명 비율'
경고가 상시 켜진다. 관측 채널을 조작 채널에서 분리한 것과 같은 이유다
(`fleet_pose` 모듈 주석).

## 왜 판정을 주기적으로 밀어내나

한 번만 보내고 끝내면 **로봇이 hold 를 영원히 들고 있는 상태가 가능해진다.**
서버가 죽거나 회선이 끊기면 풀어줄 사람이 없다. 그래서 매 tick 마다 현재
판정을 다시 보내고, 로봇은 `ttl_sec` 안에 갱신이 없으면 **스스로 푼다**.

이 설계에서 침묵은 '계속 서 있어라' 가 아니라 '조정이 없다' 다. 조정층은
안전장치가 아니라 교착 예방층이므로, 조정이 사라지면 이 기능을 붙이기 전
동작(LiDAR·MPPI)으로 돌아가는 것이 맞다.

## 워커 1개 전제

`teleop` · `arming` 과 같은 인메모리 패턴이다. `main.py` 의 advisory lock 이
그 전제를 지킨다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import db, fleet_map, fleet_pose, fleet_reserve, fleet_rooms

log = logging.getLogger("mingky")

# 판정을 다시 밀어내는 주기.
TICK_SEC = 1.0

# 로봇 쪽 데드맨. 이 시간 안에 판정이 다시 안 오면 로봇이 스스로 hold 를
# 푼다. tick 의 몇 배로 잡아 한두 번 유실에는 흔들리지 않게 한다 — 무선
# 구간이라 한 프레임쯤은 정상적으로 없어진다.
DECISION_TTL_SEC = 5.0

# 목표 보고가 이보다 오래되면 없는 것으로 본다. 로봇이 조용해졌는데 옛 목표로
# 남의 길을 계속 막으면, 그 판정을 되돌릴 근거가 아무 데도 없다.
INTENT_STALE = timedelta(seconds=15)


@dataclass(frozen=True)
class Intent:
    """로봇이 스스로 보고한 '지금 어디로 가는가'.

    `guiding` 을 서버가 세션 표에서 유추하지 않고 로봇에게 받는 이유는, 우선
    순위 판정이 **로봇이 실제로 환자를 데리고 있는지**에 달려 있기 때문이다.
    세션은 열려 있는데 로봇은 아직 출발 전일 수 있다.
    """

    goal_waypoint: str | None
    guiding: bool
    reported_at: datetime


_intents: dict[str, Intent] = {}
# robot_id → 판정을 받을 소켓. 로봇 한 대에 하나.
_links: dict[str, object] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def report(robot_id: str, goal_waypoint: str | None, guiding: bool) -> None:
    _intents[robot_id] = Intent(
        goal_waypoint=goal_waypoint or None,
        guiding=bool(guiding),
        reported_at=_now(),
    )


def forget(robot_id: str) -> None:
    """링크가 끊기면 목표도 버린다. 없는 로봇이 길을 막으면 안 된다."""
    _intents.pop(robot_id, None)


def attach(robot_id: str, socket) -> object | None:
    """새 링크를 등록하고, 밀어내야 할 옛 소켓이 있으면 돌려준다."""
    old = _links.get(robot_id)
    _links[robot_id] = socket
    return old if old is not socket else None


def detach(robot_id: str, socket) -> None:
    if _links.get(robot_id) is socket:
        del _links[robot_id]
    forget(robot_id)


def linked() -> list[str]:
    return sorted(_links)


def snapshot() -> dict[str, Intent]:
    return dict(_intents)


def current_plan(now: datetime | None = None) -> fleet_reserve.Plan:
    """지금 아는 것으로 판정한다. 소켓을 모른다 — 테스트가 이 함수만 부른다."""
    now = now or _now()
    fleet = fleet_map.get()
    poses = fleet_pose.snapshot()

    intents = []
    for robot_id in sorted(set(_intents) | set(poses)):
        intent = _intents.get(robot_id)
        if intent is not None and now - intent.reported_at > INTENT_STALE:
            intent = None
        sample = poses.get(robot_id)
        area = None
        if fleet is not None and sample is not None:
            area = fleet.area_at(sample.x, sample.y)
        goal_area = None
        if fleet is not None and intent is not None and intent.goal_waypoint:
            goal_area = fleet.area_of_waypoint(intent.goal_waypoint)
        intents.append(fleet_reserve.RobotIntent(
            robot_id=robot_id,
            area=area,
            goal_area=goal_area,
            guiding=bool(intent and intent.guiding),
            # 위치를 모르면 True 로 남긴다 — 모를 때는 보수적으로 본다.
            blocking=(fleet.blocks_at(sample.x, sample.y)
                      if fleet is not None and sample is not None else True),
        ))
    return fleet_reserve.plan(fleet, intents)


async def room_choices() -> dict[str, fleet_rooms.Choice]:
    """검사실 겹침 판정. DB 가 없거나 흔들려도 조정 전체를 멈추지 않는다.

    빈 딕셔너리를 돌려주면 로봇은 계획 순서대로 간다 — 이 기능을 붙이기 전
    동작이다. 구간 예약이 fail-open 인 것과 같은 규칙이고, 순서 재정렬은
    안전이 아니라 효율이라 더더욱 그렇다.
    """
    try:
        pool = db.get_pool()
        async with pool.acquire() as conn:
            current = await conn.fetch(fleet_rooms.CURRENT_SQL)
            remaining = await conn.fetch(fleet_rooms.REMAINING_SQL)
        return fleet_rooms.summarize(current, remaining)
    except Exception as exc:
        log.warning("검사실 판정 생략 (계획 순서로 진행): %s", exc)
        return {}


def decision_message(decision: fleet_reserve.Decision,
                     choice: fleet_rooms.Choice | None = None) -> dict:
    """로봇에게 보내는 한 프레임.

    `proceed` 하나가 전부다. 로봇은 이 값만 보고 서거나 간다 — 서버가 구간
    이름으로 무엇을 하라고 지시하지 않는다. **판정은 서버가, 주행은 로봇이**
    한다는 경계를 메시지 모양이 지키게 한다.

    나머지는 사람이 읽을 근거다. 왜 섰는지가 안 남으면 화면도 타임라인도
    "그냥 멈춰 있다" 밖에 말하지 못한다.
    """
    message = {
        "type": "decision",
        "proceed": decision.proceed,
        "reason": decision.reason,
        "blocked_by": decision.blocked_by,
        "segments": sorted(decision.holds),
        "ttl_sec": DECISION_TTL_SEC,
    }
    if choice is not None and choice.visit_name:
        # 다음에 어느 검사실로 갈 것인가. **step_order 를 같이 보낸다** —
        # 로봇이 방금 끝낸 단계를 서버가 아직 못 봤을 수 있고, 그때 이름만
        # 보내면 방금 마친 방으로 다시 간다.
        message["next_visit"] = {
            "step_order": choice.step_order,
            "visit_name": choice.visit_name,
            "reordered": choice.reordered,
            "skipped_visit": choice.skipped_visit,
            "blocked_by": choice.blocked_by,
        }
    return message


async def push(now: datetime | None = None) -> int:
    """붙어 있는 로봇들에게 현재 판정을 보낸다. 보낸 대수를 돌려준다."""
    if not _links:
        return 0
    plan = current_plan(now)
    choices = await room_choices()
    sent = 0
    for robot_id, socket in list(_links.items()):
        decision = plan.decisions.get(robot_id)
        if decision is None:
            # 위치도 목표도 모르는 로봇. 조정 대상이 아니라는 사실 자체를
            # 보내야 로봇이 데드맨으로 넘어가지 않고 정상 주행한다.
            decision = fleet_reserve.Decision(robot_id)
        try:
            await socket.send_json(
                decision_message(decision, choices.get(robot_id)))
            sent += 1
        except Exception:
            # 끊긴 소켓이다. 정리는 그쪽 핸들러의 finally 가 한다 —
            # 여기서 지우면 같은 일을 두 곳에서 하게 된다.
            continue
    return sent


async def monitor() -> None:
    """판정을 주기적으로 밀어낸다. `main.py` 의 lifespan 이 띄운다."""
    while True:
        await asyncio.sleep(TICK_SEC)
        try:
            await push()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 한 번의 판정 실패로 루프가 죽으면 그 뒤로 조정이 영영 멈춘다.
            # 로봇은 데드맨으로 풀리므로 위험하지는 않지만, 조용히 꺼진 것을
            # 아무도 모르는 상태가 나쁘다.
            log.error("군집 판정 주기 실패: %s", exc)


def reset() -> None:
    _intents.clear()
    _links.clear()
