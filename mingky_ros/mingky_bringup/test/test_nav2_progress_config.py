"""Nav2 진행 판정이 소형 로봇의 이동·회전을 모두 인정하는지 검증한다."""

from pathlib import Path

import pytest
import yaml


NAV2_PARAMS = (
    Path(__file__).resolve().parents[3]
    / 'pinky' / 'pinky_navigation' / 'params' / 'nav2_params.yaml'
)


def test_progress_checker_matches_small_robot_motion_scale() -> None:
    with NAV2_PARAMS.open(encoding='utf-8') as stream:
        params = yaml.safe_load(stream)

    progress = params['controller_server']['ros__parameters']['progress_checker']

    assert progress['plugin'] == 'nav2_controller::PoseProgressChecker'
    assert progress['required_movement_radius'] == pytest.approx(0.10)
    assert progress['required_movement_angle'] == pytest.approx(0.25)
    assert progress['movement_time_allowance'] == pytest.approx(10.0)
