import math

from mingky_localize.convergence import (
    circular_spread_rad,
    evaluate_convergence,
    particle_spread,
)


def test_particle_spread_all_same_point_is_zero():
    points = [(1.0, 2.0)] * 50
    assert particle_spread(points) == 0.0


def test_particle_spread_empty_is_infinite():
    assert particle_spread([]) == float('inf')


def test_particle_spread_scattered_is_large():
    # 반지름 1m 원 위에 고르게 뿌린 점들. RMS는 반지름과 같아야 한다.
    points = [
        (math.cos(t), math.sin(t))
        for t in (i * 2 * math.pi / 36 for i in range(36))
    ]
    assert abs(particle_spread(points) - 1.0) < 1e-9


def test_circular_spread_all_same_yaw_is_zero():
    yaws = [0.5] * 30
    assert circular_spread_rad(yaws) < 1e-9


def test_circular_spread_near_wraparound_is_small():
    # 359도와 1도 근방 — 실제로는 붙어있는 방향인데 그냥 빼면 358도 차이로
    # 잘못 계산되는 실수를 이 테스트가 잡는다.
    yaws = [math.radians(-1.0), math.radians(1.0)]
    assert circular_spread_rad(yaws) < math.radians(5.0)


def test_circular_spread_opposite_directions_is_large():
    yaws = [0.0, math.pi]
    assert circular_spread_rad(yaws) > math.radians(90.0)


def test_evaluate_convergence_tight_cluster_converges():
    points = [(1.0 + dx, 2.0 + dy, 0.1) for dx in (-0.01, 0.0, 0.01)
              for dy in (-0.01, 0.0, 0.01)]
    result = evaluate_convergence(
        points, threshold_m=0.3, yaw_threshold_rad=math.radians(15.0))
    assert result.converged
    assert result.spread_m < 0.3
    assert result.centroid == (1.0, 2.0)


def test_evaluate_convergence_scattered_position_does_not_converge():
    points = [(0.0, 0.0, 0.0), (5.0, 5.0, 0.0), (-5.0, 3.0, 0.0)]
    result = evaluate_convergence(
        points, threshold_m=0.3, yaw_threshold_rad=math.radians(15.0))
    assert not result.converged


def test_evaluate_convergence_tight_position_but_scattered_yaw_does_not_converge():
    # 사용자가 지적한 바로 그 경우: 위치는 모였는데 방향이 제각각.
    points = [(1.0, 1.0, 0.0), (1.0, 1.0, math.pi), (1.0, 1.0, math.pi / 2)]
    result = evaluate_convergence(
        points, threshold_m=0.3, yaw_threshold_rad=math.radians(15.0))
    assert result.spread_m < 0.3
    assert not result.converged


def test_evaluate_convergence_empty_does_not_converge():
    result = evaluate_convergence(
        [], threshold_m=0.3, yaw_threshold_rad=math.radians(15.0))
    assert not result.converged
    assert result.spread_m == float('inf')
