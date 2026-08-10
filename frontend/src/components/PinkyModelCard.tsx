import { useEffect, useRef, useState } from 'react'
import type { Material, Mesh } from 'three'

export function PinkyModel() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    let disposed = false
    let frame = 0
    let cleanup = () => undefined

    Promise.all([
      import('three'),
      import('three/examples/jsm/loaders/GLTFLoader.js'),
      import('three/examples/jsm/libs/meshopt_decoder.module.js'),
    ])
      .then(([THREE, { GLTFLoader }, { MeshoptDecoder }]) => {
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
        const camera = new THREE.PerspectiveCamera(30, canvas.clientWidth / canvas.clientHeight, 0.01, 20)
        camera.position.set(0.65, -0.85, 0.55)
        // ROS/URDF 좌표계는 Z-up이지만 Three.js 카메라의 기본은 Y-up이다.
        // 맞추지 않으면 로봇이 실제로는 서 있어도 화면에서 기울어 보인다.
        camera.up.set(0, 0, 1)
        camera.lookAt(0, 0, 0.2)
        scene.add(new THREE.HemisphereLight(0xffffff, 0x93a5c4, 2.5))
        const key = new THREE.DirectionalLight(0xffffff, 2.2)
        key.position.set(1, -1, 2)
        scene.add(key)

        const root = new THREE.Group()
        scene.add(root)
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
        let previous = performance.now()

        const loader = new GLTFLoader()
        loader.setMeshoptDecoder(MeshoptDecoder)
        loader.load(
          '/models/pinky.glb',
          ({ scene: model }) => {
            if (disposed) return
            const box = new THREE.Box3().setFromObject(model)
            const size = box.getSize(new THREE.Vector3())
            const center = box.getCenter(new THREE.Vector3())
            const scale = 0.54 / Math.max(size.x, size.y, size.z)
            model.scale.setScalar(scale)
            // position은 자신의 scale 영향을 받지 않으므로 스케일된
            // bounding box 중심을 직접 빼야 모델이 정확히 중앙에 옵다.
            model.position.set(
              -center.x * scale,
              -center.y * scale,
              (-center.z + size.z / 2) * scale,
            )
            root.add(model)
            camera.lookAt(0, 0, size.z * scale * 0.5)
          },
          undefined,
          () => !disposed && setFailed(true),
        )

        const render = (now: number) => {
          if (disposed) return
          const dt = Math.min((now - previous) / 1000, 0.1)
          previous = now
          if (!reduceMotion.matches) root.rotation.z += dt * 0.12
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
        }
      })
      .catch(() => !disposed && setFailed(true))

    return () => {
      disposed = true
      cleanup()
    }
  }, [])

  return (
    <div className="pinky-model-viewer" aria-label="천천히 회전하는 Pinky 로봇 모델">
      {failed ? (
        <div className="pinky-model-viewer__fallback">
          PINKY
        </div>
      ) : (
        <canvas ref={canvasRef} className="pinky-model-viewer__canvas" />
      )}
    </div>
  )
}
