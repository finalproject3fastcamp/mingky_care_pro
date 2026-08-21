import { useEffect, useRef, useState } from 'react'
import type { Group, Material, Mesh } from 'three'

type ThreeModule = typeof import('three')

/**
 * OpenManipulator-X 느낌의 stylized 로봇팔을 three.js 프리미티브로 절차 생성한다.
 * 진짜 OMX glb 가 생기면 이 함수만 loader 로 갈아끼우면 된다.
 * 반환하는 joints 참조들이 애니메이션 루프에서 각도로 구동된다.
 */
export function buildArm(THREE: ThreeModule) {
  // 은은한 무채색 금속 + 포인트 컬러 1개(앰버). 앱 accent(파랑)·핑키 톤과 겹치지 않게.
  const metal = new THREE.MeshStandardMaterial({ color: 0xb9bec9, metalness: 0.6, roughness: 0.42 })
  const dark = new THREE.MeshStandardMaterial({ color: 0x5b616f, metalness: 0.5, roughness: 0.55 })
  const accent = new THREE.MeshStandardMaterial({ color: 0xf0a83c, metalness: 0.35, roughness: 0.5 })

  const root = new THREE.Group()

  // 바닥 베이스 원통
  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.14, 0.06, 32), dark)
  base.position.y = 0.03
  root.add(base)

  // 허리 회전 조인트(수직축 회전)
  const waist = new THREE.Group()
  waist.position.y = 0.06
  root.add(waist)
  const waistCap = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.09, 0.05, 24), accent)
  waistCap.position.y = 0.025
  waist.add(waistCap)

  // 어깨 조인트 → 하완 링크
  const shoulder = new THREE.Group()
  shoulder.position.y = 0.05
  waist.add(shoulder)
  const shoulderPin = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.11, 20), metal)
  shoulderPin.rotation.x = Math.PI / 2
  shoulder.add(shoulderPin)
  const lowerLink = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.22, 0.05), metal)
  lowerLink.position.y = 0.11
  shoulder.add(lowerLink)

  // 팔꿈치 조인트 → 상완 링크
  const elbow = new THREE.Group()
  elbow.position.y = 0.22
  shoulder.add(elbow)
  const elbowPin = new THREE.Mesh(new THREE.CylinderGeometry(0.032, 0.032, 0.1, 20), accent)
  elbowPin.rotation.x = Math.PI / 2
  elbow.add(elbowPin)
  const upperLink = new THREE.Mesh(new THREE.BoxGeometry(0.045, 0.2, 0.045), metal)
  upperLink.position.y = 0.1
  elbow.add(upperLink)

  // 손목 조인트
  const wrist = new THREE.Group()
  wrist.position.y = 0.2
  elbow.add(wrist)
  const wristPin = new THREE.Mesh(new THREE.CylinderGeometry(0.028, 0.028, 0.08, 16), metal)
  wristPin.rotation.x = Math.PI / 2
  wrist.add(wristPin)

  // 그리퍼 몸통 + 집게 2개
  const gripperBase = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.04, 0.05), dark)
  gripperBase.position.y = 0.04
  wrist.add(gripperBase)
  const fingerGeo = new THREE.BoxGeometry(0.016, 0.07, 0.03)
  const fingerLeft = new THREE.Mesh(fingerGeo, accent)
  const fingerRight = new THREE.Mesh(fingerGeo, accent)
  fingerLeft.position.set(-0.028, 0.09, 0)
  fingerRight.position.set(0.028, 0.09, 0)
  wrist.add(fingerLeft, fingerRight)

  return { root, joints: { waist, shoulder, elbow, wrist, fingerLeft, fingerRight } }
}

export function OmxModel() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let disposed = false
    let frame = 0
    let cleanup = () => undefined

    import('three')
      .then((THREE) => {
        if (disposed) return
        const renderer = new THREE.WebGLRenderer({
          canvas,
          alpha: true,
          antialias: true,
          powerPreference: 'low-power',
        })
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5))
        renderer.setSize(canvas.clientWidth, canvas.clientHeight, false)
        renderer.outputColorSpace = THREE.SRGBColorSpace

        const scene = new THREE.Scene()
        const camera = new THREE.PerspectiveCamera(32, canvas.clientWidth / canvas.clientHeight, 0.01, 20)
        // 팔 전체(대략 y 0~0.55)가 카드 썸네일에 담기게 프레이밍.
        camera.position.set(0.62, 0.4, 0.66)
        camera.lookAt(0, 0.26, 0)

        // 조명: 부드러운 hemisphere + directional 한 개로 입체감. 배경은 alpha(투명).
        scene.add(new THREE.HemisphereLight(0xffffff, 0x3b3652, 2.4))
        const key = new THREE.DirectionalLight(0xffffff, 2.0)
        key.position.set(1.2, 1.6, 1.0)
        scene.add(key)

        const { root, joints } = buildArm(THREE)
        scene.add(root)

        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
        let previous = performance.now()
        let phase = 0

        const render = (now: number) => {
          if (disposed) return
          const dt = Math.min((now - previous) / 1000, 0.1)
          previous = now
          // prefers-reduced-motion 이면 정지(핑키와 동일 정책).
          if (!reduceMotion.matches) {
            // 핑키 회전 속도감(dt*0.12)과 톤을 맞춘 느린 idle pick&place.
            phase += dt * 0.5
            const reach = (1 - Math.cos(phase)) / 2 // 0..1 부드럽게 왕복
            joints.waist.rotation.y = Math.sin(phase * 0.5) * 0.5
            joints.shoulder.rotation.z = -0.35 + reach * 0.55
            joints.elbow.rotation.z = 0.95 - reach * 0.8
            joints.wrist.rotation.z = -0.25 + reach * 0.25
            // 아래로 뻗었을 때(reach 최대) 집고, 들어올릴 때 놓는 집게 개폐.
            const grip = 0.018 + (1 - reach) * 0.02
            joints.fingerLeft.position.x = -grip
            joints.fingerRight.position.x = grip
          }
          renderer.render(scene, camera)
          frame = requestAnimationFrame(render)
        }
        frame = requestAnimationFrame(render)

        cleanup = () => {
          cancelAnimationFrame(frame)
          renderer.dispose()
          scene.traverse((object) => {
            const mesh = object as Mesh
            mesh.geometry?.dispose?.()
            const materials: (Material | undefined)[] = Array.isArray(mesh.material)
              ? mesh.material
              : [mesh.material]
            materials.forEach((material) => material?.dispose())
          })
          ;(root as Group).clear()
        }
      })
      .catch(() => !disposed && setFailed(true))

    return () => {
      disposed = true
      cleanup()
    }
  }, [])

  return (
    <div className="omx-model-viewer" aria-label="집었다 놓기를 반복하는 OMX 조제 로봇팔 모델">
      {failed ? (
        <div className="omx-model-viewer__fallback">OMX</div>
      ) : (
        <canvas ref={canvasRef} className="omx-model-viewer__canvas" />
      )}
    </div>
  )
}
