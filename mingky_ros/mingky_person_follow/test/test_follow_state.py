"""팔로잉/정지 전환(follow_state) 정책 테스트."""

import collections

from mingky_person_follow.follow_state import next_following_state


def _feed(sequence, *, window_size=7, required=5):
    recent = collections.deque(maxlen=window_size)
    following = False
    for detected in sequence:
        following = next_following_state(
            recent, detected, following, required=required)
    return following


def test_five_of_seven_detections_starts_following() -> None:
    assert _feed([True, False, True, True, False, True, True])


def test_below_required_detections_stays_stopped() -> None:
    assert not _feed([True, False, True, False, False, True, True])


def test_five_consecutive_misses_stops_even_while_following() -> None:
    recent = collections.deque(maxlen=7)
    following = True
    for detected in [False, False, False, False, False]:
        following = next_following_state(
            recent, detected, following, required=5)
    assert not following


def test_ambiguous_window_keeps_previous_state() -> None:
    recent = collections.deque(maxlen=7)
    following = True
    # 2번만 놓침 -- 5 미만이라 상태 유지
    for detected in [False, False]:
        following = next_following_state(
            recent, detected, following, required=5)
    assert following
