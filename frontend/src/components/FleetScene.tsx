/**
 * 병원 3D 맵 위에 플릿 4대를 한 화면에 얹는다 — 핑키 2대는 실시간 주행
 * 좌표로 움직이고, OMX 2대는 약국 창구에 고정된다.
 *
 * ## HospitalMap3D 와의 관계
 *
 * 렌더링 골격(동적 three import·저전력 렌더러·dispose 정리·reduce-motion)만
 * 빌려 왔고, 1400줄짜리 그 컴포넌트를 복붙하지 않는다. 이 씬은 **pose 만**
 * 쓴다 — 라이다·파티클·경로·카메라·조작 UI 는 없다. 의료진 대시보드에 얹는
 * 개관용 위젯이라 가벼워야 한다.
 *
 * ## pose 를 어떻게 받나 (읽기 전용)
 *
 * 핑키마다 teleop operator 소켓에 **뷰어로만** 붙는다(usePinkyPose 2 인스턴스).
 * 그 엔드포인트는 로봇당 여러 읽기 연결을 허용한다(_operators: set). 조작
 * 명령(cmd_vel·set_pose)은 절대 보내지 않고, actor 도 붙이지 않는다 — 뷰어는
 * 조작자가 아니므로 감사 로그에 이름을 남기지 않는다.
 */

import { useEffect, useMemo, useRef } from 'react'

import { mapToModel, mapYawToModel } from './mapFrame'
import { buildArm } from './OmxModel'
import { usePinkyPose } from '../lib/usePinkyPose'
import { usePolling } from '../lib/usePolling'
import { getRobots } from '../lib/api'
import type { RobotPose } from '../lib/useTeleopSocket'
import type { Robot } from '../types/monitoring'

import './FleetScene.css'

const POLL_MS = 5000

/** 이 씬이 그리는 로봇 4대. id 는 backend seed(pinky-01/02·omx-01/02)와 같다. */
const PINKY_IDS = ['pinky-01', 'pinky-02'] as const
const OMX_IDS = ['omx-01', 'omx-02'] as const

/**
 * OMX 스테이션 위치. yun_map_highres_clean_waypoints.yaml 의 pharmacy_goal 이
 * 약국 창구다. 팔은 주행하지 않으므로 좌표를 상수로 박고, 두 대가 겹치지
 * 않게 창구 앞에서 모델 x 로 ±0.3 m 갈라 세운다.
 */
const PHARMACY_GOAL = { x: 0.108119, y: 0.474077, yaw: 1.726906 }
const OMX_SPREAD_M = 0.3

/** 상태 배지의 종류. 라벨 색과 로봇 흐리기를 함께 정한다. */
type FleetStatus = 'active' | 'idle' | 'no-pose' | 'offline'

interface FleetView {
  id: string
  name: string
  status: FleetStatus
  badge: string | null
}

/** 씬에 얹는 로봇 하나의 조작 핸들. glb·팔이 늦게 와도 루프가 안전하게 읽는다. */
interface RobotHandle {
  group: import('three').Group
  materials: import('three').Material[]
  dimmed: boolean
  /** 핑키만: 마지막으로 반영한 pose 가 있었는지. 숨김/표시를 정한다. */
  placed: boolean
}

function statusOf(robot: Robot | undefined, pose: RobotPose | null, isPinky: boolean): FleetView {
  const name = robot?.display_name ?? '(알 수 없음)'
  if (!robot || robot.link_state !== 'online') {
    return { id: robot?.robot_id ?? '', name, status: 'offline', badge: '오프라인' }
  }
  if (isPinky && !pose) {
    // 핑키가 아직 localize 안 됐거나 pose 가 안 온다. 흔한 정상 상태다 —
    // 크래시 대신 배지로 알리고 모델은 숨긴다.
    return { id: robot.robot_id, name, status: 'no-pose', badge: '위치 없음' }
  }
  const busy = robot.robot_type === 'mobile' && robot.active_session_id != null
  return {
    id: robot.robot_id,
    name,
    status: busy ? 'active' : 'idle',
    badge: busy ? '가동 중' : null,
  }
}

export function FleetScene() {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const labelHostRef = useRef<HTMLDivElement | null>(null)

  const robots = usePolling((signal) => getRobots({ signal }), POLL_MS)
  // 핑키 2대의 pose 를 각각 읽기 전용 소켓으로 구독한다(소켓 2개).
  const pose01 = usePinkyPose(PINKY_IDS[0])
  const pose02 = usePinkyPose(PINKY_IDS[1])

  const byId = useMemo(() => {
    const map = new Map<string, Robot>()
    for (const r of robots.data ?? []) map.set(r.robot_id, r)
    return map
  }, [robots.data])

  const views = useMemo<FleetView[]>(() => [
    statusOf(byId.get(PINKY_IDS[0]), pose01.pose, true),
    statusOf(byId.get(PINKY_IDS[1]), pose02.pose, true),
    statusOf(byId.get(OMX_IDS[0]), null, false),
    statusOf(byId.get(OMX_IDS[1]), null, false),
  ], [byId, pose01.pose, pose02.pose])

  // 루프가 매 프레임 읽는 최신값. React state 를 루프에 끌어들이지 않으려고
  // ref 로 넘긴다 — pose 는 초당 여러 번 들어온다.
  const liveRef = useRef({
    poses: {
      [PINKY_IDS[0]]: pose01.pose as RobotPose | null,
      [PINKY_IDS[1]]: pose02.pose as RobotPose | null,
    },
    views,
  })
  useEffect(() => {
    liveRef.current = {
      poses: { [PINKY_IDS[0]]: pose01.pose, [PINKY_IDS[1]]: pose02.pose },
      views,
    }
  }, [pose01.pose, pose02.pose, views])

  useEffect(() => {
    const host = hostRef.current
    const labelHost = labelHostRef.current
    if (!host || !labelHost) return

    let disposed = false
    let raf = 0
    let cleanup = () => undefined

    Promise.all([
      import('three'),
      import('three/examples/jsm/loaders/GLTFLoader.js'),
      import('three/examples/jsm/libs/meshopt_decoder.module.js'),
    ])
      .then(([THREE, { GLTFLoader }, { MeshoptDecoder }]) => {
        if (disposed) return

        const scene = new THREE.Scene()
        host.style.background = '#c5cbd1'

        const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 60)

        const renderer = new THREE.WebGLRenderer({
          alpha: true,
          antialias: true,
          powerPreference: 'low-power',
        })
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5))
        renderer.outputColorSpace = THREE.SRGBColorSpace
        renderer.toneMapping = THREE.ACESFilmicToneMapping
        renderer.toneMappingExposure = 0.72
        host.appendChild(renderer.domElement)

        // ---- 빛: HospitalMap3D 톤을 따르되 그림자는 뺀다(개관용, 가볍게) ----
        scene.add(new THREE.HemisphereLight(0x9fb4c6, 0xc8ccd0, 2.2))
        const sun = new THREE.DirectionalLight(0xffffff, 2.4)
        sun.position.set(0.5, 3.3, 1.0)
        scene.add(sun)

        // ---- 라벨: 캔버스 위에 HTML 로 얹고, 루프에서 화면 좌표로 투영한다 ----
        const labelEls = new Map<string, HTMLDivElement>()
        for (const id of [...PINKY_IDS, ...OMX_IDS]) {
          const el = document.createElement('div')
          el.className = 'fleet-label'
          el.dataset.id = id
          labelHost.appendChild(el)
          labelEls.set(id, el)
        }

        /** 재질에 흐리기를 반영한다. 오프라인·위치 없음이면 반투명하게 뒤로 뺀다. */
        const applyDim = (handle: RobotHandle, dim: boolean) => {
          if (handle.dimmed === dim) return
          handle.dimmed = dim
          for (const m of handle.materials) {
            const mat = m as import('three').Material & { opacity: number; transparent: boolean }
            mat.transparent = dim
            mat.opacity = dim ? 0.28 : 1
            mat.needsUpdate = true
          }
        }

        const collectMats = (root: import('three').Object3D) => {
          const mats: import('three').Material[] = []
          const seen = new Set<import('three').Material>()
          root.traverse((o) => {
            const mesh = o as import('three').Mesh
            if (!mesh.isMesh) return
            const list = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
            for (const m of list) {
              if (m && !seen.has(m)) {
                seen.add(m)
                mats.push(m)
              }
            }
          })
          return mats
        }

        const pinkyHandles = new Map<string, RobotHandle>()
        const omxHandles = new Map<string, RobotHandle>()
        const omxJoints: ReturnType<typeof buildArm>['joints'][] = []

        // ---- OMX 2대: buildArm 으로 약국 창구에 고정 ----
        const base = mapToModel(PHARMACY_GOAL.x, PHARMACY_GOAL.y)
        const omxYaw = mapYawToModel(PHARMACY_GOAL.yaw)
        OMX_IDS.forEach((id, i) => {
          const { root, joints } = buildArm(THREE)
          // buildArm 은 카드 썸네일용이라 원본 크기로 맵에 올리면 방 높이만큼
          // 커진다. 실제 데스크톱 팔(~0.5m)로 정규화해 핑키와 비율을 맞춘다.
          const bb = new THREE.Box3().setFromObject(root)
          const nativeH = bb.getSize(new THREE.Vector3()).y || 1
          root.scale.setScalar(0.5 / nativeH)
          const offset = (i === 0 ? -1 : 1) * OMX_SPREAD_M
          root.position.set(base.u + offset, 0, -base.v)
          root.rotation.y = omxYaw
          scene.add(root)
          omxHandles.set(id, { group: root, materials: collectMats(root), dimmed: false, placed: true })
          omxJoints.push(joints)
        })

        const loader = new GLTFLoader()
        loader.setMeshoptDecoder(MeshoptDecoder)

        // 회전한 glb 의 실제 바닥을 재서 발밑을 원점에 맞춘다(HospitalMap3D tightBox).
        const tightBox = (root: import('three').Object3D) => {
          const v = new THREE.Vector3()
          const box = new THREE.Box3()
          root.updateMatrixWorld(true)
          root.traverse((o) => {
            const me = o as import('three').Mesh
            const pos = me.isMesh ? me.geometry?.getAttribute('position') : null
            if (!pos) return
            for (let i = 0; i < pos.count; i += 1) {
              box.expandByPoint(v.fromBufferAttribute(pos, i).applyMatrix4(me.matrixWorld))
            }
          })
          return box
        }

        // ---- 핑키 2대: pinky.glb 를 각자 로드(재질 독립 → 개별 흐리기 가능) ----
        for (const id of PINKY_IDS) {
          const group = new THREE.Group()
          group.visible = false
          scene.add(group)
          const handle: RobotHandle = { group, materials: [], dimmed: false, placed: false }
          pinkyHandles.set(id, handle)
          loader.load('/models/pinky.glb', (gltf) => {
            if (disposed) return
            const model = gltf.scene
            // URDF 규약(z-up)을 이 씬(y-up)에 맞춰 눕혀 세운다.
            model.rotation.x = -Math.PI / 2
            model.updateMatrixWorld(true)
            const box = tightBox(model)
            const c = box.getCenter(new THREE.Vector3())
            model.position.set(-c.x, -box.min.y, -c.z)
            group.add(model)
            handle.materials = collectMats(model)
          })
        }

        // ---- 병원 맵 ----
        loader.load('/hospital-3d.glb', (gltf) => {
          if (disposed) return
          scene.add(gltf.scene)
          const box = new THREE.Box3().setFromObject(gltf.scene)
          const center = box.getCenter(new THREE.Vector3())
          const size = box.getSize(new THREE.Vector3())
          const span = Math.max(size.x, size.z)
          // 건물 전체가 담기게 비스듬히 내려다본다. 거리는 건물폭 비례라
          // 모형이 바뀌어도 깨지지 않는다.
          camera.position.set(center.x + span * 0.05, box.min.y + span * 0.95, center.z + span * 0.85)
          camera.lookAt(center.x, box.min.y, center.z)
          sun.target.position.copy(center)
          scene.add(sun.target)
        })

        // ---- 크기 맞추기 ----
        const resize = () => {
          const w = host.clientWidth
          const h = host.clientHeight
          if (!w || !h) return
          renderer.setSize(w, h, false)
          camera.aspect = w / h
          camera.updateProjectionMatrix()
        }
        const ro = new ResizeObserver(resize)
        ro.observe(host)
        resize()

        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
        const projected = new THREE.Vector3()
        let previous = performance.now()
        let phase = 0

        const frame = (now: number) => {
          if (disposed) return
          raf = requestAnimationFrame(frame)
          // 탭이 숨으면 그리지 않는다 — 개관 위젯이 배경에서 GPU 를 물지 않게.
          if (document.hidden) return
          const dt = Math.min((now - previous) / 1000, 0.1)
          previous = now
          const { poses, views: liveViews } = liveRef.current

          // 핑키: 최신 pose 로 위치·회전. pose 없으면 마지막 위치 유지, 한 번도
          // 못 받았으면 숨김(위치 없음 배지가 대신 알린다).
          for (const id of PINKY_IDS) {
            const handle = pinkyHandles.get(id)
            if (!handle) continue
            const pose = poses[id]
            if (pose) {
              const m = mapToModel(pose.x, pose.y)
              handle.group.position.set(m.u, 0, -m.v)
              handle.group.rotation.y = mapYawToModel(pose.yaw)
              handle.placed = true
            }
            handle.group.visible = handle.placed && handle.materials.length > 0
          }

          // OMX: 은은한 idle pick&place(OmxModel 과 같은 톤). reduce-motion 이면 정지.
          if (!reduceMotion.matches) {
            phase += dt * 0.5
            const reach = (1 - Math.cos(phase)) / 2
            for (const j of omxJoints) {
              j.waist.rotation.y = Math.sin(phase * 0.5) * 0.5
              j.shoulder.rotation.z = -0.35 + reach * 0.55
              j.elbow.rotation.z = 0.95 - reach * 0.8
              j.wrist.rotation.z = -0.25 + reach * 0.25
              const grip = 0.018 + (1 - reach) * 0.02
              j.fingerLeft.position.x = -grip
              j.fingerRight.position.x = grip
            }
          }

          // 상태 반영: 흐리기 + 라벨 텍스트·화면 좌표.
          for (const view of liveViews) {
            const handle = pinkyHandles.get(view.id) ?? omxHandles.get(view.id)
            if (!handle) continue
            const dim = view.status === 'offline' || view.status === 'no-pose'
            applyDim(handle, dim)

            const el = labelEls.get(view.id)
            if (!el) continue
            const visible = handle.group.visible || omxHandles.has(view.id)
            if (!visible) {
              el.style.display = 'none'
              continue
            }
            projected.copy(handle.group.position)
            projected.y += 0.6
            projected.project(camera)
            if (projected.z > 1) {
              el.style.display = 'none'
              continue
            }
            const x = (projected.x * 0.5 + 0.5) * host.clientWidth
            const y = (-projected.y * 0.5 + 0.5) * host.clientHeight
            el.style.display = ''
            el.style.transform = `translate(-50%, -100%) translate(${x}px, ${y}px)`
            el.dataset.status = view.status
            el.innerHTML = ''
            const nameEl = document.createElement('span')
            nameEl.className = 'fleet-label__name'
            nameEl.textContent = view.name
            el.appendChild(nameEl)
            if (view.badge) {
              const badgeEl = document.createElement('span')
              badgeEl.className = 'fleet-label__badge'
              badgeEl.textContent = view.badge
              el.appendChild(badgeEl)
            }
          }

          renderer.render(scene, camera)
        }
        raf = requestAnimationFrame(frame)

        cleanup = () => {
          cancelAnimationFrame(raf)
          ro.disconnect()
          for (const el of labelEls.values()) el.remove()
          scene.traverse((o) => {
            const mesh = o as import('three').Mesh
            mesh.geometry?.dispose?.()
            const mat = mesh.material
            if (Array.isArray(mat)) mat.forEach((x) => x?.dispose())
            else (mat as import('three').Material | undefined)?.dispose?.()
          })
          renderer.dispose()
          renderer.domElement.remove()
        }
      })
      .catch(() => undefined)

    return () => {
      disposed = true
      cleanup()
    }
  }, [])

  return (
    <section className="fleet-scene" aria-label="병원 3D 플릿 개관">
      <div className="fleet-scene__viewport" ref={hostRef}>
        <div className="fleet-scene__labels" ref={labelHostRef} aria-hidden="true" />
      </div>
    </section>
  )
}
