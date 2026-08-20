import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { LazyHospitalMap3D, type WaypointMarker } from '../components/LazyHospitalMap3D'
import { RobotModeControl } from '../components/RobotModeControl'
import { TeleopPad } from '../components/TeleopPad'
import { PinkyModel } from '../components/PinkyModelCard'
import {
  checkWaypoints,
  getRobots,
  getWaypoints,
  sendOrder,
  type WaypointCheckResult,
  type WaypointSet,
  type WaypointValue,
} from '../lib/api'
import { listEvents } from '../lib/eventsApi'
import { usePolling } from '../lib/usePolling'
import { isMobile } from '../types/monitoring'
import { useRobotMode } from '../lib/useRobotMode'
import { useTeleopSocket } from '../lib/useTeleopSocket'

const POLL_MS = 3000
const SETTLE_MS = 700
const EMPTY: WaypointValue = { x: 0, y: 0, yaw: 0 }

export function WaypointDashboard() {
  const robots = usePolling((signal) => getRobots({ signal }), POLL_MS)
  const [selectedRobotId, setSelectedRobotId] = useState<string | null>(null)
  const [source, setSource] = useState<WaypointSet | null>(null)
  const [drafts, setDrafts] = useState<Record<string, WaypointValue>>({})
  const [selectedName, setSelectedName] = useState('')
  const [newName, setNewName] = useState('')
  const [checkResult, setCheckResult] = useState<WaypointCheckResult | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [testAlert, setTestAlert] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [settled, setSettled] = useState(true)
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const handledTestEvent = useRef<string | null>(null)

  const robotList = useMemo(
    // isMobile 이 타입 가드라 걸러진 배열이 MobileRobot[] 이 된다.
    // 팔에는 카메라도 웨이포인트도 없다.
    () => (robots.data ?? [])
      .filter(isMobile)
      .filter((robot) => robot.robot_id.startsWith('pinky-')),
    [robots.data],
  )
  useEffect(() => {
    if (robotList.length === 0) return
    if (selectedRobotId && robotList.some((robot) => robot.robot_id === selectedRobotId)) return
    setSelectedRobotId(
      robotList.find((robot) => robot.robot_id === 'pinky-02')?.robot_id
        ?? robotList[0].robot_id,
    )
  }, [robotList, selectedRobotId])

  useEffect(() => {
    getWaypoints()
      .then((data) => {
        setSource(data)
        setDrafts(data.waypoints)
        setSelectedName(Object.keys(data.waypoints)[0] ?? '')
      })
      .catch(() => setNotice('Waypoint 설정을 불러오지 못했습니다.'))
  }, [])

  const selectedRobot = robotList.find((robot) => robot.robot_id === selectedRobotId) ?? null
  const teleop = useTeleopSocket(selectedRobotId)
  const mode = useRobotMode(selectedRobotId, POLL_MS)
  const waypointEvents = usePolling(
    async (signal) => (
      await listEvents(
        { robot_id: selectedRobotId ?? undefined, limit: 20 },
        { signal },
      )
    ).items,
    POLL_MS,
    selectedRobotId,
  )
  const selected = drafts[selectedName] ?? EMPTY
  const activeSession = selectedRobot?.active_session_id != null

  const statusByName = useMemo(
    () => new Map(checkResult?.items.map((item) => [item.name, item.status]) ?? []),
    [checkResult],
  )
  const markers: WaypointMarker[] = useMemo(
    () => Object.entries(drafts).map(([name, waypoint]) => ({
      name,
      ...waypoint,
      status: statusByName.get(name),
      selected: name === selectedName,
    })),
    [drafts, selectedName, statusByName],
  )

  const teleopDrive = teleop.drive
  const drive = useCallback((linear: number, angular: number) => {
    teleopDrive(linear, angular)
    setSettled(false)
    if (settleTimer.current) clearTimeout(settleTimer.current)
    if (linear === 0 && angular === 0) {
      settleTimer.current = setTimeout(() => setSettled(true), SETTLE_MS)
    }
  }, [teleopDrive])

  useEffect(() => () => {
    if (settleTimer.current) clearTimeout(settleTimer.current)
  }, [])

  const teleopEnabled = mode === 'manual' && teleop.appliedMode === 'manual'
    && teleop.robotConnected && !activeSession
  const captureEnabled = teleopEnabled && settled && teleop.pose !== null
  const selectedCheck = checkResult?.items.find((item) => item.name === selectedName) ?? null
  const testEnabled = Boolean(
    selectedRobotId && teleop.robotConnected && !activeSession
      && mode === 'auto' && teleop.appliedMode === 'auto'
      && selectedRobot?.system_state === 'active'
      && !selectedRobot.localization_active
      && selectedCheck && !['blocked', 'outside'].includes(selectedCheck.status),
  )
  const localizationEnabled = Boolean(
    selectedRobotId && teleop.robotConnected && !activeSession
      && mode === 'auto' && teleop.appliedMode === 'auto'
      && selectedRobot?.system_state === 'active'
      && !selectedRobot.localization_active,
  )

  useEffect(() => {
    handledTestEvent.current = null
    setTestAlert(null)
  }, [selectedRobotId])

  useEffect(() => {
    const latest = waypointEvents.data?.find((event) =>
      event.event_code === 'waypoint.test_started'
      || event.event_code === 'waypoint.test_succeeded'
      || event.event_code === 'waypoint.test_failed')
    if (!latest || latest.event_id === handledTestEvent.current) return
    handledTestEvent.current = latest.event_id
    if (Date.now() - Date.parse(latest.occurred_at) > 120_000) return

    if (latest.event_code === 'waypoint.test_started') {
      setTestAlert(null)
      return
    }
    if (latest.event_code === 'waypoint.test_succeeded') {
      setTestAlert(null)
      setNotice(`${String(latest.payload.waypoint_name ?? 'Waypoint')} 시험 주행을 완료했습니다.`)
      return
    }
    if (latest.payload.reason === 'goal_occupied'
        || Number(latest.payload.error_code) === 206) {
      setTestAlert(
        `${String(latest.payload.waypoint_name ?? 'Waypoint')} 목표 위치가 `
        + 'costmap 갱신 후에도 장애물로 확인됐습니다. 주변을 비우거나 좌표를 다시 측정하세요.',
      )
    } else {
      setTestAlert(
        `${String(latest.payload.waypoint_name ?? 'Waypoint')} 시험 주행에 실패했습니다. `
        + `오류 코드 ${String(latest.payload.error_code ?? '?')}`,
      )
    }
  }, [waypointEvents.data])

  function updateSelected(field: keyof WaypointValue, value: number) {
    if (!selectedName || !Number.isFinite(value)) return
    setDrafts((current) => ({
      ...current,
      [selectedName]: { ...(current[selectedName] ?? EMPTY), [field]: value },
    }))
    setCheckResult(null)
  }

  function addWaypoint() {
    const name = newName.trim()
    if (!/^[A-Za-z0-9_-]+$/.test(name)) {
      setNotice('Waypoint 이름은 영문, 숫자, 밑줄과 하이픈만 사용할 수 있습니다.')
      return
    }
    if (drafts[name]) {
      setNotice('이미 존재하는 이름입니다.')
      return
    }
    const pose = teleop.pose ?? EMPTY
    setDrafts((current) => ({ ...current, [name]: { ...pose } }))
    setSelectedName(name)
    setNewName('')
    setCheckResult(null)
    setNotice(`${name} 초안을 추가했습니다.`)
  }

  function capturePose() {
    if (!captureEnabled || !teleop.pose || !selectedName) return
    setDrafts((current) => ({ ...current, [selectedName]: { ...teleop.pose! } }))
    setCheckResult(null)
    setNotice(`${selectedName}에 현재 AMCL 위치를 캡처했습니다.`)
  }

  async function runCheck() {
    setBusy(true)
    setNotice(null)
    try {
      const result = await checkWaypoints(drafts)
      setCheckResult(result)
      setNotice(result.ok ? 'Waypoint Check를 통과했습니다.' : '도달 불가 좌표가 있습니다.')
    } catch {
      setNotice('Waypoint Check 요청에 실패했습니다.')
    } finally {
      setBusy(false)
    }
  }

  async function testDrive() {
    if (!testEnabled || !selectedRobotId) return
    if (!window.confirm(`${selectedRobotId}를 ${selectedName}(으)로 시험 주행할까요?`)) return
    setBusy(true)
    try {
      await sendOrder(selectedRobotId, 'goto_pose', JSON.stringify({ name: selectedName, ...selected }))
      setNotice('시험 주행 명령을 보냈습니다. 지도 경로와 비상정지를 확인하세요.')
    } catch {
      setNotice('시험 주행 명령을 보내지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  async function cancelTestDrive() {
    if (!selectedRobotId) return
    if (!window.confirm(`${selectedRobotId}의 현재 시험 주행과 복구 동작을 취소할까요?`)) return
    setBusy(true)
    try {
      await sendOrder(selectedRobotId, 'cancel_navigation', 'run')
      setNotice('시험 주행 취소 명령을 보냈습니다.')
    } catch {
      setNotice('시험 주행 취소 명령을 보내지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  async function findRobotLocation() {
    if (!localizationEnabled || !selectedRobotId) return
    if (!window.confirm(
      `${selectedRobotId}의 위치를 다시 찾을까요?\n로봇이 제자리에서 돌거나 앞뒤로 움직일 수 있으니 주변을 비워주세요.`,
    )) return
    setBusy(true)
    setNotice(null)
    try {
      await sendOrder(selectedRobotId, 'localize', 'run')
      setNotice('위치 다시 찾기 명령을 보냈습니다. 로봇 주변을 비워두고 완료될 때까지 기다리세요.')
    } catch {
      setNotice('위치 다시 찾기 명령을 보내지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  function exportYaml() {
    if (!source) return
    const lines = ['# Waypoint 도구에서 내보낸 초안', 'visit_waypoints:']
    for (const [visit, mapping] of Object.entries(source.visit_waypoints)) {
      lines.push(`  ${visit}:`)
      for (const [kind, name] of Object.entries(mapping)) {
        lines.push(`    ${kind}: ${name ?? 'null'}`)
      }
    }
    lines.push('', 'waypoints:')
    for (const [name, waypoint] of Object.entries(drafts)) {
      lines.push(`  ${name}:`)
      lines.push(`    x: ${waypoint.x.toFixed(6)}`)
      lines.push(`    y: ${waypoint.y.toFixed(6)}`)
      lines.push(`    yaw: ${waypoint.yaw.toFixed(6)}`)
    }
    const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/yaml' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${source.map_name}_waypoints.yaml`
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="waypoint-dashboard">
      <header className="waypoint-page-header">
        <div>
          <span className="waypoint-page-header__eyebrow">ENGINEER TOOL</span>
          <h1>Waypoint 관리</h1>
          <p>로봇을 직접 이동시킨 뒤 현재 AMCL 위치를 캡처하고 검사합니다.</p>
        </div>
      </header>

      <section className="waypoint-robot-picker" aria-label="작업 로봇 선택">
        <div className="waypoint-robot-picker__heading">
          <strong>작업 로봇 선택</strong>
          <span>한 번에 핑키 한 대만 조작할 수 있습니다.</span>
        </div>
        <div className="waypoint-robot-cards">
          {robotList.map((robot) => {
            const selected = robot.robot_id === selectedRobotId
            const linkLabel = robot.link_state === 'online'
              ? '온라인' : robot.link_state === 'offline' ? '오프라인' : '연결 이력 없음'
            return (
              <button
                key={robot.robot_id}
                type="button"
                className={`waypoint-robot-card${selected ? ' selected' : ''}`}
                aria-pressed={selected}
                onClick={() => setSelectedRobotId(robot.robot_id)}
              >
                <PinkyModel />
                <span className="waypoint-robot-card__content">
                  <span className="waypoint-robot-card__top">
                    <strong>{robot.display_name}</strong>
                    <span className={`waypoint-robot-card__link waypoint-robot-card__link--${robot.link_state}`}>
                      {linkLabel}
                    </span>
                  </span>
                  <span className="waypoint-robot-card__meta">
                    <code>{robot.robot_id}</code>
                    <span>{robot.battery_percent == null ? '배터리 정보 없음' : `배터리 ${robot.battery_percent}%`}</span>
                    {robot.active_session_id != null && <span>환자 안내 중</span>}
                  </span>
                </span>
              </button>
            )
          })}
          {!robots.loading && robotList.length === 0 && (
            <p className="waypoint-robot-picker__empty">등록된 핑키가 없습니다.</p>
          )}
        </div>
      </section>

      {activeSession && (
        <div className="waypoint-safety-banner" role="alert">
          이 로봇은 환자 안내 중입니다. 조회와 편집만 가능하며 조작·시험 주행은 차단됩니다.
        </div>
      )}
      {testAlert && (
        <div className="waypoint-safety-banner" role="alert">{testAlert}</div>
      )}
      {notice && <div className="waypoint-notice" role="status">{notice}</div>}

      <div className="waypoint-workspace">
        <section className="waypoint-map-panel">
          <header className="waypoint-map-panel__header">
            <div>
              <span className="waypoint-page-header__eyebrow">LIVE MAP</span>
              <strong>{selectedRobot?.display_name ?? '작업 로봇을 선택하세요'}</strong>
            </div>
            <span className={`control-deck__connection${teleop.robotConnected ? ' is-online' : ' is-offline'}`}>
              <i aria-hidden="true" />
              {teleop.robotConnected ? '실시간 연결' : '연결 끊김'}
            </span>
          </header>
          <LazyHospitalMap3D
            pose={teleop.pose}
            live={teleop.robotConnected}
            scan={teleop.scan}
            particles={teleop.particles}
            plan={teleop.plan}
            recoveryPlan={teleop.recoveryPlan}
            waypoints={markers}
            onSelectWaypoint={setSelectedName}
            robotId={selectedRobotId}
            returning={selectedRobot?.returning_to_dock ?? false}
            camera={null}
          />
        </section>

        <aside className="waypoint-command-rail" aria-label="Waypoint 작업 제어">
          <header className="waypoint-command-rail__header">
            <span className="waypoint-page-header__eyebrow">OPERATOR RAIL</span>
            <strong>주행 및 좌표 도구</strong>
          </header>
          <section className="waypoint-control-column">
            {selectedRobotId && (
              <RobotModeControl
                robotId={selectedRobotId}
                mode={mode}
                appliedMode={teleop.appliedMode}
                modeStatusRevision={teleop.modeStatusRevision}
                robotConnected={teleop.robotConnected}
              />
            )}
            <section className="card waypoint-localize-control">
              <div className="card-title">로봇 위치 다시 찾기</div>
              <strong className={selectedRobot?.localization_active ? 'waypoint-localize-running' : ''}>
                {selectedRobot?.localization_active
                  ? '위치를 찾는 중입니다'
                  : '지도와 실제 위치가 다를 때 실행하세요'}
              </strong>
              <p>로봇이 주변을 확인하며 제자리에서 돌거나 앞뒤로 잠시 움직일 수 있습니다.</p>
              <button
                type="button"
                className="btn"
                disabled={busy || !localizationEnabled}
                onClick={findRobotLocation}
              >
                위치 다시 찾기
              </button>
              {activeSession && (
                <p className="waypoint-localize-disabled">환자 안내 중에는 위치를 다시 찾을 수 없습니다.</p>
              )}
            </section>
            <TeleopPad
              drive={drive}
              enabled={teleopEnabled}
              disabledReason={activeSession
                ? '환자 안내 중에는 수동 조작할 수 없습니다.'
                : !teleop.robotConnected
                  ? '로봇이 관제에 연결되어 있지 않습니다.'
                  : teleop.appliedMode === null
                    ? '로봇 제어기의 모드 적용 상태를 확인하는 중입니다.'
                  : mode !== teleop.appliedMode
                    ? '요청 모드와 실제 적용 모드가 일치하지 않습니다.'
                  : teleop.appliedMode === 'estop'
                    ? '비상정지가 걸려 있습니다.'
                    : '수동 조작 모드로 전환하세요.'}
            />
          </section>
          <section className="waypoint-editor card">
          <div className="card-title">좌표 초안</div>
          <div className="waypoint-add-row">
            <input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="새 waypoint 이름" />
            <button type="button" className="btn" onClick={addWaypoint}>추가</button>
          </div>
          <label>
            Waypoint
            <select value={selectedName} onChange={(event) => setSelectedName(event.target.value)}>
              {Object.keys(drafts).map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <div className="waypoint-coordinate-grid">
            {(['x', 'y', 'yaw'] as const).map((field) => (
              <label key={field}>
                {field}
                <input type="number" step="0.001" value={selected[field]} onChange={(event) => updateSelected(field, Number(event.target.value))} />
              </label>
            ))}
          </div>
          <button type="button" className="btn primary waypoint-capture" disabled={!captureEnabled} onClick={capturePose}>
            현재 위치를 Waypoint로 저장
          </button>
          {!captureEnabled && (
            <p className="waypoint-help">수동 모드에서 로봇을 멈춘 뒤 약 {SETTLE_MS / 1000}초 기다리세요.</p>
          )}
          {selectedCheck && (
            <div className={`waypoint-check-item waypoint-check-item--${selectedCheck.status}`}>
              <strong>{selectedCheck.status.toUpperCase()}</strong>
              <span>{selectedCheck.message}</span>
              {selectedCheck.clearance != null && <span>차체-벽 여유 {selectedCheck.clearance.toFixed(3)}m</span>}
            </div>
          )}
          <div className="waypoint-editor-actions">
            <button type="button" className="btn" disabled={busy} onClick={runCheck}>Waypoint Check</button>
            <button type="button" className="btn primary" disabled={busy || !testEnabled} onClick={testDrive}>선택 지점 시험 주행</button>
            <button type="button" className="btn danger"
              disabled={busy || !selectedRobotId || !teleop.robotConnected
                || selectedRobot?.system_state !== 'active'}
              onClick={cancelTestDrive}>시험 주행 취소</button>
            <button type="button" className="btn" disabled={!source} onClick={exportYaml}>YAML 내보내기</button>
          </div>
          {checkResult && checkResult.conflicts.length > 0 && (
            <div className="waypoint-conflicts">
              <strong>간격 경고 {checkResult.conflicts.length}건</strong>
              {checkResult.conflicts.slice(0, 5).map((item) => (
                <span key={`${item.first}-${item.second}`}>{item.first} ↔ {item.second} · {item.distance.toFixed(3)}m</span>
              ))}
            </div>
          )}
          </section>
        </aside>
      </div>
    </div>
  )
}
