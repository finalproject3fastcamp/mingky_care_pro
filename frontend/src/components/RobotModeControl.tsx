/**
 * 주행 모드 표시와 전환, 그리고 비상정지.
 *
 * 모드의 정본은 로봇이 갖는다. 여기서 누르는 것은 **요청**이고, 화면의 상태는
 * 로봇이 돌려보낸 robot.mode_changed 이벤트로만 바뀐다. 누른 즉시 바뀐 것처럼
 * 보이게 하면, 로봇이 못 받은 경우에도 의료진은 바뀐 줄 안다.
 *
 * 그래서 누른 뒤에는 "요청함" 을 따로 표시하고, 실제 모드가 따라오면 지운다.
 * 폴링 주기(3초) + 로봇 폴링 주기(3초) 때문에 최대 몇 초 걸린다.
 *
 * 비상정지를 따로 크게 둔 이유는, 급할 때 찾는 버튼이 다른 것과 같은 크기로
 * 섞여 있으면 안 되기 때문이다. 걸어 잠기므로 해제도 명시적으로 눌러야 한다.
 */

import { useEffect, useState } from 'react'

import { sendOrder, type RobotMode } from '../lib/api'

const MODE_LABEL: Record<RobotMode, string> = {
  auto: '자동 주행',
  manual: '수동 조작',
  estop: '비상정지',
}

const MODE_CONFIRM_TIMEOUT_MS = 8000

interface Props {
  robotId: string
  /** 이벤트에서 파생한 실제 모드. 아직 모르면 null. */
  mode: RobotMode | null
  /** teleop_limiter 가 최근 상태 메시지로 확인해 준 실제 적용 모드. */
  appliedMode: RobotMode | null
  modeStatusRevision: number
  /** 로봇 브리지 연결 여부. 끊겨 있으면 조작·정지가 닿지 않는다. */
  robotConnected?: boolean
}

export function RobotModeControl({
  robotId, mode, appliedMode, modeStatusRevision, robotConnected,
}: Props) {
  // 요청이 실패하면 화면에 남긴다. 특히 비상정지는 실패를 모르면
  // 눌렀으니 섰겠거니 하고 다음 행동을 한다.
  const [error, setError] = useState<string | null>(null)
  const [requested, setRequested] = useState<{
    mode: RobotMode
    afterRevision: number
  } | null>(null)
  const [sending, setSending] = useState(false)

  // 로봇이 실제로 그 모드가 되면 "요청함" 표시를 지운다.
  useEffect(() => {
    if (
      requested !== null
      && appliedMode === requested.mode
      && modeStatusRevision > requested.afterRevision
    ) {
      setRequested(null)
    }
  }, [appliedMode, modeStatusRevision, requested])

  useEffect(() => {
    if (requested === null) return
    const timer = setTimeout(() => {
      setRequested(null)
      setError(
        `${MODE_LABEL[requested.mode]} 모드가 로봇 제어기에 적용되지 않았습니다.`,
      )
    }, MODE_CONFIRM_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [requested])

  // 로봇을 바꾸면 이전 로봇에 건 요청 표시가 남으면 안 된다.
  useEffect(() => {
    setRequested(null)
    setError(null)
  }, [robotId])

  async function request(next: RobotMode) {
    setSending(true)
    setError(null)
    try {
      await sendOrder(robotId, 'set_mode', next)
      setRequested({ mode: next, afterRevision: modeStatusRevision })
    } catch {
      setRequested(null)
      setError(
        next === 'estop'
          ? '비상정지 요청을 보내지 못했습니다. 로봇이 계속 움직일 수 있습니다.'
          : `${MODE_LABEL[next]} 전환 요청을 보내지 못했습니다.`,
      )
    } finally {
      setSending(false)
    }
  }

  const estopped = mode === 'estop' || appliedMode === 'estop'

  return (
    <section className="mode-control">
      <header className="mode-control__header">
        <span className="mode-control__label">주행 모드</span>
        <strong className={`mode-control__value mode-control__value--${appliedMode ?? 'unknown'}`}>
          {appliedMode ? MODE_LABEL[appliedMode] : '적용 확인 중'}
        </strong>
      </header>

      {error !== null && (
        <p className="mode-control__error" role="alert">
          {error}
        </p>
      )}

      {robotConnected === false && (
        <p className="mode-control__offline" role="status">
          로봇이 관제에 연결되어 있지 않습니다. 전환 요청이 전달되지 않을 수
          있습니다.
        </p>
      )}

      {requested !== null && (
        <p className="mode-control__pending" role="status">
          {MODE_LABEL[requested.mode]}(으)로 전환 요청함 — 실제 적용을 확인하는 중입니다.
        </p>
      )}

      {robotConnected && appliedMode === null && (
        <p className="mode-control__error" role="alert">
          로봇 제어기의 모드 상태를 확인할 수 없어 조작을 잠갔습니다.
        </p>
      )}

      {robotConnected && mode !== null && appliedMode !== null && mode !== appliedMode && (
        <p className="mode-control__error" role="alert">
          모드가 일치하지 않아 조작을 잠갔습니다
          {' '}(요청 {MODE_LABEL[mode]} / 적용 {MODE_LABEL[appliedMode]}).
        </p>
      )}

      <div className="mode-control__buttons">
        <button
          type="button"
          className="btn"
          disabled={sending || estopped || (mode === 'auto' && appliedMode === 'auto')}
          onClick={() => request('auto')}
        >
          자동 주행
        </button>
        <button
          type="button"
          className="btn"
          disabled={sending || estopped || (mode === 'manual' && appliedMode === 'manual')}
          onClick={() => request('manual')}
        >
          수동 조작
        </button>
      </div>

      {estopped ? (
        <button
          type="button"
          className="btn btn--estop-release"
          disabled={sending}
          onClick={() => request('auto')}
        >
          비상정지 해제
        </button>
      ) : (
        <button
          type="button"
          className="btn btn--estop"
          disabled={sending}
          onClick={() => request('estop')}
        >
          비상정지
        </button>
      )}

      {estopped && (
        <p className="mode-control__note">
          정지 상태가 걸려 있습니다. 해제하기 전에는 자동 주행도 수동 조작도
          움직이지 않습니다.
        </p>
      )}
    </section>
  )
}
