/**
 * 라이노 렌더를 그대로 지도로 쓰는 화면.
 *
 * 브라우저가 3D 를 다시 그리면 라이노가 시간을 들여 계산한 빛과 그림자를
 * 따라갈 수 없다. 그래서 렌더 결과를 **그림 그대로** 깔고, 그 위에 글자와
 * 로봇만 얹는다. 보이는 품질은 라이노 그대로가 된다.
 *
 * ## 좌표를 어떻게 맞추나
 *
 * 로봇은 바닥 위를 움직인다. 바닥은 평면이므로, 평면 위의 점을 사진 속 점으로
 * 옮기는 변환(호모그래피) 하나면 원근이 있어도 정확히 맞출 수 있다.
 *
 * 그 변환은 바닥의 ㄷ자 표식으로 구했다. 표식의 실측 좌표는 3D 모델에서,
 * 사진 속 위치는 렌더 이미지에서 뽑아 7쌍을 맞췄다. **남는 오차는 평균 0.3
 * 픽셀**이다(이미지 1734x990 기준).
 *
 * ## 렌더를 다시 뽑을 때
 *
 * **카메라를 조금이라도 움직이면 이 변환은 전부 무효**다. 라이노에서 시점을
 * 저장해 두고 항상 같은 시점으로 내보내야 한다. 시점이 바뀌면 표식 위치를
 * 다시 찍어 변환을 다시 구해야 한다.
 */

import { useMemo } from 'react'

import { SIGNS } from './mapWaypoints'
import './HospitalMapPhoto.css'

/** 렌더 이미지의 원래 크기(px). 이 값 기준으로 좌표를 잡고 화면에서는 비율로 쓴다. */
export const IMG = { w: 1734, h: 990 }

/**
 * 실측 모델 바닥(u, v) → 렌더 이미지 픽셀. 표식 7쌍으로 맞춘 값이다.
 * 렌더를 다른 시점으로 다시 뽑으면 이 값도 다시 구해야 한다.
 */
const H = [
  [486.44381, 111.306962, 93.23002],
  [0.350299, -313.904769, 869.645351],
  [0.000293, 0.128002, 1.0],
]

/** 지도(로봇) 좌표 → 실측 모델 좌표. HospitalMap3D 의 것과 같은 값이다. */
const FIT = { rotationDeg: 12.7, x: -0.094, y: -0.368, scale: 0.965 }

/**
 * 여기 값만 바꾸면 글자 모양이 바뀐다. 저장하면 브라우저가 바로 다시 그린다.
 *
 * 글자는 3D 가 아니라 보통 HTML 이라, CSS 로 할 수 있는 것은 전부 된다
 * (그림자, 외곽선, 그라데이션, 자간, 회전 …).
 */
export const LABEL = {
  /** 글꼴. 따옴표 포함해서 그대로 CSS 로 들어간다 */
  fontFamily:
    '"Pretendard Variable", Pretendard, -apple-system, "Noto Sans KR", sans-serif',
  /** 글자 크기. 이미지 가로폭에 대한 비율(%)이라 화면이 커져도 같이 커진다 */
  sizePct: 1.45,
  /** 부 안내(화장실·비상구)는 이 비율만큼 작게 */
  minorScale: 0.78,
  /** 굵기 100~900 */
  weight: 700,
  /** 글자 색 */
  color: '#1f4f7a',
  /** 자간(em) */
  letterSpacing: -0.02,
  /** 줄 간격 */
  lineHeight: 1.15,
  /** 외곽선 두께(px). 0 이면 없음 */
  strokeWidth: 0,
  strokeColor: '#ffffff',
  /** 그림자. CSS text-shadow 를 그대로 쓴다. 빈 문자열이면 없음 */
  textShadow: '0 1px 2px rgba(20,40,60,0.18)',
  /** 판(배경). 없애려면 background 를 'transparent' 로 */
  background: 'rgba(255, 255, 255, 0.68)',
  padding: '0.18em 0.62em',
  borderRadius: '0.18em',
  border: '0 solid transparent',
  /** 판 그림자 */
  boxShadow: '0 2px 6px rgba(20,40,60,0.10)',
  /** 위아래로 밀기(이미지 높이 대비 %). 음수면 위로 */
  offsetYPct: 0,
} as const

/** 로봇 표시. 이미지 가로폭 대비 비율이라 화면 크기를 따라간다. */
export const ROBOT = {
  /** 몸통 지름(%) */
  sizePct: 2.6,
  /** 바닥 고리 지름(%). 평소에는 안 보이고 선택·비상정지에만 나온다 */
  ringPct: 5.2,
  color: '#2563eb',
  selectedColor: '#22c55e',
  estopColor: '#ef4444',
} as const

export interface Pose {
  x: number
  y: number
  yaw: number
}

interface Props {
  pose: Pose | null
  selected?: boolean
  estop?: boolean
}

/** 지도 좌표를 실측 모델 좌표로. 모델을 찌그러뜨리는 대신 좌표를 옮긴다. */
function mapToModel(mx: number, my: number) {
  const a = (FIT.rotationDeg * Math.PI) / 180
  const px = (mx - FIT.x) / FIT.scale
  const py = (my - FIT.y) / FIT.scale
  return {
    u: px * Math.cos(a) + py * Math.sin(a),
    v: -px * Math.sin(a) + py * Math.cos(a),
  }
}

/** 모델 바닥 좌표를 이미지 안 백분율 위치로. */
export function toPct(u: number, v: number) {
  const w = H[2][0] * u + H[2][1] * v + H[2][2]
  const x = (H[0][0] * u + H[0][1] * v + H[0][2]) / w
  const y = (H[1][0] * u + H[1][1] * v + H[1][2]) / w
  return { left: (x / IMG.w) * 100, top: (y / IMG.h) * 100 }
}

/**
 * 이미지 안 백분율 위치를 모델 바닥 좌표로. 편집 화면에서 끌어다 놓은 자리를
 * 다시 좌표로 되돌릴 때 쓴다(toPct 의 역).
 */
export function fromPct(leftPct: number, topPct: number) {
  const x = (leftPct / 100) * IMG.w
  const y = (topPct / 100) * IMG.h
  // [a b c; d e f; g h 1] 를 (u, v) 에 대해 푼다.
  const [[a, b, c], [d, e, f], [g, h]] = H
  const A = [
    [a - g * x, b - h * x],
    [d - g * y, e - h * y],
  ]
  const B = [x - c, y - f]
  const det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
  return {
    u: (B[0] * A[1][1] - A[0][1] * B[1]) / det,
    v: (A[0][0] * B[1] - B[0] * A[1][0]) / det,
  }
}

export function HospitalMapPhoto({ pose, selected = false, estop = false }: Props) {
  const labels = useMemo(
    () =>
      SIGNS.map((s) => {
        const p = toPct(s.u, s.v)
        return { ...s, ...p }
      }),
    [],
  )

  const robot = pose
    ? (() => {
        const m = mapToModel(pose.x, pose.y)
        const p = toPct(m.u, m.v)
        // 지도 기준 yaw 를 화면 회전으로. 지도가 12.7도 돌아간 만큼 되돌리고,
        // 화면은 y 가 아래로 커지므로 부호를 뒤집는다.
        const deg = -((pose.yaw * 180) / Math.PI - FIT.rotationDeg)
        return { ...p, deg }
      })()
    : null

  const stroke =
    LABEL.strokeWidth > 0
      ? {
          WebkitTextStrokeWidth: `${LABEL.strokeWidth}px`,
          WebkitTextStrokeColor: LABEL.strokeColor,
        }
      : {}

  return (
    <div className="map-photo" style={{ aspectRatio: `${IMG.w} / ${IMG.h}` }}>
      <img className="map-photo__img" src="/hospital-render.png" alt="병원 평면" />

      {labels.map((l) => (
        <div
          key={l.label + l.u}
          className="map-photo__label"
          style={{
            left: `${l.left}%`,
            top: `${l.top + LABEL.offsetYPct}%`,
            fontFamily: LABEL.fontFamily,
            fontSize: `${LABEL.sizePct * (l.rank === 1 ? 1 : LABEL.minorScale)}cqw`,
            fontWeight: LABEL.weight,
            color: LABEL.color,
            letterSpacing: `${LABEL.letterSpacing}em`,
            lineHeight: LABEL.lineHeight,
            textShadow: LABEL.textShadow,
            background: LABEL.background,
            padding: LABEL.padding,
            borderRadius: LABEL.borderRadius,
            border: LABEL.border,
            boxShadow: LABEL.boxShadow,
            ...stroke,
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

      {robot ? (
        <div
          className="map-photo__robot"
          style={{ left: `${robot.left}%`, top: `${robot.top}%` }}
        >
          <span
            className={
              'map-photo__ring' +
              (estop ? ' is-estop' : selected ? ' is-selected' : '')
            }
            style={{
              width: `${ROBOT.ringPct}cqw`,
              height: `${ROBOT.ringPct}cqw`,
              // 선택과 비상정지가 같은 방식으로 퍼진다. 색으로만 가른다.
              // 점보다 먼저 그려져 뒤에 깔린다.
              borderColor: estop ? ROBOT.estopColor : ROBOT.selectedColor,
              background: 'transparent',
            }}
          />
          <span
            className="map-photo__body"
            style={{
              width: `${ROBOT.sizePct}cqw`,
              height: `${ROBOT.sizePct}cqw`,
              background: estop ? ROBOT.estopColor : ROBOT.color,
              transform: `translate(-50%,-50%) rotate(${robot.deg}deg)`,
            }}
          />
        </div>
      ) : null}
    </div>
  )
}
