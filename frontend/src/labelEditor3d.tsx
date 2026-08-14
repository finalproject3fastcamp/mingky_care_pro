/**
 * 3D 지도 위에서 안내 글자 자리를 잡는 화면.
 *
 * 글자를 끌어다 놓으면 그 자리의 실측 좌표(u, v)가 나온다. 화면을 돌려도
 * 좌표는 그대로다 — 끌 때 글자가 떠 있는 **높이의 수평면**에서 자리를 찾기
 * 때문이다.
 *
 * ## 왜 손으로 맞추나
 *
 * 안내판이 붙을 자리는 규칙으로 정할 수 없다. 카운터 상판 가운데, 벽 위,
 * 문 옆 — 사람이 보고 정하는 편이 빠르고 정확하다. 대신 눈으로 픽셀을 맞추는
 * 일은 힘드니 정렬 단추를 뒀다.
 *
 * ## 값을 어떻게 가져오나
 *
 * 아래 상자의 코드를 복사하거나 **파일로 내려받아** mapWaypoints.ts 의
 * SIGNS 를 통째로 갈아 끼우면 된다.
 *
 * `npm run dev` 후 /label-editor-3d.html 로 연다.
 */

import { useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { HospitalMap3D } from './components/HospitalMap3D'
import { SIGNS, type Sign } from './components/mapWaypoints'
import './index.css'
import './App.css'
import './labelEditor.css'
import './labelEditor3d.css'

/** 목록에서 고른 것들에만 적용한다. 하나만 골랐으면 할 일이 없다. */
type Align = 'v' | 'u' | 'y' | 'spread-u'

const ALIGN_LABEL: Record<Align, string> = {
  v: '가로줄 맞추기',
  u: '세로줄 맞추기',
  y: '높이 맞추기',
  'spread-u': '가로로 고르게',
}

function App() {
  const [signs, setSigns] = useState<Sign[]>(() => SIGNS.map((s) => ({ ...s })))
  const [picked, setPicked] = useState<number[]>([])

  const move = (i: number, u: number, v: number) =>
    setSigns((prev) =>
      prev.map((s, k) => (k === i ? { ...s, u: round(u), v: round(v) } : s)),
    )

  const set = (i: number, key: 'u' | 'v' | 'y', value: number) =>
    setSigns((prev) => prev.map((s, k) => (k === i ? { ...s, [key]: round(value) } : s)))

  const pick = (i: number, add: boolean) =>
    setPicked((prev) =>
      add ? (prev.includes(i) ? prev.filter((k) => k !== i) : [...prev, i]) : [i],
    )

  const align = (how: Align) => {
    if (picked.length < 2) return
    setSigns((prev) => {
      const next = prev.map((s) => ({ ...s }))
      const chosen = picked.map((i) => next[i])
      if (how === 'spread-u') {
        const sorted = [...chosen].sort((a, b) => a.u - b.u)
        const lo = sorted[0].u
        const hi = sorted[sorted.length - 1].u
        sorted.forEach((s, k) => {
          s.u = round(lo + ((hi - lo) * k) / (sorted.length - 1))
        })
        return next
      }
      // 나머지는 평균으로 모은다 — 하나를 기준 삼으면 어느 것이 기준인지
      // 헷갈리고, 평균은 전체가 조금씩만 움직여 원래 배치가 덜 흐트러진다.
      const key = how
      const mean = chosen.reduce((a, s) => a + (s[key] ?? 0.22), 0) / chosen.length
      for (const s of chosen) s[key] = round(mean)
      return next
    })
  }

  const code = useMemo(
    () =>
      'export const SIGNS: Sign[] = [\n' +
      signs
        .map(
          (s) =>
            `  { label: '${s.label}',` +
            (s.sub ? ` sub: '${s.sub}',` : '') +
            ` icon: '${s.icon}', u: ${s.u}, v: ${s.v},` +
            ` w: ${s.w}, h: ${s.h}, y: ${s.y}, rank: ${s.rank} },`,
        )
        .join('\n') +
      '\n]\n',
    [signs],
  )

  const download = () => {
    const url = URL.createObjectURL(new Blob([code], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'signs-3d.txt'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="ed">
      <div className="ed-main">
        <h2>안내 글자 자리 잡기 (3D)</h2>
        <p className="ed-hint">
          글자를 끌어 옮기세요. 빈 곳을 끌면 화면이 돌아갑니다. Shift 를 누른 채
          누르면 여러 개를 고를 수 있고, 그때만 정렬 단추가 듣습니다.
          {picked.length > 1 && ` — ${picked.length}개 고름`}
        </p>

        <HospitalMap3D
          pose={null}
          live={false}
          scan={null}
          particles={null}
          plan={null}
          signs={signs}
          editing
          onSignMove={move}
          picked={picked}
          onSignPick={pick}
        />

        <div className="ed-out">
          <div className="ed-out-head">
            <b>mapWaypoints.ts 의 SIGNS 를 이걸로 바꾸세요</b>
            <button onClick={() => navigator.clipboard?.writeText(code)}>복사</button>
            <button onClick={download}>값 파일로 내려받기</button>
          </div>
          <textarea readOnly value={code} rows={14} />
        </div>
      </div>

      <aside className="ed-side">
        <h3>정렬</h3>
        <p className="ed-note">
          Shift 로 두 개 이상 고른 뒤 누르세요. 고른 것들의 평균 자리로 모읍니다.
        </p>
        <div className="ed-btns">
          {(Object.keys(ALIGN_LABEL) as Align[]).map((k) => (
            <button key={k} disabled={picked.length < 2} onClick={() => align(k)}>
              {ALIGN_LABEL[k]}
            </button>
          ))}
        </div>

        <h3>값 직접 넣기</h3>
        <p className="ed-note">u 는 가로, v 는 세로, y 는 바닥에서의 높이입니다(m).</p>
        <div className="ed3-list">
          {signs.map((s, i) => (
            <div
              key={s.label + i}
              className={'ed3-row' + (picked.includes(i) ? ' is-picked' : '')}
              onClick={(e) => pick(i, e.shiftKey)}
            >
              <span className="ed3-name">{s.label}</span>
              {(['u', 'v', 'y'] as const).map((key) => (
                <label key={key}>
                  {key}
                  <input
                    type="number"
                    step={0.01}
                    value={s[key] ?? 0.22}
                    onChange={(e) => set(i, key, Number(e.target.value))}
                  />
                </label>
              ))}
            </div>
          ))}
        </div>

        <div className="ed-btns">
          <button
            onClick={() => {
              setSigns(SIGNS.map((s) => ({ ...s })))
              setPicked([])
            }}
          >
            전부 처음으로
          </button>
        </div>
      </aside>
    </div>
  )
}

/** 소수점 셋째 자리까지. 그 아래는 1 mm 미만이라 의미가 없다. */
const round = (v: number) => Math.round(v * 1000) / 1000

createRoot(document.getElementById('root')!).render(<App />)
