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
 * 지도는 왼쪽에 붙박여 있고 조절 바는 오른쪽에 모여 있다. 바를 만지는 동안
 * 지도가 화면 밖으로 나가면 무엇이 달라졌는지 볼 수가 없기 때문이다.
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
import './preview3d.css'

function App() {
  // 사방이 벽으로 둘러싸인 자리(1.4 m 안에서 36방향 모두 벽에 닿는다).
  // 탁 트인 곳에 두면 라이다가 거의 안 잡혀 그리기가 맞는지 판단할 수 없고,
  // 벽에 붙여 두면 로봇이 벽에 가려 보이지 않는다.
  const [x, setX] = useState(0.15)
  const [y, setY] = useState(-0.135)
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
  const [signSize, setSignSize] = useState(LOOK.signSize)
  const [scanSize, setScanSize] = useState(LOOK.scanSize)
  const [scanOpacity, setScanOpacity] = useState(LOOK.scanOpacity)
  const [scanColor, setScanColor] = useState(LOOK.scanColor)
  const [planWidth, setPlanWidth] = useState(LOOK.planWidth)
  const [planOpacity, setPlanOpacity] = useState(LOOK.planOpacity)
  const [planColor, setPlanColor] = useState(LOOK.planColor)

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
    () => ({
      sun,
      sunFrom,
      fill,
      env,
      exposure,
      background: bg,
      signSize,
      scanSize,
      scanOpacity,
      scanColor,
      planWidth,
      planOpacity,
      planColor,
    }),
    [
      sun, sunFrom, fill, env, exposure, bg, signSize,
      scanSize, scanOpacity, scanColor, planWidth, planOpacity, planColor,
    ],
  )

  const lookCode =
    `  background: '${bg}',\n` +
    `  sky: 0x${LOOK.sky.toString(16).padStart(6, '0')},\n` +
    `  ground: 0x${LOOK.ground.toString(16).padStart(6, '0')},\n` +
    `  fill: ${fill},\n` +
    `  sun: ${sun},\n` +
    `  sunFrom: [${sunFrom.map((v) => v.toFixed(3)).join(', ')}],\n` +
    `  env: ${env},\n` +
    `  exposure: ${exposure},\n` +
    `  signSize: ${signSize},\n` +
    `  scanSize: ${scanSize},\n` +
    `  scanOpacity: ${scanOpacity},\n` +
    `  scanColor: '${scanColor}',\n` +
    `  planWidth: ${planWidth},\n` +
    `  planOpacity: ${planOpacity},\n` +
    `  planColor: '${planColor}',\n`

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
    step = 0.01,
  ) => (
    <label className="pv-row">
      <span>
        {label}
        <b>{value.toFixed(2)}</b>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => set(Number(e.target.value))}
      />
    </label>
  )

  return (
    <div className="pv">
      <div className="pv-stage">
        <h2>3D 지도 미리보기</h2>
        <p className="pv-hint">
          끌어서 돌리고, 휠로 당기고, 오른쪽 버튼으로 옮깁니다. 오른쪽 바를 만지면
          지도가 바로 바뀝니다.
          {selected && ` — 고른 지점: ${selected}`}
        </p>
        {blocked && (
          <p className="pv-warn">
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
      </div>

      <aside className="pv-side">
        <fieldset className="pv-group">
          <legend>로봇</legend>
          <div className="pv-rows">
            {row('x (m)', x, -0.3, 3.2, setX)}
            {row('y (m)', y, -0.4, 2.0, setY)}
            {row('방향 (rad)', yaw, -Math.PI, Math.PI, setYaw)}
            {row('파티클 퍼짐 (m)', spread, 0.01, 0.6, setSpread)}
          </div>

          <label className="pv-field">
            경로 목표
            <select value={goal} onChange={(e) => setGoal(e.target.value)}>
              {WAYPOINTS.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.label} ({w.id})
                </option>
              ))}
            </select>
          </label>

          <div className="pv-checks">
            <label>
              <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />{' '}
              로봇 연결
            </label>
            <label>
              <input type="checkbox" checked={estop} onChange={(e) => setEstop(e.target.checked)} />{' '}
              비상정지
            </label>
            <label>
              <input type="checkbox" checked={sel} onChange={(e) => setSel(e.target.checked)} />{' '}
              선택(박동)
            </label>
            <label>
              <input type="checkbox" checked={showWp} onChange={(e) => setShowWp(e.target.checked)} />{' '}
              웨이포인트
            </label>
          </div>
        </fieldset>

        <fieldset className="pv-group">
          <legend>라이다 · 경로</legend>
          <div className="pv-rows">
            {row('라이다 점 굵기 (px)', scanSize, 1, 12, setScanSize, 0.1)}
            {row('라이다 진하기', scanOpacity, 0.05, 1, setScanOpacity)}
            {row('경로 선 굵기 (px)', planWidth, 1, 14, setPlanWidth, 0.1)}
            {row('경로 진하기', planOpacity, 0.05, 1, setPlanOpacity)}
          </div>
          <div className="pv-btns">
            <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              라이다 색
              <input
                type="color"
                value={scanColor}
                onChange={(e) => setScanColor(e.target.value)}
              />
            </label>
            <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              경로 색
              <input
                type="color"
                value={planColor}
                onChange={(e) => setPlanColor(e.target.value)}
              />
            </label>
          </div>
        </fieldset>

        <fieldset className="pv-group">
          <legend>조명 · 글자</legend>
          <div className="pv-rows">
            {row('주광 세기', sun, 0, 6, setSun)}
            {row('주광 방위 (도)', az, 0, 360, setAz, 1)}
            {row('주광 고도 (도)', el, 5, 88, setEl, 1)}
            {row('받침 세기', fill, 0, 1.5, setFill)}
            {row('주변 반사', env, 0, 1.5, setEnv)}
            {row('노출', exposure, 0.3, 2.2, setExposure)}
            {row('안내 글자 크기', signSize * 1000, 3, 25, (v) => setSignSize(v / 1000), 0.1)}
          </div>

          <div className="pv-btns">
            <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              배경색
              <input type="color" value={bg} onChange={(e) => setBg(e.target.value)} />
            </label>
            <code>{bg}</code>
            <button type="button" onClick={() => navigator.clipboard?.writeText(lookCode)}>
              값 복사
            </button>
            <button type="button" onClick={downloadLook}>
              파일로 내려받기
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
                setSignSize(LOOK.signSize)
                setScanSize(LOOK.scanSize)
                setScanOpacity(LOOK.scanOpacity)
                setScanColor(LOOK.scanColor)
                setPlanWidth(LOOK.planWidth)
                setPlanOpacity(LOOK.planOpacity)
                setPlanColor(LOOK.planColor)
              }}
            >
              처음으로
            </button>
          </div>

          <textarea className="pv-out" readOnly value={lookCode} rows={16} />
        </fieldset>
      </aside>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
