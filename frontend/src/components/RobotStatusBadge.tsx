import type { BatteryForecast } from '../lib/api'
import type { RobotState } from '../types/monitoring'
import { BatteryForecastLabel } from './BatteryForecastLabel'
import { BatteryReading } from './BatteryReading'

interface Props {
  /** 이벤트 스트림에서 파생한 로봇 상태. 자세한 규칙은 lib/derivedStatus.ts. */
  state: RobotState
  /** GET /robots 의 최신 배터리 값. null 이면 로봇이 아직 로그를 안 남긴 상태. */
  batteryPercent: number | null
  /**
   * 원본 측정값. 퍼센트는 7.6V 위가 전부 100% 인 클램프된 파생값이라,
   * 충전 중인지 만충인지 구분하려면 전압이 필요하다.
   * 의료진 화면에서는 툴팁으로만 보인다.
   */
  batteryVoltage: number | null
  /** 위 배터리 표본의 기록 시각. 나이가 임계를 넘으면 값 자체가 흐려진다. */
  batteryRecordedAt: string | null
  /** 세션 이벤트에서 파생한 현재 목적지 (visit_name). */
  currentDestination: string | null
  /** 충전/방전 예상. 시간이 없는 경우가 정상적으로 흔하다. */
  forecast?: BatteryForecast | null
}

const errorStates = new Set<RobotState>([
  'QR 인식 실패',
  '경로 이탈',
  '통신 두절',
  '배터리 부족',
])

export function RobotStatusBadge({
  state,
  batteryPercent,
  batteryVoltage,
  batteryRecordedAt,
  currentDestination,
  forecast = null,
}: Props) {
  const tone = errorStates.has(state)
    ? 'error'
    : state === '일시정지'
      ? 'warning'
      : 'ok'
  const pulsing = state === '안내중'

  return (
    <div className="card">
      <div className="card-title">로봇 상태</div>
      <div className={`state-badge ${tone}${pulsing ? ' pulsing' : ''}`}>{state}</div>
      <dl className="status-grid">
        <dt>배터리</dt>
        <dd>
          {/* 상대시각과 stale 처리를 이 컴포넌트가 따로 구현하지 않는다.
              같은 규칙이 화면마다 갈라지면 어느 쪽이 맞는지 알 수 없다. */}
          <BatteryReading
            voltage={batteryVoltage}
            percent={batteryPercent}
            recordedAt={batteryRecordedAt}
            audience="staff"
            charging={forecast?.direction === 'charging'}
          />
          <BatteryForecastLabel forecast={forecast} audience="staff" />
        </dd>
        <dt>현재 목적지</dt>
        <dd>{currentDestination ?? '—'}</dd>
      </dl>
    </div>
  )
}
