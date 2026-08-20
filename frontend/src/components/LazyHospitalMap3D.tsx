import { lazy, Suspense } from 'react'

import type { HospitalMap3DProps } from './HospitalMap3D'

const HospitalMap3D = lazy(async () => {
  const module = await import('./HospitalMap3D')
  return { default: module.HospitalMap3D }
})

export type { WaypointMarker } from './HospitalMap3D'

export function LazyHospitalMap3D(props: HospitalMap3DProps) {
  return (
    <Suspense
      fallback={(
        <div
          aria-live="polite"
          style={{
            alignItems: 'center',
            aspectRatio: '16 / 9',
            background: '#c5cbd1',
            borderRadius: 10,
            color: '#475569',
            display: 'flex',
            justifyContent: 'center',
          }}
        >
          3D 지도 불러오는 중…
        </div>
      )}
    >
      <HospitalMap3D {...props} />
    </Suspense>
  )
}
