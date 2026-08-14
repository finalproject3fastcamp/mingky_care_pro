/**
 * 3D 지도를 로봇 없이 확인하는 화면.
 *
 * 대시보드는 실제 로봇이 붙어 있어야 위치가 뜬다. 여기서는 가짜 값을 넣어
 * 시점·조명·안내 글자·레이어를 눈으로 확인한다. 슬라이더로 로봇을 옮겨 보면
 * 좌표가 제대로 맞았는지 알 수 있다.
 *
 * 조명은 여기서 손으로 맞춘 뒤, 아래 상자의 값을 HospitalMap3D 의 LOOK 에
 * 넣으면 대시보드에 그대로 적용된다.
 *
 * `npm run dev` 후 /preview-3d.html 로 연다.
 */

import { useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { HospitalMap3D, LOOK, type WaypointMarker } from './components/HospitalMap3D'
import { WAYPOINTS } from './components/mapWaypoints'
import { insideWall, simulateParticles, simulatePlan, simulateScan } from './previewSim'
import './index.css'
import './App.css'

function App() {
  // 건물 안에서 벽으로부터 가장 멀리 떨어진 자리. 벽에 붙여 두면 로봇이
  // 벽에 가려 보이지 않고, 벽 속이면 라이다가 전부 0 이 되어 화면이 빈다.
  const [x, setX] = useState(2.601)
  const [y, setY] = useState(0.314)
  const [yaw, setYaw] = useState(0.6)
  const [spread, setSpread] = useState(0.05)
  const [goal, setGoal] = useState('ct_room_goal')
  const [live, setLive] = useState(true)
  const [estop, setEstop] = useState(false)
  const [sel, setSel] = useState(true)
  const [showWp, setShowWp] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)

  // ---- 조명 ----
  // 방향은 x·y·z 대신 방위각·고도로 잡는다. "해를 어느 쪽에서 몇 도 위로
  // 띄울까" 가 사람이 생각하는 방식이고, 셋을 따로 만지면 세기가 같이 변한다.
  const [sun, setSun] = useState(LOOK.sun)
  const [az, setAz] = useState(36)
  const [el, setEl] = useState(48)
  const [fill, setFill] = useState(LOOK.fill)
  const [env, setEnv] = useState(LOOK.env)
  const [exposure, setExposure] = useState(LOOK.exposure)
  const [bg, setBg] = useState(LOOK.background)

  const sunFrom = useMemo(() => {
    const r = 3.5
    const a = (az * Math.PI) / 180
    const e = (el * Math.PI) / 180
    return [r * Math.cos(e) * Math.sin(a), r * Math.sin(e), r * Math.cos(e) * Math.cos(a)] as [
      number,
      number,
      number,
    ]
  }, [az, el])

  const look = useMemo(
    () => ({ sun, sunFrom, fill, env, exposure, background: bg }),
    [sun, sunFrom, fill, env, exposure, bg],
  )

  const lookCode =
    `  background: '${bg}',\n` +
    `  sky: 0x${LOOK.sky.toString(16).padStart(6, '0')},\n` +
    `  ground: 0x${LOOK.ground.toString(16).padStart(6, '0')},\n` +
    `  fill: ${fill},\n` +
    `  sun: ${sun},\n` +
    `  sunFrom: [${sunFrom.map((v) => v.toFixed(3)).join(', ')}],\n` +
    `  env: ${env},\n` +
    `  exposure: ${exposure},\n`

  const downloadLook = () => {
    const url = URL.createObjectURL(new Blob([lookCode], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'look.txt'
    a.click()
    URL.revokeObjectURL(url)
  }

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
        look={look}
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

      <fieldset style={{ border: '1px solid #cbd5e1', borderRadius: 8, padding: '10px 14px 14px' }}>
        <legend style={{ fontSize: 13, fontWeight: 600, padding: '0 6px' }}>조명</legend>
        <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(3, 1fr)' }}>
          {row('주광 세기', sun, 0, 6, setSun)}
          {row('주광 방위 (도)', az, 0, 360, setAz)}
          {row('주광 고도 (도)', el, 5, 88, setEl)}
          {row('받침 세기', fill, 0, 1.5, setFill)}
          {row('주변 반사', env, 0, 1.5, setEnv)}
          {row('노출', exposure, 0.3, 2.2, setExposure)}
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10, fontSize: 13 }}>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            배경색
            <input type="color" value={bg} onChange={(e) => setBg(e.target.value)} />
            <code>{bg}</code>
          </label>
          <button type="button" onClick={() => navigator.clipboard?.writeText(lookCode)}>
            값 복사
          </button>
          <button type="button" onClick={downloadLook}>
            값 파일로 내려받기
          </button>
          <button
            type="button"
            onClick={() => {
              setSun(LOOK.sun)
              setAz(36)
              setEl(48)
              setFill(LOOK.fill)
              setEnv(LOOK.env)
              setExposure(LOOK.exposure)
              setBg(LOOK.background)
            }}
          >
            처음으로
          </button>
        </div>
        <textarea readOnly value={lookCode} rows={8} style={{ width: '100%', marginTop: 10, fontSize: 12 }} />
      </fieldset>

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
