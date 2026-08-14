import { useEffect, useMemo, useState } from 'react'

import { PinkyModel } from '../components/PinkyModelCard'
import { getRobots, sendOrder, type RobotCommand } from '../lib/api'
import { usePolling } from '../lib/usePolling'
import { useRobotMode } from '../lib/useRobotMode'

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

  const robotList = useMemo(
    () => (robots.data ?? []).filter((robot) => robot.robot_type === 'mobile'),
    [robots.data],
  )
  useEffect(() => {
    if (robotList.length === 0) return
    if (selectedRobotId && robotList.some((robot) => robot.robot_id === selectedRobotId)) return
    setSelectedRobotId(robotList[0].robot_id)
  }, [robotList, selectedRobotId])

  const selectedRobot = robotList.find((robot) => robot.robot_id === selectedRobotId) ?? null
  const mode = useRobotMode(selectedRobotId, POLL_MS)
  const activeSession = selectedRobot?.active_session_id != null
  const online = selectedRobot?.link_state === 'online'
  const appliedSpeed = selectedRobot?.navigation_speed_mps ?? null
  const speedBlocked = busy || activeSession || !online || mode !== 'auto'
    || selectedRobot?.system_state !== 'active' || selectedRobot?.localization_active
    || selectedRobot?.fire_alarm_active === true || appliedSpeed == null

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
          <span>명령을 보낼 Pinky를 확인하세요.</span>
        </div>
        <div className="waypoint-robot-cards">
          {robotList.map((robot) => (
            <button key={robot.robot_id} type="button"
              className={`waypoint-robot-card${robot.robot_id === selectedRobotId ? ' selected' : ''}`}
              onClick={() => setSelectedRobotId(robot.robot_id)}>
              <PinkyModel />
              <span className="waypoint-robot-card__content">
                <span className="waypoint-robot-card__top">
                  <strong>{robot.display_name}</strong>
                  <span className={`waypoint-robot-card__link waypoint-robot-card__link--${robot.link_state}`}>
                    {robot.link_state === 'online' ? '온라인' : robot.link_state === 'offline' ? '오프라인' : '연결 이력 없음'}
                  </span>
                </span>
                <span className="waypoint-robot-card__meta">
                  <code>{robot.robot_id}</code>
                  <span>{systemStateLabel(robot.system_state)}</span>
                  {robot.active_session_id != null && <span>환자 안내 중</span>}
                </span>
              </span>
            </button>
          ))}
        </div>
      </section>

      {notice && <div className="waypoint-notice" role="status">{notice}</div>}
      {activeSession && <div className="waypoint-safety-banner" role="alert">환자 안내 중에는 시스템 중지·재시작이 차단됩니다.</div>}

      <div className="system-control-grid">
        <section className="card waypoint-system-control">
          <div className="card-title">통합 시스템</div>
          <div className="waypoint-system-status">
            <span>상태</span>
            <strong data-state={selectedRobot?.system_state ?? 'unknown'}>
              {systemStateLabel(selectedRobot?.system_state ?? 'unknown')}
            </strong>
          </div>
          <p>Nav2·안내 상태머신·카메라를 함께 관리합니다. 관제 통신과 배터리 보고는 계속 유지됩니다.</p>
          <div className="waypoint-system-actions">
            <button type="button" className="btn primary"
              disabled={busy || !online || selectedRobot?.system_state === 'active'}
              onClick={() => issue('system_start', '시스템 가동', `${selectedRobotId} 통합 시스템을 가동할까요?`)}>가동</button>
            <button type="button" className="btn"
              disabled={busy || activeSession || !online || selectedRobot?.system_state !== 'active' || selectedRobot?.localization_active}
              onClick={() => issue('system_restart', '시스템 재시작', `${selectedRobotId} 통합 시스템을 재시작할까요?`)}>재시작</button>
            <button type="button" className="btn danger"
              disabled={busy || activeSession || !online || selectedRobot?.system_state === 'inactive' || selectedRobot?.localization_active}
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
          <strong className={selectedRobot?.fire_alarm_active ? 'waypoint-localize-running' : ''}>
            {selectedRobot?.fire_alarm_active === true
              ? '경보 발동 중'
              : selectedRobot?.fire_alarm_active === false ? '정상' : '상태 확인 중'}
          </strong>
          <p>현장에 불이 없고 대피가 끝난 것을 직접 확인한 뒤에만 해제하세요. 해제 후에는 새 화재를 다시 감지할 수 있습니다.</p>
          <button type="button" className="btn danger"
            disabled={busy || !online || selectedRobot?.system_state !== 'active'
              || selectedRobot?.fire_alarm_active !== true}
            onClick={() => issue(
              'fire_alarm_reset',
              '화재 경보 해제',
              `${selectedRobotId}의 화재 경보를 해제할까요?\n현장 안전과 대피 종료를 먼저 확인하세요.`,
            )}>
            화재 경보 해제
          </button>
        </section>
      </div>
    </div>
  )
}
