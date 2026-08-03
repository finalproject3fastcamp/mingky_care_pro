import type { ExamStep } from '../types/monitoring'

interface Props {
  steps: ExamStep[]
  currentStepOrder: number
}

export function ProgressStepper({ steps, currentStepOrder }: Props) {
  return (
    <div className="card">
      <div className="card-title">진행 상황</div>
      <ol className="stepper">
        {steps.map((step) => {
          const done = step.step_order < currentStepOrder
          const current = step.step_order === currentStepOrder
          const cls = current ? 'current' : done ? 'done' : 'pending'
          return (
            <li key={step.examination_step_id} className={`step ${cls}`}>
              <span className="step-index">{step.step_order}</span>
              <span className="step-name">{step.examination_name}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
