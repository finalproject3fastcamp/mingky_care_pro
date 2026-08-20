/**
 * 지도 위에 떠 있는 카메라 창.
 *
 * 지도는 위, 카메라는 아래라 둘을 같이 보려면 화면을 굴려야 했다. 시연
 * 중에 스크롤하면 보는 사람의 흐름이 끊긴다. 그래서 지도 위에 얹고 원하는
 * 자리로 옮길 수 있게 했다.
 *
 * 보는 사람이 제일 막히는 지점은 "왜 갑자기 노란색이 됐지" 다. 지도만으로는
 * **무슨 일이 일어났는지**밖에 못 보여 준다. 카메라에서 환자가 사라지는 것을
 * 같이 보여 줘야 **왜 그렇게 됐는지**가 이어진다.
 *
 * 옮길 수 있게 만들면서 막아 둔 것들 — 안 막으면 반드시 사고가 난다:
 *
 * | 무엇 | 왜 |
 * | --- | --- |
 * | 가로 절반까지만 | 더 키우면 지도를 덮는다. 상태 보려고 만든 화면이다 |
 * | 4:3 고정 | 자유롭게 늘리면 영상이 찌그러져 QR 네모도 일그러진다 |
 * | 지도 밖으로 못 나감 | 넘기면 되찾을 방법이 없다 |
 * | 자리 저장 | 시연장에서 한 번 맞춰 두면 새로고침해도 그대로다 |
 * | 방향키로도 이동 | 끌기만 되는 창은 키보드만 쓰는 사람에게 안 열린다 |
 *
 * 끄는 게 아니라 **접는다.** 껐다 켜면 영상이 다시 붙는 데 몇 초가 걸리고,
 * 그 몇 초가 시연에서는 길다.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

interface Props {
  robotId: string
  /**
   * 어느 카메라를 보여줄지. **화면 단계를 따라간다.**
   *
   * QR 을 대는 동안은 앞쪽을, 안내가 시작되면 뒤쪽을 본다. 원래 두 화면이
   * 따로 하던 일을 이 창 하나가 이어받는다 — 영상이 두 곳에 뜨면 보는 사람이
   * 어느 쪽을 봐야 하는지 알 수 없다.
   */
  facing: 'front' | 'rear'
  /** 지금 추종 상태. 제목줄 점 색으로 쓴다 */
  tone: string
  /** `follow_source` 를 사람 말로 옮긴 것 */
  label: string
}

interface Placed {
  x: number
  y: number
  w: number
  folded: boolean
}

const STORE_KEY = 'map3d.rearcam'
const MIN_W = 116

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v))
}

export function MapRearCam({ robotId, facing, tone, label }: Props) {
  const boxRef = useRef<HTMLDivElement | null>(null)
  const [place, setPlace] = useState<Placed | null>(null)
  const [dragging, setDragging] = useState(false)
  const [streamKey, setStreamKey] = useState(0)
  const [failed, setFailed] = useState(false)

  /** 지도 영역. 창은 이 안을 벗어나지 못한다 */
  const stage = () => boxRef.current?.parentElement?.getBoundingClientRect() ?? null

  const fit = useCallback((next: Placed): Placed => {
    const s = stage()
    const el = boxRef.current
    if (!s || !el) return next
    // 지도를 다 덮으면 상태를 못 본다. 절반까지만.
    const w = clamp(next.w, MIN_W, Math.max(MIN_W, s.width * 0.5))
    const h = el.offsetHeight
    return {
      ...next,
      w,
      x: clamp(next.x, 0, Math.max(0, s.width - w)),
      y: clamp(next.y, 0, Math.max(0, s.height - h)),
    }
  }, [])

  const put = useCallback(
    (next: Placed) => {
      const fixed = fit(next)
      setPlace(fixed)
      try {
        localStorage.setItem(STORE_KEY, JSON.stringify(fixed))
      } catch {
        // 저장을 못 해도 화면은 그대로 돈다.
      }
    },
    [fit],
  )

  const reset = useCallback(() => {
    const s = stage()
    if (!s) return
    const w = clamp(s.width * 0.22, MIN_W, s.width * 0.5)
    // 오른쪽 아래. 로봇은 대개 가운데에 있어서 이 자리가 제일 덜 가린다.
    put({ x: s.width - w - 10, y: s.height - (w * 3) / 4 - 34, w, folded: false })
  }, [put])

  // 처음 한 번. 지도 크기를 알아야 자리를 잡을 수 있어 그림이 그려진 뒤에 한다.
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      let saved: Placed | null = null
      try {
        saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? 'null')
      } catch {
        saved = null
      }
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y) && Number.isFinite(saved.w)) {
        setPlace(fit(saved))
      } else {
        reset()
      }
    })
    return () => cancelAnimationFrame(id)
  }, [fit, reset])

  // 지도가 커지거나 줄면 창이 밖으로 나갈 수 있다.
  useEffect(() => {
    const el = boxRef.current?.parentElement
    if (!el) return
    const ro = new ResizeObserver(() => setPlace((p) => (p ? fit(p) : p)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [fit])

  // 카메라가 바뀌면 실패 표시를 지운다. 앞쪽 송출이 끝났다고(arming 소비)
  // 뒤쪽까지 '불러오지 못했습니다' 로 남아 있으면 안 된다.
  useEffect(() => {
    setFailed(false)
  }, [facing])

  const drag = useRef<{ dx: number; dy: number } | null>(null)
  const grow = useRef<{ x: number; w: number } | null>(null)

  if (!place) {
    return <div ref={boxRef} className="map3d__cam" style={{ visibility: 'hidden' }} />
  }

  const move = (dx: number, dy: number) => put({ ...place, x: place.x + dx, y: place.y + dy })

  return (
    <div
      ref={boxRef}
      className={`map3d__cam${dragging ? ' is-dragging' : ''}${place.folded ? ' is-folded' : ''}`}
      style={{ left: place.x, top: place.y, width: place.w }}
    >
      <div
        className="map3d__cam-bar"
        role="button"
        tabIndex={0}
        aria-label="뒤쪽 카메라 창. 끌어서 옮기고, 방향키로 미세 조정, 엔터로 접기"
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest('button')) return
          const r = e.currentTarget.parentElement!.getBoundingClientRect()
          drag.current = { dx: e.clientX - r.left, dy: e.clientY - r.top }
          setDragging(true)
          e.currentTarget.setPointerCapture(e.pointerId)
          e.preventDefault()
        }}
        onPointerMove={(e) => {
          const d = drag.current
          const s = stage()
          if (!d || !s) return
          put({ ...place, x: e.clientX - s.left - d.dx, y: e.clientY - s.top - d.dy })
        }}
        onPointerUp={() => {
          drag.current = null
          setDragging(false)
        }}
        onPointerCancel={() => {
          drag.current = null
          setDragging(false)
        }}
        onKeyDown={(e) => {
          const step = e.shiftKey ? 1 : 8
          if (e.key === 'ArrowLeft') move(-step, 0)
          else if (e.key === 'ArrowRight') move(step, 0)
          else if (e.key === 'ArrowUp') move(0, -step)
          else if (e.key === 'ArrowDown') move(0, step)
          else if (e.key === 'Enter' || e.key === ' ') put({ ...place, folded: !place.folded })
          else return
          e.preventDefault()
        }}
      >
        <span className="map3d__cam-grab" aria-hidden="true" />
        <span className="map3d__cam-title">{facing === 'front' ? '앞쪽 카메라' : '뒤쪽 카메라'}</span>
        <span className={`map3d__cam-src map3d__cam-src--${tone}`}>{label}</span>
        <button
          type="button"
          className="map3d__cam-btn"
          aria-label={place.folded ? '펴기' : '접기'}
          onClick={() => put({ ...place, folded: !place.folded })}
        >
          {place.folded ? '▫' : '–'}
        </button>
      </div>

      {!place.folded &&
        (failed ? (
          <div className="map3d__cam-fail">
            <span>영상을 불러오지 못했습니다.</span>
            <button
              type="button"
              onClick={() => {
                setFailed(false)
                setStreamKey((v) => v + 1)
              }}
            >
              다시 연결
            </button>
          </div>
        ) : (
          <img
            key={`${facing}-${streamKey}`}
            className="map3d__cam-view"
            src={`/camera/${encodeURIComponent(robotId)}/${facing}/stream`}
            alt={facing === 'front' ? '로봇 앞쪽 카메라' : '로봇 뒤쪽 카메라'}
            onError={() => setFailed(true)}
          />
        ))}

      {!place.folded && (
        <span
          className="map3d__cam-grip"
          aria-hidden="true"
          onPointerDown={(e) => {
            grow.current = { x: e.clientX, w: place.w }
            e.currentTarget.setPointerCapture(e.pointerId)
            e.preventDefault()
            e.stopPropagation()
          }}
          onPointerMove={(e) => {
            const g = grow.current
            if (!g) return
            // 가로만 잡고 세로는 따라온다. 자유롭게 늘리면 영상이 찌그러진다.
            put({ ...place, w: g.w + (e.clientX - g.x) })
          }}
          onPointerUp={() => {
            grow.current = null
          }}
        />
      )}
    </div>
  )
}
