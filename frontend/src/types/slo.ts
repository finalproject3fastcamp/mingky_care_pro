/**
 * GET /slo/completion · GET /control-audit 응답. schemas.py 와 1:1.
 *
 * 판정 정의는 백엔드 `app/slo.py` 가 정본이다. 프론트에서 다시 계산하지
 * 않는다 — 두 곳에서 세면 화면과 API 가 다른 완주율을 말하는 날이 오고,
 * 그때 어느 쪽이 맞는지 아무도 답하지 못한다.
 */

/** app/slo.py 의 실패 사유 상수. */
export type SloFailure = 'abnormal_end' | 'manual_stop' | 'operator_order'

export interface SloSession {
  session_id: number
  robot_id: string
  started_at: string
  ended_at: string
  end_reason: string
  /** 비어 있으면 성공이다. 서버가 판정한 결과를 그대로 받는다. */
  failures: SloFailure[]
}

export interface SloWindow {
  window: number
  sessions_judged: number
  /** 표본이 창을 채웠는가. false 면 완주율을 그대로 믿으면 안 된다. */
  sample_complete: boolean
  success: number
  failure: number
  /** 표본이 0 이면 null. **0 과 다르다** — 하나는 안내, 하나는 비상이다. */
  completion_rate: number | null
  target: number
  budget_total: number
  budget_used: number
  budget_remaining: number
  /** 잔량 0 과 다르다. 잔량 0 은 아직 목표 안이고, 소진은 이미 위반이다. */
  budget_exhausted: boolean
  failed_sessions: SloSession[]
}

export interface ControlAuditEntry {
  audit_id: number
  occurred_at: string
  robot_id: string
  session_id: number | null
  action: string
  argument: string | null
  /** null 이면 X-Actor 없이 들어온 명령이다. */
  actor: string | null
  actor_source: 'header' | 'absent'
  /** §1.1 판정 대상인지. 백엔드가 붙여 보낸다 — 여기서 다시 판정하지 않는다. */
  intervention: boolean
}

export interface ControlAuditPage {
  /** 목록이 아니라 **전체** 건수. limit 에 잘리지 않는다. */
  total: number
  anonymous: number
  limit: number
  items: ControlAuditEntry[]
}
