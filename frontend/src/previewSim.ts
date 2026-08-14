/**
 * 미리보기용 가짜 로봇 신호.
 *
 * 대시보드는 실제 로봇이 붙어 있어야 라이다·경로가 뜬다. 그런데 아무 값이나
 * 넣으면 **화면이 제대로 그려졌는지 판단할 수가 없다** — 벽을 뚫고 지나가는
 * 경로를 보고 "그리기가 틀렸나, 값이 틀렸나" 를 가릴 수 없기 때문이다.
 *
 * 그래서 여기서는 실제 벽(previewWalls.json, 3D 모형에서 뽑은 상자 35개)에
 * 부딪히는 라이다와, 벽을 피해 도는 경로를 만든다. 화면이 맞다면 RViz 에서
 * 보던 것과 같은 모양이 나와야 한다.
 *
 * **이 파일은 미리보기 전용이다.** 대시보드는 여기를 쓰지 않는다.
 */

import { FIT, mapToModel, mapYawToModel, modelToMap } from './components/mapFrame'
import WALLS from './previewWalls.json'

/**
 * 벽 격자. 3D 모형에서 높이 5 cm 이상인 면을 전부 바닥에 눌러 찍은 것이다
 * (1.5 cm 칸). 바깥 벽이 바닥과 한 덩어리로 들어 있어서 상자 목록으로는
 * 잡히지 않기에, 삼각형을 직접 찍어 만들었다.
 */
const { cell: CELL_W, w: GW, h: GH } = WALLS
const WALL_BITS = (() => {
  const bin = atob(WALLS.bits)
  const bits = new Uint8Array(GW * GH)
  for (let i = 0; i < bits.length; i += 1) {
    bits[i] = (bin.charCodeAt(i >> 3) >> (7 - (i & 7))) & 1
  }
  return bits
})()

const solid = (i: number, j: number) =>
  i < 0 || j < 0 || i >= GW || j >= GH ? true : WALL_BITS[j * GW + i] === 1

/**
 * 이 자리가 벽 속인가. 벽 속이면 라이다가 전부 거리 0 이 되어 화면이 텅
 * 비는데, 그게 그리기가 틀린 것처럼 보이므로 미리 알려 준다.
 */
export function insideWall(x: number, y: number): boolean {
  const m = mapToModel(x, y)
  return solid(Math.floor(m.u / CELL_W), Math.floor(m.v / CELL_W))
}

/** 라이다가 닿는 거리(m). 건물이 3.4 m 라 이보다 멀 일이 없다. */
const MAX_RANGE = 4

/**
 * 광선을 칸 단위로 따라가며 처음 만나는 벽까지의 거리. 안 만나면 Infinity.
 *
 * 칸을 하나씩 건너뛰는 방식(DDA)이라 벽 두께가 한 칸이어도 새지 않는다.
 */
function rayHit(u: number, v: number, du: number, dv: number): number {
  let i = Math.floor(u / CELL_W)
  let j = Math.floor(v / CELL_W)
  if (solid(i, j)) return 0

  const si = du > 0 ? 1 : -1
  const sj = dv > 0 ? 1 : -1
  const dti = Math.abs(du) < 1e-12 ? Infinity : Math.abs(CELL_W / du)
  const dtj = Math.abs(dv) < 1e-12 ? Infinity : Math.abs(CELL_W / dv)
  // 다음 칸 경계까지 남은 거리
  let ti =
    dti === Infinity
      ? Infinity
      : Math.abs(((du > 0 ? (i + 1) * CELL_W : i * CELL_W) - u) / du)
  let tj =
    dtj === Infinity
      ? Infinity
      : Math.abs(((dv > 0 ? (j + 1) * CELL_W : j * CELL_W) - v) / dv)

  for (let step = 0; step < 4000; step += 1) {
    const t = Math.min(ti, tj)
    if (t > MAX_RANGE) return Infinity
    if (ti < tj) {
      i += si
      ti += dti
    } else {
      j += sj
      tj += dtj
    }
    if (i < 0 || j < 0 || i >= GW || j >= GH) return Infinity
    if (WALL_BITS[j * GW + i] === 1) return t
  }
  return Infinity
}

/**
 * 라이다 한 바퀴. 결과는 브리지가 보내는 것과 같은 모양이다 —
 * 로봇 기준 [각도(rad), 거리(m)].
 */
export function simulateScan(x: number, y: number, yaw: number, rays = 360): number[][] {
  const o = mapToModel(x, y)
  const out: number[][] = []
  for (let i = 0; i < rays; i += 1) {
    const a = (i / rays) * Math.PI * 2 - Math.PI
    const m = mapYawToModel(yaw + a)
    const t = rayHit(o.u, o.v, Math.cos(m), Math.sin(m))
    if (!Number.isFinite(t) || t > MAX_RANGE) continue
    // 모델 거리를 지도 거리로. 실제 센서처럼 아주 작은 흔들림을 준다.
    out.push([a, t * FIT.scale + (Math.random() - 0.5) * 0.004])
  }
  return out
}

/** 파티클. AMCL 이 수렴했을 때처럼 로봇 둘레에 몰려 있게 만든다. */
export function simulateParticles(x: number, y: number, spread = 0.05, n = 500): number[][] {
  const out: number[][] = []
  for (let i = 0; i < n; i += 1) {
    // 정규분포에 가깝게 — 가운데가 짙고 가장자리가 성글어야 퍼짐이 읽힌다.
    const g = () => (Math.random() + Math.random() + Math.random() - 1.5) * spread
    out.push([x + g(), y + g()])
  }
  return out
}

// ---------------------------------------------------------------- 경로

const CELL = CELL_W
/**
 * 로봇 반지름만큼 벽을 부풀려 둔다. 그래야 벽에 스치는 경로가 안 나온다.
 * 핑키는 실측 11.6 cm 라 반지름이 5.8 cm 다 — 20 cm 로 잡으면 좁은 통로가
 * 전부 막혀 경로가 아예 안 나온다.
 */
const CLEAR = 0.06

const GRID = (() => {
  const w = GW
  const h = GH
  const r = Math.round(CLEAR / CELL)
  const blocked = new Uint8Array(w * h)
  for (let j = 0; j < h; j += 1) {
    for (let i = 0; i < w; i += 1) {
      if (!WALL_BITS[j * w + i]) continue
      for (let dj = -r; dj <= r; dj += 1) {
        for (let di = -r; di <= r; di += 1) {
          const ni = i + di
          const nj = j + dj
          if (ni < 0 || nj < 0 || ni >= w || nj >= h) continue
          if (di * di + dj * dj <= r * r) blocked[nj * w + ni] = 1
        }
      }
    }
  }
  return { w, h, blocked }
})()

/**
 * 시작점에서 목표까지 벽을 피해 가는 경로. 너비 우선 탐색이라 최단은 아니지만
 * 복도를 따라가는 모양은 나온다.
 */
export function simulatePlan(
  x: number,
  y: number,
  gx: number,
  gy: number,
): number[][] {
  const { w, h, blocked } = GRID
  const idx = (mx: number, my: number) => {
    const m = mapToModel(mx, my)
    const i = Math.round(m.u / CELL)
    const j = Math.round(m.v / CELL)
    if (i < 0 || j < 0 || i >= w || j >= h) return -1
    return j * w + i
  }
  /** 부풀린 벽에 걸리면 가장 가까운 빈 칸으로 옮긴다. 웨이포인트는 벽에서
   *  20 cm 안쪽에 있는 것이 많아 부풀린 뒤에는 막힌 칸이 되기 쉽다. */
  const nearestFree = (c: number) => {
    if (c < 0 || !blocked[c]) return c
    const ci = c % w
    const cj = (c - ci) / w
    for (let r = 1; r < 40; r += 1) {
      for (let dj = -r; dj <= r; dj += 1) {
        for (let di = -r; di <= r; di += 1) {
          if (Math.max(Math.abs(di), Math.abs(dj)) !== r) continue
          const ni = ci + di
          const nj = cj + dj
          if (ni < 0 || nj < 0 || ni >= w || nj >= h) continue
          if (!blocked[nj * w + ni]) return nj * w + ni
        }
      }
    }
    return -1
  }
  const start = nearestFree(idx(x, y))
  const goal = nearestFree(idx(gx, gy))
  if (start < 0 || goal < 0) return []

  const prev = new Int32Array(w * h).fill(-1)
  const seen = new Uint8Array(w * h)
  const queue = new Int32Array(w * h)
  let head = 0
  let tail = 0
  queue[tail++] = start
  seen[start] = 1
  while (head < tail) {
    const cur = queue[head++]
    if (cur === goal) break
    const ci = cur % w
    const cj = (cur - ci) / w
    for (const [di, dj] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ] as const) {
      const ni = ci + di
      const nj = cj + dj
      if (ni < 0 || nj < 0 || ni >= w || nj >= h) continue
      const n = nj * w + ni
      if (seen[n] || blocked[n]) continue
      seen[n] = 1
      prev[n] = cur
      queue[tail++] = n
    }
  }
  if (prev[goal] < 0 && goal !== start) return []

  const cells: number[] = []
  for (let c = goal; c !== -1; c = prev[c]) {
    cells.push(c)
    if (c === start) break
  }
  cells.reverse()

  // 격자를 그대로 쓰면 계단처럼 각진다. 몇 칸 걸러 뽑고 살짝 다듬는다.
  const step = Math.max(1, Math.floor(cells.length / 60))
  const pts: { x: number; y: number }[] = []
  for (let k = 0; k < cells.length; k += step) {
    const i = cells[k] % w
    const j = (cells[k] - i) / w
    pts.push(modelToMap(i * CELL, j * CELL))
  }
  const last = cells[cells.length - 1]
  const li = last % w
  pts.push(modelToMap(li * CELL, (last - li) / w * CELL))

  for (let pass = 0; pass < 3; pass += 1) {
    for (let k = 1; k < pts.length - 1; k += 1) {
      pts[k] = {
        x: (pts[k - 1].x + pts[k].x * 2 + pts[k + 1].x) / 4,
        y: (pts[k - 1].y + pts[k].y * 2 + pts[k + 1].y) / 4,
      }
    }
  }
  return pts.map((p) => [p.x, p.y])
}
