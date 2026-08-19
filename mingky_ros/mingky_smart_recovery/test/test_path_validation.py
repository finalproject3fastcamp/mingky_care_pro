import pytest

from mingky_smart_recovery.path_validation import validate_recovery_path


def test_exact_escape_path_is_valid():
    result = validate_recovery_path(
        [(0.0, 0.0), (0.12, 0.03), (0.34, 0.08)],
        expected_start=(0.0, 0.0),
        expected_goal=(0.34, 0.08),
        requested_distance_m=0.35,
    )

    assert result.valid is True


def test_nearby_fallback_path_is_rejected():
    result = validate_recovery_path(
        [(0.0, 0.0), (0.02, 0.0)],
        expected_start=(0.0, 0.0),
        expected_goal=(0.35, 0.0),
        requested_distance_m=0.35,
    )

    assert result.valid is False
    assert result.endpoint_error_m == pytest.approx(0.33)


def test_path_with_wrong_start_is_rejected():
    result = validate_recovery_path(
        [(0.20, 0.0), (0.35, 0.0)],
        expected_start=(0.0, 0.0),
        expected_goal=(0.35, 0.0),
        requested_distance_m=0.35,
    )

    assert result.valid is False
    assert result.start_error_m == pytest.approx(0.20)
