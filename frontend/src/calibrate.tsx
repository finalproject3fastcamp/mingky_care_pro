/**
 * 새 렌더에 좌표를 다시 맞추는 화면.
 *
 * 배경 렌더를 다른 시점으로 다시 뽑으면 기존 변환은 무효가 된다. 바닥 ㄷ자
 * 표식의 실측 좌표는 알고 있으므로, 그 표식이 새 그림에서 **어디에 찍혔는지**만
 * 알면 변환을 다시 구할 수 있다.
 *
 * 표식을 자동으로 찾아보려 했지만 렌더 톤에 따라 번번이 실패했다. 사람이 여섯
 * 번 누르는 편이 빠르고 확실하다.
 *
 * `npm run dev` 후 /calibrate.html 로 연다.
 */

import { useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'

import GUIDE from './markerGuide.json'
import './index.css'
import './labelEditor.css'

const IMG_SRC = '/hospital-render.png'

type Pick = { n: number; u: number; v: number; x: number; y: number }

/** 여덟 개 값을 최소제곱으로 푼다. 네 쌍이면 풀리고, 많을수록 안정된다. */
function solveH(ps: Pick[]) {
  const A: number[][] = []
  const b: number[] = []
  for (const p of ps) {
    A.push([p.u, p.v, 1, 0, 0, 0, -p.u * p.x, -p.v * p.x])
    b.push(p.x)
    A.push([0, 0, 0, p.u, p.v, 1, -p.u * p.y, -p.v * p.y])
    b.push(p.y)
  }
  // 정규방정식 AtA h = Atb 를 가우스 소거로 푼다.
  const n = 8
  const M: number[][] = Array.from({ length: n }, () => Array(n + 1).fill(0))
  for (let i = 0; i < n; i += 1) {
    for (let j = 0; j < n; j += 1) {
      let s = 0
      for (let k = 0; k < A.length; k += 1) s += A[k][i] * A[k][j]
      M[i][j] = s
    }
    let s = 0
    for (let k = 0; k < A.length; k += 1) s += A[k][i] * b[k]
    M[i][n] = s
  }
  for (let c = 0; c < n; c += 1) {
    let piv = c
    for (let r = c + 1; r < n; r += 1) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r
    ;[M[c], M[piv]] = [M[piv], M[c]]
    if (Math.abs(M[c][c]) < 1e-12) return null
    for (let r = 0; r < n; r += 1) {
      if (r === c) continue
      const f = M[r][c] / M[c][c]
      for (let k = c; k <= n; k += 1) M[r][k] -= f * M[c][k]
    }
  }
  const hv = M.map((row, i) => row[n] / M[i][i])
  return [
    [hv[0], hv[1], hv[2]],
    [hv[3], hv[4], hv[5]],
    [hv[6], hv[7], 1],
  ]
}

function App() {
  const [picks, setPicks] = useState<Pick[]>([])
  const [size, setSize] = useState({ w: 0, h: 0 })
  const next = GUIDE.find((g) => !picks.some((p) => p.n === g.n))

  const click = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!next) return
    const r = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * size.w
    const y = ((e.clientY - r.top) / r.height) * size.h
    setPicks([...picks, { n: next.n, u: next.u, v: next.v, x, y }])
  }

  const H = useMemo(() => (picks.length >= 4 ? solveH(picks) : null), [picks])

  const resid = useMemo(() => {
    if (!H) return null
    const e = picks.map((p) => {
      const w = H[2][0] * p.u + H[2][1] * p.v + 1
      const px = (H[0][0] * p.u + H[0][1] * p.v + H[0][2]) / w
      const py = (H[1][0] * p.u + H[1][1] * p.v + H[1][2]) / w
      return Math.hypot(px - p.x, py - p.y)
    })
    return { mean: e.reduce((a, b) => a + b, 0) / e.length, max: Math.max(...e) }
  }, [H, picks])

  const code = H
    ? `export const IMG = { w: ${size.w}, h: ${size.h} }\n\nconst H = [\n` +
      H.map((r) => '  [' + r.map((v) => v.toFixed(6)).join(', ') + '],').join('\n') +
      '\n]'
    : ''

  return (
    <div className="ed">
      <div className="ed-main">
        <h2>좌표 다시 맞추기</h2>
        <p className="ed-hint">
          {next
            ? `아래 그림에서 ${next.n}번 표식을 누르세요. (${picks.length}/${GUIDE.length})`
            : `다 눌렀습니다. ${picks.length}개로 맞췄습니다.`}
          {resid && ` — 남는 오차 평균 ${resid.mean.toFixed(1)}px · 최대 ${resid.max.toFixed(1)}px`}
        </p>

        <img
          src="/marker-guide.png"
          alt="표식 번호"
          style={{ width: '100%', maxWidth: 620, display: 'block', marginBottom: 14 }}
        />

        <div className="ed-map" style={{ aspectRatio: size.w ? `${size.w} / ${size.h}` : '4 / 3' }}>
          <img
            src={IMG_SRC}
            alt="새 렌더"
            onClick={click}
            onLoad={(e) =>
              setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
            }
            style={{ cursor: next ? 'crosshair' : 'default' }}
          />
          {picks.map((p) => (
            <div
              key={p.n}
              className="ed-label is-picked"
              style={{
                left: `${(p.x / size.w) * 100}%`,
                top: `${(p.y / size.h) * 100}%`,
                background: '#2563eb',
                color: '#fff',
                fontSize: '1.6cqw',
                fontWeight: 700,
                borderRadius: '50%',
                padding: '0.25em 0.55em',
                cursor: 'default',
              }}
            >
              {p.n}
            </div>
          ))}
        </div>

        <div className="ed-out">
          <div className="ed-out-head">
            <b>HospitalMapPhoto.tsx 에 넣을 값</b>
            <button disabled={!H} onClick={() => navigator.clipboard?.writeText(code)}>
              복사
            </button>
          </div>
          <textarea readOnly value={code} rows={8} />
        </div>
      </div>

      <aside className="ed-side">
        <h3>순서</h3>
        <p className="ed-note">
          위 번호 그림과 아래 렌더를 번갈아 보며, 같은 자리의 ㄷ자 표식을 번호 순서대로
          누르면 됩니다. 최소 4개, 많을수록 정확합니다. 건물 구석구석에 흩어진 것부터
          누르는 편이 좋습니다.
        </p>
        <div className="ed-btns">
          <button disabled={!picks.length} onClick={() => setPicks(picks.slice(0, -1))}>
            마지막 하나 취소
          </button>
          <button disabled={!picks.length} onClick={() => setPicks([])}>
            전부 지우기
          </button>
        </div>
        <h3>진행</h3>
        <p className="ed-note">
          {picks.map((p) => `${p.n}`).join(', ') || '아직 없음'}
        </p>
      </aside>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
