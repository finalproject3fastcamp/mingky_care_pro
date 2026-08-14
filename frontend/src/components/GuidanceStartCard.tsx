import { useEffect, useMemo, useState } from 'react'

import { sendOrder, type RobotMode } from '../lib/api'
import type { EventOut } from '../types/events'
import type { ActiveSession } from '../types/monitoring'

const RESULT_CODES = new Set([
  'nav.goal_sent',
  'nav.goal_aborted',
  'session.start_rejected',
])
const RESPONSE_TIMEOUT_MS = 15_000
const SESSION_SYNC_DELAY_MS = 15_000

const REJECTION_MESSAGE: Record<string, string> = {
  invalid_session_id: '안내 세션 번호가 올바르지 않습니다.',
  session_mismatch: '화면의 세션과 로봇의 현재 세션이 일치하지 않습니다.',
  invalid_state: '로봇이 환자 확인 대기 상태가 아닙니다.',
  battery_low: '배터리가 부족하여 안내를 시작할 수 없습니다.',
  emergency_stop: '비상정지가 걸려 있어 안내를 시작할 수 없습니다.',
  waypoint_test_active: 'Waypoint 시험 주행을 먼저 종료해야 합니다.',
  missing_visit: '안내할 첫 방문지가 없습니다.',
  missing_waypoint: '첫 방문지의 Waypoint 설정을 찾지 못했습니다.',
}

interface Props {
  session: ActiveSession
  events: EventOut[]
  mode: RobotMode | null
  robotConnected: boolean
}

export function GuidanceStartCard({ session, events, mode, robotConnected }: Props) {
  const sessionEvents = useMemo(
    () => events.filter((event) => event.session_id === session.session_id),
    [events, session.session_id],
  )
  const latestReadyIndex = sessionEvents.findIndex(
    (event) => event.event_code === 'session.ready',
  )
  const ready = latestReadyIndex >= 0
  // 활성 세션이 있다는 것 자체가 QR 인식과 백엔드 등록은 끝났다는 뜻이다.
  // session.ready 전에는 "QR 대기"가 아니라 로봇 내부 동기화 단계다.
  const syncDelayed =
    !ready && Date.now() - Date.parse(session.started_at) >= SESSION_SYNC_DELAY_MS
  const latestNavigationAttemptIndex = sessionEvents.findIndex(
    (event) =>
      event.event_code === 'nav.goal_sent' || event.event_code === 'nav.goal_aborted',
  )
  const latestNavigationAttempt = sessionEvents[latestNavigationAttemptIndex]
  const attemptedSinceReady =
    ready &&
    latestNavigationAttemptIndex >= 0 &&
    latestNavigationAttemptIndex < latestReadyIndex
  const guidanceStarted =
    attemptedSinceReady && latestNavigationAttempt?.event_code === 'nav.goal_sent'
  const retrying =
    attemptedSinceReady && latestNavigationAttempt?.event_code === 'nav.goal_aborted'
  const latestResult = sessionEvents.find((event) => RESULT_CODES.has(event.event_code))

  const [waiting, setWaiting] = useState(false)
  const [baselineResultId, setBaselineResultId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setWaiting(false)
    setBaselineResultId(null)
    setError(null)
  }, [session.session_id])

  useEffect(() => {
    if (!waiting || latestResult == null || latestResult.event_id === baselineResultId) {
      return
    }
    setWaiting(false)
    if (latestResult.event_code === 'session.start_rejected') {
      const reason = String(latestResult.payload.reason ?? '')
      setError(REJECTION_MESSAGE[reason] ?? `로봇이 안내 시작을 거부했습니다: ${reason}`)
    } else if (latestResult.event_code === 'nav.goal_aborted') {
      setError('첫 목적지로 출발하지 못했습니다. 로봇 상태와 Waypoint를 확인하세요.')
    } else {
      setError(null)
    }
  }, [baselineResultId, latestResult, waiting])

  useEffect(() => {
    if (!waiting) return
    const timeout = window.setTimeout(() => {
      setWaiting(false)
      setError('로봇의 출발 응답을 받지 못했습니다. 연결 상태를 확인하세요.')
    }, RESPONSE_TIMEOUT_MS)
    return () => window.clearTimeout(timeout)
  }, [waiting])

  if (guidanceStarted || session.current_step_order == null) return null

  const disabledReason = !ready
    ? syncDelayed
      ? 'QR 인식은 완료됐지만 로봇 세션 동기화가 지연되고 있습니다.'
      : 'QR 인식이 완료되어 로봇과 세션 정보를 동기화하는 중입니다.'
    : !robotConnected
      ? '로봇 조작 연결이 끊겨 있어 안내를 시작할 수 없습니다.'
      : mode !== 'auto'
        ? mode === 'estop'
          ? '비상정지를 해제한 뒤 안내를 시작하세요.'
          : '자동 주행 모드로 전환한 뒤 안내를 시작하세요.'
        : session.current_visit == null
          ? '안내할 첫 목적지를 확인하지 못했습니다.'
          : null

  async function startGuidance() {
    setWaiting(true)
    setBaselineResultId(latestResult?.event_id ?? null)
    setError(null)
    try {
      await sendOrder(
        session.robot_id,
        'start_guidance',
        String(session.session_id),
      )
    } catch {
      setWaiting(false)
      setError('안내 시작 요청을 관제 서버에 전달하지 못했습니다.')
    }
  }

  return (
    <section className="guidance-start card" aria-labelledby="guidance-start-title">
      <div className="guidance-start__copy">
        <span className="guidance-start__eyebrow">
          {!ready ? '환자 QR 확인 완료' : retrying ? '주행 재시도' : '환자 확인 완료'}
        </span>
        <h2 id="guidance-start-title">
          {!ready
            ? syncDelayed
              ? '로봇 세션 동기화가 지연되고 있습니다'
              : '로봇과 세션 정보를 연결하고 있습니다'
            : retrying
            ? '목적지 안내를 다시 시작할 수 있습니다'
            : '안내를 시작할 준비가 되었습니다'}
        </h2>
        <p>
          첫 목적지 <strong>{session.current_visit ?? '확인 중'}</strong>
        </p>
        {disabledReason && (
          <p className="guidance-start__note" role="status">{disabledReason}</p>
        )}
        {error && <p className="guidance-start__error" role="alert">{error}</p>}
      </div>
      <button
        type="button"
        className="btn primary guidance-start__button"
        disabled={waiting || disabledReason !== null}
        onClick={startGuidance}
      >
        {waiting ? '출발 요청 전달 중…' : retrying ? '안내 다시 시작' : '안내 시작'}
      </button>
    </section>
  )
}
