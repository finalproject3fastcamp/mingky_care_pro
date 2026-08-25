/**
 * 전체 위치 — 핑키 2대가 지금 어디 있는가.
 *
 * 지금까지 관제는 **한 번에 한 대만** 볼 수 있었다. 위치가 조작 소켓을 타고
 * 왔고 그 소켓은 로봇 하나에 매여 있었기 때문이다(`useTeleopSocket`). 두
 * 대를 동시에 돌리면서 그 둘이 서로 어디 있는지 볼 수 없다는 것이, 지금
 * 시작하는 fleet 작업의 첫 번째 벽이었다.
 *
 * ## 왜 조작 소켓을 두 벌 열지 않았나
 *
 * 조작자 소켓은 붙는 순간 `control_audit` 에 `teleop_attach` 를 남기고, 그
 * 행동은 SLO 판정에서 개입이다(§1.1). 보기만 했는데 그 로봇의 안내 세션이
 * 실패로 집계된다. 관측 전용 채널을 따로 만든 이유가 이것이다
 * (`lib/useFleetPoses.ts` · `backend/app/fleet_pose.py`).
 *
 * ## 무엇을 안 그리나
 *
 * 라이다·파티클·경로가 없다. 그건 한 대분만 오고, 두 대를 겹쳐 그리면 어느
 * 점이 어느 로봇 것인지 알 수 없어 판단이 불가능해진다. **여기서는 어디
 * 있는지만 말하고**, 위치추정이 맞는지 보려면 그 로봇의 화면으로 간다.
 */

import { useEffect, useMemo, useState } from 'react'

import { formatAge, freshnessLevel } from '../lib/freshness'
import { robotColor } from '../lib/robotColors'
import { useFleetPoses } from '../lib/useFleetPoses'
import { isMobile, type Robot } from '../types/monitoring'
import { LazyHospitalMap3D, type PeerMarker } from './LazyHospitalMap3D'

/**
 * 로봇은 0.5초마다 위치를 올린다 (teleop_bridge 의 pose_interval_sec).
 * 3초면 여섯 번을 걸렀다는 뜻이라 지금 위치로 볼 수 없고, 10초면 브리지가
 * 끊긴 것이다. heartbeat(5초 주기 · 15초 두절)와 임계가 다른 이유가 이것이다.
 */
const POSE_FRESHNESS = { warnSec: 3, staleSec: 10 }

/**
 * 나이를 다시 재는 주기.
 *
 * 이게 없으면 **위치가 멈춘 순간 나이도 멈춘다.** 판정은 그리는 시점에
 * 계산하는데, 로봇이 조용해지면 다시 그릴 일이 없어서다. 화면에는 마지막
 * 좌표가 '방금' 이라고 적힌 채 영원히 남고, 그게 정확히 이 카드가 하면
 * 안 되는 거짓말이다.
 *
 * 부모의 로봇 폴링(5초)에 얹혀 가게 둘 수도 있지만, 그러면 저쪽 주기를
 * 바꾸는 날 이 카드가 조용히 거짓말을 시작한다. 자기가 쓰는 시계는 자기가
 * 갖는다.
 */
const CLOCK_MS = 1000

interface Props {
  /** `GET /robots` 의 목록. 이름과 '위치를 한 번도 못 받은 로봇' 이 여기서 온다. */
  robots: Robot[]
}

export function FleetPoseCard({ robots }: Props) {
  const { poses, connected } = useFleetPoses()

  // 위 CLOCK_MS 주석 참고 — 좌표가 멈춰도 나이는 계속 흘러야 한다.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_MS)
    return () => clearInterval(timer)
  }, [])

  // 주행 로봇만 지도에 올린다. 조제 스테이션은 고정이라 위치가 없다.
  //
  // `robots` 를 기준으로 도는 것이 요점이다. 위치 목록만 보면 **한 번도
  // 위치를 못 받은 로봇이 화면에서 그냥 사라진다** — 그건 "거기 없다" 가
  // 아니라 "모른다" 이고, 관제에서 둘은 완전히 다른 사실이다.
  const mobile = useMemo(() => robots.filter(isMobile), [robots])

  const rows = useMemo(() => mobile.map((robot, index) => {
    const pose = poses[robot.robot_id] ?? null
    return {
      robot,
      pose,
      color: robotColor(index),
      level: freshnessLevel(pose?.observed_at, POSE_FRESHNESS, now),
    }
  }), [mobile, poses, now])

  const peers = useMemo<PeerMarker[]>(
    () => rows.flatMap(({ robot, pose, color, level }) => (pose ? [{
      robotId: robot.robot_id,
      label: robot.display_name,
      x: pose.x,
      y: pose.y,
      yaw: pose.yaw,
      color,
      stale: level === 'stale',
    }] : [])),
    [rows],
  )

  return (
    <section className="card fleet-poses" aria-label="전체 위치">
      <div className="card-title">전체 위치</div>

      <div className="fleet-poses__map">
        <LazyHospitalMap3D
          variant="overview"
          peers={peers}
          live={connected}
          pose={null}
          scan={null}
          particles={null}
          plan={null}
          recoveryPlan={null}
        />
      </div>

      <ul className="fleet-poses__legend">
        {rows.map(({ robot, pose, color, level }) => (
          <li key={robot.robot_id} className="fleet-poses__item">
            {/* 지도의 바닥 고리와 같은 색이어야 한다. 둘 다 robotColors 에서 온다. */}
            <span
              className="fleet-poses__swatch"
              style={{ background: level === 'stale' ? undefined : color }}
              data-stale={level === 'stale' ? '' : undefined}
              aria-hidden="true"
            />
            <strong>{robot.display_name}</strong>
            {pose ? (
              <>
                <code className="fleet-poses__coord">
                  x {pose.x.toFixed(2)} · y {pose.y.toFixed(2)}
                </code>
                {/* 좌표만 보여주면 화면이 거짓말을 할 수 있다. 나이가 붙어야
                    "지금 저기 있다" 와 "저기서 마지막으로 봤다" 가 갈린다. */}
                <span className={`fleet-poses__age fleet-poses__age--${level}`}>
                  {formatAge(pose.observed_at, now)}
                </span>
              </>
            ) : (
              <span className="fleet-poses__unknown">
                {/* 두절과 위치 미상은 다르다. 전자는 회선, 후자는 AMCL 이다. */}
                {robot.link_state === 'offline' ? '통신 두절' : '위치 미상'}
              </span>
            )}
          </li>
        ))}
      </ul>

      {!connected && (
        <p className="fleet-poses__offline" role="status">
          관제 서버와의 위치 연결이 끊겼습니다. 지도는 비워 둡니다 —
          마지막으로 본 자리를 현재 위치처럼 보여주지 않기 위해서입니다.
        </p>
      )}
    </section>
  )
}
