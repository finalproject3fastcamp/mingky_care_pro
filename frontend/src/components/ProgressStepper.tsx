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

export function ProgressStepper({ steps, currentStepOrder }: Props) {
  return (
    <div className="card">
      <div className="card-title">진행 상황</div>
      <ol className="stepper">
        {steps.map((step) => {
          const done = currentStepOrder != null && step.step_order < currentStepOrder
          const current = step.step_order === currentStepOrder
          const cls = current ? 'current' : done ? 'done' : 'pending'
          const statusText = stepStatusText(step)
          return (
            <li key={step.step_order} className={`step ${cls}`}>
              <span className="step-index">{step.step_order}</span>
              <span className="step-name">{step.visit_name}</span>
              {statusText && <span className="step-time">{statusText}</span>}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
