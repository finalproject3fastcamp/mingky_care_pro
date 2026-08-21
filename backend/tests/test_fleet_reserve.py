"""구간 예약 판정.

여기서 지키는 계약은 넷이다.

  1. 같은 외길에 두 대가 동시에 들어가지 않는다
  2. **기다리는 로봇은 외길을 쥐지 않는다** — 쥔 채로 기다리면 교착이다
  3. 구간 지도가 없으면 아무도 막지 않는다 (fail-open)
  4. 같은 상황에서 매번 같은 답이 나온다 (서로 양보하다 둘 다 멈추면 안 된다)

지도는 실제 파일 대신 손으로 만든 작은 그래프를 쓴다. 파일이 바뀔 때마다
판정 테스트가 깨지면 둘 중 무엇이 틀렸는지 알 수 없다 — 실제 파일이 규약을
지키는지는 test_fleet_map.py 가 따로 본다.
"""

import pytest

from app import fleet_map, fleet_reserve
from app.fleet_reserve import RobotIntent


def make_map(**overrides) -> fleet_map.FleetMap:
    """zone-A ─ seg-1 ─ seg-2 ─ zone-B, 그리고 zone-B 에 막다른 seg-3.

        zone-A ── seg-1 ── seg-2 ── zone-B ── seg-3(막다른 길)

    실제 지도의 축소판이다. zone 둘 사이에 외길 둘이 있고, 한쪽 끝에
    충전소 같은 막다른 길이 붙어 있다.
    """
    doc = {
        "map": "test", "map_sha256": "0" * 16, "robot_width": 0.12,
        "zones": {
            "zone-A": {"area_m2": 1.0, "connects": ["seg-1"], "waypoints": ["a"]},
            "zone-B": {"area_m2": 1.0, "connects": ["seg-2", "seg-3"],
                       "waypoints": ["b"]},
        },
        "segments": {
            "seg-1": {"area_m2": 0.05, "connects": ["zone-A", "seg-2"],
                      "waypoints": []},
            "seg-2": {"area_m2": 0.05, "connects": ["seg-1", "zone-B"],
                      "waypoints": ["room"]},
            "seg-3": {"area_m2": 0.04, "connects": ["zone-B"],
                      "waypoints": ["dock"], "dead_end": True},
        },
        "grid": {"width": 0, "height": 0, "resolution": 0.025,
                 "origin": [0.0, 0.0], "legend": {}, "rows": []},
    }
    doc.update(overrides)
    return fleet_map.parse(doc)


# ------------------------------------------------------------------ 기본

def test_zone_is_not_exclusive():
    """zone 을 배타로 만들면 기다릴 자리가 사라져 설계가 무너진다."""
    fleet = make_map()
    assert fleet.areas["zone-A"].exclusive is False
    assert fleet.areas["seg-1"].exclusive is True


def test_blocked_by_includes_neighbouring_segments():
    """구간이 로봇보다 조금 클 뿐이라 경계에 선 로봇은 이웃에 걸쳐 있다."""
    fleet = make_map()
    assert fleet.blocked_by("seg-1") == {"seg-1", "seg-2"}
    # 이웃이라도 zone 은 막지 않는다 — 비켜설 수 있는 곳이다.
    assert "zone-A" not in fleet.blocked_by("seg-1")
    # zone 에 서 있는 로봇은 아무것도 막지 않는다. 막으면 기다릴 자리가 없다.
    assert fleet.blocked_by("zone-A") == set()


def test_route_and_leg():
    fleet = make_map()
    assert fleet.route("zone-A", "zone-B") == [
        "zone-A", "seg-1", "seg-2", "zone-B"]
    # 한 다리는 다음 zone 까지다. 전부 잡으면 한 대가 지도 절반을 쥔다.
    assert fleet_reserve.leg(fleet, "zone-A", "zone-B") == [
        "seg-1", "seg-2", "zone-B"]
    # 목적지가 외길에 있으면 거기서 끝난다.
    assert fleet_reserve.leg(fleet, "zone-A", "seg-2") == ["seg-1", "seg-2"]


# ------------------------------------------------------------------ 배타

def test_second_robot_waits_for_the_corridor():
    fleet = make_map()
    result = fleet_reserve.plan(fleet, [
        RobotIntent("pinky-01", area="zone-A", goal_area="zone-B", guiding=True),
        RobotIntent("pinky-02", area="zone-B", goal_area="zone-A", guiding=True),
    ])

    first = result.decisions["pinky-01"]
    second = result.decisions["pinky-02"]
    assert first.proceed is True
    assert {"seg-1", "seg-2"} <= first.holds
    assert second.proceed is False
    assert second.reason == fleet_reserve.WAIT_PEER_SEGMENT
    assert second.blocked_by == "pinky-01"


def test_waiting_robot_holds_no_segment():
    """계약 2 — 쥔 채로 기다리면 그 순간 교착이 가능해진다."""
    fleet = make_map()
    result = fleet_reserve.plan(fleet, [
        RobotIntent("pinky-01", area="zone-A", goal_area="zone-B", guiding=True),
        RobotIntent("pinky-02", area="zone-B", goal_area="zone-A"),
    ])

    second = result.decisions["pinky-02"]
    assert second.proceed is False
    # zone-B 에 서 있는 것 자체는 사실이라 남지만, 외길은 하나도 안 쥔다.
    assert not any(fleet.areas[a].exclusive for a in second.holds)


def test_two_robots_never_hold_the_same_segment():
    """계약 1. 어떤 조합에서도 외길이 겹치면 안 된다."""
    fleet = make_map()
    spots = ["zone-A", "zone-B", "seg-1", "seg-2", "seg-3"]
    for a in spots:
        for b in spots:
            for ga in spots:
                for gb in spots:
                    result = fleet_reserve.plan(fleet, [
                        RobotIntent("pinky-01", area=a, goal_area=ga),
                        RobotIntent("pinky-02", area=b, goal_area=gb),
                    ])
                    one = {s for s in result.held_by("pinky-01")
                           if fleet.areas[s].exclusive}
                    two = {s for s in result.held_by("pinky-02")
                           if fleet.areas[s].exclusive}
                    # 예외 없다. 점유를 배정보다 먼저 확정하므로 외길 하나는
                    # 언제나 정확히 한 대의 것이다 — 둘이 이미 맞닿아 있는
                    # 경우에도 한쪽만 쥐고 다른 쪽은 기다린다.
                    assert one & two == set(), (a, b, ga, gb, one & two)


# ------------------------------------------------------------------ 우선순위

def test_guiding_robot_wins():
    """환자를 안내 중인 쪽이 먼저다. 복귀·대기보다 급하다."""
    fleet = make_map()
    result = fleet_reserve.plan(fleet, [
        # robot_id 는 pinky-02 가 뒤지만 안내 중이라 이긴다.
        RobotIntent("pinky-01", area="zone-A", goal_area="zone-B"),
        RobotIntent("pinky-02", area="zone-A", goal_area="zone-B", guiding=True),
    ])
    assert result.decisions["pinky-02"].proceed is True
    assert result.decisions["pinky-01"].proceed is False


def test_tie_breaks_on_robot_id_and_is_stable():
    """계약 4 — 순서를 바꿔 넣어도 같은 답이라야 서로 양보하다 멈추지 않는다."""
    fleet = make_map()
    intents = [
        RobotIntent("pinky-01", area="zone-A", goal_area="zone-B"),
        RobotIntent("pinky-02", area="zone-A", goal_area="zone-B"),
    ]
    forward = fleet_reserve.plan(fleet, intents)
    backward = fleet_reserve.plan(fleet, list(reversed(intents)))

    assert forward.decisions["pinky-01"].proceed is True
    assert forward.decisions["pinky-02"].proceed is False
    assert backward.decisions == forward.decisions


# ------------------------------------------------------------------ 목적지 점유

def test_peer_parked_at_goal_blocks_and_says_why():
    """목적지 자체가 외길인 경우 — 11/23 이 그렇다.

    상대가 그 방에 서 있으면 갈 수 없고, 화면이 그 이유를 말할 수 있어야
    방문 순서 재정렬로 넘어갈 수 있다.
    """
    fleet = make_map()
    result = fleet_reserve.plan(fleet, [
        # pinky-01 이 seg-2(room)에 주차해 있다.
        RobotIntent("pinky-01", area="seg-2", goal_area="seg-2", guiding=True),
        RobotIntent("pinky-02", area="zone-A", goal_area="seg-2", guiding=True),
    ])
    second = result.decisions["pinky-02"]
    assert second.proceed is False
    assert second.reason == fleet_reserve.WAIT_PEER_GOAL
    assert second.blocked_by == "pinky-01"


def test_dead_end_dock_is_exclusive():
    """충전소 진입로. 두 대가 동시에 들어가면 빠져나올 방법이 없다."""
    fleet = make_map()
    result = fleet_reserve.plan(fleet, [
        RobotIntent("pinky-01", area="zone-B", goal_area="seg-3"),
        RobotIntent("pinky-02", area="zone-B", goal_area="seg-3"),
    ])
    assert result.decisions["pinky-01"].proceed is True
    assert result.decisions["pinky-02"].proceed is False


# ------------------------------------------------------------------ fail-open

def test_without_a_map_nobody_is_blocked():
    """계약 3. 조정이 꺼진 것이지 전원 정지가 아니다."""
    result = fleet_reserve.plan(None, [
        RobotIntent("pinky-01", area="zone-A", goal_area="zone-B"),
        RobotIntent("pinky-02", area="zone-B", goal_area="zone-A"),
    ])
    assert all(d.proceed for d in result.decisions.values())
    assert all(not d.holds for d in result.decisions.values())


def test_robot_with_unknown_position_is_not_coordinated():
    """위치를 모르는 로봇이 남의 길을 막으면 되돌릴 근거가 없다."""
    fleet = make_map()
    result = fleet_reserve.plan(fleet, [
        RobotIntent("pinky-01", area=None, goal_area="zone-B"),
        RobotIntent("pinky-02", area="zone-A", goal_area="zone-B"),
    ])
    assert result.decisions["pinky-01"].holds == frozenset()
    assert result.decisions["pinky-02"].proceed is True


def test_unreachable_goal_says_no_route():
    fleet = make_map(segments={
        "seg-1": {"area_m2": 0.05, "connects": ["zone-A"], "waypoints": []},
        "island": {"area_m2": 0.01, "connects": [], "waypoints": ["nowhere"]},
    })
    result = fleet_reserve.plan(fleet, [
        RobotIntent("pinky-01", area="zone-A", goal_area="island"),
    ])
    decision = result.decisions["pinky-01"]
    assert decision.proceed is False
    assert decision.reason == fleet_reserve.WAIT_NO_ROUTE


def test_area_at_reads_the_raster():
    fleet = fleet_map.parse({
        "map": "t", "map_sha256": "x", "robot_width": 0.12,
        "zones": {"zone-A": {"area_m2": 1.0, "connects": [], "waypoints": []}},
        "segments": {},
        "grid": {
            "width": 4, "height": 2, "resolution": 1.0, "origin": [0.0, 0.0],
            "legend": {1: "zone-A"},
            # 아래 행(이미지 마지막 줄)이 y=0 이다.
            "rows": ["4:0", "2:0 2:1"],
        },
    })
    assert fleet.area_at(2.5, 0.5) == "zone-A"
    assert fleet.area_at(0.5, 0.5) is None      # 못 가는 곳
    assert fleet.area_at(99.0, 0.5) is None     # 맵 밖
    assert fleet.area_at(2.5, 1.5) is None      # 위쪽 행은 비어 있다


def test_missing_file_disables_coordination(tmp_path):
    """없다고 기동을 막지 않는다. 조정층은 안전장치가 아니다."""
    fleet_map.reset()
    assert fleet_map.load(tmp_path / "없는파일.yaml") is None
    assert fleet_map.get() is None


def test_broken_file_disables_coordination(tmp_path):
    """깨진 지도로 조정하면 없느니만 못하다."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("zones: [이건 목록이지 사전이 아니다]", encoding="utf-8")
    fleet_map.reset()
    assert fleet_map.load(bad) is None


def test_real_segment_map_is_consistent():
    """실제로 구운 파일이 규약을 지키는가.

    판정 테스트와 나눠 둔다 — 파일이 바뀔 때마다 판정 테스트가 깨지면 둘 중
    무엇이 틀렸는지 알 수 없다. 파일이 없는 환경(CI 러너)에서는 건너뛴다.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    path = (repo / "mingky_ros/mingky_bringup/config/fleet"
            / "yun_map_highres_clean_segments.yaml")
    if not path.is_file():
        pytest.skip("구간 지도가 없습니다 (build_fleet_segments.py --write)")

    import yaml
    fleet = fleet_map.parse(yaml.safe_load(path.read_text(encoding="utf-8")))

    assert fleet.areas, "구간이 하나도 없다"
    # 인접이 양방향이어야 경로 탐색이 방향에 따라 달라지지 않는다.
    for area in fleet.areas.values():
        for other in area.connects:
            assert area.area_id in fleet.areas[other].connects, (
                f"{area.area_id} → {other} 가 한쪽 방향뿐이다")
    # 충전소는 서로 다른 구간이어야 한다. 같은 구간이면 두 대가 동시에
    # 도킹하러 갈 때 예약층이 그것을 하나의 자원으로 보고 한 대를 세운다.
    docks = {name: fleet.area_of_waypoint(name)
             for name in ("charging_station_1", "charging_station_2")}
    assert all(docks.values()), f"충전소가 구간에 안 붙었다: {docks}"
    # 그리고 그 구간들은 서로 도달 가능해야 한다.
    assert fleet.route(docks["charging_station_1"],
                       docks["charging_station_2"]) is not None
