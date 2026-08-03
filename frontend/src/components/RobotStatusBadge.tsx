import type { RobotStatus } from '../types/monitoring'

interface Props {
  status: RobotStatus
}

const errorStates = new Set([
  'QR 인식 실패',
  '경로 이탈',
  '통신 두절',
  '배터리 부족',
])

export function RobotStatusBadge({ status }: Props) {
  const tone = errorStates.has(status.state)
    ? 'error'
    : status.state === '일시정지'
      ? 'warning'
      : 'ok'
  const pulsing = status.state === '안내중'

  return (
    <div className="card">
      <div className="card-title">로봇 상태</div>
      <div className={`state-badge ${tone}${pulsing ? ' pulsing' : ''}`}>{status.state}</div>
      <dl className="status-grid">
        <dt>배터리</dt>
        <dd>{status.battery}%</dd>
        <dt>현재 목적지</dt>
        <dd>{status.current_destination ?? '—'}</dd>
        <dt>예상 도착시간</dt>
        <dd>{status.eta_seconds != null ? `${status.eta_seconds}초` : '—'}</dd>
      </dl>
    </div>
  )
}
