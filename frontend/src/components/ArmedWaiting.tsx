import { useState } from 'react'

import { disarmRobot } from '../lib/api'
import type { MobileRobot } from '../types/monitoring'

interface Props {
  robot: MobileRobot
  /**
   * 선택한 로봇의 전방 QR 카메라 MJPEG 경로.
   *
   * 안 넘기면 카메라를 그리지 않는다. 영상을 지도 위 카메라 창 한 곳에서만
   * 보여주기로 하면서 열어 둔 길이다.
   */
  cameraStreamUrl?: string
  onDisarmed?: () => void
}

// 스캔 대기 카드. 의료진이 로봇을 활성화한 뒤 QR 이 들어오기 전까지의 상태.
// 카메라 미리보기를 이 카드에 함께 넣는 이유는, 지금은 환자에게 "여기에 QR 을
// 대세요" 를 정확히 안내해야 하는 순간이라 시선이 한 곳에 모여야 하기 때문이다.
// 폴링이 다음 tick 에 세션을 잡으면 상위(MedicalDashboard) 가 이 카드를
// 세션 뷰로 교체한다.
export function ArmedWaiting({ robot, cameraStreamUrl, onDisarmed }: Props) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleDisarm() {
    setPending(true)
    setError(null)
    try {
      await disarmRobot(robot.robot_id)
      onDisarmed?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : '해제 실패')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="card armed-waiting">
      <div className="armed-header">
        <div>
          <div className="card-title">스캔 대기</div>
          <div className="armed-robot-name">{robot.display_name}</div>
          <div className="armed-hint">환자의 QR 카드를 카메라에 대주세요.</div>
        </div>
        <button
          type="button"
          className="btn"
          disabled={pending}
          onClick={handleDisarm}
        >
          {pending ? '해제 중…' : '취소'}
        </button>
      </div>
      {error && <p className="picker-error">{error}</p>}
      {cameraStreamUrl && (
        <img
          className="camera-stream armed-camera"
          src={cameraStreamUrl}
          alt="로봇 전방 카메라 실시간"
        />
      )}
    </div>
  )
}
