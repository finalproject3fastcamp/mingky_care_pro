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

interface Props {
  robotId: string
  /** 이벤트에서 파생한 실제 모드. 아직 모르면 null. */
  mode: RobotMode | null
  /** 명령 실패를 의료진에게 알릴 때 쓴다. */
  onError?: (message: string) => void
}

export function RobotModeControl({ robotId, mode, onError }: Props) {
  const [requested, setRequested] = useState<RobotMode | null>(null)
  const [sending, setSending] = useState(false)

  // 로봇이 실제로 그 모드가 되면 "요청함" 표시를 지운다.
  useEffect(() => {
    if (requested !== null && mode === requested) setRequested(null)
  }, [mode, requested])

  // 로봇을 바꾸면 이전 로봇에 건 요청 표시가 남으면 안 된다.
  useEffect(() => {
    setRequested(null)
  }, [robotId])

  async function request(next: RobotMode) {
    setSending(true)
    try {
      await sendOrder(robotId, 'set_mode', next)
      setRequested(next)
    } catch {
      onError?.(`${MODE_LABEL[next]} 전환 요청을 보내지 못했습니다.`)
    } finally {
      setSending(false)
    }
  }

  const estopped = mode === 'estop'

  return (
    <section className="mode-control">
      <header className="mode-control__header">
        <span className="mode-control__label">주행 모드</span>
        <strong className={`mode-control__value mode-control__value--${mode ?? 'unknown'}`}>
          {mode ? MODE_LABEL[mode] : '확인 중'}
        </strong>
      </header>

      {requested !== null && (
        <p className="mode-control__pending" role="status">
          {MODE_LABEL[requested]}(으)로 전환 요청함 — 로봇 응답을 기다리는 중입니다.
        </p>
      )}

      <div className="mode-control__buttons">
        <button
          type="button"
          className="btn"
          disabled={sending || estopped || mode === 'auto'}
          onClick={() => request('auto')}
        >
          자동 주행
        </button>
        <button
          type="button"
          className="btn"
          disabled={sending || estopped || mode === 'manual'}
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
