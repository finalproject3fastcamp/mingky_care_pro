import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { ArmedWaiting } from '../components/ArmedWaiting'
import { NotificationArea } from '../components/NotificationArea'
import { PatientInfoCard } from '../components/PatientInfoCard'
import { ProgressStepper } from '../components/ProgressStepper'
import { RobotPicker } from '../components/RobotPicker'
import { RobotStatusBadge } from '../components/RobotStatusBadge'
import { getActiveSessions, getRobots } from '../lib/api'
import { deriveCurrentDestination, deriveRobotState } from '../lib/derivedStatus'
import { toNotification } from '../lib/eventMessages'
import { listEvents } from '../lib/eventsApi'
import { usePolling } from '../lib/usePolling'
import type { EventOut } from '../types/events'
import type { ActiveSession, Robot } from '../types/monitoring'

const POLL_MS = 3000
// QR 이 인식된 순간 확인 화면을 띄우고 있는 시간. 스캔 대기에서 안내 화면으로
// 그냥 바뀌면 환자·의료진 모두 "인식이 된 건가?" 를 판단할 근거가 없다.
// 너무 길면 안내 시작이 늦어지므로 읽고 넘어갈 만큼만 잡는다.
const SCAN_FLASH_MS = 2200
// 로봇 QR 리더의 MJPEG 미리보기 URL. 미설정이면 카메라 카드를 숨긴다.
// 지금은 단일 로봇 전제라 env 하나만 있다. 다중 로봇을 실제로 굴리게 되면
// 로봇별 URL 매핑이 필요하다 (예: GET /robots 응답에 preview_url 추가).
const CAMERA_STREAM_URL = import.meta.env.VITE_CAMERA_STREAM_URL as string | undefined

export function MedicalDashboard() {
  // "지금 어떤 로봇을 담당하고 있는가" 는 URL 이 갖는다 (/medical/:robotId).
  // 서버 상태가 아니라 이 탭의 시야다 — 의료진이 탭을 각자 열어 각자 다른
  // 로봇을 본다. URL 에 두면 새로고침·뒤로가기가 공짜로 따라온다.
  const { robotId } = useParams()
  const navigate = useNavigate()
  const selectedRobotId = robotId ?? null
  // 방금 활성화한 로봇의 응답. 로봇 목록은 3초 폴링이라 arm 직후 한 tick 동안은
  // armed_at 이 아직 null 이다. 그 낡은 값을 그대로 믿으면 아래 orphan 판정이
  // 방금 한 선택을 무효로 보고 선택 화면으로 튕긴다. 폴링이 따라잡을 때까지
  // 이 응답으로 덮어쓴다.
  const [justArmed, setJustArmed] = useState<Robot | null>(null)

  const sessions = usePolling((signal) => getActiveSessions({ signal }), POLL_MS)
  const robots = usePolling((signal) => getRobots({ signal }), POLL_MS)
  const events = usePolling(
    async (signal) => (await listEvents({ limit: 30 }, { signal })).items,
    POLL_MS,
  )

  // 선택한 로봇이 실제 목록에 있고 armed 이거나 세션이 있는 동안만 유효 선택이다.
  // 세션이 끝나거나 다른 경로로 disarmed 되면 선택을 자동으로 해제해서
  // 다음 tick 에 RobotPicker 로 돌아간다.
  const robotList = robots.data ?? []
  const polledRobot = selectedRobotId
    ? robotList.find((r) => r.robot_id === selectedRobotId) ?? null
    : null
  const selectedRobot =
    polledRobot &&
    polledRobot.armed_at == null &&
    justArmed?.robot_id === polledRobot.robot_id
      ? { ...polledRobot, armed_at: justArmed.armed_at }
      : polledRobot
  const selectionOrphaned = Boolean(
    selectedRobotId &&
      robots.data && // 첫 tick 이 아직 안 왔으면 판단 보류 (선택이 없어졌다고 오해하지 않게)
      (!selectedRobot ||
        (selectedRobot.armed_at == null && selectedRobot.active_session_id == null)),
  )

  // 선택한 로봇에 붙은 진행 중 세션. 아래 훅들이 참조하므로 조기 return 앞에서 구한다.
  const activeSession =
    selectedRobot?.active_session_id != null
      ? (sessions.data ?? []).find(
          (s) => s.session_id === selectedRobot.active_session_id,
        ) ?? null
      : null

  // 스캔 대기 → 세션 시작으로 넘어가는 순간을 잡아 확인 화면을 띄운다.
  // "같은 로봇을 계속 보고 있는데 세션이 없다가 생겼다" 일 때만이다:
  //   - 세션 목록이 아직 안 온 tick 은 지나간다. 로딩 중의 null 을 "세션 없음"
  //     으로 읽으면, 응답이 도착하는 순간이 새 스캔으로 둔갑한다 (새로고침할
  //     때마다 확인 화면이 뜨던 원인).
  //   - 로봇 선택이 막 바뀐 tick 도 지나간다 (이미 안내 중인 로봇을 이어받은 경우)
  const [scanFlash, setScanFlash] = useState<ActiveSession | null>(null)
  const prevSessionRef = useRef<{
    robotId: string | null
    sessionId: number | null | undefined
  }>({ robotId: null, sessionId: undefined })

  useEffect(() => {
    if (sessions.data == null) return
    const current = activeSession?.session_id ?? null
    const prev = prevSessionRef.current
    const sameRobot = prev.robotId === selectedRobotId
    prevSessionRef.current = { robotId: selectedRobotId, sessionId: current }
    if (sameRobot && prev.sessionId === null && current != null && activeSession) {
      setScanFlash(activeSession)
    }
  }, [activeSession, selectedRobotId, sessions.data])

  useEffect(() => {
    if (scanFlash == null) return
    const id = window.setTimeout(() => setScanFlash(null), SCAN_FLASH_MS)
    return () => window.clearTimeout(id)
  }, [scanFlash])

  useEffect(() => {
    if (selectionOrphaned) {
      // replace: 무효해진 로봇 URL 을 히스토리에 남기지 않는다. 남기면
      // 뒤로가기가 이미 사라진 화면으로 되돌아간다.
      navigate('/medical', { replace: true })
    }
  }, [selectionOrphaned, navigate])

  // 폴링이 arm 을 따라잡으면 덮어쓰기를 걷어낸다. 이후로는 서버 상태만 보고
  // 판단하므로 다른 경로로 해제되면 정상적으로 선택 화면으로 돌아간다.
  useEffect(() => {
    if (justArmed == null) return
    const polled = robots.data?.find((r) => r.robot_id === justArmed.robot_id)
    if (polled && (polled.armed_at != null || polled.active_session_id != null)) {
      setJustArmed(null)
    }
  }, [robots.data, justArmed])

  function handleSelect(robot: Robot) {
    setJustArmed(robot)
    navigate(`/medical/${robot.robot_id}`)
  }

  function handleDisarmed() {
    setJustArmed(null)
    // replace: 해제된 로봇 화면으로 뒤로가기 하면 곧장 다시 튕겨나온다.
    navigate('/medical', { replace: true })
  }

  // 활성화는 그대로 두고 선택 화면으로만 돌아간다. 로봇 여러 대를 차례로
  // 켜려면 필요하다 — 2호를 살려둔 채 1호도 켜는 식이다. 선택은 이 탭의
  // 시야일 뿐이라 URL 을 떠나도 로봇은 계속 armed 상태로 스캔을 기다린다.
  function handleBackToPicker() {
    setJustArmed(null)
    navigate('/medical')
  }

  // 초회 로딩: robots 가 상태 전이의 뼈대다. 이것만 있으면 화면을 그릴 수 있다.
  if (robots.loading && !robots.data) {
    return <p>불러오는 중…</p>
  }

  // 어느 소스든 최신 tick 이 실패하면 배너를 띄우고, 카드는 마지막 성공값으로
  // 계속 그린다. 다음 tick 이 성공하면 usePolling 이 error 를 null 로 지워
  // 배너는 자동으로 사라진다.
  const stale = Boolean(sessions.error || events.error || robots.error)
  const sessionEvents = events.data ?? []

  if (!selectedRobot) {
    return (
      <div className="dashboard">
        {stale && <ErrorBanner />}
        <RobotPicker robots={robotList} onArmed={handleSelect} />
      </div>
    )
  }

  if (scanFlash) {
    return (
      <div className="dashboard">
        <ScanConfirmation session={scanFlash} />
      </div>
    )
  }

  return (
    <div className="dashboard">
      {stale && <ErrorBanner />}
      {/* 활성화·안내를 유지한 채 선택 화면으로. 다른 로봇을 추가로 켜는 통로다. */}
      <button
        type="button"
        className="btn back-to-picker"
        onClick={handleBackToPicker}
        title="활성화는 유지된 채 선택 화면으로 돌아갑니다"
      >
        ← 로봇 선택으로
      </button>
      {activeSession ? (
        // 환자가 확인된 뒤로는 카메라를 보여주지 않는다. QR 을 대는 순간을
        // 안내하려고 띄우는 화면이라 그 순간이 지나면 쓸모가 없고, 로봇도
        // arming 이 소비되면서 송출을 멈춘다.
        <SessionView
          session={activeSession}
          robot={selectedRobot}
          events={sessionEvents}
        />
      ) : (
        // 스캔 대기 순간엔 카메라가 카드 안에 들어가 있어야 "여기에 QR 을
        // 대세요" 안내와 시선이 한 곳에 모인다.
        <ArmedWaiting
          robot={selectedRobot}
          cameraStreamUrl={CAMERA_STREAM_URL}
          onDisarmed={handleDisarmed}
        />
      )}
    </div>
  )
}

interface SessionViewProps {
  session: ActiveSession
  robot: Robot
  events: EventOut[]
}

function SessionView({ session, robot, events }: SessionViewProps) {
  const derivedState = deriveRobotState(events, {
    session_id: session.session_id,
    ended_at: session.ended_at,
  })
  const derivedDestination =
    deriveCurrentDestination(events, session.session_id)?.visitName ?? null

  const sessionOnlyEvents = events.filter((e) => e.session_id === session.session_id)

  return (
    <>
      <div className="dashboard-row">
        <PatientInfoCard
          patient={session.patient}
          robotId={session.robot_id}
          startedAt={session.started_at}
        />
        <RobotStatusBadge
          state={derivedState}
          batteryPercent={robot.battery_percent ?? null}
          batteryRecordedAt={robot.battery_recorded_at ?? null}
          currentDestination={derivedDestination}
        />
      </div>
      <ProgressStepper
        steps={session.steps}
        currentStepOrder={session.current_step_order}
      />
      <NotificationArea
        notifications={sessionOnlyEvents.slice(0, 10).map(toNotification)}
      />
    </>
  )
}

// QR 이 막 인식된 순간에만 잠깐 뜨는 확인 화면.
// role="status" 로 스크린리더에도 읽히게 한다 — 스스로 사라지는 화면이라
// 놓치면 되돌릴 방법이 없다.
function ScanConfirmation({ session }: { session: ActiveSession }) {
  return (
    <div className="card scan-confirm" role="status">
      <div className="scan-confirm-mark" aria-hidden="true">
        ✓
      </div>
      <div className="scan-confirm-title">QR 인식되었습니다</div>
      <div className="scan-confirm-patient">
        {session.patient.name}
        <span className="scan-confirm-id mono">{session.patient.patient_id}</span>
      </div>
      <div className="scan-confirm-hint">안내를 시작합니다…</div>
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
