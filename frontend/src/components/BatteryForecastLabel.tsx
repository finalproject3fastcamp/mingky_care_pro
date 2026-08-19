/**
 * 충전/방전 예상 시간.
 *
 * **시간이 없는 경우가 정상적으로 흔하다.** 부하가 출렁이거나 표본이
 * 모자라면 서버가 시간을 내지 않는다 — 틀린 시간은 없는 시간보다 나쁘다.
 * 그때는 방향만 보여준다.
 *
 * 화면마다 어휘가 다르다. 의료진에게는 "충전 중 · 약 40분 남음" 이면
 * 충분하고, 왜 시간을 못 냈는지(reason)는 엔지니어만 본다.
 */

import type { BatteryForecast } from '../lib/api'

const DIRECTION_LABEL: Record<BatteryForecast['direction'], string> = {
  charging: '충전 중',
  discharging: '사용 중',
  idle: '변화 없음',
  unknown: '추이 정보 없음',
}

/** 왜 시간을 못 냈는지. 엔지니어 화면에서만 노출한다. */
const REASON_LABEL: Record<string, string> = {
  insufficient_samples: '표본 부족',
  unstable_slope: '부하 변동으로 기울기 불안정',
  voltage_flat: '전압 변화 없음',
  target_reached: '이미 목표 전압 도달',
  beyond_horizon: '12시간 초과',
}

function formatRemaining(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `약 ${minutes}분 남음`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest === 0 ? `약 ${hours}시간 남음` : `약 ${hours}시간 ${rest}분 남음`
}

interface Props {
  forecast: BatteryForecast | null
  audience: 'staff' | 'engineer'
}

export function BatteryForecastLabel({ forecast, audience }: Props) {
  if (!forecast) return null

  // 방향조차 모르면 의료진에게는 아무것도 안 띄운다. "추이 정보 없음" 은
  // 의료진이 할 수 있는 일이 없는 정보라 화면만 어지럽힌다.
  if (forecast.direction === 'unknown' && audience === 'staff') return null

  const direction = DIRECTION_LABEL[forecast.direction]
  const remaining = forecast.seconds != null ? formatRemaining(forecast.seconds) : null

  return (
    <span className={`battery-forecast battery-forecast--${forecast.direction}`}>
      {direction}
      {remaining && <span className="battery-forecast-time"> · {remaining}</span>}
      {audience === 'engineer' && !remaining && forecast.reason && (
        <span className="battery-forecast-reason">
          {' '}
          ({REASON_LABEL[forecast.reason] ?? forecast.reason})
        </span>
      )}
      {audience === 'engineer' && forecast.slope_v_per_hour != null && (
        <span
          className="battery-forecast-slope mono"
          title={`결정계수 ${forecast.r_squared ?? '—'} · 표본 ${forecast.sample_count}개`}
        >
          {' '}
          {forecast.slope_v_per_hour > 0 ? '+' : ''}
          {forecast.slope_v_per_hour}V/h
        </span>
      )}
    </span>
  )
}
