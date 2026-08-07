import { useEffect, useRef, useState } from 'react'

import { ArmedWaiting } from '../components/ArmedWaiting'
import { CameraStream } from '../components/CameraStream'
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

// 브라우저 탭이 "지금 어떤 로봇을 담당하고 있는가" 를 기억한다. 새로고침해도
// 잃지 않으려고 localStorage. 서버 상태가 아니라 클라이언트 시야다 —
// 여러 의료진이 탭을 각자 열어 각자 다른 로봇을 골라 쓴다.
const SELECTION_KEY = 'mingky.medical.selectedRobotId'

function loadSelection(): string | null {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem(SELECTION_KEY)
}

function saveSelection(robotId: string | null) {
  if (typeof window === 'undefined') return
  if (robotId == null) window.localStorage.removeItem(SELECTION_KEY)
  else window.localStorage.setItem(SELECTION_KEY, robotId)
}

export function MedicalDashboard() {
  const [selectedRobotId, setSelectedRobotId] = useState<string | null>(loadSelection)
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
  //   - 첫 tick 은 prev 가 undefined 라 지나간다 (세션 도중 새로고침)
  //   - 로봇 선택이 막 바뀐 tick 도 지나간다 (이미 안내 중인 로봇을 이어받은 경우)
  // 둘 다 방금 스캔한 게 아닌데 확인 화면이 뜨면 거짓 신호가 된다.
  const [scanFlash, setScanFlash] = useState<ActiveSession | null>(null)
  const prevSessionRef = useRef<{
    robotId: string | null
    sessionId: number | null | undefined
  }>({ robotId: null, sessionId: undefined })

  useEffect(() => {
    const current = activeSession?.session_id ?? null
    const prev = prevSessionRef.current
    const sameRobot = prev.robotId === selectedRobotId
    prevSessionRef.current = { robotId: selectedRobotId, sessionId: current }
    if (sameRobot && prev.sessionId === null && current != null && activeSession) {
      setScanFlash(activeSession)
    }
  }, [activeSession, selectedRobotId])

  useEffect(() => {
    if (scanFlash == null) return
    const id = window.setTimeout(() => setScanFlash(null), SCAN_FLASH_MS)
    return () => window.clearTimeout(id)
  }, [scanFlash])

  useEffect(() => {
    if (selectionOrphaned) {
      saveSelection(null)
      setSelectedRobotId(null)
    }
  }, [selectionOrphaned])

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
    saveSelection(robot.robot_id)
    setSelectedRobotId(robot.robot_id)
    setJustArmed(robot)
  }

  function handleDisarmed() {
    saveSelection(null)
    setSelectedRobotId(null)
    setJustArmed(null)
  }

  // 초회 로딩: robots 가 상태 전이의 뼈대다. 이것만 있으면 화면을 그릴 수 있다.
  if (robots.loading && !robots.data) {
    return <p>불러오는 중…</p>
  }

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
      {activeSession ? (
        <>
          <SessionView
            session={activeSession}
            robot={selectedRobot}
            events={sessionEvents}
          />
          {/* 세션 진행 중엔 카메라를 별도 카드로 (환자·진행 상황·알림 사이에서
              시선을 뺏지 않도록 하단에 붙인다). */}
          {CAMERA_STREAM_URL && <CameraStream streamUrl={CAMERA_STREAM_URL} />}
        </>
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
