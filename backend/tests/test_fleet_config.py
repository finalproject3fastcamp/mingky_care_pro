"""형상 판정 (§7.2 · 로드맵 10).

"어제는 됐는데 오늘 안 된다" 의 원인 대부분이 형상 불일치다. 이 판정이
잠그는 것은 세 가지다.

  1. 타입 안에서만 비교하는가 (팔의 SHA 와 핑키의 SHA 는 비교 대상이 아니다)
  2. 보고 안 한 로봇을 불일치로 세지 않는가 ('다르다' 와 '모른다' 는 다르다)
  3. 맵을 이름이 아니라 지문으로 비교하는가
"""

from datetime import datetime, timezone

from app import fleet_config
from app.schemas import ManipulatorDetail

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)


def _detail(checkpoint=None, revision=None) -> ManipulatorDetail:
    return ManipulatorDetail(
        policy_checkpoint_id=checkpoint, policy_dataset_revision=revision,
        window=20, window_cycles=0, sample_complete=False,
        cycles_completed=0, cycles_aborted=0,
        pick_succeeded=0, pick_failed=0, pick_retried=0)


def _mobile(robot_id: str, commit="a1b2c3d", map_hash="mapfingerprint",
            *, dirty=False, workspaces=None, reported=True) -> dict:
    payload = {
        "workspaces": workspaces if workspaces is not None else [
            {"path": "/home/pinky/mingky_care_pro", "commit": commit,
             "branch": "main", "dirty": dirty, "process_count": 7},
        ],
        "map_name": "yun_map_highres_clean",
        "map_hash": map_hash,
    }
    return {
        "robot_id": robot_id, "robot_type": "mobile", "display_name": robot_id,
        "payload": payload if reported else None,
        "reported_at": NOW if reported else None,
    }


def _arm(robot_id: str) -> dict:
    return {
        "robot_id": robot_id, "robot_type": "manipulator",
        "display_name": robot_id, "payload": None, "reported_at": None,
    }


# --- 접기 --------------------------------------------------------------------

def test_the_three_versions_land_in_one_row():
    """커밋·맵·정책이 한 화면에 모이는 것이 이 패널의 전부다."""
    result = fleet_config.summarize(
        [_mobile("pinky-01"), _arm("omx-01")],
        {"omx-01": _detail("act_omx_020000", "v3")})

    robots = {r.robot_id: r for r in result.robots}
    assert robots["pinky-01"].commit == "a1b2c3d"
    assert robots["pinky-01"].map_hash == "mapfingerprint"
    assert robots["omx-01"].policy_checkpoint_id == "act_omx_020000"
    assert robots["omx-01"].policy_dataset_revision == "v3"


def test_robots_that_never_reported_still_appear():
    """빠지면 화면에서 '이 로봇의 형상을 모른다' 자체가 안 보인다."""
    result = fleet_config.summarize([_mobile("pinky-01", reported=False)])

    assert result.robots[0].commit is None
    assert result.robots[0].reported_at is None
    assert result.mismatches == []


def test_manufacturer_workspaces_are_not_our_configuration():
    """제조사 플랫폼(~/pinky_pro)은 git 저장소가 아니고 우리 형상도 아니다.

    이걸 세면 정상 배치에서도 커밋이 갈린 것처럼 보이고, 그러면 이 패널을
    아무도 안 본다. inventory_rules.has_mixed_workspaces 와 같은 기준이다.
    """
    workspaces = [
        {"path": "/home/pinky/pinky_pro", "commit": None,
         "branch": None, "dirty": False, "process_count": 3},
        {"path": "/home/pinky/mingky_care_pro", "commit": "a1b2c3d",
         "branch": "main", "dirty": False, "process_count": 7},
        # 빌드만 해두고 안 쓰는 워크스페이스. 지금 도는 코드가 아니다.
        {"path": "/home/pinky/old_ws", "commit": "0000000",
         "branch": "main", "dirty": False, "process_count": 0},
    ]
    result = fleet_config.summarize(
        [_mobile("pinky-01", workspaces=workspaces)])

    assert result.robots[0].commit == "a1b2c3d"
    assert result.robots[0].workspace_path.endswith("mingky_care_pro")


def test_uncommitted_changes_are_surfaced():
    """커밋 해시만으로 재현이 불가능한 상태다. 숨기면 안 된다."""
    result = fleet_config.summarize([_mobile("pinky-01", dirty=True)])

    assert result.robots[0].dirty is True


# --- 불일치 판정 --------------------------------------------------------------

def test_split_commits_are_reported_with_who_is_on_what():
    """"갈렸다" 만으로는 무엇을 되돌려야 할지 모른다. 몇 대 몇인지가 필요하다."""
    result = fleet_config.summarize(
        [_mobile("pinky-01", commit="a1b2c3d"),
         _mobile("pinky-02", commit="9999999")])

    found = {m.axis: m for m in result.mismatches}
    assert found["commit"].values == {
        "a1b2c3d": ["pinky-01"], "9999999": ["pinky-02"]}
    assert found["commit"].robot_type == "mobile"


def test_identical_fleet_raises_nothing():
    result = fleet_config.summarize(
        [_mobile("pinky-01"), _mobile("pinky-02")])

    assert result.mismatches == []


def test_maps_are_compared_by_fingerprint_not_by_name():
    """같은 이름의 다른 맵이 실제로 있다. 이름으로 비교하면 못 잡는다."""
    result = fleet_config.summarize(
        [_mobile("pinky-01", map_hash="aaaa"),
         _mobile("pinky-02", map_hash="bbbb")])

    axes = {m.axis for m in result.mismatches}
    assert axes == {"map"}


def test_a_robot_that_has_not_reported_is_not_a_mismatch():
    """OMX 는 게이트웨이가 아직 없어 코드 형상을 정상적으로 보고하지 않는다
    (로드맵 6). 그걸 불일치로 세면 패널이 영구히 빨갛다."""
    result = fleet_config.summarize(
        [_mobile("pinky-01"), _mobile("pinky-02", reported=False)])

    assert result.mismatches == []


def test_a_partial_comparison_says_so():
    """2대만 비교한 '같다' 를 4대의 '같다' 로 읽으면 안 된다."""
    result = fleet_config.summarize(
        [_mobile("pinky-01", commit="a1b2c3d"),
         _mobile("pinky-02", commit="9999999"),
         _mobile("pinky-03", reported=False)])

    assert result.mismatches[0].unreported == ["pinky-03"]


def test_arms_and_wheels_are_never_compared_to_each_other():
    """§4.4 — 팔의 버전은 코드 SHA 가 아니라 체크포인트다. 두 값을 나란히
    놓으면 항상 다르고, 항상 빨간 경고는 없는 것과 같다."""
    result = fleet_config.summarize(
        [_mobile("pinky-01", commit="a1b2c3d"), _arm("omx-01")],
        {"omx-01": _detail("act_omx_020000")})

    assert result.mismatches == []


def test_split_policy_checkpoints_are_caught():
    """어제 되던 pick 이 오늘 안 되는 원인의 대부분이다 (§4.4)."""
    result = fleet_config.summarize(
        [_arm("omx-01"), _arm("omx-02")],
        {"omx-01": _detail("act_omx_020000", "v3"),
         "omx-02": _detail("act_omx_015000", "v3")})

    found = {m.axis: m for m in result.mismatches}
    assert set(found) == {"policy"}
    assert found["policy"].robot_type == "manipulator"


def test_split_dataset_revisions_are_caught_separately():
    """같은 체크포인트라도 다른 데이터셋으로 학습됐으면 다른 정책이다."""
    result = fleet_config.summarize(
        [_arm("omx-01"), _arm("omx-02")],
        {"omx-01": _detail("act_omx_020000", "v3"),
         "omx-02": _detail("act_omx_020000", "v2")})

    assert [m.axis for m in result.mismatches] == ["dataset"]
