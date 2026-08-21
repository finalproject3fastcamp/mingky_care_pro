/**
 * 실측 3D 병원 모형을 브라우저에서 직접 그리고, 그 위에 로봇과 진단 레이어를 얹는다.
 *
 * 기존 흑백 격자 지도(RobotMap)를 대신한다. 보던 정보는 그대로 두고, 보는
 * 방식만 바꾼 것이다. 판단 기준은 docs/nav2-debugging.md 를 따른다.
 *
 * | 레이어 | 무엇을 판단하나 |
 * | --- | --- |
 * | 라이다 | **벽과 겹치나** — 안 겹치면 위치추정이 틀렸다 |
 * | 파티클 | **넓게 퍼졌나** — 퍼지면 AMCL 발산 |
 * | 경로 | 목표까지 그려지나 |
 *
 * ## 왜 3D 를 브라우저에서 그리나
 *
 * 렌더 그림을 깔면 화질은 좋지만 시점이 하나로 굳는다. 벽에 가린 자리는
 * 영영 못 본다. 직접 그리면 돌려보고 당겨볼 수 있어서, 라이다가 벽 **뒤로**
 * 새는지 **앞에서** 어긋나는지를 구분할 수 있다. 이 구분이 위치추정 문제를
 * 잡는 핵심이다.
 *
 * ## 좌표
 *
 * 모델은 실측 그대로 두고 로봇 좌표를 모델 쪽으로 옮긴다(mapFrame.ts).
 * 모델 좌표 (u, v) 는 three.js 의 (x, -z) 다. 높이가 y 다.
 *
 * ## 3D 파일
 *
 * public/hospital-3d.glb — 라이노에서 내보낸 모형을 gltfpack 으로 줄인 것이다
 * (118 MB → 556 KB, 삼각형 260만 → 11만). 재질은 네 갈래(FLOOR·WALL·MARK·
 * FIXTURE)로 묶여 있어서 색을 여기서 바꿀 수 있다.
 * **원점을 옮겨 다시 내보내면 좌표가 전부 어긋난다.**
 *
 * public/models/pinky.glb — 로봇. 로봇 카드(PinkyModelCard)가 쓰는 것과 같은
 * 모형이다. 화면마다 다른 로봇이 나오면 안 되고, 같은 파일을 두 벌 두면
 * 한쪽만 갱신되어 어긋난다.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js'
import { Line2 } from 'three/examples/jsm/lines/Line2.js'
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'

import { getQrObservation } from '../lib/api'
import { usePolling } from '../lib/usePolling'
import { MapRearCam } from './MapRearCam'
import { FIT, mapToModel, mapYawToModel, modelToMap, modelYawToMap } from './mapFrame'
import { SIGNS } from './mapSigns'
import type { LowObstacleState, QrObservation } from '../types/monitoring'
import type {
  DiagLayers,
  LowObstacleObservation,
  RobotPose,
} from '../lib/useTeleopSocket'
import './HospitalMap3D.css'

export interface WaypointMarker {
  name: string
  x: number
  y: number
  yaw: number
  status?: 'ok' | 'warning' | 'blocked' | 'outside'
  selected?: boolean
}

export interface HospitalMap3DProps extends DiagLayers {
  pose: RobotPose | null
  live: boolean
  /** 3D 바닥을 찍어 "로봇이 여기 있다" 를 알린다. 없으면 지정 모드가 안 뜬다. */
  onSetPose?: (x: number, y: number, yaw: number) => void
  waypoints?: WaypointMarker[]
  onSelectWaypoint?: (name: string) => void
  /** 비상정지 중이면 로봇을 붉게 칠하고 붉게 박동한다. */
  estop?: boolean
  /** 이 로봇을 고른 상태인지. 켜면 초록으로 박동한다. */
  selected?: boolean
  /**
   * 환자 추종 상태. 로봇 쪽 `follow_state` 를 그대로 받는다.
   * 안 넘기면 이 화면은 예전과 똑같이 움직인다.
   */
  follow?: (
    Pick<QrObservation, 'follow_state' | 'follow_distance' | 'follow_source'>
    & Partial<Pick<QrObservation, 'patient_wait_remaining_sec'>>
  ) | null
  /**
   * 충전소로 돌아가는 중인지.
   *
   * `follow_state` 에 없는 상태라(inactive/normal/slow/waiting 뿐) 따로 받는다.
   *
   * **환자를 놓쳤다고 저절로 복귀하지는 않는다.** 로봇이 복귀를 시작하는 경우는
   * 배터리 부족 · 의료진이 안내를 취소 · 안내 완료, 이 셋뿐이다. 환자를 놓치면
   * 그 자리에서 **무기한 기다린다.**
   */
  returning?: boolean
  /**
   * 추종 상태를 스스로 받아 올 로봇. `follow` 를 직접 넘기면 그쪽이 이긴다.
   *
   * 화면 쪽에서 받아다 넘기게 하면 대시보드마다 같은 폴링을 적어야 한다.
   * 지도 컴포넌트가 상태 표시와 카메라 제목을 함께 갱신한다.
   */
  robotId?: string | null
  /**
   * 지도 위 카메라 창이 어느 쪽을 보여줄지. **화면 단계를 따라간다.**
   *
   * QR 을 대는 동안은 앞쪽, 안내가 시작되면 뒤쪽이다. 원래 두 화면이 따로
   * 하던 일을 이 창 하나가 이어받는다 — 영상이 두 곳에 뜨면 보는 사람이
   * 어느 쪽을 봐야 하는지 알 수 없다.
   *
   * `null` 이면 창을 띄우지 않는다(보여줄 카메라가 없는 화면).
   */
  camera?: 'front' | 'rear' | null
  /**
   * 환자를 못 찾은 채 이만큼(초) 지나면 로봇이 안내를 접고 충전소로 간다.
   *
   * 로봇 쪽 `patient_follow_wait_limit_sec` 과 같은 값이어야 한다. 화면이
   * 세는 숫자와 로봇이 세는 숫자가 다르면, 0 이 됐는데 안 가거나 아직
   * 남았는데 가버리는 것으로 보인다.
   */
  waitLimitSec?: number
  /**
   * 로봇이 **환자를 기다리느라 멈춰 있는지.**
   *
   * 남은 시간을 셀지 말지를 이 값으로 정한다. 로봇은 안내 목표를 향해
   * 가던 중에 환자를 놓쳤을 때만 복귀 시계를 돌린다. 이미 도착해서 서
   * 있었다면 시계를 아예 시작하지 않고, 그때는 환자를 놓쳐도 영영
   * 기다린다.
   *
   * 그 차이를 무시하고 세면 화면은 0 까지 세고서 아무 일도 안 일어난다.
   * **일어나지 않을 일을 예고하는 화면이 아무것도 안 보여주는 화면보다
   * 나쁘다.**
   */
  paused?: boolean
  /** 전방 초음파/LiDAR 저상 장애물 상태머신의 현재 상태. */
  lowObstacleState?: LowObstacleState | null
  /** 실시간 조작 소켓으로 받는 로봇 전방 저상 장애물 추정 영역. */
  lowObstacle?: LowObstacleObservation | null
}

type MapState = 'idle' | 'escort' | 'slow' | 'waiting' | 'returning' | 'estop'

/**
 * 화면에 보이는 상태는 여섯 개뿐이다. 세 가지가 각자 한 가지만 맡는다.
 *
 * | 무엇 | 뜻 |
 * | --- | --- |
 * | 움직임(`pulseMs`) | **얼마나 급한가** — 없음 · 느림(2초) · 빠름(0.5초) 세 단계 |
 * | 색(`tone`) | **어떤 종류인가** |
 * | 글자(`text`) | **정확히 무엇인가** |
 *
 * 한 요소가 두 가지를 뜻하기 시작하면 보는 사람이 설명을 들어야 알 수 있다.
 *
 * 복귀는 박동하지 않는다. **문제가 아니라 진행 중**이기 때문이다. 대신
 * 고리가 한 번 퍼지며 나타난다 — 박동이 멎는 것만으로는 눈이 못 잡는다.
 */
/** `follow_source` 를 사람 말로. 뒤쪽 카메라 창 제목줄에 쓴다. */
function sourceText(source: string | null | undefined): string {
  switch (source) {
    case 'qr': return 'QR 인식'
    case 'visual': return 'YOLO 추정'
    case 'partial_near': return '일부만 보임'
    case 'acquiring': return '시야 확보 중'
    case 'grace': return '유예'
    case 'stale': return '놓침'
    case 'none': return '추적 꺼짐'
    default: return '확인 중'
  }
}

const MAP_STATE: Record<
  MapState,
  { text: string; tone: string; pulseMs: number | null; ring: boolean }
> = {
  idle: { text: '대기', tone: 'idle', pulseMs: null, ring: false },
  escort: { text: '안내 중', tone: 'normal', pulseMs: null, ring: false },
  slow: { text: '감속 중', tone: 'slow', pulseMs: null, ring: false },
  waiting: { text: '기다리는 중', tone: 'wait', pulseMs: 2000, ring: false },
  returning: { text: '충전소로 복귀 중', tone: 'wait', pulseMs: null, ring: true },
  estop: { text: '비상정지', tone: 'alarm', pulseMs: 500, ring: false },
}

const LOW_OBSTACLE_DISPLAY: Record<
  LowObstacleState,
  { text: string; tone: 'normal' | 'wait' | 'slow' | 'alarm' | 'muted' }
> = {
  STARTING: { text: '저상 감시 준비 중', tone: 'muted' },
  DISABLED: { text: '저상 감지 꺼짐', tone: 'muted' },
  CLEAR: { text: '저상 감시 정상', tone: 'normal' },
  UNCERTAIN: { text: '낮은 장애물 확인 중', tone: 'wait' },
  CONFIRMED: { text: '낮은 장애물 감지', tone: 'wait' },
  SLOW: { text: '저상 장애물 감속 회피', tone: 'slow' },
  FORWARD_BLOCKED: { text: '전방 저상 장애물 · 이동 제한', tone: 'alarm' },
  STALE_RANGE: { text: '초음파 센서 확인 필요', tone: 'muted' },
  STALE_LIDAR: { text: 'LiDAR 연결 확인 필요', tone: 'muted' },
}

interface LabelHandle {
  el: HTMLDivElement
  /** 3D 안의 자리. x=u, y=높이, z=-v */
  x: number
  y: number
  z: number
}

const LAYER_LABEL = {
  scan: '라이다',
  particles: '파티클',
  plan: '원래 경로',
  signs: '안내',
} as const

type LayerKey = keyof typeof LAYER_LABEL

/**
 * 여기 값만 바꾸면 화면 인상이 바뀐다.
 *
 * 빛은 두 갈래다. `sun` 은 그림자를 만들어 벽의 윗면과 옆면을 갈라 놓고,
 * `sky`/`ground` 는 그림자 안쪽이 새까매지지 않게 받쳐 준다.
 * **둘의 차이가 형태를 읽히게 하므로, 받침을 올리면 입체감이 사라진다.**
 */
export interface Look {
  background: string
  sky: number
  ground: number
  fill: number
  sun: number
  sunFrom: readonly [number, number, number]
  env: number
  exposure: number
  /** 안내 글자 크기. 지도 가로폭에 대한 비율이라 지도를 키우면 같이 커진다 */
  signSize: number
  /** 라이다 점 지름(화면 픽셀). 화면 기준이라 당겨도 또렷하다 */
  scanSize: number
  scanOpacity: number
  scanColor: string
  /** 경로 선 굵기(화면 픽셀) */
  planWidth: number
  planOpacity: number
  planColor: string
  recoveryPlanColor: string
  viewFrom: readonly [number, number, number]
}

const LOOK: Look = {
  /**
   * 3D 뒤에 깔리는 색. 빛이 아니라서 **건물 밝기에는 영향이 없다** —
   * 건물이 흰색이라 배경이 밝으면 경계가 흐려지므로 어둡게 깔아 도드라지게 한다.
   *
   * 3D 안이 아니라 캔버스 뒤에 깐다. 3D 안에 넣으면 톤매핑을 타서 지정한
   * 값과 다른 색으로 나온다(밝은 회색을 넣었더니 더 밝게 나왔다).
   */
  background: '#c5cbd1',
  /** 하늘빛·바닥반사 받침 */
  sky: 0x9fb4c6,
  ground: 0xc8ccd0,
  fill: 0,
  /** 주광. 그림자를 만든다 */
  sun: 6,
  sunFrom: [0.463, 3.309, 1.041],
  /** 주변 반사(있는 듯 없는 듯). 재질에 생기를 준다 */
  env: 0.4,
  exposure: 0.71,
  /**
   * 안내 글자 크기. 지도 가로폭의 몇 배인가 — 지도가 커지면 글자도 같이
   * 커지므로, 지도를 넓게 쓰려면 이 값을 줄여야 글자가 커 보이지 않는다.
   */
  signSize: 0.0147,
  /**
   * 라이다·경로의 모양. 둘 다 화면 픽셀 기준이라 얼마나 당기든 굵기가 같다 —
   * 실제 치수로 잡으면 멀리서 볼 때 사라져 판단을 못 한다.
   */
  scanSize: 2.2,
  scanOpacity: 0.68,
  scanColor: '#f94848',
  planWidth: 2.6,
  planOpacity: 0.71,
  planColor: '#2563eb',
  recoveryPlanColor: '#f97316',
  /**
   * 기본 시점 방향. 건물 중심에서 이쪽으로 물러나 바라본다.
   * 낮게 볼수록 입체감은 살지만 바닥이 벽에 가린다 — 바닥에 그리는 라이다를
   * 보려면 어느 정도 높이가 필요하다.
   */
  viewFrom: [0.08, 1.0, 0.62],
}

// 라이다·경로의 색은 LOOK 에 있다(화면에서 맞출 수 있게).
const COLOR = {
  particles: 0x10b981,
  /** 박동 색. 선택은 초록, 비상정지는 빨강 — 움직임은 같고 색만 다르다 */
  selected: 0x22c55e,
  estop: 0xef4444,
  wpOk: 0x2563eb,
  wpWarn: 0xf59e0b,
  wpBad: 0xdc2626,
  /** 사람을 기다리는 중. 복귀 고리도 같은 색이다 */
  wait: 0xd9a300,
  /** 아직 정상이되 벌어지는 중. 정상(초록)과 같은 계열에서 한 칸 옮긴 것 */
  slow: 0x4f9bb5,
} as const

/**
 * 모형이 바라보는 쪽과 지도의 0도 사이의 차이(rad).
 * 모형이 놓인 방향이 지도의 0도와 같을 이유가 없어서 여기서 맞춘다.
 */
const FACING = 0

/** 추종 상태를 받아 오는 주기. 뒤쪽 카메라 화면과 같은 값이다. */
const QR_POLL_MS = 500

/**
 * 복귀 예고가 빗나갔다고 인정하기까지 기다리는 시간.
 *
 * `guide_robot_state` 가 오는 주기(3초)와 같게 둔다. 그보다 짧으면 상태가
 * 아직 안 왔을 뿐인데 틀렸다고 말하고, 길면 틀린 채로 오래 서 있는다.
 */
const WAIT_OVERDUE_SEC = 3

/** 박동 한 주기(ms). 2D 지도에서 쓰던 1.5초와 같다. */
const PULSE_MS = 1500

/** 미리 잡아 두는 점 개수. 이보다 많이 오면 앞에서부터 잘라 쓴다. */
const MAX_SCAN = 4096
const MAX_PARTICLES = 8192
const MAX_PLAN = 4096

interface Handles {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  controls: OrbitControls
  renderer: THREE.WebGLRenderer
  robot: THREE.Group
  sun: THREE.DirectionalLight
  hemi: THREE.HemisphereLight
  /** 건물·로봇의 모든 재질. 주변 반사 세기를 한꺼번에 바꿀 때 쓴다 */
  mats: THREE.MeshStandardMaterial[]
  host: HTMLDivElement
  pulse: THREE.Mesh<THREE.CircleGeometry, THREE.MeshBasicMaterial>
  /** 로봇 재질들. 비상정지 때 통째로 붉게 물들인다 */
  robotMats: { m: THREE.MeshStandardMaterial; base: THREE.Color; estop: number }[]
  scan: THREE.Points
  particles: THREE.Points
  plan: Line2
  recoveryPlan: Line2
  lowObstacle: THREE.Group
  lowObstacleFan: THREE.Mesh<THREE.CircleGeometry, THREE.MeshBasicMaterial>
  lowObstaclePoint: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>
  waypoints: THREE.Group
  invalidate: () => void
  resetView: () => void
  /**
   * 로봇 뒤에서 로봇이 바라보는 쪽을 본다.
   * `auto` 면 사람이 최근에 화면을 만졌을 때 스스로 물러난다.
   */
  focusRobot: (auto?: boolean) => void
  /** 박동 주기(ms)와 색. 급한 정도를 나타낸다 */
  setPulse: (ms: number | null, color: number) => void
  /** 복귀 고리. 켤 때 한 번 퍼진다 */
  setRing: (on: boolean) => void
  setRingPosition: (x: number, z: number) => void
  pick: (clientX: number, clientY: number, atY?: number) => { u: number; v: number } | null
  pickWaypoint: (clientX: number, clientY: number) => string | null
}

export function HospitalMap3D({
  pose,
  live,
  scan,
  particles,
  plan,
  recoveryPlan,
  onSetPose,
  waypoints = [],
  onSelectWaypoint,
  estop = false,
  selected = false,
  follow = null,
  returning = false,
  robotId = null,
  camera = 'rear',
  waitLimitSec = 20,
  paused = false,
  lowObstacleState = null,
  lowObstacle = null,
}: HospitalMap3DProps) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const labelHostRef = useRef<HTMLDivElement | null>(null)
  const handles = useRef<Handles | null>(null)
  const labelsRef = useRef<LabelHandle[]>([])

  const [ready, setReady] = useState(false)
  const [robotReady, setRobotReady] = useState(0)
  const [failed, setFailed] = useState(false)
  const [placing, setPlacing] = useState(false)
  /**
   * 카메라 창은 **켜진 채로 시작한다.**
   *
   * 이 창이 이제 영상을 보여주는 **유일한 자리**다. 지도 아래에 있던 카드를
   * 걷어냈으므로 꺼 두면 영상을 볼 방법이 없다.
   */
  const [camOn, setCamOn] = useState(true)
  const [drag, setDrag] = useState<{ u: number; v: number } | null>(null)
  const [visible, setVisible] = useState<Record<LayerKey, boolean>>({
    scan: true,
    particles: true,
    plan: true,
    signs: true,
  })

  // ---------------------------------------------------------------- 장면 만들기
  // 딱 한 번 만든다. 이후 값이 바뀌면 만들어 둔 물체를 고쳐 쓴다.
  // 매번 다시 만들면 라이다가 올 때마다 장면이 통째로 재생성돼 화면이 끊긴다.
  useEffect(() => {
    const host = hostRef.current
    const labelHost = labelHostRef.current
    if (!host || !labelHost) return

    let disposed = false
    const scene = new THREE.Scene()
    // 배경은 캔버스 뒤(CSS)에 깐다. 위 LOOK.background 주석 참고.
    host.style.background = LOOK.background

    const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 60)

    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = LOOK.exposure
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    host.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.screenSpacePanning = false
    // 바닥 아래로 내려가면 건물이 뒤집혀 보인다. 위쪽도 완전한 직상방은 막는다.
    controls.minPolarAngle = THREE.MathUtils.degToRad(8)
    controls.maxPolarAngle = THREE.MathUtils.degToRad(84)
    controls.minDistance = 0.6
    controls.maxDistance = 14

    // ---- 빛 ----
    const hemi = new THREE.HemisphereLight(LOOK.sky, LOOK.ground, LOOK.fill)
    scene.add(hemi)

    const sun = new THREE.DirectionalLight(0xffffff, LOOK.sun)
    sun.position.set(...LOOK.sunFrom)
    sun.castShadow = true
    sun.shadow.mapSize.set(2048, 2048)
    sun.shadow.bias = -0.0006
    sun.shadow.normalBias = 0.004
    const sc = sun.shadow.camera
    sc.left = -2.6
    sc.right = 2.6
    sc.top = 2.6
    sc.bottom = -2.6
    sc.near = 0.5
    sc.far = 9
    sc.updateProjectionMatrix()
    scene.add(sun)
    scene.add(sun.target)

    const pmrem = new THREE.PMREMGenerator(renderer)
    // 방 하나짜리 기본 환경. 외부 파일이 필요 없고 재질에 은은한 반사를 준다.
    const envScene = new THREE.Scene()
    envScene.background = new THREE.Color(0xdfe4e9)
    const envTex = pmrem.fromScene(envScene, 0.04).texture
    scene.environment = envTex

    // ---- 로봇 ----
    // 실물 모형을 실제 크기 그대로 놓는다. 도형으로 대충 그리면 통로에
    // 들어가는지를 판단할 수 없다.
    const robot = new THREE.Group()
    robot.visible = false
    scene.add(robot)

    /**
     * 발밑 접지 그림자.
     *
     * 해 그림자 지도는 건물 전체에 펼쳐 쓰기 때문에 로봇처럼 작은 물체에는
     * 거의 안 잡힌다. 그런데 사람 눈은 물체가 바닥에 닿았는지를 발밑 그림자로
     * 판단해서, 없으면 공중에 떠 보인다. 바퀴 밑이 바닥에 정확히 닿아 있는데도
     * 떠 보인다는 지적을 받았다.
     *
     * 해 그림자를 키우는 대신 발밑에 원을 하나 깐다 — 전체 보기처럼 멀리서
     * 볼 때도 로봇과 같이 작아져서 항상 붙어 있는 것으로 읽힌다.
     */
    const contactTex = (() => {
      const cv = document.createElement('canvas')
      cv.width = 128
      cv.height = 128
      const g = cv.getContext('2d')
      if (g) {
        const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64)
        grad.addColorStop(0, 'rgba(0,0,0,0.40)')
        grad.addColorStop(0.5, 'rgba(0,0,0,0.16)')
        grad.addColorStop(1, 'rgba(0,0,0,0)')
        g.fillStyle = grad
        g.fillRect(0, 0, 128, 128)
      }
      const t = new THREE.CanvasTexture(cv)
      t.colorSpace = THREE.SRGBColorSpace
      return t
    })()
    const contact = new THREE.Mesh(
      new THREE.CircleGeometry(0.09, 32),
      new THREE.MeshBasicMaterial({ map: contactTex, transparent: true, depthWrite: false }),
    )
    contact.rotation.x = -Math.PI / 2
    // 바닥 0mm, 바닥 표시선 1mm, 그림자 2mm, 박동 4mm 순으로 쌓는다.
    contact.position.y = 0.002
    contact.renderOrder = 3
    robot.add(contact)

    // 초음파에는 좌우 각도가 없으므로 장애물을 점으로 단정하지 않는다.
    // 센서가 실제로 말할 수 있는 전방 부채꼴과 추정 거리만 표시한다.
    const lowObstacleGroup = new THREE.Group()
    const lowObstacleMat = new THREE.MeshBasicMaterial({
      color: 0xf59e0b,
      transparent: true,
      opacity: 0.38,
      depthWrite: false,
      depthTest: false,
      side: THREE.DoubleSide,
    })
    const lowObstacleFan = new THREE.Mesh(
      new THREE.CircleGeometry(1, 32, -0.13, 0.26),
      lowObstacleMat,
    )
    lowObstacleFan.rotation.x = -Math.PI / 2
    lowObstacleFan.position.y = 0.024
    lowObstacleFan.renderOrder = 7
    lowObstacleGroup.add(lowObstacleFan)
    const lowObstaclePoint = new THREE.Mesh(
      new THREE.SphereGeometry(0.024, 16, 10),
      lowObstacleMat.clone(),
    )
    lowObstaclePoint.position.y = 0.04
    lowObstaclePoint.renderOrder = 8
    lowObstacleGroup.add(lowObstaclePoint)
    // URDF의 ultrasonic_link x=0.0267m를 모델 좌표계 배율로 옮긴다.
    lowObstacleGroup.position.x = 0.0267 / FIT.scale
    lowObstacleGroup.visible = false
    robot.add(lowObstacleGroup)

    /**
     * 로봇 아래에서 번지는 원. 어제 2D 지도에서 쓰던 것과 같은 박동이다 —
     * 1.5초에 한 번, 커지면서 옅어진다. 선택과 비상정지가 같은 움직임이고
     * 색으로만 갈린다.
     *
     * 조명을 받지 않는 재질(Basic)을 쓴다. 그림자가 지면 표시가 아니라
     * 바닥에 그린 무늬처럼 보인다.
     */
    const pulseMat = new THREE.MeshBasicMaterial({
      color: COLOR.selected,
      transparent: true,
      opacity: 0,
      depthWrite: false,
      side: THREE.DoubleSide,
    })
    const pulse = new THREE.Mesh(new THREE.CircleGeometry(0.14, 48), pulseMat)
    pulse.rotation.x = -Math.PI / 2
    pulse.position.y = 0.004
    pulse.renderOrder = 4
    pulse.visible = false
    scene.add(pulse)

    // 박동 주기. **급한 정도를 이 값 하나가 나타낸다.**
    let pulseMs = PULSE_MS

    /**
     * 복귀 고리. 박동이 아니라 고리다 — 복귀는 문제가 아니라 진행 중이라
     * 계속 뛰면 안 된다. 대신 나타날 때 한 번만 퍼진다.
     */
    const ringMat = new THREE.MeshBasicMaterial({
      color: COLOR.wait,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
      side: THREE.DoubleSide,
    })
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.15, 0.168, 56), ringMat)
    ring.rotation.x = -Math.PI / 2
    ring.position.y = 0.003
    ring.renderOrder = 4
    ring.visible = false
    scene.add(ring)
    let ringStart = 0

    // ---- 진단 레이어 ----
    // 미리 자리를 잡아 두고 개수만 바꾼다. 매번 새로 만들면 초당 수십 번
    // 버퍼를 새로 올리게 돼 화면이 끊긴다.
    const makePoints = (
      max: number,
      color: number | string,
      size: number,
      y: number,
      order: number,
      /** 크기를 실제 치수(m)로 볼지, 화면 픽셀로 볼지 */
      worldScale: boolean,
    ) => {
      const g = new THREE.BufferGeometry()
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(max * 3), 3))
      g.setDrawRange(0, 0)
      // 점이 카메라 밖으로 나가도 잘리지 않게 넉넉히 잡는다.
      g.boundingSphere = new THREE.Sphere(new THREE.Vector3(1.7, y, -0.95), 40)
      const p = new THREE.Points(
        g,
        // depthWrite 는 끈다. 켜 두면 앞뒤로 겹친 점끼리 서로를 지워 성글어 보인다.
        // depthTest 는 켠다 — 벽 뒤의 점이 가려져야 "라이다가 벽을 넘었다" 가 보이고,
        // 로봇 표시가 파티클에 덮이지 않는다.
        new THREE.PointsMaterial({
          color,
          size,
          sizeAttenuation: worldScale,
          transparent: true,
          opacity: 0.9,
          depthWrite: false,
        }),
      )
      p.frustumCulled = false
      p.renderOrder = order
      scene.add(p)
      return p
    }
    // 라이다는 화면 기준 크기다. 얼마나 당기든 벽선이 또렷해야 한다.
    const scanPts = makePoints(MAX_SCAN, LOOK.scanColor, LOOK.scanSize, 0.05, 2, false)
    // 파티클은 화면 픽셀 크기로 유지한다. 실제 크기를 사용하면 전체 지도를
    // 보는 기본 시점에서 거의 사라져 위치추정 분포를 판단할 수 없다.
    const particlePts = makePoints(MAX_PARTICLES, COLOR.particles, 2.4, 0.62, 1, false)

    // 경로는 굵은 선이라야 읽힌다.
    // three.js 의 기본 선(THREE.Line)은 굵기 지정이 대부분의 브라우저에서
    // 무시돼 언제나 1픽셀로 나온다. 비스듬히 본 3D 화면에서 1픽셀 선은
    // 계단처럼 끊겨 보여서, 화면 공간에서 두께를 만드는 Line2 를 쓴다.
    // 경로도 가리지 않는다. 로봇이 어디로 가려는지는 계획이지 측정이 아니라,
    // 20 cm 짜리 벽에 가려 안 보이면 레이어 자체가 쓸모없어진다.
    const planMat = new LineMaterial({
      color: LOOK.planColor,
      linewidth: LOOK.planWidth,
      transparent: true,
      opacity: LOOK.planOpacity,
      depthWrite: false,
      depthTest: false,
      dashed: false,
    })
    const planLine = new Line2(new LineGeometry(), planMat)
    planLine.frustumCulled = false
    planLine.renderOrder = 5
    planLine.visible = false
    scene.add(planLine)

    const recoveryPlanMat = new LineMaterial({
      color: LOOK.recoveryPlanColor,
      linewidth: LOOK.planWidth,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
      depthTest: false,
      dashed: true,
      dashSize: 5,
      gapSize: 4,
    })
    const recoveryPlanLine = new Line2(new LineGeometry(), recoveryPlanMat)
    recoveryPlanLine.frustumCulled = false
    recoveryPlanLine.renderOrder = 6
    recoveryPlanLine.visible = false
    scene.add(recoveryPlanLine)

    const wpGroup = new THREE.Group()
    scene.add(wpGroup)

    // ---- 안내 글자 ----
    // 3D 안에 글자를 넣는 대신 HTML 을 위에 띄운다. 그래야 항상 화면을
    // 정면으로 보고, 글꼴·굵기·색을 CSS 로 그대로 쓸 수 있다.
    labelsRef.current = SIGNS.map((sign) => {
      const el = document.createElement('div')
      el.className = 'map3d__sign' + (sign.rank === 1 ? '' : ' map3d__sign--minor')
      el.textContent = sign.label
      if (sign.sub) {
        el.appendChild(document.createElement('br'))
        el.appendChild(document.createTextNode(sign.sub))
      }
      labelHost.appendChild(el)
      return { el, x: sign.u, y: sign.y, z: -sign.v }
    })

    // ---- 바닥(찍기용) ----
    // 눈에 보이지는 않지만 광선이 부딪힐 면이 있어야 클릭 지점을 알 수 있다.
    const floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
    const raycaster = new THREE.Raycaster()
    raycaster.params.Points = { threshold: 0.03 }
    const ndc = new THREE.Vector2()
    const hit = new THREE.Vector3()

    const toNdc = (clientX: number, clientY: number) => {
      const r = renderer.domElement.getBoundingClientRect()
      ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1)
      return r
    }

    /**
     * 화면의 한 점이 가리키는 바닥 좌표. `atY` 를 주면 그 높이의 수평면에서
     * 찾는다 — 안내 글자를 끌 때 글자가 떠 있는 높이 그대로 움직여야 하기
     * 때문이다.
     */
    const pick = (clientX: number, clientY: number, atY = 0) => {
      toNdc(clientX, clientY)
      raycaster.setFromCamera(ndc, camera)
      floorPlane.constant = -atY
      const ok = raycaster.ray.intersectPlane(floorPlane, hit)
      floorPlane.constant = 0
      if (!ok) return null
      return { u: hit.x, v: -hit.z }
    }

    const pickWaypoint = (clientX: number, clientY: number) => {
      toNdc(clientX, clientY)
      raycaster.setFromCamera(ndc, camera)
      const found = raycaster.intersectObjects(wpGroup.children, true)[0]
      if (!found) return null
      let o: THREE.Object3D | null = found.object
      while (o && !o.userData.waypointName) o = o.parent
      return (o?.userData.waypointName as string) ?? null
    }

    // ---- 그리기 ----
    let dirty = true
    const invalidate = () => {
      dirty = true
    }
    controls.addEventListener('change', invalidate)
    /**
     * 사람이 화면을 만진 시각. 자동으로 카메라를 옮길 때 이걸 본다 —
     * 보는 사람 손에서 화면을 뺏는 건 3D 화면에서 가장 하면 안 되는 짓이다.
     */
    // 0 으로 두면 안 된다 — 화면을 연 직후에는 performance.now() 가 작아서
    // "방금 만졌다"로 잘못 읽히고, 첫 자동 이동이 통째로 무시된다.
    let lastTouch = Number.NEGATIVE_INFINITY
    const onGrab = () => {
      lastTouch = performance.now()
      // 날아가는 중이었다면 즉시 멈추고 조작권을 돌려준다.
      flying = false
      controls.enabled = true
    }
    controls.addEventListener('start', onGrab)

    // ---- 카메라 이동 ----
    // 900ms. 더 빠르면 순간이동처럼 보여 어디로 갔는지 못 따라가고,
    // 더 느리면 답답하다.
    const FLY_MS = 900
    const flyFrom = new THREE.Vector3()
    const flyTo = new THREE.Vector3()
    const flyFromTarget = new THREE.Vector3()
    const flyToTarget = new THREE.Vector3()
    let flyStart = 0
    let flying = false

    const projected = new THREE.Vector3()
    const syncLabels = (w: number, h: number, show: boolean) => {
      for (const l of labelsRef.current) {
        if (!show) {
          l.el.style.display = 'none'
          continue
        }
        projected.set(l.x, l.y, l.z).project(camera)
        if (projected.z > 1) {
          l.el.style.display = 'none'
          continue
        }
        l.el.style.display = ''
        l.el.style.transform =
          `translate(-50%,-50%) translate(${(projected.x * 0.5 + 0.5) * w}px,` +
          `${(-projected.y * 0.5 + 0.5) * h}px)`
      }
    }

    let showSigns = true
    const setShowSigns = (v: boolean) => {
      showSigns = v
      invalidate()
    }

    let size = { w: 1, h: 1 }
    let raf = 0
    const tick = () => {
      raf = requestAnimationFrame(tick)
      if (flying) {
        const t = Math.min(1, (performance.now() - flyStart) / FLY_MS)
        // 가속했다 감속하는 곡선. 등속으로 밀면 기계가 미는 것처럼 보인다.
        const e = t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2
        controls.target.lerpVectors(flyFromTarget, flyToTarget, e)
        camera.position.lerpVectors(flyFrom, flyTo, e)
        // 아래 루프가 `!moved && !dirty` 로 일찍 빠지므로 직접 켜 줘야 한다.
        // 안 켜면 카메라 값만 바뀌고 화면은 멈춰 있다.
        dirty = true
        if (t >= 1) {
          flying = false
          controls.enabled = true
        }
      }
      const moved = controls.update()
      // 박동. 1.5초를 한 주기로 커지면서 옅어진다. 처음에 빠르고 끝에서
      // 느려지는 곡선(ease-out)이라야 심장 박동처럼 읽힌다.
      if (ring.visible && ringStart) {
        // 0.28 배에서 1.2 배까지 퍼졌다가 1 배로 앉는다. 한 번뿐이다.
        const t = Math.min(1, (performance.now() - ringStart) / 620)
        ring.scale.setScalar(t < 0.55 ? 0.28 + (t / 0.55) * 0.92 : 1.2 - ((t - 0.55) / 0.45) * 0.2)
        dirty = true
        if (t >= 1) ringStart = 0
      }
      if (pulse.visible) {
        const t = (performance.now() % pulseMs) / pulseMs
        const e = 1 - (1 - t) ** 3
        pulse.scale.setScalar(0.6 + e * 1.4)
        pulseMat.opacity = 0.55 * (1 - e)
      } else if (!moved && !dirty) {
        return
      }
      dirty = false
      renderer.render(scene, camera)
      syncLabels(size.w, size.h, showSigns)
    }

    // ---- 크기 맞추기 ----
    const resize = () => {
      const w = host.clientWidth
      const h = host.clientHeight
      if (!w || !h) return
      size = { w, h }
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      // 굵은 선은 화면 크기를 알아야 두께를 계산한다.
      planMat.resolution.set(w, h)
      recoveryPlanMat.resolution.set(w, h)
      // 글자 크기는 화면 폭을 따라간다.
      labelHost.style.fontSize = `${w * LOOK.signSize}px`
      invalidate()
    }
    const ro = new ResizeObserver(resize)
    ro.observe(host)

    // ---- 시점 ----
    const center = new THREE.Vector3(1.72, 0.1, -0.95)
    const dir = new THREE.Vector3(...LOOK.viewFrom).normalize()
    let corners: THREE.Vector3[] = []

    /**
     * 건물이 화면에 꽉 차게 뒤로 물러난다.
     *
     * 기준은 **바닥 네 귀퉁이**다. 상자 전체(벽 높이 포함)로 맞추면 건물
     * 위쪽 빈 공간까지 화면에 넣으려 해서 건물이 작아진다.
     */
    const resetView = () => {
      if (!corners.length) return
      let d = 5
      for (let i = 0; i < 24; i += 1) {
        camera.position.copy(center).addScaledVector(dir, d)
        camera.lookAt(center)
        camera.updateMatrixWorld()
        let m = 0
        for (const c of corners) {
          const p = c.clone().project(camera)
          m = Math.max(m, Math.abs(p.x), Math.abs(p.y))
        }
        if (Math.abs(m - 0.94) < 0.005) break
        d *= Math.max(0.6, Math.min(1.6, m / 0.94))
      }
      controls.target.copy(center)
      camera.position.copy(center).addScaledVector(dir, d)
      controls.update()
      invalidate()
    }

    /**
     * 로봇 뒤에서 **로봇이 바라보는 쪽**을 함께 보는 시점.
     *
     * 처음에는 기본 시점 각도를 그대로 두고 거리만 줄였는데, 그러면 거의
     * 내려다보는 그림이라 로봇이 어느 쪽을 향하는지가 안 읽힌다. 그래서
     * 방위는 로봇 뒤로 돌리고 고도는 아래 값으로 낮춘다.
     *
     * 거리는 **건물 가로폭에 비례**시킨다. 고정 미터값을 쓰면 모형이 바뀔 때
     * 깨진다 — 실제로 한 번 박아 뒀다가, 그 값이 건물 전체와 비슷해서 당기라고
     * 했더니 오히려 화면이 멀어지는 일이 있었다.
     *
     * 바닥에 딱 맞추는 계산은 쓰지 않는다. 낮은 각도에서는 바닥 사각형의
     * 앞뒤가 크게 늘어나 카메라가 뒤로 밀리고, **정작 로봇이 작아진다.**
     *
     * **로봇이 엉뚱한 쪽을 보고 있으면 `FACING` 이 틀린 것이다.** 지금까지
     * 위에서만 봐서 티가 안 났을 뿐, 이 각도에서는 바로 드러난다.
     */
    const FOCUS_ELEV_DEG = 46

    const focusDistance = () => {
      if (corners.length < 3) return 0.8
      const w = Math.max(
        Math.abs(corners[1].x - corners[0].x),
        Math.abs(corners[2].z - corners[0].z),
      )
      return THREE.MathUtils.clamp(
        w * 0.22,
        controls.minDistance + 0.05,
        controls.maxDistance,
      )
    }

    const AUTO_FOCUS_HOLD_MS = 8000

    const focusRobot = (auto = false) => {
      if (!robot.visible) return
      if (auto && performance.now() - lastTouch < AUTO_FOCUS_HOLD_MS) return
      const at = robot.position.clone()
      at.y = 0.1
      // 로봇이 바라보는 쪽. `robot.rotation.y` 는 지도 각도에 FACING 을 더한
      // 값이므로, 여기서 나오는 방향은 **화면에 그려진 로봇이 향한 쪽과 항상
      // 같다.** 둘 다 같은 값에서 나오기 때문이다. 다만 그것이 **실제 로봇의
      // 앞**과 같은지는 FACING 이 맞아야 성립하고, FACING 은 아직 실물로
      // 확인하지 않았다.
      const forward = new THREE.Vector3(1, 0, 0).applyAxisAngle(
        new THREE.Vector3(0, 1, 0),
        robot.rotation.y,
      )
      const elev = THREE.MathUtils.degToRad(FOCUS_ELEV_DEG)
      const flat = Math.cos(elev)
      const viewDir = new THREE.Vector3(
        -forward.x * flat,
        Math.sin(elev),
        -forward.z * flat,
      )
      if (viewDir.lengthSq() < 1e-6) viewDir.copy(dir)
      viewDir.normalize()
      flyFromTarget.copy(controls.target)
      flyFrom.copy(camera.position)
      flyToTarget.copy(at)
      flyTo.copy(at).addScaledVector(viewDir, focusDistance())
      flyStart = performance.now()
      flying = true
      // 관성(damping)이 켜져 있어 그대로 두면 코드가 옮기는 것과 서로 싸워 떤다.
      controls.enabled = false
      invalidate()
    }

    /**
     * 회전한 모형의 **실제** 상자를 잰다.
     *
     * `Box3.setFromObject` 는 기하의 상자를 통째로 돌린 뒤 그 여덟 귀퉁이에
     * 다시 상자를 씌운다. 모형이 비스듬히 돌아 있으면 이 상자가 실제 모형보다
     * 커진다. 그 값으로 바닥에 맞추면 **로봇이 공중에 뜬다.**
     *
     * pinky.glb 가 정확히 그 경우다 — 노드에 쿼터니언 회전
     * (0.653, -0.271, 0.271, 0.653) 이 들어 있어서, 상자 기준으로는 바닥에
     * 닿았는데 눈으로는 떠 보였다.
     *
     * 꼭짓점을 하나씩 재면 정확하다. 모형을 읽을 때 한 번만 하므로 비용은
     * 문제가 되지 않는다(14만 점, 몇 ms).
     */
    const tightBox = (root: THREE.Object3D) => {
      const v = new THREE.Vector3()
      const box = new THREE.Box3()
      root.updateMatrixWorld(true)
      root.traverse((o) => {
        const me = o as THREE.Mesh
        const pos = me.isMesh ? me.geometry?.getAttribute('position') : null
        if (!pos) return
        for (let i = 0; i < pos.count; i += 1) {
          box.expandByPoint(v.fromBufferAttribute(pos, i).applyMatrix4(me.matrixWorld))
        }
      })
      return box
    }

    // ---- 모형 읽기 ----
    const loader = new GLTFLoader()
    loader.setMeshoptDecoder(MeshoptDecoder)

    const mats: THREE.MeshStandardMaterial[] = []
    const robotMats: Handles['robotMats'] = []
    loader.load('/models/pinky.glb', (gltf) => {
      if (disposed) return
      const model = gltf.scene
      // 팀 모형은 ROS/URDF 규약대로 z 가 위다. 이 화면은 y 가 위라 눕혀 세운다.
      model.rotation.x = -Math.PI / 2
      model.updateMatrixWorld(true)
      // 원점을 발밑 한가운데로 옮긴다. 그래야 로봇 좌표를 그대로 얹을 수 있다.
      // 상자를 씌워 재면 이 모형은 실제보다 아래가 커진다 — tightBox 주석 참고.
      const box = tightBox(model)
      const c = box.getCenter(new THREE.Vector3())
      model.position.set(-c.x, -box.min.y, -c.z)

      const seen = new Set<THREE.Material>()
      model.traverse((o) => {
        const m = o as THREE.Mesh
        if (!m.isMesh) return
        m.castShadow = true
        const mat = m.material as THREE.MeshStandardMaterial
        if (!mat || seen.has(mat)) return
        seen.add(mat)
        mat.envMapIntensity = LOOK.env
        mats.push(mat)
        // 무늬가 있는 재질이라 색은 곱해진다 — 붉게 칠하면 로봇 전체가
        // 무늬를 유지한 채 붉어진다.
        robotMats.push({ m: mat, base: mat.color.clone(), estop: COLOR.estop })
      })
      robot.add(model)
      // 모형은 늦게 온다. 그 사이에 이미 비상정지였다면 여기서 칠해 준다 —
      // 안 그러면 상태가 다시 바뀔 때까지 파란 로봇이 그대로 서 있는다.
      setRobotReady((n) => n + 1)
      invalidate()
    })

    loader.load(
      '/hospital-3d.glb',
      (gltf) => {
        if (disposed) return
        gltf.scene.traverse((o) => {
          const m = o as THREE.Mesh
          if (!m.isMesh) return
          m.castShadow = true
          m.receiveShadow = true
          const mat = m.material as THREE.MeshStandardMaterial
          if (mat) {
            mat.envMapIntensity = LOOK.env
            // 바닥은 그림자를 받기만 한다. 스스로 그림자를 만들면 자기 면에
            // 얼룩이 진다.
            if (mat.name === 'FLOOR') m.castShadow = false
            if (!mats.includes(mat)) mats.push(mat)
          }
        })
        scene.add(gltf.scene)

        const box = new THREE.Box3().setFromObject(gltf.scene)
        box.getCenter(center)
        center.y = box.min.y + (box.max.y - box.min.y) * 0.35
        corners = [
          new THREE.Vector3(box.min.x, box.min.y, box.min.z),
          new THREE.Vector3(box.max.x, box.min.y, box.min.z),
          new THREE.Vector3(box.min.x, box.min.y, box.max.z),
          new THREE.Vector3(box.max.x, box.min.y, box.max.z),
        ]
        sun.target.position.copy(center)
        sun.position.copy(center).add(new THREE.Vector3(...LOOK.sunFrom))
        resize()
        resetView()
        setReady(true)
      },
      undefined,
      () => !disposed && setFailed(true),
    )

    handles.current = {
      scene,
      camera,
      controls,
      renderer,
      robot,
      sun,
      hemi,
      mats,
      host,
      pulse,
      robotMats,
      scan: scanPts,
      particles: particlePts,
      plan: planLine,
      recoveryPlan: recoveryPlanLine,
      lowObstacle: lowObstacleGroup,
      lowObstacleFan,
      lowObstaclePoint,
      waypoints: wpGroup,
      invalidate,
      resetView,
      focusRobot,
      setPulse: (ms, color) => {
        pulseMs = ms ?? PULSE_MS
        pulseMat.color.setHex(color)
      },
      setRing: (on) => {
        if (on && !ring.visible) ringStart = performance.now()
        ring.visible = on
        invalidate()
      },
      setRingPosition: (x, z) => ring.position.set(x, 0.003, z),
      pick,
      pickWaypoint,
    }
    // 토글에서 부를 수 있게 걸어 둔다.
    ;(handles.current as Handles & { setShowSigns: (v: boolean) => void }).setShowSigns = setShowSigns

    resize()
    tick()

    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      ro.disconnect()
      controls.removeEventListener('change', invalidate)
      controls.removeEventListener('start', onGrab)
      controls.dispose()
      for (const l of labelsRef.current) l.el.remove()
      labelsRef.current = []
      scene.traverse((o) => {
        const m = o as THREE.Mesh
        if (m.geometry) m.geometry.dispose()
        const mat = m.material
        if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
        else if (mat) (mat as THREE.Material).dispose()
      })
      // 재질을 버려도 텍스처는 같이 안 버려진다. 위 traverse 가 재질까지만
      // 치우므로 손으로 만든 텍스처는 여기서 따로 버린다.
      contactTex.dispose()
      envTex.dispose()
      pmrem.dispose()
      renderer.dispose()
      renderer.domElement.remove()
      handles.current = null
    }
  }, [])

  // ---------------------------------------------------------------- 값 반영
  useEffect(() => {
    const h = handles.current
    if (!h) return
    if (pose) {
      const m = mapToModel(pose.x, pose.y)
      h.robot.position.set(m.u, 0, -m.v)
      h.robot.rotation.y = mapYawToModel(pose.yaw) + FACING
      h.robot.visible = true
      h.pulse.position.set(m.u, 0.004, -m.v)
      h.setRingPosition(m.u, -m.v)
    }
    h.robot.visible = !!pose
    // 박동은 여기서 건드리지 않는다. 위치는 초당 여러 번 들어오는데 그때마다
    // 색을 칠하면, 이탈로 노랗게 바꿔 둔 것이 다음 위치 갱신에 초록으로
    // 덮인다. 박동은 아래 한 곳에서만 정한다.
    for (const r of h.robotMats) {
      if (estop) r.m.color.setHex(r.estop)
      else r.m.color.copy(r.base)
    }
    h.invalidate()
  }, [pose, estop, robotReady])

  useEffect(() => {
    const h = handles.current
    if (!h) return
    const active = Boolean(
      pose && lowObstacle?.active &&
      typeof lowObstacle.distance === 'number' &&
      typeof lowObstacle.fov === 'number',
    )
    h.lowObstacle.visible = active
    if (active && lowObstacle?.distance != null && lowObstacle.fov != null) {
      const radius = THREE.MathUtils.clamp(lowObstacle.distance, 0.03, 0.50) / FIT.scale
      const fov = THREE.MathUtils.clamp(lowObstacle.fov, 0.10, Math.PI / 2)
      h.lowObstacleFan.geometry.dispose()
      h.lowObstacleFan.geometry = new THREE.CircleGeometry(
        1, 32, -fov / 2, fov)
      h.lowObstacleFan.scale.set(radius, radius, 1)
      h.lowObstaclePoint.position.x = radius
      const alarm = lowObstacle.state === 'FORWARD_BLOCKED'
      const color = alarm ? 0xef4444 : 0xf59e0b
      h.lowObstacleFan.material.color.setHex(color)
      h.lowObstaclePoint.material.color.setHex(color)
    }
    h.invalidate()
  }, [lowObstacle, pose, robotReady])

  // ------------------------------------------------------------- 추종 상태
  /**
   * 유예(`grace`)는 **놓친 게 아니다.** 카메라는 정상 동작 중에도 한두 프레임씩
   * 인식을 놓치는데, 그때마다 노란불을 켜면 잘 돌아가는 중에도 고장난 것처럼
   * 보인다. 로봇 쪽이 이미 `follow_source` 로 유예를 구분해 주므로 그냥 넘긴다.
   */
  // 넘겨받은 값이 있으면 그것만 쓴다. 없을 때만 스스로 받아 온다.
  const observed = usePolling(
    (signal) => (robotId && !follow ? getQrObservation(robotId, { signal }) : Promise.resolve(null)),
    QR_POLL_MS,
    robotId ?? null,
  )
  /**
   * 요청이 실패했을 때의 원칙: **좋은 소식은 즉시 버리고, 나쁜 소식은 붙든다.**
   *
   * 낡은 normal/slow 를 계속 보여주면 끊긴 추적을 정상으로 오인한다 — 그래서
   * 오류가 나면 비운다(기존 동작 유지). 그런데 waiting 까지 같이 비우면 반대
   * 방향의 거짓말이 생긴다. 폴링이 한 번만 실패해도 화면이 대기 상태를 잠깐
   * 벗어나면서 복귀 카운트다운이 20초부터 다시 시작하는데, 로봇의 시계는 그
   * 500 에러를 모르고 계속 돈다. 하네스로 재보니 오류 한 번에 화면이 7초를
   * 잃었고, 로봇이 떠나는 순간 화면에는 "복귀까지 10초" 가 남아 있었다.
   * 이탈 경보가 껌뻑이며 이벤트 로그도 두 줄로 갈라진다.
   *
   * waiting 은 경보다. 경보를 오류 동안 유지하는 것은 안전한 방향이고,
   * 그 사이에도 로봇은 실제로 세고 있으므로 사실에도 맞다. 연결이 돌아오면
   * 다음 폴링(0.5초)이 진실로 되돌린다.
   */
  const heldWaiting = useRef<QrObservation | null>(null)
  useEffect(() => {
    if (observed.error) return
    heldWaiting.current =
      observed.data?.follow_state === 'waiting' ? observed.data : null
  }, [observed.data, observed.error])
  const followNow =
    follow ?? (observed.error ? heldWaiting.current : observed.data)

  const mapState: MapState = useMemo(() => {
    if (estop) return 'estop'
    if (returning) return 'returning'
    if (!pose) return 'idle'
    if (followNow?.follow_state === 'waiting' && followNow.follow_source !== 'grace') return 'waiting'
    if (followNow?.follow_state === 'slow') return 'slow'
    if (followNow?.follow_state === 'normal') return 'escort'
    return 'idle'
  }, [estop, returning, pose, followNow?.follow_state, followNow?.follow_source])

  /**
   * 박동을 정하는 **유일한 곳**.
   *
   * 우선순위는 **비상정지 > 환자 이탈 > 선택됨** 이다. 두 곳에서 색을 칠하면
   * 위치가 갱신될 때마다 낮은 우선순위가 높은 것을 덮는다.
   *
   * | 무엇 | 색 | 주기 |
   * | --- | --- | --- |
   * | 비상정지 | 빨강 | 0.5초 (빠름) |
   * | 환자 이탈 | 노랑 | 2초 (느림) |
   * | 고른 로봇 | 초록 | 1.5초 (평상시) |
   *
   * 이탈은 **고르지 않은 로봇에서도** 박동한다. 고르지 않았다고 놓친 것을
   * 안 알리면, 두 대를 볼 때 한 대의 사고를 통째로 놓친다.
   */
  useEffect(() => {
    const h = handles.current
    if (!h) return
    const beat =
      mapState === 'estop'
        ? { ms: MAP_STATE.estop.pulseMs, color: COLOR.estop }
        : mapState === 'waiting'
          ? { ms: MAP_STATE.waiting.pulseMs, color: COLOR.wait }
          : selected
            ? { ms: PULSE_MS, color: COLOR.selected }
            : null

    h.pulse.visible = !!pose && beat !== null
    if (beat) h.setPulse(beat.ms, beat.color)
    h.setRing(MAP_STATE[mapState].ring && !!pose)
    h.invalidate()
  }, [mapState, pose, selected])

  /**
   * 복귀까지 남은 시간.
   *
   * 기다리는 것만 보여주면 보는 사람은 "언제까지 기다리나" 를 모른다.
   * 남은 시간이 보이면 **곧 무슨 일이 일어날지 알고 지켜볼 수 있다.**
   *
   * Guide Manager가 단조 시계로 계산한 실제 남은 시간을 우선 표시한다.
   * 브라우저를 새로고침해도 로봇의 시계는 계속되므로 숫자가 20초로
   * 되돌아가지 않는다.
   *
   * | 신호 | 여기서 맡는 일 |
   * |---|---|---|
   * | `patient_wait_remaining_sec` | 실제 남은 시간 |
   * | `follow_state` | 기다리는 상태인지 |
   * | `guide_robot_state` | 실제 복귀 시계가 도는지 |
   *
   * 배포 중 구버전 로봇은 새 값을 보내지 않는다. 그 경우에만 기존의
   * 브라우저 타이머를 fallback으로 사용해 화면 자체가 사라지지 않게 한다.
   */
  const waitStartedAt = useRef<number | null>(null)
  const [waitSec, setWaitSec] = useState<number | null>(null)
  const authoritativeWaitLeft =
    followNow?.patient_wait_remaining_sec ?? null

  useEffect(() => {
    if (mapState !== 'waiting') {
      // 환자가 돌아오면 로봇도 `_patient_wait_started_at` 을 0 으로 되돌린다.
      // 짧게 여러 번 놓친 것을 합산하면 잘 따라오는데도 복귀해 버린다.
      waitStartedAt.current = null
      setWaitSec(null)
      return
    }
    if (authoritativeWaitLeft !== null) {
      // 로봇의 실제 시계가 있으면 브라우저가 별도 시계를 만들지 않는다.
      waitStartedAt.current = null
      setWaitSec(null)
      return
    }
    if (waitStartedAt.current === null) waitStartedAt.current = performance.now()
    if (!paused) {
      // 로봇이 시계를 안 돌리는 경우다. 세지 않는다.
      setWaitSec(null)
      return
    }
    const startedAt = waitStartedAt.current
    let id = 0
    const tick = () => {
      const sec = (performance.now() - startedAt) / 1000
      setWaitSec(sec)
      // 시간이 지났는데도 상태가 그대로면 더 셀 이유가 없다.
      if (sec > waitLimitSec + WAIT_OVERDUE_SEC) window.clearInterval(id)
    }
    tick()
    id = window.setInterval(tick, 250)
    return () => window.clearInterval(id)
  }, [authoritativeWaitLeft, mapState, paused, waitLimitSec])

  /**
   * `paused` 는 **"시계가 돈다" 보다 넓다.** 노드를 띄워 재 보니, 이미 멈춰
   * 있던 로봇이 목표 없이 서 있는 상태에서 환자를 놓치면 시계는 안 도는데
   * `paused` 는 그대로 남아 있었다.
   *
   * 그 조합에서 화면만 세면 막대가 다 비고도 로봇이 안 간다. 그래서 시간이
   * 지나도 상태가 안 바뀌면 **화면이 스스로 말을 바꾼다.** 예고한 일이 안
   * 일어났을 때 조용히 틀린 채로 있는 것보다 낫다.
   */
  const fallbackWaitLeft = waitSec === null ? null : waitLimitSec - waitSec
  const waitLeft = mapState === 'waiting' && paused
    ? authoritativeWaitLeft ?? fallbackWaitLeft
    : null
  const waitOverdue = waitLeft !== null && waitLeft <= -WAIT_OVERDUE_SEC

  /**
   * 무슨 일이 있었는지 남긴다.
   *
   * 보는 사람은 화면을 계속 보고 있지 않는다. 잠깐 다른 데를 본 사이에
   * 이탈이 났다 복귀까지 끝나면 아무것도 못 본 것이 된다. **지나간 일이
   * 남아 있어야 놓쳐도 따라잡을 수 있다.**
   *
   * 지도 위에 겹쳐 놓는다 — 지도 아래에 줄을 더하면 이 컴포넌트가 높아져서
   * 팀이 짜 둔 화면 배치가 밀린다.
   */
  const prevState = useRef<MapState>('idle')
  const [log, setLog] = useState<{ id: number; at: string; text: string; tone: string }[]>([])

  useEffect(() => {
    const from = prevState.current
    prevState.current = mapState
    if (from === mapState) return
    const text =
      mapState === 'escort'
        ? from === 'slow'
          ? '환자가 따라붙어 정상 속도로'
          : '안내 시작'
        : mapState === 'slow'
          ? '환자가 멀어져 감속'
          : mapState === 'waiting'
            ? '경로이탈 — 그 자리에서 대기'
            : mapState === 'returning'
              // 사유는 화면까지 오지 않는다. 로봇 쪽 복귀 사유는 battery /
              // guidance_canceled / session_completed 셋인데 밖으로는
              // returning_to_dock 참·거짓만 나온다. 짐작해 적으면 틀린 말이
              // 화면에 남으므로 사실만 적는다.
              ? '충전소로 복귀 시작'
              : mapState === 'estop'
                ? '비상정지'
                : from === 'returning'
                  ? '충전소 복귀 완료'
                  : '안내 종료'
    const now = new Date()
    setLog((rows) =>
      [
        {
          id: now.getTime(),
          at: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`,
          text,
          tone: MAP_STATE[mapState].tone,
        },
        ...rows,
        // 네 줄이면 충분하다. 더 쌓이면 아무도 안 본다.
      ].slice(0, 4),
    )
  }, [mapState])

  /**
   * 환자를 놓친 순간 그쪽으로 화면을 당긴다. 시연에서는 이게 박동보다 세다 —
   * 화면이 들어가면 모든 눈이 그쪽으로 간다.
   *
   * 다만 **보는 사람이 화면을 만지는 중이면 하지 않는다.** 손에서 화면을
   * 뺏기지 않는 것이 자동으로 맞춰 주는 것보다 중요하다.
   */
  useEffect(() => {
    if (mapState !== 'waiting') return
    handles.current?.focusRobot(true)
  }, [mapState])

  useEffect(() => {
    const h = handles.current
    if (!h) return
    const attr = h.scan.geometry.getAttribute('position') as THREE.BufferAttribute
    const arr = attr.array as Float32Array
    let n = 0
    // 라이다는 로봇 기준 극좌표로 온다. pose 가 있어야 지도 위 어디인지 안다.
    if (visible.scan && scan && pose) {
      for (const [angle, range] of scan) {
        if (n >= MAX_SCAN) break
        if (!Number.isFinite(range)) continue
        const m = mapToModel(
          pose.x + range * Math.cos(pose.yaw + angle),
          pose.y + range * Math.sin(pose.yaw + angle),
        )
        arr[n * 3] = m.u
        arr[n * 3 + 1] = 0.05
        arr[n * 3 + 2] = -m.v
        n += 1
      }
    }
    h.scan.geometry.setDrawRange(0, n)
    attr.needsUpdate = true
    h.invalidate()
  }, [scan, pose, visible.scan])

  useEffect(() => {
    const h = handles.current
    if (!h) return
    const attr = h.particles.geometry.getAttribute('position') as THREE.BufferAttribute
    const arr = attr.array as Float32Array
    let n = 0
    if (visible.particles && particles) {
      for (const [x, y] of particles) {
        if (n >= MAX_PARTICLES) break
        const m = mapToModel(x, y)
        arr[n * 3] = m.u
        arr[n * 3 + 1] = 0.02
        arr[n * 3 + 2] = -m.v
        n += 1
      }
    }
    h.particles.geometry.setDrawRange(0, n)
    attr.needsUpdate = true
    h.invalidate()
  }, [particles, visible.particles])

  useEffect(() => {
    const h = handles.current
    if (!h) return
    // 굵은 선은 점을 통째로 다시 넘겨야 한다. 경로는 초당 몇 번뿐이라 괜찮다.
    const pts: number[] = []
    if (visible.plan && plan) {
      for (const [x, y] of plan) {
        if (pts.length >= MAX_PLAN * 3) break
        const m = mapToModel(x, y)
        // 바닥에서 1.5 cm 띄운다. 바닥면과 같은 높이면 얼룩덜룩 깜빡인다.
        pts.push(m.u, 0.015, -m.v)
      }
    }
    // 점이 둘 미만이면 선이 될 수 없다.
    h.plan.visible = pts.length >= 6
    if (h.plan.visible) {
      h.plan.geometry.setPositions(pts)
      h.plan.computeLineDistances()
    }
    h.invalidate()
  }, [plan, visible.plan])

  useEffect(() => {
    const h = handles.current
    if (!h) return
    const pts: number[] = []
    if (visible.plan && recoveryPlan) {
      for (const [x, y] of recoveryPlan) {
        if (pts.length >= MAX_PLAN * 3) break
        const m = mapToModel(x, y)
        // 원래 경로보다 조금 높여 겹쳐도 주황 점선이 구분되게 한다.
        pts.push(m.u, 0.02, -m.v)
      }
    }
    h.recoveryPlan.visible = pts.length >= 6
    if (h.recoveryPlan.visible) {
      h.recoveryPlan.geometry.setPositions(pts)
      h.recoveryPlan.computeLineDistances()
    }
    h.invalidate()
  }, [recoveryPlan, visible.plan])

  useEffect(() => {
    const h = handles.current as (Handles & { setShowSigns?: (v: boolean) => void }) | null
    h?.setShowSigns?.(visible.signs)
  }, [visible.signs])

  // 웨이포인트는 자주 바뀌지 않으므로 통째로 다시 만든다.
  useEffect(() => {
    const h = handles.current
    if (!h) return
    const g = h.waypoints
    g.traverse((o) => {
      const m = o as THREE.Mesh
      if (m.geometry) m.geometry.dispose()
      if (m.material) (m.material as THREE.Material).dispose()
    })
    g.clear()
    for (const w of waypoints) {
      const m = mapToModel(w.x, w.y)
      const color =
        w.status === 'blocked' || w.status === 'outside'
          ? COLOR.wpBad
          : w.status === 'warning'
            ? COLOR.wpWarn
            : COLOR.wpOk
      const pin = new THREE.Group()
      pin.position.set(m.u, 0, -m.v)
      pin.rotation.y = mapYawToModel(w.yaw)
      pin.userData.waypointName = w.name
      const disc = new THREE.Mesh(
        new THREE.CylinderGeometry(w.selected ? 0.05 : 0.036, w.selected ? 0.05 : 0.036, 0.006, 20),
        new THREE.MeshStandardMaterial({ color, roughness: 0.6 }),
      )
      disc.position.y = 0.004
      pin.add(disc)
      // 방향 표시. 어느 쪽을 보고 정차하는지가 도킹에서 중요하다.
      const nose = new THREE.Mesh(
        new THREE.ConeGeometry(0.018, 0.05, 14),
        new THREE.MeshStandardMaterial({ color, roughness: 0.6 }),
      )
      nose.rotation.z = -Math.PI / 2
      nose.position.set(0.062, 0.004, 0)
      pin.add(nose)
      g.add(pin)
    }
    h.invalidate()
  }, [waypoints])

  const spread = useMemo(
    () => (particles && particles.length > 1 ? particleSpread(particles) : null),
    [particles],
  )

  // 지정 모드에서는 화면을 돌리면 안 된다. 돌리려던 손짓이 위치 지정으로
  // 새면 로봇 위치가 엉뚱하게 잡힌다.
  useEffect(() => {
    const h = handles.current
    if (!h) return
    h.controls.enabled = !placing
  }, [placing])

  return (
    <section className="robot-map map3d">
      <header className="robot-map__header">
        <span className="robot-map__label">위치 · 로컬라이제이션</span>
        {/*
          색만으로는 구분하지 않는다. 화면을 비스듬히 보거나 조명이 다르면
          색이 틀어지므로 상태 이름을 항상 같이 적는다.
        */}
        <span className={`map3d__state map3d__state--${MAP_STATE[mapState].tone}`}>
          <i aria-hidden="true" />
          {MAP_STATE[mapState].text}
          {/* 거리는 실제로 환자를 잡고 있을 때만 쓴다. 놓친 뒤에도 마지막
              숫자가 남아 있으면 아직 보고 있는 것으로 잘못 읽힌다. */}
          {typeof followNow?.follow_distance === 'number' &&
            (mapState === 'escort' || mapState === 'slow') && (
              <b>{followNow.follow_distance.toFixed(2)} m</b>
            )}
          {/*
              줄어드는 막대가 주인공이다. 이 화면은 시연용 모니터에 띄워
              여러 사람이 떨어져서 본다 -- 두 자리 숫자는 안 읽히고 막대는
              읽힌다. 게다가 화면이 세는 값은 어림이라, 막대가 "대략 이만큼"
              을 숫자보다 정직하게 말한다.

              마지막 1초는 숫자 대신 "복귀 준비" 로 바꾼다. 0 을 띄워 두고
              멈춰 있는 것이 진행 표시에서 가장 오래된 결함이다.
          */}
          {waitLeft !== null && (
            <span className="map3d__wait">
              {!waitOverdue && (
                <span className="map3d__wait-bar" aria-hidden="true">
                  <i style={{ width: `${Math.max(0, waitLeft / waitLimitSec) * 100}%` }} />
                </span>
              )}
              <b>
                {waitOverdue
                  ? '복귀 시간 지남'
                  : waitLeft <= 1
                    ? '복귀 준비'
                    : `복귀까지 ${Math.ceil(waitLeft)}초`}
              </b>
            </span>
          )}
        </span>
        {lowObstacleState && (
          <span
            className={`map3d__obstacle map3d__obstacle--${LOW_OBSTACLE_DISPLAY[lowObstacleState].tone}`}
            role="status"
          >
            <i aria-hidden="true" />
            {LOW_OBSTACLE_DISPLAY[lowObstacleState].text}
          </span>
        )}
        {pose ? (
          <span className="robot-map__coord">
            x {pose.x.toFixed(2)} · y {pose.y.toFixed(2)} ·{' '}
            {((pose.yaw * 180) / Math.PI).toFixed(0)}°
          </span>
        ) : (
          <span className="robot-map__coord robot-map__coord--none">
            {live ? '위치 수신 대기 중' : '로봇 연결 없음'}
          </span>
        )}
      </header>

      <div className="robot-map__legend">
        {(Object.keys(LAYER_LABEL) as LayerKey[]).map((key) => (
          <button
            key={key}
            type="button"
            className={`robot-map__toggle robot-map__toggle--${key}${visible[key] ? '' : ' off'}`}
            onClick={() => setVisible((v) => ({ ...v, [key]: !v[key] }))}
          >
            {LAYER_LABEL[key]}
          </button>
        ))}
        {recoveryPlan && recoveryPlan.length > 1 && (
          <span className="robot-map__recovery-key">복구 경로</span>
        )}
        {onSetPose && (
          <button
            type="button"
            className={`robot-map__place${placing ? ' on' : ''}`}
            onClick={() => setPlacing((v) => !v)}
            title="로봇이 실제로 있는 위치를 바닥에서 찍어 알려줍니다. 끌면 방향까지 정해집니다"
          >
            {placing ? '바닥을 클릭하세요' : '위치 지정'}
          </button>
        )}
        {robotId && camera && (
          <button
            type="button"
            className={`robot-map__toggle${camOn ? '' : ' off'}`}
            onClick={() => setCamOn((v) => !v)}
            title="로봇 카메라를 지도 위에 띄웁니다. 끌어서 옮길 수 있습니다"
          >
            카메라
          </button>
        )}
        <button
          type="button"
          className="map3d__reset"
          onClick={() => handles.current?.focusRobot()}
          disabled={!pose}
          title="보는 각도는 그대로 두고 로봇 쪽으로 당깁니다"
        >
          로봇 보기
        </button>
        <button type="button" className="map3d__reset" onClick={() => handles.current?.resetView()}>
          시점 초기화
        </button>
        {spread !== null && (
          <span
            className={`robot-map__spread${spread > 0.5 ? ' robot-map__spread--wide' : ''}`}
            title="파티클이 넓게 퍼지면 위치추정이 발산한 것입니다"
          >
            퍼짐 {spread.toFixed(2)} m
          </span>
        )}
      </div>

      <div
        className={`map3d__stage${placing ? ' map3d__stage--placing' : ''}`}
        onPointerDown={(e) => {
          const h = handles.current
          if (!h) return
          if (!placing) {
            if (!onSelectWaypoint || !waypoints.length) return
            const name = h.pickWaypoint(e.clientX, e.clientY)
            if (name) onSelectWaypoint(name)
            return
          }
          const p = h.pick(e.clientX, e.clientY)
          if (p) setDrag(p)
        }}
        onPointerUp={(e) => {
          const h = handles.current
          if (!placing || !drag || !onSetPose || !h) return
          const end = h.pick(e.clientX, e.clientY)
          // 끌어서 방향을 준다. 그냥 누르면(거의 안 움직이면) 방향은 0 이다.
          const moved = end ? Math.hypot(end.u - drag.u, end.v - drag.v) : 0
          const modelYaw = end ? Math.atan2(end.v - drag.v, end.u - drag.u) : 0
          const m = modelToMap(drag.u, drag.v)
          onSetPose(m.x, m.y, moved > 0.05 ? modelYawToMap(modelYaw) : 0)
          setDrag(null)
          setPlacing(false)
        }}
      >
        <div ref={hostRef} className="map3d__canvas" />
        <div ref={labelHostRef} className="map3d__signs" />
        {camOn && robotId && camera && (
          <MapRearCam
            robotId={robotId}
            facing={camera}
            tone={MAP_STATE[mapState].tone}
            label={sourceText(followNow?.follow_source)}
          />
        )}
        {log.length > 0 && (
          <ul className="map3d__log">
            {log.map((row) => (
              <li key={row.id} className={`map3d__log-row map3d__log-row--${row.tone}`}>
                <time>{row.at}</time>
                <i aria-hidden="true" />
                <span>{row.text}</span>
              </li>
            ))}
          </ul>
        )}
        {!ready && !failed && <p className="map3d__loading">3D 지도를 불러오는 중…</p>}
        {failed && <p className="map3d__loading">3D 지도를 불러오지 못했습니다.</p>}
      </div>

      {!pose && (
        <p className="robot-map__empty">
          {live
            ? 'Nav2(AMCL)가 실행 중이어야 위치가 표시됩니다.'
            : '로봇의 조작 브리지가 연결되면 표시됩니다.'}
        </p>
      )}
    </section>
  )
}

/** 파티클 표준편차. 넓을수록 AMCL 이 확신을 잃은 것이다. */
function particleSpread(points: number[][]): number {
  const n = points.length
  const mx = points.reduce((a, p) => a + p[0], 0) / n
  const my = points.reduce((a, p) => a + p[1], 0) / n
  const varSum = points.reduce((a, p) => a + (p[0] - mx) ** 2 + (p[1] - my) ** 2, 0)
  return Math.sqrt(varSum / n)
}
