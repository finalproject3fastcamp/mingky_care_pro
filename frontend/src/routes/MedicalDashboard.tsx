import { CameraStream } from '../components/CameraStream'
import { NotificationArea } from '../components/NotificationArea'
import { PatientInfoCard } from '../components/PatientInfoCard'
import { ProgressStepper } from '../components/ProgressStepper'
import { RobotStatusBadge } from '../components/RobotStatusBadge'
import { mockApi } from '../lib/mock'
import { usePolling } from '../lib/usePolling'

const CURRENT_PATIENT_ID = 'p001'
const POLL_MS = 3000
// 로봇 QR 리더의 MJPEG 미리보기 URL. 미설정이면 카메라 카드를 숨긴다.
const CAMERA_STREAM_URL = import.meta.env.VITE_CAMERA_STREAM_URL as string | undefined

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
      {CAMERA_STREAM_URL && <CameraStream streamUrl={CAMERA_STREAM_URL} />}
      <ProgressStepper
        steps={schedule.data.steps}
        currentStepOrder={schedule.data.current_step_order}
      />
      <NotificationArea notifications={notifications.data ?? []} />
    </div>
  )
}
