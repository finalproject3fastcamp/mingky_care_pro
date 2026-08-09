/**
 * 누르고 있는 동안만 로봇이 움직이는 조작 패드.
 *
 * ## 왜 토글이 아니라 누르고 있어야 하나
 *
 * 토글이면 "켜 둔 것을 잊은" 상태가 만들어진다. 누르고 있는 동안만 명령이
 * 나가면, 손을 떼는 순간 — 브라우저를 닫든 인터넷이 끊기든 — 로봇이 선다.
 * 조작자가 무엇을 하지 않아도 멈추는 쪽이 안전하다.
 *
 * ## 왜 반복해서 보내나
 *
 * 로봇의 twist_mux 는 1초 동안 조용한 입력을 후보에서 뺀다. 한 번만 보내면
 * 1초 뒤 Nav2 로 제어권이 넘어간다. 누르고 있는 동안 10Hz 로 계속 보낸다.
 *
 * ## 손을 뗄 때
 *
 * 0 속도를 한 번 보낸다. 안 보내도 1초 뒤 워치독이 세우지만, 그 1초 동안
 * 계속 가는 것이 조작자에게는 "안 멈춘다" 로 보인다. 다만 이 신호에
 * **의존하지는 않는다** — 연결이 이미 끊긴 상황에서는 나가지 않는다.
 */

import { useCallback, useEffect, useRef } from 'react'

const SEND_HZ = 10
// 로봇에서 어차피 0.15 m/s · 0.6 rad/s 로 잘린다(teleop_limiter).
// 여기 값은 "그보다 느리게 몰고 싶다" 는 뜻이다.
const LINEAR = 0.12
const ANGULAR = 0.5

interface Props {
  drive: (linear: number, angular: number) => void
  /** manual 모드가 아니면 로봇이 무시한다. 눌러도 소용없음을 화면에서 막는다. */
  enabled: boolean
  disabledReason?: string
}

export function TeleopPad({ drive, enabled, disabledReason }: Props) {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stop = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current)
      timerRef.current = null
      drive(0, 0)
    }
  }, [drive])

  const start = useCallback(
    (linear: number, angular: number) => {
      if (!enabled) return
      stop()
      drive(linear, angular)
      timerRef.current = setInterval(() => drive(linear, angular), 1000 / SEND_HZ)
    },
    [drive, enabled, stop],
  )

  // 버튼 위에서 손을 떼지 않고 탭을 벗어나는 경우까지 잡는다.
  useEffect(() => {
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    window.addEventListener('blur', stop)
    return () => {
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
      window.removeEventListener('blur', stop)
      stop()
    }
  }, [stop])

  function hold(linear: number, angular: number) {
    return {
      onPointerDown: () => start(linear, angular),
      onPointerUp: stop,
      onPointerLeave: stop,
      disabled: !enabled,
      type: 'button' as const,
      className: 'teleop-pad__btn',
    }
  }

  return (
    <section className="teleop-pad">
      <header className="teleop-pad__header">
        <span className="teleop-pad__label">수동 조작</span>
        <span className="teleop-pad__hint">누르고 있는 동안만 움직입니다</span>
      </header>

      <div className="teleop-pad__grid">
        <button {...hold(LINEAR, 0)} style={{ gridArea: 'up' }}>↑</button>
        <button {...hold(0, ANGULAR)} style={{ gridArea: 'left' }}>↰</button>
        <button
          type="button"
          className="teleop-pad__btn teleop-pad__btn--stop"
          style={{ gridArea: 'stop' }}
          disabled={!enabled}
          onClick={() => drive(0, 0)}
        >
          ■
        </button>
        <button {...hold(0, -ANGULAR)} style={{ gridArea: 'right' }}>↱</button>
        <button {...hold(-LINEAR, 0)} style={{ gridArea: 'down' }}>↓</button>
      </div>

      {!enabled && disabledReason && (
        <p className="teleop-pad__disabled">{disabledReason}</p>
      )}
    </section>
  )
}
