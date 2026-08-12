import { useState } from 'react'

import { armRobot } from '../lib/api'
import type { Robot } from '../types/monitoring'

interface Props {
  robots: Robot[]
  /**
   * 활성화 API 가 성공한 뒤 대시보드가 어떤 로봇을 골랐는지 알아야 한다.
   * 응답 객체를 그대로 넘긴다 — 대시보드의 로봇 목록은 3초 폴링이라
   * 아직 armed_at 이 null 인 낡은 상태다. 그 낡은 상태로 판단하면 방금
   * 고른 선택이 무효로 취급돼 선택 화면으로 튕긴다.
   */
  onArmed?: (robot: Robot) => void
}

// 의료진이 로봇을 고를 때 최소로 요구하는 배터리 잔량.
// 백엔드 MIN_BATTERY_PERCENT 와 같은 값이어야 한다. 프론트에서 미리 걸러
// 버튼 자체를 비활성화하고, 백엔드가 마지막 안전망이다.
const MIN_BATTERY_PERCENT = 40
const MAX_BATTERY_AGE_MS = 5 * 60 * 1000
const RECENT_CANCELLATION_MS = 10 * 60 * 1000

interface Candidate {
  robot: Robot
  eligible: boolean
  /** 이미 활성화됐거나 안내 중인 로봇으로 되돌아가는 선택. 새로 arm 하는 게 아니다. */
  resume?: boolean
  /** resume 카드에 붙는 현재 상태 라벨. */
  status?: string
  /** eligible=false 인 이유. 사용자에게 왜 못 고르는지 알린다. */
  reason?: string
}

function categorize(robot: Robot): Candidate {
  if (!robot.is_active) return { robot, eligible: false, reason: '비활성' }
  if (robot.robot_type !== 'mobile') return { robot, eligible: false, reason: '주행 로봇 아님' }
  // 통신이 확인되지 않은 로봇은 기존 세션/arming 표시와 무관하게 선택하지
  // 못하게 한다. 상태 라벨은 카드 상단에서 별도로 항상 보여준다.
  if (robot.link_state === 'offline') {
    return {
      robot,
      eligible: false,
      reason: robot.active_session_id != null
        ? '통신 두절 · 안내 취소 대기'
        : '통신 두절',
    }
  }
  if (robot.link_state === 'unknown') {
    return { robot, eligible: false, reason: '연결 이력 없음' }
  }
  if (robot.system_state === 'inactive' || robot.system_state === 'failed') {
    return {
      robot,
      eligible: false,
      reason: robot.active_session_id != null
        ? '시스템 장애 · 안내 취소 대기'
        : '안내 시스템 중지',
    }
  }
  // 이미 활성화됐거나 안내 중인 로봇도 고를 수 있어야 한다. 막아두면 한 번
  // 나온 뒤에는 되돌아갈 방법이 없어 취소조차 못 한다. 이 화면은 "새로 켜는
  // 곳" 이 아니라 "담당할 로봇을 고르는 곳" 이다 — 여러 대를 번갈아 본다.
  if (robot.active_session_id != null) {
    return { robot, eligible: true, resume: true, status: '안내 중' }
  }
  if (robot.armed_at != null) {
    return { robot, eligible: true, resume: true, status: 'QR 스캔 대기' }
  }
  if (robot.battery_percent == null) {
    return { robot, eligible: false, reason: '배터리 정보 없음' }
  }
  if (robot.battery_percent < MIN_BATTERY_PERCENT) {
    return {
      robot,
      eligible: false,
      reason: `배터리 ${robot.battery_percent}% (${MIN_BATTERY_PERCENT}% 미만)`,
    }
  }
  if (
    robot.battery_recorded_at == null ||
    Date.now() - new Date(robot.battery_recorded_at).getTime() > MAX_BATTERY_AGE_MS
  ) {
    return { robot, eligible: false, reason: '배터리 정보가 오래됨' }
  }
  return { robot, eligible: true }
}

function linkLabel(robot: Robot) {
  if (robot.link_state === 'online') return '온라인'
  if (robot.link_state === 'offline') return '오프라인'
  return '연결 이력 없음'
}

function cancellationLabel(robot: Robot) {
  if (robot.active_session_id != null || robot.armed_at != null) return null
  if (
    robot.last_session_ended_at == null ||
    Date.now() - new Date(robot.last_session_ended_at).getTime() > RECENT_CANCELLATION_MS
  ) return null
  if (robot.last_session_end_reason === 'robot_offline') {
    return '최근 안내 취소 · 통신 두절'
  }
  if (robot.last_session_end_reason === 'system_failure') {
    return '최근 안내 취소 · 시스템 장애'
  }
  return null
}

export function RobotPicker({ robots, onArmed }: Props) {
  // 클릭한 로봇의 API 진행 상태. 여러 로봇을 동시에 눌러도 각각 표시하려면
  // 로봇별 상태가 필요하지만, 실사용에선 순차 클릭이라 단일 값으로 충분하다.
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 주행 로봇만 후보에 올린다. 조제 스테이션(omx-*)은 목록에서 아예 뺀다.
  const candidates = robots
    .filter((r) => r.robot_type === 'mobile')
    .map(categorize)

  async function handlePick(robot: Robot) {
    // 안내 중인 로봇은 arm 을 다시 부르면 안 된다 — 백엔드가 409 busy 로
    // 막는다(정당한 방어다). 이 경우는 이미 켜져 있는 걸 열어보는 것뿐이라
    // API 없이 선택만 넘긴다.
    if (robot.active_session_id != null) {
      onArmed?.(robot)
      return
    }
    setPending(robot.robot_id)
    setError(null)
    try {
      const armed = await armRobot(robot.robot_id)
      onArmed?.(armed)
    } catch (err) {
      const message = err instanceof Error ? err.message : '활성화 실패'
      setError(message)
    } finally {
      setPending(null)
    }
  }

  return (
    <div className="card robot-picker">
      <div className="card-title">로봇 선택</div>
      <p className="picker-hint">
        담당할 핑키를 선택하세요. 대기 중인 로봇은 선택하면 QR 스캔이 켜지고,
        이미 활성화됐거나 안내 중인 로봇은 그 화면으로 바로 들어갑니다.
      </p>
      {error && <p className="picker-error">{error}</p>}
      {candidates.length === 0 ? (
        <p className="empty">등록된 주행 로봇이 없습니다.</p>
      ) : (
        <ul className="robot-grid">
          {candidates.map(({ robot, eligible, resume, status, reason }) => {
            const isPending = pending === robot.robot_id
            const clickable = eligible && !isPending
            const canceled = cancellationLabel(robot)
            // 버튼 대신 카드 전체를 <button> 으로 만든다. 표시는 카드지만
            // 시맨틱은 버튼이라 스페이스·엔터·포커스링·스크린리더 라벨이 공짜다.
            return (
              <li key={robot.robot_id}>
                <button
                  type="button"
                  className={`robot-card${eligible ? '' : ' disabled'}${
                    isPending ? ' pending' : ''
                  }`}
                  disabled={!clickable}
                  onClick={() => handlePick(robot)}
                  aria-label={`${robot.display_name} 선택`}
                >
                  <div className="robot-card-heading">
                    <div className="robot-card-name">{robot.display_name}</div>
                    <div className={`robot-card-link robot-card-link--${robot.link_state}`}>
                      {linkLabel(robot)}
                    </div>
                  </div>
                  <div className="robot-card-id mono">{robot.robot_id}</div>
                  <div className="robot-card-battery">
                    <span className="robot-card-battery-value">
                      {robot.battery_percent != null
                        ? `${robot.battery_percent}%`
                        : '—'}
                    </span>
                    <span className="robot-card-battery-label">배터리</span>
                  </div>
                  {(reason || status) && (
                    <div className={`robot-card-reason${status ? ' active' : ''}`}>
                      {reason ?? status}
                    </div>
                  )}
                  {canceled && (
                    <div className="robot-card-cancellation">{canceled}</div>
                  )}
                  <div className="robot-card-cta">
                    {isPending
                      ? '활성화 중…'
                      : resume
                        ? '이어서 사용'
                        : eligible
                          ? '선택'
                          : ''}
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
