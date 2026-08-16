"""형상 — 4대가 지금 무엇으로 돌고 있는가 (§7.2 · 로드맵 10).

"데모가 어제는 됐는데 오늘 안 된다" 의 원인 대부분이 형상 불일치다. 그런데
지금까지 그걸 한 화면에서 볼 곳이 없었다 — 커밋은 인벤토리 안쪽에, 맵은
아무 데도, 정책 체크포인트는 조제 패널에 흩어져 있었다. 셋을 나란히 놓고
갈린 것을 표시하는 것이 이 모듈의 전부다.

## 타입 안에서만 비교한다

§4.4 — 팔의 버전은 코드 SHA 가 아니라 **정책 체크포인트 + 데이터셋
revision** 이다. OMX 의 SHA 와 핑키의 SHA 를 나란히 놓으면 항상 다르고, 항상
빨간 경고는 없는 것과 같다. 그래서 축마다 대상 타입이 정해져 있다.

  commit · map      mobile      같은 저장소·같은 맵으로 돌아야 한다
  policy · dataset  manipulator 같은 체크포인트로 집어야 한다

## '다르다' 와 '모른다' 를 구분한다

보고하지 않은 로봇은 불일치가 아니다. OMX 는 게이트웨이가 아직 없어
(로드맵 6) 코드 형상을 정상적으로 보고하지 않는다. 그걸 불일치로 세면 패널이
영구히 빨갛고, 진짜 불일치가 그 속에 묻힌다. 대신 **판정이 몇 대를 본
것인지**를 같이 내려보낸다 — 2대만 비교한 "같다" 를 4대의 "같다" 로 읽으면
안 된다.

## 저장하지 않는다

전부 이미 있는 재료다 — 커밋·맵은 `robot_inventory` 의 payload, 정책은
`events`(§6.2 의 `manipulator.policy_loaded`). 같은 사실을 컬럼으로 한 번 더
들고 있으면 둘이 갈라지는 날이 오고, 그때 어느 쪽이 맞는지 판단할 근거가
없다(§7.3 과 같은 규칙).
"""

from __future__ import annotations

import json

from .schemas import (
    ConfigMismatchOut,
    FleetConfigOut,
    ManipulatorDetail,
    RobotConfigOut,
)

# 로봇 목록에 인벤토리를 붙인다. 보고가 없는 로봇도 목록에 남아야 한다 —
# 빠지면 화면에서 "이 로봇은 형상을 모른다" 자체가 안 보인다.
CONFIG_SQL = """
    SELECT r.robot_id, r.robot_type, r.display_name,
           i.payload, i.reported_at
    FROM robots r
    LEFT JOIN robot_inventory i ON i.robot_id = r.robot_id
    WHERE r.is_active
    ORDER BY r.robot_id
"""

# 축 → 비교 대상 타입. 여기가 정본이고 아래 판정은 이 표만 따른다.
AXES = (
    ("commit", "mobile"),
    ("map", "mobile"),
    ("policy", "manipulator"),
    ("dataset", "manipulator"),
)


def _payload(row) -> dict:
    """asyncpg 는 JSONB 를 문자열로 돌려준다 (dispense.py 와 같은 이유)."""
    raw = row["payload"]
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return dict(raw)


def primary_workspace(workspaces: list[dict]) -> dict | None:
    """이 로봇에서 '우리 코드' 가 돌고 있는 워크스페이스.

    inventory_rules.has_mixed_workspaces 와 같은 기준으로 거른다 — 노드가
    안 잡힌 워크스페이스(빌드만 해둔 것)와 git 이 아닌 워크스페이스(제조사
    플랫폼)는 우리 형상이 아니다. 그걸 세면 정상 배치에서도 커밋이 갈린
    것처럼 보인다.

    둘 이상 남으면 노드가 가장 많이 뜬 쪽을 고른다. 그 상태 자체가 이미
    문제이고(`mixed_workspaces` 가 따로 경고한다), 여기서는 대표값 하나를
    골라 형상 비교를 계속할 수 있게 하는 것이 목적이다.
    """
    ours = [w for w in workspaces
            if w.get("commit") and (w.get("process_count") or 0) > 0]
    if not ours:
        return None
    return max(ours, key=lambda w: w.get("process_count") or 0)


def _row_to_config(row, detail: ManipulatorDetail | None) -> RobotConfigOut:
    payload = _payload(row)
    workspace = primary_workspace(payload.get("workspaces") or [])

    return RobotConfigOut(
        robot_id=row["robot_id"],
        robot_type=row["robot_type"],
        display_name=row["display_name"],
        reported_at=row["reported_at"],
        commit=(workspace or {}).get("commit"),
        branch=(workspace or {}).get("branch"),
        dirty=bool((workspace or {}).get("dirty")),
        workspace_path=(workspace or {}).get("path"),
        map_name=payload.get("map_name"),
        map_hash=payload.get("map_hash"),
        # 팔의 정책은 인벤토리가 아니라 events 에서 온다. 발행 측이 ROS
        # 게이트웨이가 아니라 조제 사이클이기 때문이다 (§6.2).
        policy_checkpoint_id=detail.policy_checkpoint_id if detail else None,
        policy_dataset_revision=detail.policy_dataset_revision if detail else None,
        policy_loaded_at=detail.policy_loaded_at if detail else None,
    )


def _value_of(robot: RobotConfigOut, axis: str) -> str | None:
    if axis == "commit":
        return robot.commit
    if axis == "map":
        # 이름이 아니라 지문으로 비교한다. 같은 이름의 다른 맵이 실제로 있다.
        return robot.map_hash
    if axis == "policy":
        return robot.policy_checkpoint_id
    return robot.policy_dataset_revision


def mismatches(robots: list[RobotConfigOut]) -> list[ConfigMismatchOut]:
    """축마다 값이 갈렸는지 본다. 갈린 축만 돌려준다."""
    found = []
    for axis, robot_type in AXES:
        group = [r for r in robots if r.robot_type == robot_type]
        values: dict[str, list[str]] = {}
        unreported = []
        for robot in group:
            value = _value_of(robot, axis)
            if value is None:
                unreported.append(robot.robot_id)
            else:
                values.setdefault(value, []).append(robot.robot_id)

        # 값이 하나뿐이면 같은 것이고, 하나도 없으면 판정할 게 없다.
        # 보고 안 한 로봇은 불일치가 아니다 — '다르다' 와 '모른다' 는 다르다.
        if len(values) > 1:
            found.append(ConfigMismatchOut(
                axis=axis, robot_type=robot_type,
                values=values, unreported=unreported))
    return found


def summarize(rows, details: dict[str, ManipulatorDetail] | None = None,
              ) -> FleetConfigOut:
    """CONFIG_SQL 의 행을 형상 패널 응답으로 접는다. DB 를 모른다 — 행만 받는다."""
    details = details or {}
    robots = [_row_to_config(row, details.get(row["robot_id"])) for row in rows]
    return FleetConfigOut(robots=robots, mismatches=mismatches(robots))
