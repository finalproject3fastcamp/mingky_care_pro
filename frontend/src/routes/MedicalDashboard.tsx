import { CameraStream } from '../components/CameraStream'
import { NotificationArea } from '../components/NotificationArea'
import { PatientInfoCard } from '../components/PatientInfoCard'
import { ProgressStepper } from '../components/ProgressStepper'
import { RobotStatusBadge } from '../components/RobotStatusBadge'
import { getActiveSessions } from '../lib/api'
import { toNotification } from '../lib/eventMessages'
import { listEvents } from '../lib/eventsApi'
import { mockApi } from '../lib/mock'
import { usePolling } from '../lib/usePolling'

const POLL_MS = 3000
// 로봇 QR 리더의 MJPEG 미리보기 URL. 미설정이면 카메라 카드를 숨긴다.
const CAMERA_STREAM_URL = import.meta.env.VITE_CAMERA_STREAM_URL as string | undefined

export function MedicalDashboard() {
  // 시연은 로봇 1대 전제라 [0] 만 본다. 여러 대로 늘리면 세션 셀렉터가 필요하다.
  const sessions = usePolling((signal) => getActiveSessions({ signal }), POLL_MS)
  // 로봇 상태(state·목적지·ETA)는 아직 백엔드 API 가 없어 mock 이다.
  // mock 은 signal 을 무시해도 되지만 시그니처가 맞아야 타입 체크가 통과한다.
  const status = usePolling(() => mockApi.getRobotStatus(), POLL_MS)
  // 엔지니어 대시보드는 useEventFeed(필터·정지 지원)를 쓴다. 여기는 최근 알림만
  // 얻으면 되므로 usePolling + listEvents({ limit }) 로 충분하다.
  const events = usePolling(
    async (signal) => (await listEvents({ limit: 10 }, { signal })).items,
    POLL_MS,
  )

  // 초회 로딩: 세션이 대시보드의 뼈대라 이것만 기준으로 판단한다. status/events
  // 는 실패해도 세션 카드는 그릴 수 있다.
  if (sessions.loading && !sessions.data) {
    return <p>불러오는 중…</p>
  }

  const session = sessions.data?.[0]

  // 어느 소스든 최신 tick 이 실패하면 배너를 띄우고, 카드는 마지막 성공값으로
  // 계속 그린다. 다음 tick 이 성공하면 usePolling 이 error 를 null 로 지워
  // 배너는 자동으로 사라진다.
  const stale = sessions.error || status.error || events.error

  if (!session) {
    return (
      <div className="dashboard">
        {stale && <ErrorBanner />}
        <p>진행 중인 안내 세션이 없습니다.</p>
      </div>
    )
  }

  return (
    <div className="dashboard">
      {stale && <ErrorBanner />}
      <div className="dashboard-row">
        <PatientInfoCard
          patient={session.patient}
          robotId={session.robot_id}
          startedAt={session.started_at}
        />
        {status.data && <RobotStatusBadge status={status.data} />}
      </div>
      {CAMERA_STREAM_URL && <CameraStream streamUrl={CAMERA_STREAM_URL} />}
      <ProgressStepper
        steps={session.steps}
        currentStepOrder={session.current_step_order}
      />
      <NotificationArea notifications={(events.data ?? []).map(toNotification)} />
    </div>
  )
}

function ErrorBanner() {
  return (
    <div className="poll-error-banner" role="status">
      일부 데이터를 갱신하지 못했습니다. 재시도 중…
    </div>
  )
}
