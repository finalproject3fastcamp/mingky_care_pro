import type { RobotStatus } from '../types/monitoring'

interface Props {
  status: RobotStatus
  /**
   * GET /robots 의 최신 배터리 값.
   * - number: 실제 로그 최신값
   * - null:   로봇이 배터리 로그를 아직 남기지 않음
   * - undefined: /robots 응답이 아직 도착 전 → mock 으로 폴백
   */
  batteryPercent?: number | null
  /** 위 배터리 표본의 기록 시각. "N분 전" 부제로 표시. */
  batteryRecordedAt?: string | null
}

const errorStates = new Set([
  'QR 인식 실패',
  '경로 이탈',
  '통신 두절',
  '배터리 부족',
])

/**
 * "N분 전" 상대 시각. 배터리가 2분 주기 로그라 사용자가 stale 여부를 알아야 한다.
 * 초 단위는 안 씀 — 노이즈. 하루가 넘으면 절대 시각으로 대체할 수도 있지만
 * 그럴 세션은 없을 것.
 */
function relativeTime(iso: string): string {
  const secs = (Date.now() - new Date(iso).getTime()) / 1000
  if (secs < 60) return '방금 전'
  if (secs < 3600) return `${Math.floor(secs / 60)}분 전`
  if (secs < 86400) return `${Math.floor(secs / 3600)}시간 전`
  return `${Math.floor(secs / 86400)}일 전`
}

export function RobotStatusBadge({ status, batteryPercent, batteryRecordedAt }: Props) {
  const tone = errorStates.has(status.state)
    ? 'error'
    : status.state === '일시정지'
      ? 'warning'
      : 'ok'
  const pulsing = status.state === '안내중'

  // /robots 이 아직 로드 안 됐으면 mock 으로 폴백. 로드됐지만 로봇에 로그가
  // 없으면(null) — 시연에서는 있을 수 없지만 — "—" 로 표시한다.
  const batteryDisplay =
    batteryPercent === undefined
      ? `${status.battery}%`
      : batteryPercent == null
        ? '—'
        : `${batteryPercent}%`

  return (
    <div className="card">
      <div className="card-title">로봇 상태</div>
      <div className={`state-badge ${tone}${pulsing ? ' pulsing' : ''}`}>{status.state}</div>
      <dl className="status-grid">
        <dt>배터리</dt>
        <dd>
          {batteryDisplay}
          {batteryRecordedAt && (
            <span className="dd-meta"> · {relativeTime(batteryRecordedAt)} 기록</span>
          )}
        </dd>
        <dt>현재 목적지</dt>
        <dd>{status.current_destination ?? '—'}</dd>
        <dt>예상 도착시간</dt>
        <dd>{status.eta_seconds != null ? `${status.eta_seconds}초` : '—'}</dd>
      </dl>
    </div>
  )
}
