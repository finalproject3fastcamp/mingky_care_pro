import { useEffect, useMemo, useState } from 'react'

import { getRobots } from '../lib/api'
import { usePolling } from '../lib/usePolling'

type CameraSide = 'front' | 'rear'
type StreamState = 'idle' | 'connecting' | 'live' | 'error'

const POLL_MS = 5000

const CAMERA_LABEL: Record<CameraSide, string> = {
  front: '전방 카메라',
  rear: '후방 카메라',
}

function streamUrl(robotId: string, camera: CameraSide): string {
  return `/camera/${encodeURIComponent(robotId)}/${camera}/stream`
}

export function CameraDashboard() {
  const robots = usePolling((signal) => getRobots({ signal }), POLL_MS)
  const [selectedRobotId, setSelectedRobotId] = useState<string | null>(null)
  const [camera, setCamera] = useState<CameraSide>('front')
  const [streaming, setStreaming] = useState(false)
  const [streamState, setStreamState] = useState<StreamState>('idle')
  const [streamKey, setStreamKey] = useState(0)

  const robotList = useMemo(
    () => (robots.data ?? []).filter((robot) => (
      robot.robot_type === 'mobile' && robot.robot_id.startsWith('pinky-')
    )),
    [robots.data],
  )

  useEffect(() => {
    if (robotList.length === 0) return
    if (selectedRobotId && robotList.some(
      (robot) => robot.robot_id === selectedRobotId)) return
    setSelectedRobotId(robotList[0].robot_id)
  }, [robotList, selectedRobotId])

  useEffect(() => {
    setStreaming(false)
    setStreamState('idle')
  }, [selectedRobotId, camera])

  const selectedRobot = robotList.find(
    (robot) => robot.robot_id === selectedRobotId) ?? null

  function openStream() {
    if (!selectedRobotId) return
    setStreamState('connecting')
    setStreamKey((value) => value + 1)
    setStreaming(true)
  }

  function closeStream() {
    setStreaming(false)
    setStreamState('idle')
  }

  return (
    <div className="camera-dashboard">
      <header className="camera-page-header">
        <div>
          <span className="camera-page-header__eyebrow">ROBOT VISION</span>
          <h1>카메라 모니터링</h1>
          <p>선택한 영상만 저해상도·저FPS로 연결합니다.</p>
        </div>
      </header>

      <section className="card camera-controls">
        <label>
          로봇
          <select
            value={selectedRobotId ?? ''}
            onChange={(event) => setSelectedRobotId(event.target.value)}
          >
            {robotList.map((robot) => (
              <option key={robot.robot_id} value={robot.robot_id}>
                {robot.display_name} ({robot.robot_id})
              </option>
            ))}
          </select>
        </label>

        <div className="camera-side-picker" role="group" aria-label="카메라 방향">
          {(['front', 'rear'] as CameraSide[]).map((side) => (
            <button
              key={side}
              type="button"
              className={`btn${camera === side ? ' primary' : ''}`}
              onClick={() => setCamera(side)}
            >
              {CAMERA_LABEL[side]}
            </button>
          ))}
        </div>

        {streaming ? (
          <button type="button" className="btn" onClick={closeStream}>
            스트림 닫기
          </button>
        ) : (
          <button
            type="button"
            className="btn primary"
            disabled={!selectedRobotId}
            onClick={openStream}
          >
            스트림 열기
          </button>
        )}
      </section>

      <section className="camera-view card">
        <header className="camera-view__header">
          <div>
            <strong>{selectedRobot?.display_name ?? 'Pinky'}</strong>
            <span>{CAMERA_LABEL[camera]}</span>
          </div>
          <span className={`camera-state camera-state--${streamState}`}>
            {streamState === 'live' && 'LIVE'}
            {streamState === 'connecting' && '연결 중'}
            {streamState === 'error' && '연결 실패'}
            {streamState === 'idle' && '대기'}
          </span>
        </header>

        <div className="camera-view__frame">
          {streaming && selectedRobotId ? (
            <img
              key={`${selectedRobotId}-${camera}-${streamKey}`}
              src={streamUrl(selectedRobotId, camera)}
              alt={`${selectedRobot?.display_name ?? selectedRobotId} ${CAMERA_LABEL[camera]}`}
              onLoad={() => setStreamState('live')}
              onError={() => setStreamState('error')}
            />
          ) : (
            <p>로봇과 카메라를 선택한 뒤 스트림을 여세요.</p>
          )}
        </div>

        <p className="camera-view__note">
          기본 전송 설정: 최대 640px · 3 FPS · JPEG 품질 60
        </p>
        {streamState === 'error' && (
          <p className="camera-view__error">
            로봇의 카메라 노드와 mingky-camera-tunnel 서비스를 확인하세요.
          </p>
        )}
      </section>
    </div>
  )
}
