/**
 * 선택한 로봇의 현재 주행 모드.
 *
 * ## 왜 대시보드의 이벤트 목록을 쓰지 않나
 *
 * 그 목록은 최근 N 건(지금 30건)이다. 주행 중에는 nav.* 이벤트가 빠르게
 * 쌓이므로, 모드를 바꾼 지 얼마 안 돼도 robot.mode_changed 가 창 밖으로
 * 밀린다. DB 에서 지워진 것이 아닌데 화면만 "확인 중" 으로 돌아간다.
 *
 * 조작 패드가 그 값을 보고 열리고 닫히므로, 모드를 모르는 상태는 곧
 * "조작이 안 되는 상태" 다. 그래서 모드만 따로, 로봇을 지정해 조회한다.
 * 서버가 occurred_at DESC 로 주므로 첫 건이 최신이다.
 */

import { useEffect, useState } from 'react'

import type { RobotMode } from './api'
import { listEvents } from './eventsApi'

const MODE_CODE = 'robot.mode_changed'

export function useRobotMode(robotId: string | null, pollMs: number): RobotMode | null {
  const [mode, setMode] = useState<RobotMode | null>(null)

  useEffect(() => {
    setMode(null)
    if (!robotId) return

    const id = robotId
    let alive = true
    const controller = new AbortController()

    async function poll() {
      try {
        const page = await listEvents(
          { robot_id: id, code_prefix: MODE_CODE, limit: 1 },
          { signal: controller.signal },
        )
        if (!alive) return
        const value = page.items[0]?.payload?.mode
        setMode(
          value === 'auto' || value === 'manual' || value === 'estop' ? value : null,
        )
      } catch {
        // 폴링 실패는 다음 주기가 곧 재시도다. 여기서 모드를 지우면
        // 일시적인 네트워크 오류에 조작 패드가 닫힌다.
      }
    }

    poll()
    const timer = setInterval(poll, pollMs)
    return () => {
      alive = false
      controller.abort()
      clearInterval(timer)
    }
  }, [robotId, pollMs])

  return mode
}
