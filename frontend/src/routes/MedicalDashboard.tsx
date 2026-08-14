import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { ArmedWaiting } from '../components/ArmedWaiting'
import { GuidanceStartCard } from '../components/GuidanceStartCard'
import { GuidanceRearCamera } from '../components/GuidanceRearCamera'
import { NotificationArea } from '../components/NotificationArea'
import { PatientInfoCard } from '../components/PatientInfoCard'
import { ProgressStepper } from '../components/ProgressStepper'
import { RobotPicker } from '../components/RobotPicker'
import { HospitalMapPhoto } from '../components/HospitalMapPhoto'
import { HospitalMap3D } from '../components/HospitalMap3D'
import { RobotModeControl } from '../components/RobotModeControl'
import { RobotStatusBadge } from '../components/RobotStatusBadge'
import { TeleopPad } from '../components/TeleopPad'
import { getActiveSessions, getRobots, sendOrder } from '../lib/api'
import { deriveCurrentDestination, deriveRobotState } from '../lib/derivedStatus'
import { toNotification } from '../lib/eventMessages'
import { listEvents } from '../lib/eventsApi'
import { useRobotMode } from '../lib/useRobotMode'
import { usePolling } from '../lib/usePolling'
import { useTeleopSocket } from '../lib/useTeleopSocket'
import type { EventOut } from '../types/events'
import type { ActiveSession, Robot } from '../types/monitoring'

const POLL_MS = 3000
// QR 이 인식된 순간 확인 화면을 띄우고 있는 시간. 스캔 대기에서 안내 화면으로
// 그냥 바뀌면 환자·의료진 모두 "인식이 된 건가?" 를 판단할 근거가 없다.
// 너무 길면 안내 시작이 늦어지므로 읽고 넘어갈 만큼만 잡는다.
const SCAN_FLASH_MS = 2200
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
  const [locationBusy, setLocationBusy] = useState(false)
  const [locationNotice, setLocationNotice] = useState<string | null>(null)

  const sessions = usePolling((signal) => getActiveSessions({ signal }), POLL_MS)
  const robots = usePolling((signal) => getRobots({ signal }), POLL_MS)
  const events = usePolling(
    async (signal) => (
      await listEvents(
        { robot_id: selectedRobotId ?? undefined, limit: 100 },
        { signal },
      )
    ).items,
    POLL_MS,
  )

  // 조작과 위치는 폴링이 아니라 소켓이다. 방향키를 누른 뒤 3초 뒤에 움직이면
  // 조작이라 할 수 없고, 위치도 지도 위에서 끊겨 보인다.
  const teleop = useTeleopSocket(selectedRobotId)

  // 모드는 대시보드의 이벤트 목록에서 찾지 않는다. 그 목록은 선택 로봇의 최근
  // 100건이라
  // nav.* 이 쌓이면 모드 변경이 창 밖으로 밀려 "확인 중" 으로 돌아간다.
  const mode = useRobotMode(selectedRobotId, POLL_MS)

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

  async function findRobotLocation() {
    if (!selectedRobotId || activeSession || selectedRobot?.localization_active) return
    if (!window.confirm(
      '로봇 위치를 다시 찾을까요?\n로봇이 제자리에서 돌거나 앞뒤로 움직일 수 있으니 주변을 비워주세요.',
    )) return
    setLocationBusy(true)
    setLocationNotice(null)
    try {
      await sendOrder(selectedRobotId, 'localize', 'run')
      setLocationNotice('로봇이 위치를 찾기 시작했습니다.')
    } catch {
      setLocationNotice('위치 찾기 요청을 보내지 못했습니다. 잠시 후 다시 시도하세요.')
    } finally {
      setLocationBusy(false)
    }
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
      {/* 안내 중이든 대기 중이든 항상 보여야 한다. 급할 때 찾는 것이
          화면 상태에 따라 사라지면 안 된다. */}
      {selectedRobotId && (
          <div className="control-deck">
            {/* 끊김은 조작·정지가 안 닿는다는 뜻이라 화면 위쪽에 크게 알린다.
                패널마다 흩어 놓으면 어느 것이 진짜 상태인지 알기 어렵다. */}
            {!teleop.robotConnected && (
              <p className="control-deck__offline" role="alert">
                로봇 연결 끊김 — 조작과 비상정지가 전달되지 않습니다.
                {teleop.connected
                  ? ' 관제 서버는 정상이며 로봇 쪽 브리지를 확인하세요.'
                  : ' 관제 서버와의 연결도 끊겨 있습니다.'}
              </p>
            )}
            <RobotModeControl
              robotId={selectedRobotId}
              mode={mode}
              robotConnected={teleop.robotConnected}
            />
            <HospitalMapPhoto
              pose={teleop.pose}
              selected
              estop={mode === 'estop'}
            />
            <HospitalMap3D
              pose={teleop.pose}
              live={teleop.robotConnected}
              scan={teleop.scan}
              particles={teleop.particles}
              plan={teleop.plan}
              onSetPose={teleop.setPose}
              estop={mode === 'estop'}
            />
            <TeleopPad
              drive={teleop.drive}
              enabled={mode === 'manual' && teleop.robotConnected}
              disabledReason={
                !teleop.robotConnected
                  ? '로봇이 관제에 연결되어 있지 않습니다.'
                  : mode === 'estop'
                    ? '비상정지가 걸려 있습니다. 해제해야 움직입니다.'
                    : '수동 조작 모드로 전환해야 움직입니다.'
              }
            />
          </div>
      )}
      {selectedRobotId && (
        <section className="card medical-location-control">
          <div>
            <div className="card-title">로봇 위치 다시 찾기</div>
            <p>
              지도에서 로봇 위치가 실제와 다를 때 사용하세요.
              로봇이 주변을 확인하며 잠시 움직일 수 있습니다.
            </p>
          </div>
          <div className="medical-location-control__action">
            <strong>
              {selectedRobot.localization_active ? '위치를 찾는 중입니다' : '필요할 때만 실행하세요'}
            </strong>
            <button
              type="button"
              className="btn"
              disabled={locationBusy || activeSession != null
                || !teleop.robotConnected || mode !== 'auto'
                || selectedRobot.system_state !== 'active'
                || selectedRobot.localization_active}
              onClick={findRobotLocation}
            >
              로봇 위치 다시 찾기
            </button>
          </div>
          {locationNotice && <p className="medical-location-control__notice" role="status">{locationNotice}</p>}
          {activeSession && <p className="medical-location-control__disabled">환자 안내 중에는 위치를 다시 찾을 수 없습니다.</p>}
        </section>
      )}
      {activeSession ? (
        // 환자가 확인된 뒤로는 카메라를 보여주지 않는다. QR 을 대는 순간을
        // 안내하려고 띄우는 화면이라 그 순간이 지나면 쓸모가 없고, 로봇도
        // arming 이 소비되면서 송출을 멈춘다.
        <SessionView
          session={activeSession}
          robot={selectedRobot}
          events={sessionEvents}
          mode={mode}
          robotConnected={teleop.robotConnected}
        />
      ) : (
        // 스캔 대기 순간엔 카메라가 카드 안에 들어가 있어야 "여기에 QR 을
        // 대세요" 안내와 시선이 한 곳에 모인다.
        <ArmedWaiting
          robot={selectedRobot}
          cameraStreamUrl={`/camera/${encodeURIComponent(selectedRobot.robot_id)}/front/stream`}
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
  mode: 'auto' | 'manual' | 'estop' | null
  robotConnected: boolean
}

function SessionView({
  session,
  robot,
  events,
  mode,
  robotConnected,
}: SessionViewProps) {
  const derivedState = deriveRobotState(events, {
    session_id: session.session_id,
    robot_id: session.robot_id,
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
      <GuidanceStartCard
        session={session}
        events={sessionOnlyEvents}
        mode={mode}
        robotConnected={robotConnected}
      />
      {derivedState === '안내중' && (
        <GuidanceRearCamera robotId={session.robot_id} />
      )}
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
      <div className="scan-confirm-hint">
        환자가 확인되었습니다. 안내 시작 버튼을 눌러주세요.
      </div>
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
