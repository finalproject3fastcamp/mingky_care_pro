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
    assert progress['required_movement_radius'] == pytest.approx(0.01)
    assert progress['required_movement_angle'] == pytest.approx(0.035)
    assert progress['movement_time_allowance'] == pytest.approx(4.0)


def test_rotation_shim_wraps_mppi_for_heading_then_dynamic_avoidance() -> None:
    with NAV2_PARAMS.open(encoding='utf-8') as stream:
        params = yaml.safe_load(stream)

    controller = params['controller_server']['ros__parameters']
    follow_path = controller['FollowPath']
    local_costmap = params['local_costmap']['local_costmap']['ros__parameters']

    assert follow_path['plugin'] == (
        'nav2_rotation_shim_controller::RotationShimController')
    assert follow_path['primary_controller'] == (
        'nav2_mppi_controller::MPPIController')
    assert follow_path['rotate_to_goal_heading'] is True
    assert follow_path['rotate_to_heading_once'] is True
    assert follow_path['use_path_orientations'] is False
    assert follow_path['angular_dist_threshold'] == pytest.approx(0.52)
    assert follow_path['angular_disengage_threshold'] == pytest.approx(0.17)
    assert follow_path['model_dt'] * follow_path['time_steps'] == pytest.approx(2.0)
    assert follow_path['time_steps'] == 20
    assert follow_path['model_dt'] == pytest.approx(0.1)
    assert follow_path['batch_size'] == 200
    assert follow_path['iteration_count'] == 1
    assert follow_path['vx_min'] == pytest.approx(0.0)
    assert follow_path['PreferForwardCritic']['cost_weight'] == pytest.approx(8.0)
    assert follow_path['GoalAngleCritic']['threshold_to_consider'] == (
        pytest.approx(0.10))
    assert follow_path['CostCritic']['consider_footprint'] is True
    assert controller['controller_frequency'] == pytest.approx(10.0)
    assert controller['failure_tolerance'] == pytest.approx(1.0)
    assert local_costmap['update_frequency'] == pytest.approx(5.0)
    assert local_costmap['publish_frequency'] == pytest.approx(1.0)
    assert local_costmap['plugins'] == ['obstacle_layer', 'inflation_layer']
    assert local_costmap['obstacle_layer']['plugin'] == (
        'nav2_costmap_2d::ObstacleLayer')
    assert local_costmap['inflation_layer']['inflation_radius'] == pytest.approx(
        0.10)


def test_smac2d_is_the_primary_global_planner() -> None:
    with NAV2_PARAMS.open(encoding='utf-8') as stream:
        params = yaml.safe_load(stream)

    planner = params['planner_server']['ros__parameters']

    assert 'Smac2D' in planner['planner_plugins']
    assert planner['Smac2D']['plugin'] == 'nav2_smac_planner::SmacPlanner2D'
    assert planner['GridBased']['tolerance'] == 0.0
    assert planner['Smac2D']['tolerance'] == 0.0
    assert planner['expected_planner_frequency'] == pytest.approx(5.0)


def test_bt_action_ack_timeout_tolerates_loaded_robot() -> None:
    with NAV2_PARAMS.open(encoding='utf-8') as stream:
        params = yaml.safe_load(stream)

    bt = params['bt_navigator']['ros__parameters']

    assert bt['default_server_timeout'] == 500


def test_amcl_waits_for_verified_lidar_initial_pose() -> None:
    with NAV2_PARAMS.open(encoding='utf-8') as stream:
        params = yaml.safe_load(stream)

    amcl = params['amcl']['ros__parameters']

    assert amcl['set_initial_pose'] is False
    assert amcl['always_reset_initial_pose'] is True
    assert amcl['update_min_d'] == pytest.approx(0.05)
    assert amcl['update_min_a'] == pytest.approx(0.05)
    assert 'initial_pose' not in amcl
