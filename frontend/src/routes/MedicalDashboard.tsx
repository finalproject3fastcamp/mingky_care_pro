import { CameraStream } from '../components/CameraStream'
import { NotificationArea } from '../components/NotificationArea'
import { PatientInfoCard } from '../components/PatientInfoCard'
import { ProgressStepper } from '../components/ProgressStepper'
import { RobotStatusBadge } from '../components/RobotStatusBadge'
import { getActiveSessions, getRobots } from '../lib/api'
import { deriveCurrentDestination, deriveRobotState } from '../lib/derivedStatus'
import { toNotification } from '../lib/eventMessages'
import { listEvents } from '../lib/eventsApi'
import { usePolling } from '../lib/usePolling'

const POLL_MS = 3000
// 로봇 QR 리더의 MJPEG 미리보기 URL. 미설정이면 카메라 카드를 숨긴다.
const CAMERA_STREAM_URL = import.meta.env.VITE_CAMERA_STREAM_URL as string | undefined

export function MedicalDashboard() {
  // 시연은 로봇 1대 전제라 [0] 만 본다. 여러 대로 늘리면 세션 셀렉터가 필요하다.
  const sessions = usePolling((signal) => getActiveSessions({ signal }), POLL_MS)
  // 로봇 목록 (배터리 실 데이터 소스). session.robot_id 로 매칭해서 쓴다.
  const robots = usePolling((signal) => getRobots({ signal }), POLL_MS)
  // limit 30 은 알림 표시(최근 10건 슬라이스) 와 파생 상태(현재 목적지·상태) 를
  // 함께 감당하기 위한 크기다. 파생은 세션의 최근 nav 이벤트가 window 안에
  // 남아야 정확한데, 30 은 시연 한 세션의 이력을 넉넉히 담는다.
  const events = usePolling(
    async (signal) => (await listEvents({ limit: 30 }, { signal })).items,
    POLL_MS,
  )

  // 초회 로딩: 세션이 대시보드의 뼈대라 이것만 기준으로 판단한다. robots/events
  // 는 실패하거나 늦어도 세션 카드는 그릴 수 있다.
  if (sessions.loading && !sessions.data) {
    return <p>불러오는 중…</p>
  }

  const session = sessions.data?.[0]

  // 어느 소스든 최신 tick 이 실패하면 배너를 띄우고, 카드는 마지막 성공값으로
  // 계속 그린다. 다음 tick 이 성공하면 usePolling 이 error 를 null 로 지워
  // 배너는 자동으로 사라진다.
  const stale = sessions.error || events.error || robots.error

  if (!session) {
    return (
      <div className="dashboard">
        {stale && <ErrorBanner />}
        <p>진행 중인 안내 세션이 없습니다.</p>
      </div>
    )
  }

  const robot = robots.data?.find((r) => r.robot_id === session.robot_id)
  const sessionEvents = events.data ?? []

  // 이벤트가 아직 안 왔으면 파생값이 '대기'/null 로 나오는데, 첫 tick(3초 이내)만
  // 짧게 그러다 실제 값으로 바뀐다. 사용자에게는 순간이라 별도 로딩 처리 안 함.
  const derivedState = deriveRobotState(sessionEvents, {
    session_id: session.session_id,
    ended_at: session.ended_at,
  })
  const derivedDestination =
    deriveCurrentDestination(sessionEvents, session.session_id)?.visitName ?? null

  return (
    <div className="dashboard">
      {stale && <ErrorBanner />}
      <div className="dashboard-row">
        <PatientInfoCard
          patient={session.patient}
          robotId={session.robot_id}
          startedAt={session.started_at}
        />
        <RobotStatusBadge
          state={derivedState}
          batteryPercent={robot?.battery_percent ?? null}
          batteryRecordedAt={robot?.battery_recorded_at ?? null}
          currentDestination={derivedDestination}
        />
      </div>
      {CAMERA_STREAM_URL && <CameraStream streamUrl={CAMERA_STREAM_URL} />}
      <ProgressStepper
        steps={session.steps}
        currentStepOrder={session.current_step_order}
      />
      {/* 알림은 목록이 무한정 자라지 않도록 최근 10건만. events 는 파생용으로 30건 받는다. */}
      <NotificationArea
        notifications={sessionEvents.slice(0, 10).map(toNotification)}
      />
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
