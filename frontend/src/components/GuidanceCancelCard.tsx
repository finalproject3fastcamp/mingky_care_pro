import { useEffect, useState } from 'react'

import { sendOrder } from '../lib/api'
import type { ActiveSession } from '../types/monitoring'

interface Props {
  session: ActiveSession
  robotConnected: boolean
}

export function GuidanceCancelCard({ session, robotConnected }: Props) {
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setSubmitting(false)
    setNotice(null)
    setError(null)
  }, [session.session_id])

  async function cancelGuidance() {
    const confirmed = window.confirm(
      `${session.patient.name} 환자의 안내를 취소할까요?\n\n` +
      '현재 주행을 즉시 중단하고 안내 세션을 종료합니다.',
    )
    if (!confirmed) return

    setSubmitting(true)
    setNotice(null)
    setError(null)
    try {
      await sendOrder(
        session.robot_id,
        'cancel_guidance',
        String(session.session_id),
      )
      setNotice('안내 취소 요청을 보냈습니다. 로봇 정지와 세션 종료를 확인 중입니다.')
    } catch {
      setSubmitting(false)
      setError('안내 취소 요청을 전달하지 못했습니다. 연결 상태를 확인하고 다시 시도하세요.')
    }
  }

  return (
    <section className="guidance-cancel card" aria-labelledby="guidance-cancel-title">
      <div className="guidance-cancel__copy">
        <span className="guidance-cancel__eyebrow">안내 제어</span>
        <h2 id="guidance-cancel-title">진행 중인 안내 취소</h2>
        <p>취소하면 로봇이 이동을 멈추고 현재 안내 세션이 종료됩니다.</p>
        {!robotConnected && (
          <p className="guidance-cancel__error" role="alert">
            로봇 조작 연결이 끊겨 있어 취소 명령을 보낼 수 없습니다.
          </p>
        )}
        {notice && <p className="guidance-cancel__notice" role="status">{notice}</p>}
        {error && <p className="guidance-cancel__error" role="alert">{error}</p>}
      </div>
      <button
        type="button"
        className="btn danger guidance-cancel__button"
        disabled={submitting || !robotConnected}
        onClick={cancelGuidance}
      >
        {submitting ? '취소 요청 확인 중…' : '안내 취소'}
      </button>
    </section>
  )
}
