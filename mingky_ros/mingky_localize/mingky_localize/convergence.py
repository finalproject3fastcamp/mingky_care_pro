"""파티클 클라우드의 위치·방향 퍼짐으로 AMCL 수렴 여부를 판단한다.

ROS 타입에 의존하지 않는다. (x, y, yaw) 숫자만 받아 계산하므로 로봇 없이도
판단 규칙을 단위 테스트할 수 있다.

위치만 보면 안 되는 이유: 파티클들이 한 점에 모여도 각자 다른 방향을 보고
있을 수 있다. 위치는 합의됐는데 로봇이 어느 쪽을 보는지는 여전히 안 맞을 수
있다는 뜻이라, 방향도 같이 모였는지 확인한다.

한계: 이 값이 좁게 나온다고 "맞다"는 보장은 아니다. map_ambiguity.py 가 찾은
것처럼 서로 다른 두 실제 위치가 라이다로 거의 똑같이 보이면, 파티클 전체가
틀린 쪽으로 확신을 갖고 모일 수도 있다. 이 모듈은 "다들 동의했다"만 재고,
"그 동의가 맞다"는 별도로 검증해야 한다. auto_localize_node는 맵과 LiDAR의
Top-K 점수·후보 간 차이·여러 scan의 일관성으로 먼저 위치를 결정하고, 이
모듈은 그 seed를 AMCL이 정상적으로 인수했는지만 최종 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence, Tuple


@dataclass(frozen=True)
class ConvergenceResult:
    """한 번의 관찰 결과. 파티클이 없으면 두 퍼짐 모두 inf, converged 는 False."""

    converged: bool
    spread_m: float
    yaw_spread_rad: float
    centroid: Tuple[float, float]


def particle_spread(points: Sequence[Tuple[float, float]]) -> float:
    """평균 위치에서 각 파티클까지 거리의 RMS(m). 대시보드의 "퍼짐" 표시와 같은 정의.

    RMS(제곱평균제곱근)를 쓰는 이유: 몇 개가 크게 벗어나 있으면(=수렴 안 됨)
    단순 평균거리보다 더 크게 반응해서, 잘못된 "거의 수렴" 판단을 줄인다.
    """
    if not points:
        return float('inf')
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    sq = sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in points)
    return math.sqrt(sq / n)


def circular_spread_rad(yaws: Sequence[float]) -> float:
    """방향(라디안)들이 얼마나 흩어져 있는지, 원형 표준편차로 잰다.

    359도와 1도는 실제로 2도 차이인데 그냥 빼면 358도로 나온다. 그래서 각
    방향을 단위벡터(cos, sin)로 바꿔 평균 낸 뒤, 그 평균벡터 길이(R, 0~1)로
    흩어진 정도를 재는 원형통계 표준 방법을 쓴다.

    R=1(전부 같은 방향)이면 결과 0. R=0(사방으로 흩어짐)이면 결과는 무한대에
    가까워진다.
    """
    if not yaws:
        return float('inf')
    n = len(yaws)
    mean_cos = sum(math.cos(y) for y in yaws) / n
    mean_sin = sum(math.sin(y) for y in yaws) / n
    r = math.hypot(mean_cos, mean_sin)
    if r >= 1.0:
        # 전부 같은 방향이면 이론상 r=1인데, 부동소수 오차로 1.0000001 같은
        # 값이 나와 log(양수)가 되어 sqrt(음수)로 터질 수 있다. 정확히 1로
        # 눌러서 결과를 0으로 만든다 (완전히 같은 방향 → 퍼짐 0).
        return 0.0
    if r <= 0.0:
        return float('inf')
    return math.sqrt(-2.0 * math.log(r))


def evaluate_convergence(
    points: Sequence[Tuple[float, float, float]],
    *,
    threshold_m: float,
    yaw_threshold_rad: float,
) -> ConvergenceResult:
    """위치 퍼짐이 threshold_m 미만이고 방향 퍼짐이 yaw_threshold_rad 미만이면 수렴.

    ``points`` 는 파티클마다 (x, y, yaw) 튜플. 두 임계값 모두 map_ambiguity.py
    가 계측한 값이 아니라 운영 중 실측으로 맞춰야 하는 파라미터라, 노드
    쪽에서 ROS 파라미터로 노출해 재배포 없이 조정할 수 있게 한다.
    """
    if not points:
        return ConvergenceResult(
            converged=False, spread_m=float('inf'), yaw_spread_rad=float('inf'),
            centroid=(0.0, 0.0))
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    spread = particle_spread([(p[0], p[1]) for p in points])
    yaw_spread = circular_spread_rad([p[2] for p in points])
    converged = spread < threshold_m and yaw_spread < yaw_threshold_rad
    return ConvergenceResult(
        converged=converged, spread_m=spread, yaw_spread_rad=yaw_spread,
        centroid=(cx, cy))
