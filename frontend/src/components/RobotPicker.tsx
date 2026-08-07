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

interface Candidate {
  robot: Robot
  eligible: boolean
  /** 이미 활성화된 로봇으로 되돌아가는 선택. arm 이 idempotent 라 그대로 재진입한다. */
  resume?: boolean
  /** eligible=false 인 이유. 사용자에게 왜 못 고르는지 알린다. */
  reason?: string
}

function categorize(robot: Robot): Candidate {
  if (!robot.is_active) return { robot, eligible: false, reason: '비활성' }
  if (robot.robot_type !== 'mobile') return { robot, eligible: false, reason: '주행 로봇 아님' }
  if (robot.active_session_id != null) {
    return { robot, eligible: false, reason: '안내 중' }
  }
  // 이미 활성화된 로봇도 고를 수 있어야 한다. 탭을 닫았다 열거나 다른 자리에서
  // 이어받는 경우, 막아두면 취소할 방법조차 없이 잠긴다. arm 은 idempotent 라
  // 재요청해도 armed_at 과 이벤트가 새로 생기지 않는다.
  if (robot.armed_at != null) {
    return { robot, eligible: true, resume: true }
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
  return { robot, eligible: true }
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

  async function handleArm(robotId: string) {
    setPending(robotId)
    setError(null)
    try {
      const armed = await armRobot(robotId)
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
        안내를 시작할 핑키를 선택하세요. 선택한 로봇의 QR 스캔이 켜집니다.
      </p>
      {error && <p className="picker-error">{error}</p>}
      {candidates.length === 0 ? (
        <p className="empty">등록된 주행 로봇이 없습니다.</p>
      ) : (
        <ul className="robot-grid">
          {candidates.map(({ robot, eligible, resume, reason }) => {
            const isPending = pending === robot.robot_id
            const clickable = eligible && !isPending
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
                  onClick={() => handleArm(robot.robot_id)}
                  aria-label={`${robot.display_name} 선택`}
                >
                  <div className="robot-card-name">{robot.display_name}</div>
                  <div className="robot-card-id mono">{robot.robot_id}</div>
                  <div className="robot-card-battery">
                    <span className="robot-card-battery-value">
                      {robot.battery_percent != null
                        ? `${robot.battery_percent}%`
                        : '—'}
                    </span>
                    <span className="robot-card-battery-label">배터리</span>
                  </div>
                  {!eligible && reason && (
                    <div className="robot-card-reason">{reason}</div>
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
