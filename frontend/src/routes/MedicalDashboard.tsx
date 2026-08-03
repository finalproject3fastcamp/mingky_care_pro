import { NotificationArea } from '../components/NotificationArea'
import { PatientInfoCard } from '../components/PatientInfoCard'
import { ProgressStepper } from '../components/ProgressStepper'
import { RobotStatusBadge } from '../components/RobotStatusBadge'
import { mockApi } from '../lib/mock'
import { usePolling } from '../lib/usePolling'

const CURRENT_PATIENT_ID = 'p001'
const POLL_MS = 3000

export function MedicalDashboard() {
  const schedule = usePolling(() => mockApi.getTodaySchedule(CURRENT_PATIENT_ID), POLL_MS)
  const status = usePolling(() => mockApi.getRobotStatus(), POLL_MS)
  const notifications = usePolling(() => mockApi.getNotifications(), POLL_MS)

  if (schedule.loading || status.loading || notifications.loading) {
    return <p>불러오는 중…</p>
  }

  if (!schedule.data) {
    return <p>환자 정보를 찾을 수 없습니다.</p>
  }

  return (
    <div className="dashboard">
      <div className="dashboard-row">
        <PatientInfoCard patient={schedule.data.patient} />
        {status.data && <RobotStatusBadge status={status.data} />}
      </div>
      <ProgressStepper
        steps={schedule.data.steps}
        currentStepOrder={schedule.data.current_step_order}
      />
      <NotificationArea notifications={notifications.data ?? []} />
    </div>
  )
}
