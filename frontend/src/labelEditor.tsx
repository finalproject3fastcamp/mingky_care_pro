/**
 * 안내판 위치·모양을 직접 끌어서 맞추는 편집 화면.
 *
 * 말로 "조금 왼쪽" 을 주고받는 것보다 직접 옮기는 편이 빠르다. 끌어다 놓으면
 * 그 자리를 실측 좌표로 되돌려(fromPct) 아래 칸에 코드로 찍어 준다. 그대로
 * 복사해 mapWaypoints.ts 의 SIGNS 를 갈아 끼우면 화면에 반영된다.
 *
 * 이 파일은 편집 도구일 뿐 실제 화면과 무관하다. `npm run dev` 후
 * /label-editor.html 로 연다.
 */

import { useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { SIGNS, type Sign } from './components/mapWaypoints'
import { IMG, LABEL, fromPct, toPct } from './components/HospitalMapPhoto'
import './index.css'
import './labelEditor.css'

type Style = {
  sizePct: number
  minorScale: number
  weight: number
  color: string
  letterSpacing: number
  offsetYPct: number
  plate: boolean
  plateColor: string
  plateOpacity: number
  radius: number
  shadow: boolean
}

const START: Style = {
  sizePct: LABEL.sizePct,
  minorScale: LABEL.minorScale,
  weight: LABEL.weight,
  color: LABEL.color,
  letterSpacing: LABEL.letterSpacing,
  offsetYPct: LABEL.offsetYPct,
  plate: true,
  plateColor: '#ffffff',
  plateOpacity: 0.92,
  radius: 0.18,
  shadow: true,
}

function Editor() {
  const [signs, setSigns] = useState<Sign[]>(SIGNS)
  const [st, setSt] = useState<Style>(START)
  const [picked, setPicked] = useState<number | null>(null)
  // 정렬은 여러 개를 한꺼번에 옮기는 일이라 고른 것들을 따로 들고 있는다.
  const [sel, setSel] = useState<number[]>([])
  const boxRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<{ i: number; dx: number; dy: number } | null>(null)

  const placed = useMemo(
    () => signs.map((s) => ({ ...s, ...toPct(s.u, s.v) })),
    [signs],
  )

  const onDown = (i: number) => (e: React.PointerEvent) => {
    const box = boxRef.current
    if (!box) return
    const r = box.getBoundingClientRect()
    const p = toPct(signs[i].u, signs[i].v)
    dragRef.current = {
      i,
      dx: ((e.clientX - r.left) / r.width) * 100 - p.left,
      dy: ((e.clientY - r.top) / r.height) * 100 - p.top,
    }
    setPicked(i)
    // Shift 를 누른 채 누르면 골라 담는다. 그냥 누르면 그것만 고른다.
    setSel((prev) =>
      e.shiftKey
        ? prev.includes(i) ? prev.filter((k) => k !== i) : [...prev, i]
        : [i],
    )
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }

  const onMove = (e: React.PointerEvent) => {
    const d = dragRef.current
    const box = boxRef.current
    if (!d || !box) return
    const r = box.getBoundingClientRect()
    const left = ((e.clientX - r.left) / r.width) * 100 - d.dx
    const top = ((e.clientY - r.top) / r.height) * 100 - d.dy
    const { u, v } = fromPct(left, top)
    setSigns((prev) =>
      prev.map((s, k) => (k === d.i ? { ...s, u: +u.toFixed(3), v: +v.toFixed(3) } : s)),
    )
  }

  const onUp = () => {
    dragRef.current = null
  }

  /**
   * 고른 것들을 한 줄로 맞춘다.
   *
   * 'room' 은 실제 방에서 같은 줄에 세우는 것이고, 'screen' 은 화면에서 같은
   * 높이로 보이게 하는 것이다. 원근이 있어서 둘은 다르다 — 방에서 나란해도
   * 멀리 있는 것은 화면에서 조금 위에 찍힌다.
   */
  const align = (how: 'room-y' | 'room-x' | 'screen-y' | 'spread-x') => {
    if (sel.length < 2) return
    setSigns((prev) => {
      const next = [...prev]
      if (how === 'room-y') {
        const m = sel.reduce((a, i) => a + prev[i].v, 0) / sel.length
        sel.forEach((i) => (next[i] = { ...next[i], v: +m.toFixed(3) }))
      } else if (how === 'room-x') {
        const m = sel.reduce((a, i) => a + prev[i].u, 0) / sel.length
        sel.forEach((i) => (next[i] = { ...next[i], u: +m.toFixed(3) }))
      } else if (how === 'screen-y') {
        const tops = sel.map((i) => toPct(prev[i].u, prev[i].v).top)
        const m = tops.reduce((a, b) => a + b, 0) / tops.length
        sel.forEach((i) => {
          const left = toPct(prev[i].u, prev[i].v).left
          const { u, v } = fromPct(left, m)
          next[i] = { ...next[i], u: +u.toFixed(3), v: +v.toFixed(3) }
        })
      } else {
        // 가로 간격을 고르게. 양 끝은 그대로 두고 사이를 나눈다.
        const ord = [...sel].sort((a, b) => prev[a].u - prev[b].u)
        const u0 = prev[ord[0]].u
        const u1 = prev[ord[ord.length - 1]].u
        ord.forEach((i, k) => {
          const u = u0 + ((u1 - u0) * k) / (ord.length - 1)
          next[i] = { ...next[i], u: +u.toFixed(3) }
        })
      }
      return next
    })
  }

  const plateBg = useMemo(() => {
    const h = st.plateColor.replace('#', '')
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16)
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${st.plateOpacity})`
  }, [st.plateColor, st.plateOpacity])

  const code = useMemo(
    () =>
      signs
        .map(
          (s) =>
            `  { label: '${s.label}',${s.sub ? ` sub: '${s.sub}',` : ''} icon: '${s.icon}'` +
            `, u: ${s.u}, v: ${s.v}, w: ${s.w}, h: ${s.h}, y: ${s.y}, rank: ${s.rank} },`,
        )
        .join('\n'),
    [signs],
  )

  const styleCode = useMemo(
    () =>
      [
        `  sizePct: ${st.sizePct},`,
        `  minorScale: ${st.minorScale},`,
        `  weight: ${st.weight},`,
        `  color: '${st.color}',`,
        `  letterSpacing: ${st.letterSpacing},`,
        `  offsetYPct: ${st.offsetYPct},`,
        `  background: ${st.plate ? `'${plateBg}'` : `'transparent'`},`,
        `  borderRadius: '${st.radius}em',`,
        `  boxShadow: ${st.shadow ? `'0 2px 6px rgba(20,40,60,0.10)'` : `'none'`},`,
      ].join('\n'),
    [st, plateBg],
  )

  /**
   * 값을 파일로 내려받는다. 복사해서 붙여넣는 것보다 손이 덜 가고, 무엇보다
   * 내려받은 파일은 다른 사람이 직접 열어 볼 수 있다(브라우저 안의 값은 못 본다).
   */
  const download = () => {
    const text =
      `// 안내판 값 — ${new Date().toLocaleString('ko-KR')}\n\n` +
      `// src/components/mapWaypoints.ts 의 SIGNS 안쪽\n${code}\n\n` +
      `// src/components/HospitalMapPhoto.tsx 의 LABEL 안쪽\n${styleCode}\n`
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'label-values.txt'
    a.click()
    URL.revokeObjectURL(url)
  }

  const num = (
    label: string,
    key: keyof Style,
    min: number,
    max: number,
    step: number,
  ) => (
    <label className="ed-row">
      <span>{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={st[key] as number}
        onChange={(e) => setSt({ ...st, [key]: Number(e.target.value) })}
      />
      <b>{st[key] as number}</b>
    </label>
  )

  return (
    <div className="ed">
      <div className="ed-main">
        <h2>안내판 편집</h2>
        <p className="ed-hint">
          글자를 끌어서 옮기세요. 옮긴 자리는 아래에 코드로 나옵니다.
          {picked !== null ? ` — 지금 잡은 것: ${signs[picked].label}` : ''}
        </p>

        <div
          className="ed-map"
          ref={boxRef}
          style={{ aspectRatio: `${IMG.w} / ${IMG.h}` }}
          onPointerMove={onMove}
          onPointerUp={onUp}
        >
          <img src="/hospital-render.png" alt="병원 평면" draggable={false} />
          {placed.map((l, i) => (
            <div
              key={l.label + i}
              className={`ed-label${sel.includes(i) ? ' is-picked' : ''}`}
              onPointerDown={onDown(i)}
              style={{
                left: `${l.left}%`,
                top: `${l.top + st.offsetYPct}%`,
                fontFamily: LABEL.fontFamily,
                fontSize: `${st.sizePct * (l.rank === 1 ? 1 : st.minorScale)}cqw`,
                fontWeight: st.weight,
                color: st.color,
                letterSpacing: `${st.letterSpacing}em`,
                background: st.plate ? plateBg : 'transparent',
                borderRadius: `${st.radius}em`,
                boxShadow: st.shadow ? '0 2px 6px rgba(20,40,60,0.10)' : 'none',
              }}
            >
              {l.label}
              {l.sub ? (
                <>
                  <br />
                  {l.sub}
                </>
              ) : null}
            </div>
          ))}
        </div>

        <div className="ed-out">
          <div className="ed-out-head">
            <b>SIGNS 좌표</b>
            <button onClick={() => navigator.clipboard?.writeText(code)}>복사</button>
          </div>
          <textarea readOnly value={code} rows={12} />
          <div className="ed-out-head">
            <b>LABEL 값</b>
            <button onClick={() => navigator.clipboard?.writeText(styleCode)}>복사</button>
          </div>
          <textarea readOnly value={styleCode} rows={9} />
          <p className="ed-hint">
            위는 <code>src/components/mapWaypoints.ts</code> 의 <code>SIGNS</code> 안쪽,
            아래는 <code>src/components/HospitalMapPhoto.tsx</code> 의 <code>LABEL</code>{' '}
            안쪽에 붙여 넣으면 실제 화면에 반영됩니다.
          </p>
        </div>
      </div>

      <aside className="ed-side">
        <h3>글자</h3>
        {num('크기', 'sizePct', 0.6, 3, 0.05)}
        {num('부 안내 비율', 'minorScale', 0.4, 1, 0.02)}
        {num('굵기', 'weight', 300, 900, 100)}
        {num('자간', 'letterSpacing', -0.08, 0.1, 0.005)}
        {num('위아래 밀기', 'offsetYPct', -6, 6, 0.1)}
        <label className="ed-row">
          <span>글자 색</span>
          <input
            type="color"
            value={st.color}
            onChange={(e) => setSt({ ...st, color: e.target.value })}
          />
          <b>{st.color}</b>
        </label>

        <h3>판</h3>
        <label className="ed-row">
          <span>판 쓰기</span>
          <input
            type="checkbox"
            checked={st.plate}
            onChange={(e) => setSt({ ...st, plate: e.target.checked })}
          />
          <b>{st.plate ? '켬' : '끔'}</b>
        </label>
        <label className="ed-row">
          <span>판 색</span>
          <input
            type="color"
            value={st.plateColor}
            onChange={(e) => setSt({ ...st, plateColor: e.target.value })}
          />
          <b>{st.plateColor}</b>
        </label>
        {num('판 투명도', 'plateOpacity', 0, 1, 0.02)}
        {num('모서리', 'radius', 0, 1.2, 0.02)}
        <label className="ed-row">
          <span>판 그림자</span>
          <input
            type="checkbox"
            checked={st.shadow}
            onChange={(e) => setSt({ ...st, shadow: e.target.checked })}
          />
          <b>{st.shadow ? '켬' : '끔'}</b>
        </label>

        <h3>정렬</h3>
        <p className="ed-note">
          Shift 를 누른 채 눌러 여러 개를 고른 뒤 누르세요.
          {sel.length > 1 ? ` (${sel.length}개 고름)` : ' (2개 이상 필요)'}
        </p>
        <div className="ed-btns">
          <button disabled={sel.length < 2} onClick={() => align('screen-y')}>
            화면에서 가로 맞추기
          </button>
          <button disabled={sel.length < 2} onClick={() => align('room-y')}>
            방에서 가로 맞추기
          </button>
          <button disabled={sel.length < 2} onClick={() => align('room-x')}>
            세로 맞추기
          </button>
          <button disabled={sel.length < 3} onClick={() => align('spread-x')}>
            가로 간격 고르게
          </button>
        </div>

        <button className="ed-save" onClick={download}>
          값 파일로 내려받기
        </button>

        <button className="ed-reset" onClick={() => { setSigns(SIGNS); setSt(START); setSel([]) }}>
          처음으로 되돌리기
        </button>
      </aside>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<Editor />)
