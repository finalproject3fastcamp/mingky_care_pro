"""검사실 점유 — 다음에 어디로 보낼 것인가.

## 왜 구간 예약으로는 안 되는가

`fleet_reserve` 는 **공간**을 다룬다. 두 대가 같은 외길에 동시에 들어가지
않게 하는 것이 전부다. 그런데 "환자 둘이 다 X-ray 를 받아야 한다" 는 공간
문제가 아니다 — 검사실 앞에 두 대가 나란히 설 수 있어도, 방은 한 번에 한
명만 받는다.

시드를 보면 이게 예외가 아니라 기본이다. 세 증상이 전부 1단계가 X-ray 라
(`001_initial_data.sql`), 환자 둘을 받으면 반드시 겹친다.

## 답은 기다리는 것이 아니라 순서를 바꾸는 것

앞 환자가 X-ray 를 끝낼 때까지 뒤 환자를 세워 두면, 그 시간만큼 안내가
길어지고 복도에 로봇 한 대가 서 있게 된다. 대신 **아직 안 한 다른 검사를
먼저** 보낸다 — X-ray 가 차 있으면 CT 부터.

## 무엇을 '차 있다' 로 보는가

도착해서 검사 중인 것만 세면 늦다. 상대가 X-ray 로 **가는 중**일 때 이쪽도
X-ray 로 보내면, 둘 다 도착한 뒤에야 겹친 것을 알게 된다. 그래서 다른 세션의
**현재 단계**(`session_current_step`)를 전부 차 있는 것으로 본다 — 가는 중도
포함된다.

## 저장하지 않는다

점유 상태를 따로 들고 있지 않는다. `session_steps` 의 도착·완료 시각과
그 위의 뷰가 이미 답을 갖고 있다. 같은 사실을 컬럼으로 한 번 더 들면 둘이
갈라지는 날이 오고, 그때 어느 쪽이 맞는지 판단할 근거가 없다 (§7.3 과 같은 규칙).
"""

from __future__ import annotations

from dataclasses import dataclass

# 진행 중인 세션마다 '지금 쓰고 있는 검사실'. 가는 중도 포함된다 —
# session_current_step 이 '완료 안 된 것 중 방문에 들어간 가장 최근 것' 이라
# 이동 중에도 목적지가 잡힌다 (013).
#
# 로봇별로 나눠 묻지 않고 한 번에 가져온다. 판정은 두 대를 같이 봐야 하고,
# 로봇마다 따로 물으면 그 사이에 상대 세션이 바뀔 수 있다.
CURRENT_SQL = """
    SELECT gs.robot_id, gs.session_id, cs.visit_name
    FROM guidance_sessions gs
    LEFT JOIN session_current_step cs USING (session_id)
    WHERE gs.ended_at IS NULL
"""

# 진행 중인 세션들의 남은 단계. 계획 순서대로.
REMAINING_SQL = """
    SELECT ss.session_id, ss.step_order, ss.visit_name,
           ss.visit_seq IS NOT NULL AS started
    FROM session_steps ss
    JOIN guidance_sessions gs USING (session_id)
    WHERE gs.ended_at IS NULL AND ss.completed_at IS NULL
    ORDER BY ss.session_id, ss.step_order
"""


@dataclass(frozen=True)
class Step:
    step_order: int
    visit_name: str
    # 이미 방문에 들어간 단계인가. 들어갔으면 바꾸지 않는다.
    started: bool


@dataclass(frozen=True)
class Choice:
    """다음에 갈 곳과, 계획과 다르다면 그 이유."""

    step_order: int | None
    visit_name: str | None
    # 계획 순서를 건너뛰었는가. 화면이 "X-ray 사용 중 — CT 먼저" 를 말할 근거.
    reordered: bool = False
    # 건너뛴 방과 그 방을 쓰고 있는 로봇.
    skipped_visit: str | None = None
    blocked_by: str | None = None


def choose_next(remaining: list[Step], busy: dict[str, str]) -> Choice:
    """남은 단계 중 지금 갈 수 있는 곳. DB 를 모른다 — 값만 받는다.

    `busy` 는 검사실 이름 → 그 방을 쓰고 있는 로봇.

    규칙은 셋이다.

      1. 이미 방문에 들어간 단계가 있으면 그것을 계속한다. 가는 중에 목적지가
         바뀌면 로봇이 왔다 갔다 하고, 환자는 무슨 일이 벌어지는지 모른다.
      2. 계획 순서를 우선한다. 앞의 것이 비어 있으면 굳이 바꾸지 않는다.
      3. 앞의 것이 차 있으면 **뒤에서 비어 있는 첫 번째**를 먼저 간다.

    전부 차 있으면 `reordered=False` 로 계획대로 돌려준다 — 어차피 기다려야
    하고, 그때는 `fleet_reserve` 가 복도에서 세운다. 갈 곳이 없는 것과
    기다리는 것은 다르다.
    """
    if not remaining:
        return Choice(None, None)

    started = next((s for s in remaining if s.started), None)
    if started is not None:
        return Choice(started.step_order, started.visit_name)

    planned = remaining[0]
    if planned.visit_name not in busy:
        return Choice(planned.step_order, planned.visit_name)

    for step in remaining[1:]:
        if step.visit_name not in busy:
            return Choice(
                step.step_order, step.visit_name,
                reordered=True,
                skipped_visit=planned.visit_name,
                blocked_by=busy[planned.visit_name],
            )

    # 남은 곳이 전부 차 있다. 계획대로 두고 복도에서 기다린다.
    return Choice(
        planned.step_order, planned.visit_name,
        skipped_visit=planned.visit_name,
        blocked_by=busy[planned.visit_name],
    )


def summarize(current_rows, remaining_rows) -> dict[str, Choice]:
    """DB 행 두 묶음을 로봇별 판정으로 접는다. 커넥션을 모른다 — 행만 받는다.

    `slo.judge` · `fleet_config.summarize` 와 같은 규칙이다. 테스트가 DB 없이
    이 함수만 부를 수 있어야 판정을 실기 없이 검증할 수 있다.
    """
    session_of: dict[str, int] = {}
    busy_by_robot: dict[str, str] = {}
    for row in current_rows:
        session_of[row["robot_id"]] = row["session_id"]
        if row["visit_name"]:
            busy_by_robot[row["robot_id"]] = row["visit_name"]

    remaining: dict[int, list[Step]] = {}
    for row in remaining_rows:
        remaining.setdefault(row["session_id"], []).append(Step(
            step_order=int(row["step_order"]),
            visit_name=str(row["visit_name"]),
            started=bool(row["started"]),
        ))

    out: dict[str, Choice] = {}
    for robot_id, session_id in session_of.items():
        # 남이 쓰는 방만 센다. 자기가 쓰는 방을 차 있다고 보면 자기 목적지를
        # 자기가 막아 영원히 순서를 바꾸게 된다.
        busy = {visit: owner for owner, visit in busy_by_robot.items()
                if owner != robot_id}
        out[robot_id] = choose_next(remaining.get(session_id, []), busy)
    return out
