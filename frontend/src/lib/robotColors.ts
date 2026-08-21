/**
 * 지도 위에서 로봇을 서로 구분하는 색.
 *
 * ## 왜 지도 컴포넌트 밖에 있나
 *
 * `HospitalMap3D` 는 three.js 를 끌고 오기 때문에 `LazyHospitalMap3D` 로
 * 늦게 읽는다. 범례를 그리는 화면이 색을 저기서 가져오면 **그 순간 three.js
 * 가 첫 화면 번들에 딸려 들어와** 늦게 읽는 의미가 사라진다.
 *
 * ## 왜 순서로 정하나
 *
 * 로봇 목록은 `robot_id` 오름차순으로 고정이다(백엔드가 정렬해서 준다).
 * 같은 자리의 로봇이 늘 같은 색이어야, 색을 보고 어느 로봇인지 아는 습관이
 * 생긴다. 로봇이 하나 빠졌다고 남은 로봇의 색이 바뀌면 그 습관이 깨진다 —
 * 그래서 **목록의 인덱스가 아니라 로봇 자체에 색을 매어야** 하는데, 지금은
 * 2대뿐이라 정렬된 목록의 인덱스로 충분하다. 대수가 늘거나 로봇이 오가면
 * `robots` 마스터에 색 컬럼을 두는 쪽으로 옮긴다.
 */

/** 파랑 · 주황 · 보라 · 청록. 색각 이상에서도 갈리는 조합을 골랐다. */
export const ROBOT_COLORS = ['#2563eb', '#f59e0b', '#8b5cf6', '#14b8a6'] as const

/** 위치를 믿을 수 없을 때. 회색은 팔레트에 없으므로 로봇 색과 섞이지 않는다. */
export const ROBOT_STALE_COLOR = '#94a3b8'

export function robotColor(index: number): string {
  return ROBOT_COLORS[index % ROBOT_COLORS.length]
}
