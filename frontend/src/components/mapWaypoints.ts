/**
 * 안내 목적지 좌표. 디자인 시안 비교용으로 잠시 여기 둔다.
 *
 * 정본은 로봇 쪽 `mingky_ros/mingky_bringup/config/waypoints/
 * yun_map_highres_clean_waypoints.yaml` 이고, 실제 화면에 넣을 때는 백엔드에서
 * 받아와야 한다. 지도를 다시 만들면 이 좌표는 전부 무효가 된다.
 */

export type WaypointKind = 'goal' | 'wait' | 'charge'

export interface Waypoint {
  id: string
  label: string
  kind: WaypointKind
  /** 지도 좌표(m) */
  x: number
  y: number
}

export const WAYPOINTS: Waypoint[] = [
  { id: 'reception_goal', label: '접수', kind: 'goal', x: 0.017, y: 0.334 },
  { id: 'payment_goal', label: '수납', kind: 'goal', x: 0.115, y: -0.093 },
  { id: 'pharmacy_goal', label: '약국', kind: 'goal', x: 0.108, y: 0.474 },
  { id: 'treatment_room_goal', label: '진료실', kind: 'goal', x: 1.21, y: 1.507 },
  { id: 'xray_room_goal', label: '엑스레이', kind: 'goal', x: 1.716, y: 1.597 },
  { id: 'ct_room_goal', label: 'CT', kind: 'goal', x: 2.587, y: 1.72 },
  { id: 'mri_room_goal', label: 'MRI', kind: 'goal', x: 2.098, y: 1.19 },
  { id: 'clinical_pathology_room_goal', label: '진단검사', kind: 'goal', x: 1.629, y: 0.985 },
  { id: 'physical_therapy_goal', label: '물리치료', kind: 'goal', x: 0.694, y: 1.294 },
  { id: 'ward_goal', label: '병동', kind: 'goal', x: 2.73, y: 1.263 },
  { id: 'restroom_goal', label: '화장실', kind: 'goal', x: 0.797, y: 0.426 },

  { id: 'reception_payment_waiting', label: '접수·수납', kind: 'wait', x: 0.356, y: -0.061 },
  { id: 'pharmacy_waiting', label: '약국', kind: 'wait', x: -0.074, y: 0.435 },
  { id: 'treatment_room_waiting', label: '진료실', kind: 'wait', x: 1.385, y: 1.417 },
  { id: 'xray_room_waiting', label: '엑스레이', kind: 'wait', x: 1.974, y: 1.677 },
  { id: 'ct_room_waiting', label: 'CT', kind: 'wait', x: 2.368, y: 1.73 },
  { id: 'mri_room_waiting', label: 'MRI', kind: 'wait', x: 1.964, y: 1.053 },
  { id: 'clinical_pathology_room_waiting', label: '진단검사', kind: 'wait', x: 1.524, y: 0.952 },
  { id: 'physical_therapy_waiting', label: '물리치료', kind: 'wait', x: 0.891, y: 1.384 },
  { id: 'ward_waiting', label: '병동', kind: 'wait', x: 2.505, y: 1.391 },
  { id: 'restroom_waiting', label: '화장실', kind: 'wait', x: 0.986, y: 0.495 },

  { id: 'charging_station_1', label: '충전소 1', kind: 'charge', x: 1.173, y: 0.177 },
  { id: 'charging_station_2', label: '충전소 2', kind: 'charge', x: 1.201, y: 0.085 },
]

/**
 * 카운터·벽에 붙는 안내판.
 *
 * 좌표는 **실측 모델 기준**(u, v)이다. 지도 좌표가 아니므로 변환하지 않는다.
 * 카운터 상판 위치는 3D 모델에서 뽑았다(윗면 0.21m, 0.578 x 0.152m 짜리 7개와
 * 접수 데스크 1개). 나머지는 해당 목적지 좌표를 모델 좌표로 옮겨 잡았다.
 */
export type SignIcon =
  | 'reception' | 'pharmacy' | 'clinic' | 'scan' | 'lab' | 'ward'
  | 'therapy' | 'restroom' | 'exit' | 'charge'

export interface Sign {
  label: string
  sub?: string
  icon: SignIcon
  /** 실측 모델 좌표(m) */
  u: number
  v: number
  /** 판의 가로·세로(m) */
  w: number
  h: number
  /** 놓이는 높이(m). 카운터 상판은 0.21 */
  y: number
  /** 주(1)·부(2) 위계. 2 는 작고 옅게 그린다 */
  rank: 1 | 2
}

export const SIGNS: Sign[] = [
  { label: '물리치료실', icon: 'therapy', u: 1.316, v: 2.047, w: 0.578, h: 0.152, y: 0.211, rank: 1 },
  { label: '진료실', icon: 'clinic', u: 1.933, v: 2.047, w: 0.578, h: 0.152, y: 0.211, rank: 1 },
  { label: 'X-ray 촬영실', icon: 'scan', u: 2.548, v: 2.048, w: 0.578, h: 0.152, y: 0.211, rank: 1 },
  { label: 'CT 촬영실', icon: 'scan', u: 3.167, v: 2.048, w: 0.578, h: 0.152, y: 0.211, rank: 1 },
  { label: '임상병리실', icon: 'lab', u: 1.954, v: 0.95, w: 0.578, h: 0.152, y: 0.211, rank: 1 },
  { label: 'MRI 촬영실', icon: 'scan', u: 2.589, v: 0.95, w: 0.578, h: 0.152, y: 0.211, rank: 1 },
  { label: '입원 병동', icon: 'ward', u: 3.206, v: 0.95, w: 0.578, h: 0.152, y: 0.211, rank: 1 },
  { label: '접수', sub: '수납', icon: 'reception', u: 0.465, v: 0.682, w: 0.19, h: 0.3, y: 0.221, rank: 1 },
  { label: '약국', icon: 'pharmacy', u: 0.458, v: 2.024, w: 0.34, h: 0.12, y: 0.211, rank: 1 },
  { label: '충전소', icon: 'charge', u: 1.397, v: 0.089, w: 0.3, h: 0.13, y: 0.211, rank: 2 },
  { label: '화장실', icon: 'restroom', u: 1.587, v: 0.746, w: 0.3, h: 0.14, y: 0.211, rank: 2 },
  { label: '비상구', icon: 'exit', u: 3.54, v: 1.513, w: 0.34, h: 0.14, y: 0.211, rank: 2 },
]
