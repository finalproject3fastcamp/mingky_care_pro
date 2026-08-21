/**
 * teleop operator 소켓에 **뷰어로만** 붙어 로봇 pose 만 읽는다.
 *
 * useTeleopSocket 은 조작(cmd_vel·set_pose)까지 물고 있고 actor 를 실어
 * 감사 로그에 조작자로 남는다. 플릿 개관 씬은 조작하지 않으므로, 여기서는
 * pose 만 파싱하고 아무것도 보내지 않으며 actor 도 붙이지 않는다 — 뷰어가
 * 조작자로 오인돼 감사 로그를 오염시키지 않게.
 *
 * operator 엔드포인트는 로봇당 여러 읽기 연결을 허용한다(backend
 * routers/teleop.py, `_operators: dict[str, set]`). 그래서 대시보드가 이미
 * 그 로봇에 붙어 있어도 이 뷰어가 나란히 붙어 pose 를 받는다.
 */

import { useEffect, useState } from 'react'

import type { RobotPose } from './useTeleopSocket'

/** 소켓 URL. useTeleopSocket 의 규칙과 같되 actor 쿼리는 붙이지 않는다. */
function operatorUrl(robotId: string): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api'
  const absolute = base.startsWith('http')
    ? base
    : `${window.location.origin}${base}`
  return `${absolute.replace(/^http/, 'ws')}/robots/${robotId}/teleop/operator`
}

export function usePinkyPose(robotId: string): { pose: RobotPose | null; connected: boolean } {
  const [pose, setPose] = useState<RobotPose | null>(null)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    let closed = false
    let retry: ReturnType<typeof setTimeout> | undefined
    let socket: WebSocket | null = null

    function open() {
      if (closed) return
      socket = new WebSocket(operatorUrl(robotId))
      socket.onopen = () => setConnected(true)
      socket.onmessage = (event) => {
        let message: Record<string, unknown>
        try {
          message = JSON.parse(event.data as string)
        } catch {
          return
        }
        // pose 만 쓴다. status·mode·scan·particles·plan 은 이 씬에 불필요하다.
        if (message.type === 'pose') {
          setPose({
            x: Number(message.x),
            y: Number(message.y),
            yaw: Number(message.yaw),
          })
        }
      }
      socket.onclose = () => {
        setConnected(false)
        // pose 는 지우지 않는다 — 끊겨도 마지막 위치를 씬에 유지한다.
        // 재연결마다 attach/detach 감사 행이 남으므로 백오프를 넉넉히 둔다.
        if (!closed) retry = setTimeout(open, 5000)
      }
    }

    open()
    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      socket?.close()
      socket = null
    }
  }, [robotId])

  return { pose, connected }
}
