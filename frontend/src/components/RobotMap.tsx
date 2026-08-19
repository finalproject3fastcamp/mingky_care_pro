/**
 * 지도 위에 로봇 위치와 로컬라이제이션 진단 레이어를 그린다.
 *
 * 무엇을 그릴지는 docs/nav2-debugging.md 가 정해 준다.
 *
 * | 레이어 | 무엇을 판단하나 |
 * | --- | --- |
 * | 라이다 | **맵과 겹치나** — 안 겹치면 위치추정이 틀렸다 |
 * | 파티클 | **넓게 퍼졌나** — 퍼지면 AMCL 발산 |
 * | 경로 | 목표까지 그려지나 |
 *
 * ## 좌표 변환
 *
 * ROS 는 미터, y 가 위로 증가한다. 이미지는 픽셀, y 가 아래로 증가한다.
 *
 *     px = (x - originX) / resolution
 *     py = height - (y - originY) / resolution
 *
 * ## 라이다를 왜 여기서 회전시키나
 *
 * 브리지는 센서 장착 TF를 적용한 로봇 기준 극좌표를 보낸다. 지도 좌표로
 * 옮기려면 pose 와 짝을 맞춰야 하는데, 그 정합은 둘을 함께 들고 있는 화면
 * 쪽이 간단하다.
 * **pose 가 없으면 라이다도 안 그린다** — 기준이 없으면 어디에 찍을지 모른다.
 */

import { useEffect, useRef, useState } from 'react'

import { api } from '../lib/api'
import type { DiagLayers, RobotPose } from '../lib/useTeleopSocket'

interface MapMeta {
  width: number
  height: number
  resolution: number
  origin: number[]
}

interface Props extends DiagLayers {
  pose: RobotPose | null
  live: boolean
  /** 지도를 찍어 "로봇이 여기 있다" 를 알린다. 없으면 지정 모드가 안 뜬다. */
  onSetPose?: (x: number, y: number, yaw: number) => void
  waypoints?: WaypointMarker[]
  onSelectWaypoint?: (name: string) => void
}

export interface WaypointMarker {
  name: string
  x: number
  y: number
  yaw: number
  status?: 'ok' | 'warning' | 'blocked' | 'outside'
  selected?: boolean
}

const LAYER_LABEL = {
  scan: '라이다',
  particles: '파티클',
  plan: '원래 경로',
} as const

type LayerKey = keyof typeof LAYER_LABEL

export function RobotMap({
  pose, live, scan, particles, plan, recoveryPlan, onSetPose,
  waypoints = [], onSelectWaypoint,
}: Props) {
  // 위치 지정 모드. 실수로 지도를 눌러 로봇 위치가 바뀌면 안 되므로,
  // 버튼으로 한 번 켜야 찍을 수 있게 한다.
  const [placing, setPlacing] = useState(false)
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null)
  const [meta, setMeta] = useState<MapMeta | null>(null)
  const [failed, setFailed] = useState(false)
  const [tick, setTick] = useState(0)
  const [visible, setVisible] = useState<Record<LayerKey, boolean>>({
    scan: true,
    particles: true,
    plan: true,
  })
  const imageRef = useRef<HTMLImageElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    let alive = true
    api
      .get<MapMeta>('/map')
      .then(({ data }) => {
        if (!alive) return
        setMeta(data)
        const img = new Image()
        img.onload = () => {
          imageRef.current = img
          setTick((t) => t + 1)
        }
        img.src = `${api.defaults.baseURL}/map/image`
      })
      .catch(() => alive && setFailed(true))
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    const image = imageRef.current
    if (!canvas || !meta || !image) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const scale = Math.max(1, Math.floor(canvas.width / meta.width))
    const toPx = (x: number, y: number): [number, number] => [
      ((x - meta.origin[0]) / meta.resolution) * scale,
      (meta.height - (y - meta.origin[1]) / meta.resolution) * scale,
    ]

    ctx.imageSmoothingEnabled = false
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(image, 0, 0, meta.width * scale, meta.height * scale)

    // 경로 — 가장 아래. 위치·라이다를 가리면 안 된다.
    if (visible.plan && plan && plan.length > 1) {
      ctx.strokeStyle = 'rgba(37, 99, 235, 0.85)'
      ctx.lineWidth = 2
      ctx.beginPath()
      plan.forEach(([x, y], i) => {
        const [px, py] = toPx(x, y)
        if (i === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      })
      ctx.stroke()
    }

    // 실제로 선택되어 이동 중인 복구 경로만 별도 표시한다. 후보 검증용
    // ComputePathToPose 결과는 로봇에서 걸러져 여기까지 오지 않는다.
    if (visible.plan && recoveryPlan && recoveryPlan.length > 1) {
      ctx.save()
      ctx.strokeStyle = 'rgba(249, 115, 22, 0.95)'
      ctx.lineWidth = 2.5
      ctx.setLineDash([7, 5])
      ctx.beginPath()
      recoveryPlan.forEach(([x, y], i) => {
        const [px, py] = toPx(x, y)
        if (i === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      })
      ctx.stroke()
      ctx.restore()
    }

    // 파티클 — 퍼짐과 수렴 상태를 한눈에 확인할 수 있도록 선명하게 표시한다.
    if (visible.particles && particles) {
      ctx.fillStyle = 'rgba(16, 185, 129, 0.75)'
      for (const [x, y] of particles) {
        const [px, py] = toPx(x, y)
        ctx.fillRect(px - 2, py - 2, 4, 4)
      }
    }

    for (const waypoint of waypoints) {
      const [wx, wy] = toPx(waypoint.x, waypoint.y)
      const color = waypoint.status === 'blocked' || waypoint.status === 'outside'
        ? '#dc2626' : waypoint.status === 'warning' ? '#f59e0b' : '#2563eb'
      ctx.fillStyle = color
      ctx.strokeStyle = waypoint.selected ? '#111827' : '#ffffff'
      ctx.lineWidth = waypoint.selected ? 3 : 1.5
      ctx.beginPath()
      ctx.arc(wx, wy, waypoint.selected ? 6 : 4, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.beginPath()
      ctx.moveTo(wx, wy)
      ctx.lineTo(wx + Math.cos(-waypoint.yaw) * 13, wy + Math.sin(-waypoint.yaw) * 13)
      ctx.strokeStyle = color
      ctx.lineWidth = 2
      ctx.stroke()
      if (waypoint.selected) {
        ctx.font = '12px sans-serif'
        ctx.fillStyle = '#111827'
        ctx.fillText(waypoint.name, wx + 9, wy - 9)
      }
    }

    if (!pose) return

    // 라이다 — 로봇 기준 극좌표를 pose 로 회전·평행이동해 지도에 얹는다.
    if (visible.scan && scan) {
      ctx.fillStyle = 'rgba(239, 68, 68, 0.9)'
      for (const [angle, range] of scan) {
        const wx = pose.x + range * Math.cos(pose.yaw + angle)
        const wy = pose.y + range * Math.sin(pose.yaw + angle)
        const [px, py] = toPx(wx, wy)
        ctx.fillRect(px - 1.5, py - 1.5, 3, 3)
      }
    }

    // 로봇 — 가장 위.
    const [px, py] = toPx(pose.x, pose.y)
    ctx.strokeStyle = '#111'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(px, py)
    ctx.lineTo(px + Math.cos(-pose.yaw) * 16, py + Math.sin(-pose.yaw) * 16)
    ctx.stroke()

    ctx.fillStyle = '#ef4444'
    ctx.beginPath()
    ctx.arc(px, py, 5, 0, Math.PI * 2)
    ctx.fill()
    ctx.strokeStyle = '#fff'
    ctx.lineWidth = 1.5
    ctx.stroke()
  }, [meta, pose, scan, particles, plan, recoveryPlan, visible, tick, waypoints])

  if (failed) {
    return (
      <section className="robot-map">
        <p className="robot-map__empty">지도를 불러오지 못했습니다.</p>
      </section>
    )
  }

  // 파티클이 얼마나 퍼졌나 — 발산 판단의 핵심 수치다.
  const spread = particles && particles.length > 1 ? particleSpread(particles) : null

  return (
    <section className="robot-map">
      <header className="robot-map__header">
        <span className="robot-map__label">위치 · 로컬라이제이션</span>
        {pose ? (
          <span className="robot-map__coord">
            x {pose.x.toFixed(2)} · y {pose.y.toFixed(2)} ·{' '}
            {((pose.yaw * 180) / Math.PI).toFixed(0)}°
          </span>
        ) : (
          <span className="robot-map__coord robot-map__coord--none">
            {live ? '위치 수신 대기 중' : '로봇 연결 없음'}
          </span>
        )}
      </header>

      <div className="robot-map__legend">
        {(Object.keys(LAYER_LABEL) as LayerKey[]).map((key) => (
          <button
            key={key}
            type="button"
            className={`robot-map__toggle robot-map__toggle--${key}${
              visible[key] ? '' : ' off'
            }`}
            onClick={() => setVisible((v) => ({ ...v, [key]: !v[key] }))}
          >
            {LAYER_LABEL[key]}
          </button>
        ))}
        {recoveryPlan && recoveryPlan.length > 1 && (
          <span className="robot-map__recovery-key">복구 경로</span>
        )}
        {onSetPose && (
          <button
            type="button"
            className={`robot-map__place${placing ? ' on' : ''}`}
            onClick={() => setPlacing((v) => !v)}
            title="로봇이 실제로 있는 위치를 지도에서 찍어 알려줍니다"
          >
            {placing ? '지도를 클릭하세요' : '위치 지정'}
          </button>
        )}
        {spread !== null && (
          <span
            className={`robot-map__spread${spread > 0.5 ? ' robot-map__spread--wide' : ''}`}
            title="파티클이 넓게 퍼지면 위치추정이 발산한 것입니다"
          >
            퍼짐 {spread.toFixed(2)} m
          </span>
        )}
      </div>

      <canvas
        ref={canvasRef}
        width={576}
        height={441}
        className={`robot-map__canvas${placing ? ' robot-map__canvas--placing' : ''}`}
        onPointerDown={(e) => {
          if (!placing && meta && onSelectWaypoint && waypoints.length) {
            const point = toMapCoords(e, meta)
            if (point) {
              const nearest = waypoints
                .map((waypoint) => ({ waypoint, distance: Math.hypot(waypoint.x - point.x, waypoint.y - point.y) }))
                .sort((a, b) => a.distance - b.distance)[0]
              if (nearest && nearest.distance < meta.resolution * 10) {
                onSelectWaypoint(nearest.waypoint.name)
              }
            }
            return
          }
          if (!placing || !meta) return
          const p = toMapCoords(e, meta)
          if (p) setDrag(p)
        }}
        onPointerUp={(e) => {
          if (!placing || !meta || !drag || !onSetPose) return
          const end = toMapCoords(e, meta)
          // 끌어서 방향을 준다. 그냥 누르면(거의 안 움직이면) 방향은 0 이다.
          const yaw = end
            ? Math.atan2(end.y - drag.y, end.x - drag.x)
            : 0
          const moved = end
            ? Math.hypot(end.x - drag.x, end.y - drag.y)
            : 0
          onSetPose(drag.x, drag.y, moved > 0.05 ? yaw : 0)
          setDrag(null)
          setPlacing(false)
        }}
      />

      {!pose && (
        <p className="robot-map__empty">
          {live
            ? 'Nav2(AMCL)가 실행 중이어야 위치가 표시됩니다.'
            : '로봇의 조작 브리지가 연결되면 표시됩니다.'}
        </p>
      )}
    </section>
  )
}

/** 캔버스 클릭 지점을 지도(미터) 좌표로 옮긴다. */
function toMapCoords(
  e: React.PointerEvent<HTMLCanvasElement>,
  meta: MapMeta,
): { x: number; y: number } | null {
  const canvas = e.currentTarget
  const rect = canvas.getBoundingClientRect()
  // 캔버스는 CSS 로 늘어나 있으므로 실제 픽셀로 되돌린다.
  const cx = ((e.clientX - rect.left) / rect.width) * canvas.width
  const cy = ((e.clientY - rect.top) / rect.height) * canvas.height
  const scale = Math.max(1, Math.floor(canvas.width / meta.width))
  return {
    x: (cx / scale) * meta.resolution + meta.origin[0],
    y: (meta.height - cy / scale) * meta.resolution + meta.origin[1],
  }
}

/** 파티클 표준편차. 넓을수록 AMCL 이 확신을 잃은 것이다. */
function particleSpread(points: number[][]): number {
  const n = points.length
  const mx = points.reduce((a, p) => a + p[0], 0) / n
  const my = points.reduce((a, p) => a + p[1], 0) / n
  const varSum = points.reduce(
    (a, p) => a + (p[0] - mx) ** 2 + (p[1] - my) ** 2,
    0,
  )
  return Math.sqrt(varSum / n)
}
