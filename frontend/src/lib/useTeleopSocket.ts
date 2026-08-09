/**
 * 실시간 조작 소켓. 명령을 보내고 로봇 위치를 받는다.
 *
 * orders(HTTP 폴링) 를 쓰지 않는 이유는 routers/teleop.py 에 적혀 있다 —
 * 누른 뒤 최대 3초 뒤에 움직이고, 손을 뗀 것과 명령이 없는 것을 구분할 수 없다.
 *
 * ## 끊기면
 *
 * **아무것도 하지 않는다.** 명령이 끊기면 로봇의 twist_mux timeout 과
 * pinky_bringup 워치독이 1초 안에 모터를 세운다. 여기서 정지 명령을 보내려
 * 해도 이미 끊긴 소켓으로는 못 보낸다. 정지를 연결에 의존시키지 않는다.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export interface RobotPose {
  x: number
  y: number
  yaw: number
}

/** 로컬라이제이션 진단 레이어. docs/nav2-debugging.md 가 보라는 것들이다. */
export interface DiagLayers {
  /** 라이다. [각도(rad), 거리(m)] — 로봇 기준 극좌표라 pose 로 회전시켜 그린다. */
  scan: number[][] | null
  /** AMCL 파티클. 넓게 퍼지면 발산이다. */
  particles: number[][] | null
  /** 전역 경로. 로봇이 어디로 가려는지. */
  plan: number[][] | null
}

interface TeleopState extends DiagLayers {
  /** 이 브라우저와 서버 사이. 로봇까지 닿는지는 robotConnected 가 말한다. */
  connected: boolean
  /** 서버가 그 로봇의 브리지를 잡고 있는지. 없으면 눌러도 아무 일이 없다. */
  robotConnected: boolean
  pose: RobotPose | null
}

function socketUrl(robotId: string): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api'
  // 상대 경로면 지금 보고 있는 호스트를 그대로 쓴다. 배포에서는 nginx 가
  // 같은 오리진으로 프록시하므로 주소를 따로 둘 이유가 없다.
  const absolute = base.startsWith('http')
    ? base
    : `${window.location.origin}${base}`
  return `${absolute.replace(/^http/, 'ws')}/robots/${robotId}/teleop/operator`
}

export function useTeleopSocket(robotId: string | null): TeleopState & {
  drive: (linear: number, angular: number) => void
  setPose: (x: number, y: number, yaw: number) => void
} {
  const [state, setState] = useState<TeleopState>({
    connected: false,
    robotConnected: false,
    pose: null,
    scan: null,
    particles: null,
    plan: null,
  })
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!robotId) return

    let closed = false
    let retry: ReturnType<typeof setTimeout> | undefined

    function open() {
      if (closed || !robotId) return
      const socket = new WebSocket(socketUrl(robotId))
      socketRef.current = socket

      socket.onopen = () => setState((s) => ({ ...s, connected: true }))

      socket.onmessage = (event) => {
        let message: Record<string, unknown>
        try {
          message = JSON.parse(event.data as string)
        } catch {
          return
        }
        if (message.type === 'pose') {
          setState((s) => ({
            ...s,
            pose: {
              x: Number(message.x),
              y: Number(message.y),
              yaw: Number(message.yaw),
            },
          }))
        } else if (message.type === 'status') {
          setState((s) => ({ ...s, robotConnected: Boolean(message.robot_connected) }))
        } else if (
          message.type === 'scan' ||
          message.type === 'particles' ||
          message.type === 'plan'
        ) {
          const key = message.type === 'scan' ? 'scan'
            : message.type === 'particles' ? 'particles' : 'plan'
          setState((s) => ({ ...s, [key]: message.points as number[][] }))
        }
      }

      socket.onclose = () => {
        // 끊기면 화면에 남아 있는 레이어는 과거값이다. 지우지 않으면 낡은
        // 라이다가 지금 상태처럼 보인다.
        setState((s) => ({
          ...s, connected: false, robotConnected: false,
          scan: null, particles: null, plan: null,
        }))
        // 회선이 흔들리는 환경이라 끊기는 것을 정상으로 보고 다시 건다.
        if (!closed) retry = setTimeout(open, 3000)
      }
    }

    open()
    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [robotId])

  const drive = useCallback((linear: number, angular: number) => {
    const socket = socketRef.current
    if (socket?.readyState !== WebSocket.OPEN) return
    socket.send(JSON.stringify({ type: 'cmd_vel', linear, angular }))
  }, [])

  /**
   * 로봇에게 "너 지금 여기 있다" 를 알린다. RViz 의 2D Pose Estimate 와 같다.
   *
   * AMCL 은 부팅할 때 (0,0,0) 에서 시작한다. 로봇이 실제로 거기 있지 않으면
   * **틀린 위치를 확신한 채** 출발한다. 지금까지는 RViz 를 띄울 수 있는
   * 사람만 고칠 수 있었다.
   */
  const setPose = useCallback((x: number, y: number, yaw: number) => {
    const socket = socketRef.current
    if (socket?.readyState !== WebSocket.OPEN) return
    socket.send(JSON.stringify({ type: 'set_pose', x, y, yaw }))
  }, [])

  return { ...state, drive, setPose }
}
