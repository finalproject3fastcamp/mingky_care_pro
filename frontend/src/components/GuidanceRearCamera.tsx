import { useState } from 'react'

import { getQrObservation } from '../lib/api'
import { usePolling } from '../lib/usePolling'


interface Props {
  robotId: string
}


export function GuidanceRearCamera({ robotId }: Props) {
  const observation = usePolling(
    (signal) => getQrObservation(robotId, { signal }),
    500,
  )
  const [streamFailed, setStreamFailed] = useState(false)
  const [streamKey, setStreamKey] = useState(0)
  const distance = !observation.error && observation.data?.visible
    ? observation.data.distance
    : null

  return (
    <section className="card guidance-rear-camera">
      <div className="guidance-rear-camera__header">
        <div>
          <div className="card-title">환자와의 거리</div>
          <strong className="guidance-rear-camera__distance">
            {distance != null ? `${distance.toFixed(2)} m` : 'QR 인식되지 않음'}
          </strong>
        </div>
        <span className={`camera-state camera-state--${distance != null ? 'live' : 'waiting'}`}>
          {distance != null ? 'QR 인식 중' : 'QR 탐색 중'}
        </span>
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
        안내 주행 중 후방 카메라에 보이는 환자 QR까지의 거리입니다.
      </p>
    </section>
  )
}
