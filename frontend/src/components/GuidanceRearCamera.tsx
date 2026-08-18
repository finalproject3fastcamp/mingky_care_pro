import { useState } from 'react'

import { getQrObservation } from '../lib/api'
import { usePolling } from '../lib/usePolling'


interface Props {
  robotId: string
}

type FollowTone = 'normal' | 'slow' | 'waiting' | 'inactive'

function followPresentation(
  state: 'inactive' | 'normal' | 'slow' | 'waiting' | null | undefined,
  distance: number | null,
  source: string | null | undefined,
) {
  if (source === 'acquiring') {
    return { tone: 'slow' as FollowTone, title: '환자 확인 위치 확보 중 · 저속 주행' }
  }
  if (source === 'grace') {
    const tone = state === 'normal' ? 'normal' : 'slow'
    return { tone: tone as FollowTone, title: '환자 추적 순간 유실 · 주행 유지' }
  }
  switch (state) {
    case 'normal':
      return { tone: 'normal' as FollowTone, title: '환자 확인 · 정상 주행' }
    case 'slow':
      return { tone: 'slow' as FollowTone, title: '환자 멀어짐 · 감속 35%' }
    case 'waiting':
      return distance == null
        ? { tone: 'waiting' as FollowTone, title: '환자 추적 끊김 · 안전 대기' }
        : { tone: 'waiting' as FollowTone, title: '환자 멀어짐 · 대기 중' }
    case 'inactive':
      return { tone: 'inactive' as FollowTone, title: '환자 추적 비활성' }
    default:
      return { tone: 'inactive' as FollowTone, title: '환자 추적 상태 확인 중' }
  }
}

function sourceLabel(source: string | null | undefined): string {
  switch (source) {
    case 'qr': return 'QR 거리 측정'
    case 'visual': return 'YOLO 거리 추정'
    case 'acquiring': return '출발 시야 확보'
    case 'grace': return '추적 유실 유예'
    case 'stale': return '추적 정보 지연'
    case 'none': return '추적 비활성'
    default: return '상태 동기화 중'
  }
}


export function GuidanceRearCamera({ robotId }: Props) {
  const observation = usePolling(
    (signal) => getQrObservation(robotId, { signal }),
    500,
  )
  const [streamFailed, setStreamFailed] = useState(false)
  const [streamKey, setStreamKey] = useState(0)
  const data = observation.error ? null : observation.data
  const distance = data?.follow_distance
    ?? (data?.visible ? data.distance : null)
  const presentation = followPresentation(
    data?.follow_state, distance, data?.follow_source,
  )

  return (
    <section className="card guidance-rear-camera">
      <div className="guidance-rear-camera__header">
        <div>
          <div className="card-title">환자 추적 상태</div>
          <strong className={`guidance-rear-camera__state guidance-rear-camera__state--${presentation.tone}`}>
            {presentation.title}
          </strong>
          <div className="guidance-rear-camera__distance">
            {distance != null ? `측정 거리 ${distance.toFixed(2)} m` : '측정 거리 없음'}
          </div>
        </div>
        <span className={`camera-state camera-state--${presentation.tone}`}>
          {sourceLabel(data?.follow_source)}
        </span>
      </div>
      <div
        className={`guidance-rear-camera__summary guidance-rear-camera__summary--${presentation.tone}`}
        role="status"
        aria-live="polite"
      >
        {data?.follow_source === 'acquiring'
          ? '벽에서 멀어지며 후방 카메라에서 환자를 확인할 공간을 확보하고 있습니다.'
          : data?.follow_source === 'grace'
            ? '추적 흔들림을 고려해 최대 2초간 직전 주행 상태를 유지합니다.'
            : data?.qr_visible
          ? '현재 환자의 QR을 확인했습니다.'
          : data?.visual_visible
            ? 'YOLO 인형 영상으로 환자 거리를 추정하고 있습니다.'
            : data?.follow_state === 'waiting'
              ? '환자를 다시 확인할 때까지 로봇이 현재 위치에서 기다립니다.'
              : '현재 환자의 추적 정보를 기다리고 있습니다.'}
      </div>
      {Boolean(observation.error) && (
        <p className="guidance-rear-camera__notice" role="status">
          거리 정보를 받지 못하고 있습니다.
        </p>
      )}
      {streamFailed ? (
        <div className="guidance-rear-camera__unavailable" role="status">
          <span>후방 카메라 영상을 불러오지 못했습니다.</span>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setStreamFailed(false)
              setStreamKey((value) => value + 1)
            }}
          >
            다시 연결
          </button>
        </div>
      ) : (
        <img
          key={streamKey}
          className="camera-stream guidance-rear-camera__stream"
          src={`/camera/${encodeURIComponent(robotId)}/rear/stream`}
          alt="로봇 후방 카메라 실시간 영상"
          onLoad={() => setStreamFailed(false)}
          onError={() => setStreamFailed(true)}
        />
      )}
      <p className="guidance-rear-camera__hint">
        안내 경로는 유지하며, 환자와의 거리에 따라 정상·감속·대기를 전환합니다.
      </p>
    </section>
  )
}
