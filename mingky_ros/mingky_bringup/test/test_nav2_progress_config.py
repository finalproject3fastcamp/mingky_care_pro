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


def test_mppi_uses_live_costmap_for_dynamic_avoidance() -> None:
    with NAV2_PARAMS.open(encoding='utf-8') as stream:
        params = yaml.safe_load(stream)

    controller = params['controller_server']['ros__parameters']
    follow_path = controller['FollowPath']
    local_costmap = params['local_costmap']['local_costmap']['ros__parameters']

    assert follow_path['plugin'] == 'nav2_mppi_controller::MPPIController'
    assert follow_path['model_dt'] * follow_path['time_steps'] == pytest.approx(2.0)
    assert follow_path['CostCritic']['consider_footprint'] is True
    assert controller['failure_tolerance'] == pytest.approx(1.0)
    assert local_costmap['update_frequency'] == pytest.approx(10.0)
    assert local_costmap['plugins'] == ['voxel_layer', 'inflation_layer']
    assert local_costmap['inflation_layer']['inflation_radius'] == pytest.approx(
        0.08)


def test_smac2d_is_the_primary_global_planner() -> None:
    with NAV2_PARAMS.open(encoding='utf-8') as stream:
        params = yaml.safe_load(stream)

    planner = params['planner_server']['ros__parameters']

    assert 'Smac2D' in planner['planner_plugins']
    assert planner['Smac2D']['plugin'] == 'nav2_smac_planner::SmacPlanner2D'
