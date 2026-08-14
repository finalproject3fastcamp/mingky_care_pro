/**
 * 세션이 왜 그렇게 끝났는지.
 *
 * `end_reason` 만 보면 "배터리 부족으로 끝났다" 까지다. 그게 갑자기 벌어진
 * 일인지 40초 전부터 예고돼 있었는지는 알 수 없는데, 후자면 임계값이 늦은
 * 것이라 대응이 다르다.
 *
 * 서버는 사실만 준다(SessionEndingContextOut). 문장은 화면이 만든다 —
 * 의료진에게는 한 줄 요약, 엔지니어에게는 타임라인 전체.
 */

import type { SessionEndingContext } from '../lib/api'
import { messageFor } from '../lib/eventMessages'

/**
 * end_reason → 의료진 어휘.
 *
 * 내부 열거형을 그대로 노출하면 의료진이 해석하려다 잘못 판단한다.
 * 모르는 값은 문자열을 그대로 보여준다 — 빈 칸보다는 낫고, 미등록 사유가
 * 늘어나는 것도 눈에 띈다.
 */
const END_REASON_LABEL: Record<string, string> = {
  completed: '안내를 마쳤습니다.',
  staff: '의료진이 종료했습니다.',
  robot_offline: '로봇과 통신이 끊겨 중단됐습니다.',
  system_failure: '로봇 시스템에 장애가 생겨 중단됐습니다.',
  battery_low: '배터리가 부족해 중단됐습니다.',
  patient_lost: '환자를 놓쳐 중단됐습니다.',
}

interface Props {
  context: SessionEndingContext
  audience: 'staff' | 'engineer'
}

function leadClause(context: SessionEndingContext): string | null {
  if (!context.lead_event_code || context.lead_sec == null) return null
  const label = messageFor(context.lead_event_code, {})
  return `중단 ${context.lead_sec}초 전부터 ${label} 상태였습니다.`
}

export function SessionEndingSummary({ context, audience }: Props) {
  // 아직 안 끝난 세션에는 창이 없다. 서버가 200 + 빈 결과를 준다.
  if (!context.ended_at) return null

  const reason = context.end_reason
    ? (END_REASON_LABEL[context.end_reason] ?? context.end_reason)
    : '종료 사유가 기록되지 않았습니다.'
  const lead = leadClause(context)

  if (audience === 'staff') {
    return (
      <div className="ending-summary">
        <span className="ending-summary-reason">{reason}</span>
        {lead && <span className="ending-summary-lead"> {lead}</span>}
      </div>
    )
  }

  return (
    <div className="card">
      <div className="card-title">종료 직전 60초</div>
      <div className="ending-summary">
        <span className="ending-summary-reason">{reason}</span>
        {lead && <span className="ending-summary-lead"> {lead}</span>}
      </div>
      {context.events.length === 0 ? (
        <p className="empty">이 구간에 남은 이벤트가 없습니다.</p>
      ) : (
        <ol className="ending-timeline">
          {context.events.map((event) => {
            // 종료보다 몇 초 앞섰는지. 절대 시각보다 이쪽이 인과를 읽기 쉽다.
            const lead_sec = Math.round(
              (new Date(context.ended_at!).getTime() -
                new Date(event.occurred_at).getTime()) / 1000,
            )
            return (
              <li key={event.event_id} className={`ending-timeline-row ${event.level}`}>
                <span className="ending-timeline-offset mono">-{lead_sec}초</span>
                <span className="ending-timeline-code mono">{event.event_code}</span>
                <span className="ending-timeline-message">
                  {messageFor(event.event_code, event.payload)}
                </span>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
