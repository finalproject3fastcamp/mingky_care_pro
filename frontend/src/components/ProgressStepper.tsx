import type { SessionStep } from '../types/monitoring'

interface Props {
  steps: SessionStep[]
  currentStepOrder: number | null
}

// 시간은 브라우저 로컬 24시간. 병원 내부라 오전/오후 혼선 없이 24시간이 낫다.
const timeFormatter = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

// event_codes.yaml 의 session.step_completed.payload.source 값과 매칭.
// 그 파일에 값이 추가되면 여기도 넓혀 표시 라벨을 정한다.
function sourceLabel(source: string | null): string {
  switch (source) {
    case 'qr':
      return 'QR'
    case 'manual':
      return '수동'
    default:
      return ''
  }
}

function stepStatusText(step: SessionStep): string {
  if (step.completed_at) {
    const time = timeFormatter.format(new Date(step.completed_at))
    const label = sourceLabel(step.completed_source)
    return label ? `${time} 완료 · ${label}` : `${time} 완료`
  }
  if (step.arrived_at) {
    return `${timeFormatter.format(new Date(step.arrived_at))} 도착`
  }
  return ''
}

/**
 * 계획과 실제 방문 순서가 갈렸을 때만 붙이는 표시.
 *
 * 검사실이 겹치면 관제가 순서를 바꾼다(013). 그 사실이 화면에 안 보이면
 * 의료진은 "왜 2번을 먼저 갔지" 를 알 수 없고, 목록이 뒤죽박죽으로 보인다.
 * 계획대로 갔으면 아무것도 안 붙인다 — 늘 보이는 번호는 정보가 아니다.
 */
function visitOrderNote(step: SessionStep): string {
  if (step.visit_seq == null || step.visit_seq === step.step_order) return ''
  return `${step.visit_seq}번째로 방문`
}

export function ProgressStepper({ steps, currentStepOrder }: Props) {
  return (
    <div className="card">
      <div className="card-title">진행 상황</div>
      <ol className="stepper">
        {steps.map((step) => {
          // **완료 여부는 시각으로 판정한다.** 순서 번호로 세면 안 된다 —
          // 검사실이 겹치면 관제가 순서를 바꾸므로(013), CT 를 먼저 하고
          // X-ray 로 돌아온 상태에서 '번호가 작으면 완료' 로 세면 이미 끝난
          // CT 가 대기로 보인다.
          const done = step.completed_at != null
          const current = step.step_order === currentStepOrder && !done
          const cls = current ? 'current' : done ? 'done' : 'pending'
          const statusText = stepStatusText(step)
          const orderNote = visitOrderNote(step)
          return (
            <li key={step.step_order} className={`step ${cls}`}>
              <span className="step-index">{step.step_order}</span>
              <span className="step-name">{step.visit_name}</span>
              {orderNote && <span className="step-reorder">{orderNote}</span>}
              {statusText && <span className="step-time">{statusText}</span>}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
