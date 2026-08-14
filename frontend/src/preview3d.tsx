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
import { insideWall, simulateParticles, simulatePlan, simulateScan } from './previewSim'
import './index.css'
import './App.css'

function App() {
  // 복도 한가운데. 벽 속이면 라이다가 전부 0 이 되어 화면이 빈다.
  const [x, setX] = useState(1.524)
  const [y, setY] = useState(0.952)
  const [yaw, setYaw] = useState(0.6)
  const [spread, setSpread] = useState(0.05)
  const [goal, setGoal] = useState('ct_room_goal')
  const [live, setLive] = useState(true)
  const [estop, setEstop] = useState(false)
  const [sel, setSel] = useState(true)
  const [showWp, setShowWp] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)

  const pose = live ? { x, y, yaw } : null
  const blocked = insideWall(x, y)

  // 실제 벽에 부딪히는 라이다. RViz 에서 보던 것과 같은 모양이 나와야 맞다.
  const scan = useMemo(() => simulateScan(x, y, yaw), [x, y, yaw])
  const particles = useMemo(() => simulateParticles(x, y, spread), [x, y, spread])

  // 벽을 피해 도는 경로. 목표는 아래에서 고른다.
  const plan = useMemo(() => {
    const g = WAYPOINTS.find((w) => w.id === goal)
    return g ? simulatePlan(x, y, g.x, g.y) : []
  }, [x, y, goal])

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
      {blocked && (
        <p style={{ margin: 0, fontSize: 13, color: '#b45309', fontWeight: 600 }}>
          로봇이 벽 안에 있습니다 — 라이다가 전부 거리 0 이라 아무것도 안 보입니다.
          x·y 를 옮기세요.
        </p>
      )}

      <HospitalMap3D
        pose={pose}
        live={live}
        scan={scan}
        particles={particles}
        plan={plan}
        estop={estop}
        selected={sel}
        waypoints={waypoints}
        onSelectWaypoint={setSelected}
        onSetPose={(nx, ny, nyaw) => {
          setX(nx)
          setY(ny)
          setYaw(nyaw)
        }}
      />

      <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(4, 1fr)' }}>
        {row('x (m)', x, -0.3, 3.2, setX)}
        {row('y (m)', y, -0.4, 2.0, setY)}
        {row('방향 (rad)', yaw, -Math.PI, Math.PI, setYaw)}
        {row('파티클 퍼짐 (m)', spread, 0.01, 0.6, setSpread)}
      </div>

      <label style={{ display: 'grid', gap: 4, fontSize: 13, maxWidth: 260 }}>
        <span>경로 목표</span>
        <select value={goal} onChange={(e) => setGoal(e.target.value)}>
          {WAYPOINTS.map((w) => (
            <option key={w.id} value={w.id}>
              {w.label} ({w.id})
            </option>
          ))}
        </select>
      </label>

      <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
        <label>
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> 로봇 연결
        </label>
        <label>
          <input type="checkbox" checked={estop} onChange={(e) => setEstop(e.target.checked)} /> 비상정지
        </label>
        <label>
          <input type="checkbox" checked={sel} onChange={(e) => setSel(e.target.checked)} /> 선택(박동)
        </label>
        <label>
          <input type="checkbox" checked={showWp} onChange={(e) => setShowWp(e.target.checked)} /> 웨이포인트
        </label>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
