/**
 * 전체 로봇 위치를 받는 읽기 전용 소켓.
 *
 * `useTeleopSocket` 과 나란히 두되 **보내는 것이 하나도 없다.** 반환값에
 * `drive` 도 `setPose` 도 없는 것이 이 훅의 계약이다.
 *
 * ## 왜 조작 소켓을 한 벌 더 열지 않는가
 *
 * 조작자 소켓은 붙는 순간 서버가 `control_audit` 에 `teleop_attach` 를
 * 남기고, 그 행동은 SLO 판정에서 **개입**이다(§1.1 의 "teleop 없음").
 * 두 번째 로봇을 보려고 조작 소켓을 하나 더 열면 보기만 했는데 그 로봇의
 * 안내 세션이 실패로 집계된다. 관측과 조작은 다른 권한이라 채널이 다르다
 * (backend/app/fleet_pose.py).
 *
 * ## 끊기면
 *
 * 지도에서 로봇을 **지운다.** 남겨두면 마지막으로 본 자리가 현재 위치처럼
 * 보인다. `useTeleopSocket` 이 진단 레이어를 지우는 것과 같은 판단이다 —
 * 값이 없는 화면은 사람이 기다리지만, 틀린 값이 있는 화면은 사람이
 * 판단해버린다.
 *
 * 서버가 붙는 즉시 스냅샷을 한 벌 보내므로 다시 연결되면 곧바로 복구된다.
 */

import { useEffect, useState } from 'react'

import type { FleetPose } from '../types/monitoring'

/** 회선이 흔들리는 환경이라 끊기는 것을 정상으로 보고 다시 건다. */
const RETRY_MS = 3000

export interface FleetPoseState {
  /** robot_id → 마지막 위치. 한 번도 안 올린 로봇은 여기 없다. */
  poses: Record<string, FleetPose>
  /** 이 브라우저와 관제 서버 사이. 로봇까지 닿는지는 각 pose 의 나이가 말한다. */
  connected: boolean
}

function socketUrl(): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api'
  // 상대 경로면 지금 보고 있는 호스트를 그대로 쓴다. 배포에서는 nginx 가
  // 같은 오리진으로 프록시한다 (useTeleopSocket 과 같은 규칙).
  const absolute = base.startsWith('http')
    ? base
    : `${window.location.origin}${base}`
  // actor 쿼리가 없다. 관측은 감사 대상이 아니고, 서버도 여기서는 읽지 않는다.
  return `${absolute.replace(/^http/, 'ws')}/fleet/poses/stream`
}

export function useFleetPoses(enabled = true): FleetPoseState {
  const [state, setState] = useState<FleetPoseState>({
    poses: {},
    connected: false,
  })

  useEffect(() => {
    if (!enabled) {
      setState({ poses: {}, connected: false })
      return
    }

    let closed = false
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined

    function open() {
      if (closed) return
      socket = new WebSocket(socketUrl())

      socket.onopen = () => setState((s) => ({ ...s, connected: true }))

      socket.onmessage = (event) => {
        let message: { type?: string; poses?: FleetPose[] } & Partial<FleetPose>
        try {
          message = JSON.parse(event.data as string)
        } catch {
          return
        }

        // 붙자마자 오는 첫 프레임. 통째로 갈아끼운다 — 서버가 아는 전부가
        // 이것이므로, 합치면 이미 사라진 로봇이 화면에 남는다.
        if (message.type === 'snapshot') {
          const poses: Record<string, FleetPose> = {}
          for (const pose of message.poses ?? []) poses[pose.robot_id] = pose
          setState((s) => ({ ...s, poses }))
          return
        }

        if (message.type === 'pose' && message.robot_id) {
          const pose = message as FleetPose
          setState((s) => ({
            ...s,
            poses: { ...s.poses, [pose.robot_id]: pose },
          }))
        }
      }

      socket.onclose = () => {
        // 위 주석 참고 — 낡은 위치를 현재처럼 두지 않는다.
        setState({ poses: {}, connected: false })
        if (!closed) retry = setTimeout(open, RETRY_MS)
      }
    }

    open()
    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      socket?.close()
      socket = null
    }
  }, [enabled])

  return state
}
