"""전압 추이로 충전/방전 예상 시간을 낸다.

## 왜 퍼센트로 계산하면 안 되는가

퍼센트는 전압의 클램프된 파생값이다 (004_battery_voltage.sql).

    percent = (V - 6.8) / (7.6 - 6.8) * 100

7.6V 위는 전부 100% 라 **기울기가 0 이다.** 충전 중인데 퍼센트만 보면
아무 일도 안 일어나는 것처럼 보인다. 판정은 전압으로 한다.

## 왜 추정을 안 내는 경우가 있는가

주행 부하가 변하면 전압이 출렁인다. 그 구간의 기울기로 시간을 내면 "3분
남음" 이 다음 표본에 "47분 남음" 이 된다. **틀린 시간은 없는 시간보다
나쁘다** — 의료진이 그 숫자를 믿고 일정을 잡는다.

그래서 다음 경우에는 None 을 돌려주고 화면은 "충전 중" 만 표시한다.

    표본이 모자람        기울기를 낼 수 없다
    시간 폭이 너무 짧음  잡음이 기울기를 지배한다
    적합도가 나쁨        부하가 출렁이는 중이다
    기울기가 거의 0      정체 중이거나 이미 만충이다
"""

from dataclasses import dataclass
from datetime import datetime

# 2셀 리튬이온. pinkylib 기준 6.8V(0%) ~ 7.6V(100%).
FULL_VOLTAGE = 7.6
EMPTY_VOLTAGE = 6.8

# 최소 요건. 이보다 적거나 짧으면 기울기가 잡음에 묻힌다.
MIN_SAMPLES = 5
MIN_SPAN_SEC = 300.0

# 결정계수. 이보다 나쁘면 부하 변동으로 보고 추정하지 않는다.
MIN_R_SQUARED = 0.7

# 시간당 전압 변화가 이보다 작으면 정체로 본다. 나누면 수십 시간이 나온다.
MIN_SLOPE_V_PER_HOUR = 0.02

# 추정 상한. 이보다 길게 나오면 숫자를 보여줘도 쓸모가 없다.
MAX_FORECAST_SEC = 12 * 3600


@dataclass(frozen=True)
class Forecast:
    """추정 결과.

    direction 은 항상 있다. 전압이 오르는지 내리는지는 기울기 부호만으로
    알 수 있고, 그것만으로도 "충전 중" 을 표시할 수 있기 때문이다.
    seconds 는 신뢰할 만할 때만 채운다.
    """

    direction: str            # "charging" | "discharging" | "idle"
    seconds: int | None       # 목표 도달까지. 신뢰할 수 없으면 None
    slope_v_per_hour: float
    r_squared: float
    sample_count: int
    # 왜 시간을 못 냈는지. 화면이 아니라 엔지니어가 읽는 값이다.
    reason: str | None = None


def linear_fit(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """최소제곱 직선. (기울기, 절편, 결정계수).

    numpy 를 끌어오지 않는다. 표본이 수십 개짜리 1차 적합이라 표준
    라이브러리로 충분하고, 배포에 의존성을 더할 이유가 없다.

    공개 함수인 이유는 서보 온도 추이(app/servo_health.py)가 같은 적합을
    쓰기 때문이다. 최소제곱을 두 번 적어두면 한쪽만 고쳐지는 날이 온다.
    """
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n

    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    if sxx == 0:
        return 0.0, mean_y, 0.0

    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for _, y in points)
    if ss_tot == 0:
        # 전압이 완전히 평평하다. 적합은 완벽하지만 기울기는 0 이다.
        return slope, intercept, 1.0
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    return slope, intercept, 1.0 - ss_res / ss_tot


def forecast(samples: list[tuple[datetime, float | None]]) -> Forecast | None:
    """(시각, 전압) 표본에서 목표 도달 시간을 추정한다.

    표본은 시간 오름차순이라고 가정하지 않는다 — 두절 후 몰아 들어온
    기록이 섞일 수 있어 여기서 정렬한다.

    전압이 없는 표본(변환 노드는 살아 있는데 ADC 읽기가 실패한 경우)은
    버린다. 퍼센트로 대신 계산하지 않는다.
    """
    points = sorted(
        (s for s in samples if s[1] is not None),
        key=lambda s: s[0],
    )
    if len(points) < MIN_SAMPLES:
        return None

    origin = points[0][0]
    span_sec = (points[-1][0] - origin).total_seconds()
    if span_sec < MIN_SPAN_SEC:
        return None

    fit_points = [((at - origin).total_seconds(), v) for at, v in points]
    slope_per_sec, _intercept, r_squared = linear_fit(fit_points)
    slope_per_hour = slope_per_sec * 3600.0

    if abs(slope_per_hour) < MIN_SLOPE_V_PER_HOUR:
        return Forecast(
            direction="idle", seconds=None, slope_v_per_hour=round(slope_per_hour, 4),
            r_squared=round(r_squared, 3), sample_count=len(points),
            reason="voltage_flat")

    direction = "charging" if slope_per_hour > 0 else "discharging"
    current = points[-1][1]

    if r_squared < MIN_R_SQUARED:
        # 부하가 출렁이는 중이다. 방향만 알리고 시간은 내지 않는다.
        return Forecast(
            direction=direction, seconds=None,
            slope_v_per_hour=round(slope_per_hour, 4),
            r_squared=round(r_squared, 3), sample_count=len(points),
            reason="unstable_slope")

    target = FULL_VOLTAGE if direction == "charging" else EMPTY_VOLTAGE
    remaining = (target - current) / slope_per_sec

    if remaining <= 0:
        # 이미 목표를 지났다. 충전 중이면 만충, 방전 중이면 이미 바닥이다.
        return Forecast(
            direction=direction, seconds=None,
            slope_v_per_hour=round(slope_per_hour, 4),
            r_squared=round(r_squared, 3), sample_count=len(points),
            reason="target_reached")

    if remaining > MAX_FORECAST_SEC:
        return Forecast(
            direction=direction, seconds=None,
            slope_v_per_hour=round(slope_per_hour, 4),
            r_squared=round(r_squared, 3), sample_count=len(points),
            reason="beyond_horizon")

    return Forecast(
        direction=direction, seconds=int(remaining),
        slope_v_per_hour=round(slope_per_hour, 4),
        r_squared=round(r_squared, 3), sample_count=len(points))
