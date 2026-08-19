/**
 * 지도 좌표(로봇이 쓰는 것)와 실측 모델 좌표(3D 파일이 쓰는 것)를 잇는다.
 *
 * ## 왜 두 좌표계가 다른가
 *
 * SLAM 은 로봇이 처음 켜진 자리를 원점으로, 그때 바라보던 방향을 축으로 삼는다.
 * 그 방향이 건물과 나란할 이유가 없어서, 만들어진 지도는 실제 건물보다 12.7도
 * 돌아가 있다.
 *
 * ## 어느 쪽을 고치나
 *
 * **모델은 손대지 않는다.** 모델 좌표는 자로 잰 실측값이라, 이쪽을 비틀면
 * 화면에 뜨는 치수가 전부 거짓이 된다. 대신 로봇 좌표를 모델 쪽으로 옮긴다.
 * 그래서 화면에 보이는 값은 언제나 진짜 미터다.
 */

/** 지도를 모델에 겹쳐 재서 얻은 값. 벽으로 맞췄고 오차는 약 2.5 cm(지도 1픽셀)다. */
export const FIT = { rotationDeg: 12.7, x: -0.094, y: -0.368, scale: 0.965 }

const A = (FIT.rotationDeg * Math.PI) / 180

/** 지도 좌표(m) → 실측 모델 좌표(m). */
export function mapToModel(mx: number, my: number) {
  const px = (mx - FIT.x) / FIT.scale
  const py = (my - FIT.y) / FIT.scale
  return {
    u: px * Math.cos(A) + py * Math.sin(A),
    v: -px * Math.sin(A) + py * Math.cos(A),
  }
}

/** 실측 모델 좌표(m) → 지도 좌표(m). 3D 화면을 찍어 위치를 알려줄 때 쓴다. */
export function modelToMap(u: number, v: number) {
  const px = u * Math.cos(A) - v * Math.sin(A)
  const py = u * Math.sin(A) + v * Math.cos(A)
  return { x: px * FIT.scale + FIT.x, y: py * FIT.scale + FIT.y }
}

/**
 * 지도 기준 방향(rad) → 모델 기준 방향(rad).
 *
 * 위치를 12.7도 돌렸으면 방향도 같이 돌려야 한다. 안 그러면 로봇이 엉뚱한
 * 쪽을 보고 선다.
 */
export function mapYawToModel(yaw: number) {
  return yaw - A
}

/** 모델 기준 방향(rad) → 지도 기준 방향(rad). */
export function modelYawToMap(yaw: number) {
  return yaw + A
}
