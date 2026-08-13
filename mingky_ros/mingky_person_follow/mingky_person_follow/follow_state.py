"""최근 몇 프레임을 봤을 때 "따라가는 중"인지 "멈춰야 하는지" 판단한다.

fire_evac 의 window_size/required_detections 패턴과 같은 이유다: 카메라
프레임 한두 장이 순간적으로 검출을 놓치거나(조명, 순간적 가림) 잘못
잡아도(오탐) 그때마다 속도를 홱홱 바꾸면 안내가 뚝뚝 끊긴다. 최근
`window_size`프레임 중 `required`프레임 이상 같은 판정이 나와야만 상태를
바꾼다 -- 연속 카운트가 아니라 "최근 창 안의 개수"라 순간적으로 한 프레임만
놓쳐도 바로 안 끊긴다.
"""

import collections


def next_following_state(
    recent: collections.deque,
    detected: bool,
    was_following: bool,
    *,
    required: int,
) -> bool:
    """`recent`(최근 detected 이력)에 이번 프레임을 반영하고, 새 팔로잉 상태를 낸다.

    한쪽으로 확정되기 전(연속 감지도 연속 미감지도 required 미만)에는
    `was_following`을 그대로 돌려준다 -- 애매한 구간에서 매 프레임 상태가
    흔들리는 걸 막는다.
    """
    recent.append(detected)
    if sum(recent) >= required:
        return True
    if sum(1 for d in recent if not d) >= required:
        return False
    return was_following
