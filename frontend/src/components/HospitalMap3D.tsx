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
 * public/pinky.glb — 로봇. 예전 모형 파일에 붙박이로 들어 있던 핑키를 한 대만
 * 떼어 발밑 한가운데를 원점으로 옮긴 것이다(실측 11.6 x 13.6 x 11.0 cm).
 * 색은 전부 파랑 계열로 다시 칠했다 — 몸통(베이스링크)이 가장 밝고,
 * 바퀴와 윗판은 그보다 진하다. 원래는 흰 몸통에 검은 바퀴였는데 바닥도
 * 흰색이라 화면에서 로봇이 사라졌다.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js'
import { Line2 } from 'three/examples/jsm/lines/Line2.js'
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'

import { mapToModel, mapYawToModel, modelToMap, modelYawToMap } from './mapFrame'
import { SIGNS, type Sign } from './mapWaypoints'
import type { DiagLayers, RobotPose } from '../lib/useTeleopSocket'
import './HospitalMap3D.css'

export interface WaypointMarker {
  name: string
  x: number
  y: number
  yaw: number
  status?: 'ok' | 'warning' | 'blocked' | 'outside'
  selected?: boolean
}

interface Props extends DiagLayers {
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
  /** 안내판 목록. 기본은 mapWaypoints 의 SIGNS 다. 편집 화면만 이걸 바꿔 넘긴다. */
  signs?: Sign[]
  /** 켜면 글자를 끌어 옮길 수 있다. 편집 화면 전용이다. */
  editing?: boolean
  /** 글자를 끌었을 때. 실측 모델 좌표(u, v)로 알려 준다. */
  onSignMove?: (index: number, u: number, v: number) => void
  /** 편집 화면에서 고른 글자들. 테두리로 표시한다. */
  picked?: readonly number[]
  /** 글자를 눌렀을 때. shift 를 누른 채면 add 가 true 다. */
  onSignPick?: (index: number, add: boolean) => void
  /** 조명·배경을 덮어쓴다. 값을 맞춰 보는 화면에서만 쓴다. */
  look?: Partial<Look>
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
  plan: '경로',
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
  viewFrom: readonly [number, number, number]
}

export const LOOK: Look = {
  /**
   * 3D 뒤에 깔리는 색. 빛이 아니라서 **건물 밝기에는 영향이 없다** —
   * 건물이 흰색이라 배경이 밝으면 경계가 흐려지므로 어둡게 깔아 도드라지게 한다.
   *
   * 3D 안이 아니라 캔버스 뒤에 깐다. 3D 안에 넣으면 톤매핑을 타서 지정한
   * 값과 다른 색으로 나온다(밝은 회색을 넣었더니 더 밝게 나왔다).
   */
  background: '#b9bcbf',
  /** 하늘빛·바닥반사 받침 */
  sky: 0x9fb4c6,
  ground: 0xc8ccd0,
  fill: 0.55,
  /** 주광. 그림자를 만든다 */
  sun: 2.6,
  sunFrom: [1.4, 2.6, 1.9],
  /** 주변 반사(있는 듯 없는 듯). 재질에 생기를 준다 */
  env: 0.3,
  exposure: 1.0,
  /**
   * 안내 글자 크기. 지도 가로폭의 몇 배인가 — 지도가 커지면 글자도 같이
   * 커지므로, 지도를 넓게 쓰려면 이 값을 줄여야 글자가 커 보이지 않는다.
   */
  signSize: 0.0105,
  /**
   * 기본 시점 방향. 건물 중심에서 이쪽으로 물러나 바라본다.
   * 낮게 볼수록 입체감은 살지만 바닥이 벽에 가린다 — 바닥에 그리는 라이다를
   * 보려면 어느 정도 높이가 필요하다.
   */
  viewFrom: [0.08, 1.0, 0.62],
}

const COLOR = {
  scan: 0xef4444,
  particles: 0x10b981,
  plan: 0x2563eb,
  /** 박동 색. 선택은 초록, 비상정지는 빨강 — 움직임은 같고 색만 다르다 */
  selected: 0x22c55e,
  estop: 0xef4444,
  wpOk: 0x2563eb,
  wpWarn: 0xf59e0b,
  wpBad: 0xdc2626,
} as const

/**
 * 모형 안에서 로봇이 바라보던 쪽과 우리가 0도로 삼는 쪽의 차이(rad).
 * 라이노에서 놓인 방향이 지도의 0도와 같을 이유가 없어서 여기서 맞춘다.
 */
const FACING = Math.PI

/**
 * 비상정지 때 로봇을 물들일 색. 재질 이름별로 짝을 지어, 평소의 밝고 어두운
 * 차례를 그대로 붉은 쪽으로 옮긴다. 전부 같은 빨강으로 칠하면 형태가 뭉개져
 * 로봇이 아니라 붉은 덩어리로 보인다.
 */
const ESTOP_TINT: Record<string, number> = {
  BASE: 0x5e1618,
  BODY: 0xed3f3f,
  TOP: 0x8f2224,
}

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
  waypoints: THREE.Group
  invalidate: () => void
  setSignSize: (v: number) => void
  resetView: () => void
  pick: (clientX: number, clientY: number, atY?: number) => { u: number; v: number } | null
  pickWaypoint: (clientX: number, clientY: number) => string | null
}

export function HospitalMap3D({
  pose,
  live,
  scan,
  particles,
  plan,
  onSetPose,
  waypoints = [],
  onSelectWaypoint,
  estop = false,
  selected = false,
  signs = SIGNS,
  editing = false,
  onSignMove,
  picked,
  onSignPick,
  look,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const labelHostRef = useRef<HTMLDivElement | null>(null)
  const handles = useRef<Handles | null>(null)
  const labelsRef = useRef<LabelHandle[]>([])

  const [ready, setReady] = useState(false)
  const [robotReady, setRobotReady] = useState(0)
  const [failed, setFailed] = useState(false)
  const [placing, setPlacing] = useState(false)
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
    // 핑키 실물 모형(public/pinky.glb)을 쓴다. 실측 11.6 x 13.6 x 11.0 cm 이라
    // 그대로 놓으면 통로에 들어가는지가 눈에 보인다. 도형으로 대충 그리면
    // 그 판단을 못 한다.
    const robot = new THREE.Group()
    robot.visible = false
    scene.add(robot)

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

    // ---- 진단 레이어 ----
    // 미리 자리를 잡아 두고 개수만 바꾼다. 매번 새로 만들면 초당 수십 번
    // 버퍼를 새로 올리게 돼 화면이 끊긴다.
    const makePoints = (
      max: number,
      color: number,
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
    const scanPts = makePoints(MAX_SCAN, COLOR.scan, 3.5, 0.05, 2, false)
    // 파티클은 실제 크기(1.4 cm)다. 수백 개가 한자리에 모였을 때 화면 기준
    // 크기면 초록 덩어리가 되어 로봇을 덮어 버리는데, 실제 크기면 멀리서
    // 저절로 작아져 모였을 때는 옅은 무리로, 퍼졌을 때는 넓은 안개로 읽힌다.
    const particlePts = makePoints(MAX_PARTICLES, COLOR.particles, 0.014, 0.02, 1, true)

    // 경로는 굵은 선이라야 읽힌다.
    // three.js 의 기본 선(THREE.Line)은 굵기 지정이 대부분의 브라우저에서
    // 무시돼 언제나 1픽셀로 나온다. 비스듬히 본 3D 화면에서 1픽셀 선은
    // 계단처럼 끊겨 보여서, 화면 공간에서 두께를 만드는 Line2 를 쓴다.
    // 경로도 가리지 않는다. 로봇이 어디로 가려는지는 계획이지 측정이 아니라,
    // 20 cm 짜리 벽에 가려 안 보이면 레이어 자체가 쓸모없어진다.
    const planMat = new LineMaterial({
      color: COLOR.plan,
      linewidth: 3.5,
      transparent: true,
      opacity: 0.95,
      depthWrite: false,
      depthTest: false,
      dashed: false,
    })
    const planLine = new Line2(new LineGeometry(), planMat)
    planLine.frustumCulled = false
    planLine.renderOrder = 5
    planLine.visible = false
    scene.add(planLine)

    const wpGroup = new THREE.Group()
    scene.add(wpGroup)

    // ---- 안내 글자 ----
    // 3D 안에 글자를 넣는 대신 HTML 을 위에 띄운다. 그래야 항상 화면을
    // 정면으로 보고, 글꼴·굵기·색을 CSS 로 그대로 쓸 수 있다.

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

    let signSize = LOOK.signSize
    const setSignSize = (v: number) => {
      signSize = v
      labelHost.style.fontSize = `${host.clientWidth * v}px`
      invalidate()
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
      const moved = controls.update()
      // 박동. 1.5초를 한 주기로 커지면서 옅어진다. 처음에 빠르고 끝에서
      // 느려지는 곡선(ease-out)이라야 심장 박동처럼 읽힌다.
      if (pulse.visible) {
        const t = (performance.now() % PULSE_MS) / PULSE_MS
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
      // 글자 크기는 화면 폭을 따라간다.
      labelHost.style.fontSize = `${w * signSize}px`
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

    // ---- 모형 읽기 ----
    const loader = new GLTFLoader()
    loader.setMeshoptDecoder(MeshoptDecoder)

    const mats: THREE.MeshStandardMaterial[] = []
    const robotMats: Handles['robotMats'] = []
    loader.load('/pinky.glb', (gltf) => {
      if (disposed) return
      const seen = new Set<THREE.Material>()
      gltf.scene.traverse((o) => {
        const m = o as THREE.Mesh
        if (!m.isMesh) return
        m.castShadow = true
        const mat = m.material as THREE.MeshStandardMaterial
        if (!mat || seen.has(mat)) return
        seen.add(mat)
        mat.envMapIntensity = LOOK.env
        mats.push(mat)
        robotMats.push({
          m: mat,
          base: mat.color.clone(),
          estop: ESTOP_TINT[mat.name] ?? ESTOP_TINT.BODY,
        })
      })
      // 모형 원점은 발밑 한가운데다(뽑을 때 그렇게 옮겼다). 그대로 얹으면 된다.
      robot.add(gltf.scene)
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
      setSignSize,
      scan: scanPts,
      particles: particlePts,
      plan: planLine,
      waypoints: wpGroup,
      invalidate,
      resetView,
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
      envTex.dispose()
      pmrem.dispose()
      renderer.dispose()
      renderer.domElement.remove()
      handles.current = null
    }
  }, [])

  // ---------------------------------------------------------------- 조명
  // 값을 손으로 맞춰 보려면 장면을 다시 만들지 않고 바로 바뀌어야 한다.
  const lookKey = JSON.stringify(look ?? null)
  useEffect(() => {
    const h = handles.current
    if (!h) return
    const L = { ...LOOK, ...(look ?? {}) }
    h.sun.intensity = L.sun
    h.sun.position.copy(h.sun.target.position).add(new THREE.Vector3(...L.sunFrom))
    h.hemi.intensity = L.fill
    h.hemi.color.set(L.sky)
    h.hemi.groundColor.set(L.ground)
    h.renderer.toneMappingExposure = L.exposure
    h.host.style.background = L.background
    h.setSignSize(L.signSize)
    for (const m of h.mats) m.envMapIntensity = L.env
    h.invalidate()
    // lookKey 로 값이 실제로 바뀐 때만 돈다(객체는 매번 새로 만들어진다).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookKey, ready, robotReady])

  // ---------------------------------------------------------------- 안내 글자
  // 콜백은 렌더마다 새 함수가 되므로 상자에 담아 둔다. 이렇게 해야 글자
  // DOM 을 다시 만들지 않아도 최신 콜백이 불린다 — 끄는 도중에 DOM 이
  // 새로 만들어지면 손을 놓친다.
  const cbMove = useRef(onSignMove)
  const cbPick = useRef(onSignPick)
  cbMove.current = onSignMove
  cbPick.current = onSignPick

  // 글자 목록이 바뀔 때만 DOM 을 다시 만든다.
  const signKey = signs.map((s) => `${s.label}${s.sub ?? ''}${s.rank}`).join('')
  useEffect(() => {
    const labelHost = labelHostRef.current
    if (!labelHost) return
    for (const l of labelsRef.current) l.el.remove()

    labelsRef.current = signs.map((s, i) => {
      const el = document.createElement('div')
      el.className = 'map3d__sign' + (s.rank === 1 ? '' : ' map3d__sign--minor')
      el.textContent = s.label
      if (s.sub) {
        el.appendChild(document.createElement('br'))
        el.appendChild(document.createTextNode(s.sub))
      }
      if (editing) {
        el.classList.add('map3d__sign--editing')
        el.addEventListener('pointerdown', (e) => {
          // 글자를 끄는 동안 화면이 같이 돌면 안 된다.
          e.stopPropagation()
          e.preventDefault()
          cbPick.current?.(i, e.shiftKey)
          const h = handles.current
          const cur = labelsRef.current[i]
          if (!h || !cur || !cbMove.current) return
          const start = h.pick(e.clientX, e.clientY, cur.y)
          if (!start) return
          // 글자 가운데가 아니라 **집은 자리**를 기준으로 따라오게 한다.
          const offU = cur.x - start.u
          const offV = -cur.z - start.v
          el.setPointerCapture(e.pointerId)
          const move = (ev: PointerEvent) => {
            const p = h.pick(ev.clientX, ev.clientY, cur.y)
            if (p) cbMove.current?.(i, p.u + offU, p.v + offV)
          }
          const up = () => {
            el.removeEventListener('pointermove', move)
            el.removeEventListener('pointerup', up)
            el.removeEventListener('pointercancel', up)
          }
          el.addEventListener('pointermove', move)
          el.addEventListener('pointerup', up)
          el.addEventListener('pointercancel', up)
        })
      }
      labelHost.appendChild(el)
      return { el, x: s.u, y: s.y ?? 0.22, z: -s.v }
    })
    handles.current?.invalidate()
    // signKey 가 같으면 글자 내용이 그대로라 다시 만들 필요가 없다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signKey, editing])

  // 자리와 고름 표시는 DOM 을 새로 만들지 않고 값만 고친다.
  useEffect(() => {
    const ls = labelsRef.current
    signs.forEach((s, i) => {
      const l = ls[i]
      if (!l) return
      l.x = s.u
      l.y = s.y ?? 0.22
      l.z = -s.v
      l.el.classList.toggle('is-picked', !!picked?.includes(i))
    })
    handles.current?.invalidate()
  }, [signs, picked])

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
    }
    h.robot.visible = !!pose
    // 박동은 고른 로봇이거나 비상정지일 때만. 늘 뛰면 아무것도 알리지 못한다.
    h.pulse.visible = !!pose && (selected || estop)
    h.pulse.material.color.setHex(estop ? COLOR.estop : COLOR.selected)
    for (const r of h.robotMats) {
      if (estop) r.m.color.setHex(r.estop)
      else r.m.color.copy(r.base)
    }
    h.invalidate()
  }, [pose, estop, selected, robotReady])

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
