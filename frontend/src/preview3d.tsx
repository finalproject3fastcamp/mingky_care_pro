/**
 * 3D 지도를 로봇 없이 확인하는 화면.
 *
 * 대시보드는 실제 로봇이 붙어 있어야 위치가 뜬다. 여기서는 가짜 값을 넣어
 * 시점·조명·안내 글자·레이어를 눈으로 확인한다. 슬라이더로 로봇을 옮겨 보면
 * 좌표가 제대로 맞았는지 알 수 있다.
 *
 * `npm run dev` 후 /preview-3d.html 로 연다.
 */

import { useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { HospitalMap3D, type WaypointMarker } from './components/HospitalMap3D'
import { WAYPOINTS } from './components/mapWaypoints'
import './index.css'
import './App.css'

function App() {
  const [x, setX] = useState(1.3)
  const [y, setY] = useState(0.7)
  const [yaw, setYaw] = useState(0.6)
  const [live, setLive] = useState(true)
  const [estop, setEstop] = useState(false)
  const [showWp, setShowWp] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)

  const pose = live ? { x, y, yaw } : null

  // 라이다처럼 보이는 값. 실제 벽 거리가 아니라 그리기만 확인하는 용도다.
  const scan = useMemo(() => {
    const out: number[][] = []
    for (let i = 0; i < 360; i += 1) {
      const a = (i / 360) * Math.PI * 2 - Math.PI
      out.push([a, 0.45 + 0.35 * Math.abs(Math.sin(a * 2)) + 0.05 * Math.sin(a * 11)])
    }
    return out
  }, [])

  const particles = useMemo(() => {
    const out: number[][] = []
    for (let i = 0; i < 600; i += 1) {
      out.push([x + (Math.random() - 0.5) * 0.16, y + (Math.random() - 0.5) * 0.16])
    }
    return out
  }, [x, y])

  const plan = useMemo(() => {
    const goal = WAYPOINTS.find((w) => w.id === 'ct_room_goal')!
    const out: number[][] = []
    for (let i = 0; i <= 40; i += 1) {
      const t = i / 40
      out.push([x + (goal.x - x) * t, y + (goal.y - y) * t + Math.sin(t * Math.PI) * 0.18])
    }
    return out
  }, [x, y])

  const waypoints: WaypointMarker[] = useMemo(
    () =>
      showWp
        ? WAYPOINTS.map((w) => ({
            name: w.id,
            x: w.x,
            y: w.y,
            yaw: 0,
            status: w.kind === 'charge' ? ('warning' as const) : ('ok' as const),
            selected: w.id === selected,
          }))
        : [],
    [showWp, selected],
  )

  const row = (
    label: string,
    value: number,
    min: number,
    max: number,
    set: (v: number) => void,
  ) => (
    <label style={{ display: 'grid', gap: 4, fontSize: 13 }}>
      <span>
        {label} <b>{value.toFixed(2)}</b>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={0.01}
        value={value}
        onChange={(e) => set(Number(e.target.value))}
      />
    </label>
  )

  return (
    <div style={{ maxWidth: 980, margin: '0 auto', padding: 20, display: 'grid', gap: 16 }}>
      <h2 style={{ margin: 0 }}>3D 지도 미리보기</h2>
      <p style={{ margin: 0, fontSize: 13, color: '#64748b' }}>
        끌어서 돌리고, 휠로 당기고, 오른쪽 버튼으로 옮깁니다. 슬라이더로 로봇을 옮겨
        좌표가 맞는지 확인하세요.
        {selected && ` — 고른 지점: ${selected}`}
      </p>

      <HospitalMap3D
        pose={pose}
        live={live}
        scan={scan}
        particles={particles}
        plan={plan}
        estop={estop}
        waypoints={waypoints}
        onSelectWaypoint={setSelected}
        onSetPose={(nx, ny, nyaw) => {
          setX(nx)
          setY(ny)
          setYaw(nyaw)
        }}
      />

      <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(3, 1fr)' }}>
        {row('x (m)', x, -0.3, 3.2, setX)}
        {row('y (m)', y, -0.4, 2.0, setY)}
        {row('방향 (rad)', yaw, -Math.PI, Math.PI, setYaw)}
      </div>

      <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
        <label>
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> 로봇 연결
        </label>
        <label>
          <input type="checkbox" checked={estop} onChange={(e) => setEstop(e.target.checked)} /> 비상정지
        </label>
        <label>
          <input type="checkbox" checked={showWp} onChange={(e) => setShowWp(e.target.checked)} /> 웨이포인트
        </label>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
