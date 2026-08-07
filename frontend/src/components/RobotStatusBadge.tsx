import type { RobotState } from '../types/monitoring'

interface Props {
  /** 이벤트 스트림에서 파생한 로봇 상태. 자세한 규칙은 lib/derivedStatus.ts. */
  state: RobotState
  /** GET /robots 의 최신 배터리 값. null 이면 로봇이 아직 로그를 안 남긴 상태. */
  batteryPercent: number | null
  /** 위 배터리 표본의 기록 시각. "N분 전 기록" 부제로 표시. */
  batteryRecordedAt: string | null
  /** 세션 이벤트에서 파생한 현재 목적지 (visit_name). */
  currentDestination: string | null
}

const errorStates = new Set<RobotState>([
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

export function RobotStatusBadge({
  state,
  batteryPercent,
  batteryRecordedAt,
  currentDestination,
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
          {batteryPercent != null ? `${batteryPercent}%` : '—'}
          {batteryRecordedAt && (
            <span className="dd-meta"> · {relativeTime(batteryRecordedAt)} 기록</span>
          )}
        </dd>
        <dt>현재 목적지</dt>
        <dd>{currentDestination ?? '—'}</dd>
      </dl>
    </div>
  )
}
