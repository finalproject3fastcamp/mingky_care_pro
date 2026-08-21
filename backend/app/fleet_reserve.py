"""구간 예약 — 누가 어느 외길에 들어가도 되는가.

핑키 2대를 동시에 돌릴 때 생기는 세 상황이 전부 한 표를 읽고 쓴다.

    둘이 가까워질 때        "이 외길 지금 비었나"
    둘의 경로가 겹칠 때     "내가 지날 외길들 비었나"
    같은 진료실 앞 대기     "그 방이 있는 구간 비었나"

waypoint 23곳 중 11곳은 로봇이 서 있으면 복도를 막는다. 그래서 '방을
점유했다' 와 '복도를 막았다' 가 물리적으로 같은 사실이고, 표 하나가 셋을 다
답한다.

## 이것은 안전장치가 아니다

여기서 나오는 것은 `hold` 와 `release` 뿐이고 **모터 명령은 없다.** 물리적
충돌 회피는 LiDAR → local costmap → MPPI 가 하고, 최종 정지는 twist_mux
워치독과 `emergency_stop` 이 한다. 이 층은 그 위에 얹는 **교착 예방**이다.

그래서 실패 방향이 정해져 있다 — 구간 지도가 없거나 위치를 모르면 조정을
하지 않는다(fail-open). 조정이 사라지면 이 기능을 붙이기 전 동작으로 돌아갈
뿐이고, 로봇이 위험해지지는 않는다.

## 왜 zone 에서만 기다리게 하나

이 지도는 절반 이상이 외길이라 **외길 안에서 멈추면 그게 봉쇄다.** 상대가
비켜 지나갈 수 없으므로, 양보한 로봇이 그대로 교착의 원인이 된다.

그래서 규칙이 하나 선다 — **한 로봇은 zone 에서 출발할 때 다음 zone 까지의
외길 전부를 한꺼번에 잡는다.** 중간에 못 잡으면 애초에 출발하지 않는다.
이러면 외길 안에서 기다리는 일이 없고, '쥔 채로 기다리는' 순환도 생기지
않아 교착이 구조적으로 불가능해진다.

예외가 하나 있다. 목적지 자체가 외길에 있는 경우(11/23)다. 그때는 그 구간을
쥔 채로 서 있게 되고, 이건 피할 수 없다 — 상대는 그 구간이 풀릴 때까지
기다리거나 다른 목적지를 먼저 가야 한다. 후자가 방문 순서 재정렬이다.

## 우선순위

동시에 원하면 누가 먼저인가. 규칙은 둘뿐이고 **결정적**이어야 한다 — 같은
상황에서 매번 같은 답이 나오지 않으면 두 대가 서로 양보하다 둘 다 멈춘다.

    1. 환자를 안내 중인 로봇이 먼저 (세션 없는 복귀·대기보다 급하다)
    2. 그래도 같으면 robot_id 가 작은 쪽

2번은 임의다. 임의라는 것이 중요하다 — '더 가까운 쪽' 같은 규칙은 두 로봇이
서로 자기가 가깝다고 판단할 여지를 남긴다. 여기서는 서버 한 곳이 판정하므로
그럴 일이 없지만, 규칙 자체가 대칭을 깨는 편이 나중에 로봇 쪽으로 판정을
옮길 때도 안전하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fleet_map import FleetMap

# 왜 기다리는가. 화면과 이벤트가 이 코드를 그대로 쓴다.
WAIT_PEER_SEGMENT = "peer_in_segment"      # 지날 외길을 상대가 쥐고 있다
WAIT_PEER_GOAL = "peer_at_goal"            # 목적지 구간을 상대가 쥐고 있다
WAIT_NO_ROUTE = "no_route"                 # 구간 그래프로 길이 없다


@dataclass(frozen=True)
class RobotIntent:
    """한 대가 지금 어디 있고 어디로 가려는가.

    pose 는 `fleet_pose`, goal 은 로봇이 보고한 값이다. 둘 중 하나라도 없으면
    그 로봇은 조정 대상이 아니다 — 모르는 채로 남의 길을 막으면 안 된다.
    """

    robot_id: str
    area: str | None                # 지금 있는 구간
    goal_area: str | None           # 목표가 있는 구간
    guiding: bool = False           # 환자 안내 중인가 (우선순위 1번)


@dataclass(frozen=True)
class Decision:
    robot_id: str
    # 이 로봇이 쥐어야 할 구간들. 빈 집합이면 쥘 것이 없다(zone 안에 있다).
    holds: frozenset[str] = frozenset()
    # 갈 수 있는가. False 면 지금 있는 자리에서 기다린다.
    proceed: bool = True
    reason: str | None = None
    # 기다린다면 누구 때문인가. 화면이 "핑키 2호를 기다리는 중" 을 말할 수 있게.
    blocked_by: str | None = None


@dataclass(frozen=True)
class Plan:
    decisions: dict[str, Decision] = field(default_factory=dict)

    def held_by(self, robot_id: str) -> frozenset[str]:
        decision = self.decisions.get(robot_id)
        return decision.holds if decision else frozenset()

    def waiting(self) -> dict[str, Decision]:
        return {r: d for r, d in self.decisions.items() if not d.proceed}


def _priority(intent: RobotIntent) -> tuple[int, str]:
    """작을수록 먼저. 안내 중이 우선, 그다음 robot_id 순."""
    return (0 if intent.guiding else 1, intent.robot_id)


def leg(fleet: FleetMap, start: str, goal: str) -> list[str] | None:
    """지금 zone 에서 **다음 zone(또는 목적지)까지** 지나는 외길들.

    경로 전체를 한꺼번에 잡지 않는 것이 요점이다. 전부 잡으면 한 대가 지도
    절반을 쥐고, 상대는 그 세션이 끝날 때까지 아무 데도 못 간다.

    한 다리(leg)만 잡되 **끝까지** 잡는다 — 중간에 못 잡아 외길 안에 서면
    그게 봉쇄이기 때문이다(모듈 주석).
    """
    path = fleet.route(start, goal)
    if path is None:
        return None
    out: list[str] = []
    for area_id in path[1:]:           # 지금 있는 구간은 이미 내 것이다
        area = fleet.areas.get(area_id)
        if area is None:
            return None
        out.append(area_id)
        if area.kind == "zone":
            # 다음 zone 에 닿았다. 여기까지가 한 다리다 — 저기서 다시 판단한다.
            break
    return out


def plan(fleet: FleetMap | None, intents: list[RobotIntent]) -> Plan:
    """누가 무엇을 쥐고 누가 기다리는지 정한다. DB 도 소켓도 모른다.

    구간 지도가 없으면 **아무도 막지 않는다** — 조정이 꺼진 상태이지 모두
    정지가 아니다(fail-open, 모듈 주석).
    """
    if fleet is None:
        return Plan({i.robot_id: Decision(i.robot_id) for i in intents})

    # 위치를 모르는 로봇은 조정에서 뺀다. 지도에 없는 로봇이 남의 길을
    # 막으면, 그 판정을 되돌릴 근거가 아무 데도 없다.
    known = [i for i in intents if i.area is not None and i.area in fleet.areas]
    decisions: dict[str, Decision] = {
        i.robot_id: Decision(i.robot_id) for i in intents if i not in known}

    # --- 1단계: 점유. 지금 서 있는 자리는 사실이지 요청이 아니다 ---
    #
    # **배정보다 먼저 전부 확정해야 한다.** 섞어서 돌리면 우선순위가 높은
    # 로봇에게 상대가 지금 서 있는 복도를 내주게 된다 — 실제로 그렇게 만들었다가
    # zone-A 의 로봇이 seg-1 에 서 있는 상대의 자리를 배정받았다.
    occupied: dict[str, str] = {}
    touching: dict[str, str] = {}       # 이미 맞닿아 버린 로봇 → 상대
    for intent in sorted(known, key=_priority):
        for area_id in fleet.blocked_by(intent.area):
            other = occupied.get(area_id)
            if other is None:
                occupied[area_id] = intent.robot_id
            elif other != intent.robot_id:
                touching[intent.robot_id] = other

    # --- 2단계: 배정 ---
    taken = dict(occupied)
    for intent in sorted(known, key=_priority):
        here = {a for a in fleet.blocked_by(intent.area)
                if taken.get(a) == intent.robot_id}

        if intent.robot_id in touching:
            # 둘이 이미 맞닿아 있다. 조정이 늦은 것이고 여기서 할 수 있는
            # 일은 없다 — 물리적 회피는 LiDAR·MPPI 가 맡는다.
            decisions[intent.robot_id] = Decision(
                intent.robot_id, holds=frozenset(here), proceed=False,
                reason=WAIT_PEER_SEGMENT,
                blocked_by=touching[intent.robot_id])
            continue

        if intent.goal_area is None or intent.goal_area == intent.area:
            decisions[intent.robot_id] = Decision(
                intent.robot_id, holds=frozenset(here))
            continue

        wanted = leg(fleet, intent.area, intent.goal_area)
        if wanted is None:
            decisions[intent.robot_id] = Decision(
                intent.robot_id, holds=frozenset(here), proceed=False,
                reason=WAIT_NO_ROUTE)
            continue

        need: set[str] = set()
        for area_id in wanted:
            need |= fleet.blocked_by(area_id)
        clash = sorted(a for a in need if taken.get(a, intent.robot_id)
                       != intent.robot_id)
        if clash:
            blocker = taken[clash[0]]
            reason = (WAIT_PEER_GOAL
                      if intent.goal_area in clash else WAIT_PEER_SEGMENT)
            # 잡지 못했으면 **아무것도 쥐지 않는다.** 일부만 쥐고 기다리면
            # 그게 hold-and-wait 이고, 그 순간 교착이 가능해진다.
            decisions[intent.robot_id] = Decision(
                intent.robot_id, holds=frozenset(here), proceed=False,
                reason=reason, blocked_by=blocker)
            continue

        for area_id in need:
            taken[area_id] = intent.robot_id
        decisions[intent.robot_id] = Decision(
            intent.robot_id, holds=frozenset(here | need))

    return Plan(decisions)
