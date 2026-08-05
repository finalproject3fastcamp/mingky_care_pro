import { useEffect, useState } from 'react'

interface Props {
  streamUrl: string
  /** 연결 실패 시 재시도 간격(ms). */
  retryMs?: number
}

/**
 * 로봇 QR 리더 노드가 송출하는 MJPEG 스트림을 그대로 <img> 로 임베드한다.
 * 노드는 `preview_port` 파라미터가 켜져 있을 때만 스트림을 연다.
 *
 * 스트림이 아직 안 떴거나 잠깐 끊겨도 스스로 복구하도록, 로드 실패 시
 * 일정 간격으로 캐시버스터를 붙여 재시도한다.
 */
export function CameraStream({ streamUrl, retryMs = 3000 }: Props) {
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!failed) return
    const timer = setTimeout(() => {
      setFailed(false)
      setAttempt((n) => n + 1)
    }, retryMs)
    return () => clearTimeout(timer)
  }, [failed, retryMs])

  const src = attempt === 0 ? streamUrl : `${streamUrl}?retry=${attempt}`

  return (
    <div className="card">
      <div className="card-title">로봇 카메라</div>
      {failed ? (
        <p className="camera-offline">카메라 연결 대기 중… (자동 재시도)</p>
      ) : (
        <img
          className="camera-stream"
          src={src}
          alt="로봇 카메라 실시간"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  )
}
