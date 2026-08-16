import { useEffect, useMemo, useState } from 'react'

import { ManipulatorPanel } from '../components/ManipulatorPanel'
import { PinkyModel } from '../components/PinkyModelCard'
import { TopicWatchCard } from '../components/TopicWatchCard'
import { getRobots, sendOrder, type RobotCommand } from '../lib/api'
import { usePolling } from '../lib/usePolling'
import { useRobotMode } from '../lib/useRobotMode'
import { isManipulator, isMobile, type Robot } from '../types/monitoring'

const POLL_MS = 3000
const MIN_NAVIGATION_SPEED = 0.05
const MAX_NAVIGATION_SPEED = 0.25
const DEFAULT_NAVIGATION_SPEED = 0.20
const NAVIGATION_SPEED_STEP = 0.01

function clampNavigationSpeed(value: number) {
  return Math.min(
    MAX_NAVIGATION_SPEED,
    Math.max(MIN_NAVIGATION_SPEED, Math.round(value * 100) / 100),
  )
}

/** 선택기 카드의 아랫줄. 종류마다 '지금 무엇을 하고 있나' 의 답이 다르다. */
function cardStatus(robot: Robot): string {
  if (isMobile(robot)) return systemStateLabel(robot.system_state)
  if (robot.detail.homing_required) return '홈 복귀 필요'
  return robot.detail.active_dispense_id === null ? '조제 대기' : '조제 중'
}

function systemStateLabel(state: string) {
  if (state === 'active') return '가동 중'
  if (state === 'activating') return '시작 중'
  if (state === 'deactivating') return '종료 중'
  if (state === 'inactive') return '중지됨'
  if (state === 'failed') return '실패'
  return '확인 불가'
}

export function SystemDashboard() {
  const robots = usePolling((signal) => getRobots({ signal }), POLL_MS)
  const [selectedRobotId, setSelectedRobotId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [speedDraft, setSpeedDraft] = useState(DEFAULT_NAVIGATION_SPEED)

  // §7.3: 선택기는 4대를 모두 보여주고 제어 패널만 타입에 따라 바꾼다.
  // 여기서 팔을 거르면 "팔은 관제 대상이 아니다" 라는 뜻이 되고, 실제로
  // 조제 로봇 2대가 그 이유로 관제 사각지대에 있었다.
  const robotList = useMemo(() => robots.data ?? [], [robots.data])
  useEffect(() => {
    if (robotList.length === 0) return
    if (selectedRobotId && robotList.some((robot) => robot.robot_id === selectedRobotId)) return
    // 첫 화면은 주행 로봇이다. 목록은 robot_id 순이라 omx-* 가 pinky-* 앞에
    // 오는데, 이 탭에 오는 이유는 대개 Nav2·통합 시스템 제어라 팔이 기본으로
    // 잡히면 매번 한 번 더 클릭하게 된다. 팔만 있는 설치에서는 팔을 고른다.
    const first = robotList.find(isMobile) ?? robotList[0]
    setSelectedRobotId(first.robot_id)
  }, [robotList, selectedRobotId])

  const selectedRobot = robotList.find((robot) => robot.robot_id === selectedRobotId) ?? null
  // 아래 제어 카드는 전부 주행 로봇 것이다. 한 번 좁혀두면 팔이 선택된 동안
  // mobile 필드를 읽는 자리가 컴파일 타임에 막힌다.
  const mobileRobot = selectedRobot && isMobile(selectedRobot) ? selectedRobot : null
  const manipulatorRobot = selectedRobot && isManipulator(selectedRobot)
    ? selectedRobot : null
  // 주행 모드는 팔에 없는 개념이다. 팔을 고른 동안 폴링하면 없는 이벤트를
  // 3초마다 묻게 된다.
  const mode = useRobotMode(mobileRobot?.robot_id ?? null, POLL_MS)
  const activeSession = mobileRobot?.active_session_id != null
  const online = selectedRobot?.link_state === 'online'
  const appliedSpeed = mobileRobot?.navigation_speed_mps ?? null
  const speedBlocked = busy || activeSession || !online || mode !== 'auto'
    || mobileRobot?.system_state !== 'active' || mobileRobot?.localization_active
    || mobileRobot?.fire_alarm_active === true || appliedSpeed == null

  useEffect(() => {
    setSpeedDraft(appliedSpeed ?? DEFAULT_NAVIGATION_SPEED)
  }, [selectedRobotId, appliedSpeed])

  async function issue(command: RobotCommand, label: string, confirmMessage: string) {
    if (!selectedRobotId || !window.confirm(confirmMessage)) return
    setBusy(true)
    setNotice(null)
    try {
      await sendOrder(selectedRobotId, command, 'run')
      setNotice(`${label} 명령을 보냈습니다. 상태 반영에는 수 초 걸릴 수 있습니다.`)
    } catch {
      setNotice(`${label} 명령을 보내지 못했습니다.`)
    } finally {
      setBusy(false)
    }
  }

  async function applyNavigationSpeed() {
    if (!selectedRobotId || speedBlocked) return
    if (!window.confirm(
      `${selectedRobotId}의 최대 주행 속도를 ${speedDraft.toFixed(2)} m/s로 변경할까요?`,
    )) return
    setBusy(true)
    setNotice(null)
    try {
      await sendOrder(selectedRobotId, 'set_navigation_speed', speedDraft.toFixed(2))
      setNotice('주행 속도 변경 명령을 보냈습니다. 실제 적용값을 확인하고 있습니다.')
    } catch {
      setNotice('주행 속도 변경 명령을 보내지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="system-dashboard">
      <header className="waypoint-page-header">
        <span className="waypoint-page-header__eyebrow">ENGINEER TOOL</span>
        <h1>로봇 시스템 관리</h1>
        <p>로봇 통합 시스템의 가동 상태를 확인하고 제어합니다.</p>
      </header>

      <section className="waypoint-robot-picker" aria-label="관리할 로봇 선택">
        <div className="waypoint-robot-picker__heading">
          <strong>관리할 로봇 선택</strong>
          <span>주행 로봇과 조제 로봇을 모두 관리합니다.</span>
        </div>
        <div className="waypoint-robot-cards">
          {robotList.map((robot) => (
            <button key={robot.robot_id} type="button"
              className={`waypoint-robot-card${robot.robot_id === selectedRobotId ? ' selected' : ''}`}
              onClick={() => setSelectedRobotId(robot.robot_id)}>
              {/* OMX 3D 모델이 없다. 핑키 모델을 팔 자리에 놓으면 어느 쪽을
                  고르는지가 화면에서 거짓이 된다. */}
              {isMobile(robot)
                ? <PinkyModel />
                : <span className="waypoint-robot-card__glyph">OMX</span>}
              <span className="waypoint-robot-card__content">
                <span className="waypoint-robot-card__top">
                  <strong>{robot.display_name}</strong>
                  <span className={`waypoint-robot-card__link waypoint-robot-card__link--${robot.link_state}`}>
                    {robot.link_state === 'online' ? '온라인' : robot.link_state === 'offline' ? '오프라인' : '연결 이력 없음'}
                  </span>
                </span>
                <span className="waypoint-robot-card__meta">
                  <code>{robot.robot_id}</code>
                  <span>{cardStatus(robot)}</span>
                  {isMobile(robot) && robot.active_session_id != null
                    && <span>환자 안내 중</span>}
                </span>
              </span>
            </button>
          ))}
        </div>
      </section>

      {notice && <div className="waypoint-notice" role="status">{notice}</div>}
      {activeSession && <div className="waypoint-safety-banner" role="alert">환자 안내 중에는 시스템 중지·재시작이 차단됩니다.</div>}

      {/* 제어 패널만 타입에 따라 바꾼다 (§7.3). 팔에는 Nav2 도 화재 감지도
          안내 세션도 없어서 아래 세 카드가 하나도 성립하지 않는다. */}
      {manipulatorRobot && <ManipulatorPanel robot={manipulatorRobot} />}

      {/* 유닛 상태 위에 둔다. "가동 중" 이라는 글자보다 데이터가 흐르는지가
          먼저 나와야 한다 — 실제 장애는 유닛이 active 인 채로 온다 (§7.2). */}
      {mobileRobot && <TopicWatchCard robot={mobileRobot} />}

      {mobileRobot && <div className="system-control-grid">
        <section className="card waypoint-system-control">
          <div className="card-title">통합 시스템</div>
          <div className="waypoint-system-status">
            <span>상태</span>
            <strong data-state={mobileRobot?.system_state ?? 'unknown'}>
              {systemStateLabel(mobileRobot?.system_state ?? 'unknown')}
            </strong>
          </div>
          <p>Nav2·안내 상태머신·카메라를 함께 관리합니다. 관제 통신과 배터리 보고는 계속 유지됩니다.</p>
          <div className="waypoint-system-actions">
            <button type="button" className="btn primary"
              disabled={busy || !online || mobileRobot?.system_state === 'active'}
              onClick={() => issue('system_start', '시스템 가동', `${selectedRobotId} 통합 시스템을 가동할까요?`)}>가동</button>
            <button type="button" className="btn"
              disabled={busy || activeSession || !online || mobileRobot?.system_state !== 'active' || mobileRobot?.localization_active}
              onClick={() => issue('system_restart', '시스템 재시작', `${selectedRobotId} 통합 시스템을 재시작할까요?`)}>재시작</button>
            <button type="button" className="btn danger"
              disabled={busy || activeSession || !online || mobileRobot?.system_state === 'inactive' || mobileRobot?.localization_active}
              onClick={() => issue('system_stop', '시스템 중지', `${selectedRobotId} 통합 시스템을 중지할까요?`)}>중지</button>
          </div>
        </section>

        <section className="card waypoint-speed-control">
          <div className="card-title">주행 속도 설정</div>
          <p>Nav2 자동주행의 직진 목표속도를 조절합니다. 기존 안전 상한을 넘을 수 없습니다.</p>
          <div className="waypoint-speed-applied">
            <span>현재 적용값</span>
            <strong>{appliedSpeed == null ? '확인 중' : `${appliedSpeed.toFixed(2)} m/s`}</strong>
          </div>
          <div className="waypoint-speed-stepper" aria-label="주행 속도 조절">
            <button type="button" className="btn" aria-label="주행 속도 낮추기"
              disabled={speedBlocked || speedDraft <= MIN_NAVIGATION_SPEED}
              onClick={() => setSpeedDraft((value) => clampNavigationSpeed(value - NAVIGATION_SPEED_STEP))}>−</button>
            <output aria-live="polite">{speedDraft.toFixed(2)} m/s</output>
            <button type="button" className="btn" aria-label="주행 속도 높이기"
              disabled={speedBlocked || speedDraft >= MAX_NAVIGATION_SPEED}
              onClick={() => setSpeedDraft((value) => clampNavigationSpeed(value + NAVIGATION_SPEED_STEP))}>＋</button>
          </div>
          <small>조절 범위 {MIN_NAVIGATION_SPEED.toFixed(2)}–{MAX_NAVIGATION_SPEED.toFixed(2)} m/s · 0.01 m/s 단위</small>
          {mode !== 'auto' && <p>자동 주행 모드에서만 변경할 수 있습니다.</p>}
          <button type="button" className="btn primary"
            disabled={speedBlocked || Math.abs(speedDraft - (appliedSpeed ?? speedDraft)) < 0.001}
            onClick={applyNavigationSpeed}>적용</button>
        </section>

        <section className="card waypoint-localize-control">
          <div className="card-title">화재 경보</div>
          <strong className={mobileRobot?.fire_alarm_active ? 'waypoint-localize-running' : ''}>
            {mobileRobot?.fire_alarm_active === true
              ? '경보 발동 중'
              : mobileRobot?.fire_alarm_active === false ? '정상' : '상태 확인 중'}
          </strong>
          <p>현장에 불이 없고 대피가 끝난 것을 직접 확인한 뒤에만 해제하세요. 해제 후에는 새 화재를 다시 감지할 수 있습니다.</p>
          <button type="button" className="btn danger"
            disabled={busy || !online || mobileRobot?.system_state !== 'active'
              || mobileRobot?.fire_alarm_active !== true}
            onClick={() => issue(
              'fire_alarm_reset',
              '화재 경보 해제',
              `${selectedRobotId}의 화재 경보를 해제할까요?\n현장 안전과 대피 종료를 먼저 확인하세요.`,
            )}>
            화재 경보 해제
          </button>
        </section>
      </div>}
    </div>
  )
}
