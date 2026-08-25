import type { EventOut } from '../types/events'
import type { NotificationEvent } from '../types/monitoring'

// event_code → 사람이 읽는 메시지. 정본은 config/event_codes.yaml 이다.
// 여기 없는 코드는 event_code 문자열을 그대로 보여준다 (미등록도 눈에 띈다).
export function messageFor(code: string, payload: Record<string, unknown>): string {
  const p = payload
  switch (code) {
    case 'qr.scan_ok':
      return `QR 스캔 완료: ${p.patient_id ?? ''}`
    case 'qr.scan_failed':
      return `QR 스캔 실패: ${p.reason ?? ''}`
    case 'patient.lost':
      return `환자 놓침 (marker ${p.marker_id ?? '?'})`
    case 'patient.regained':
      return '환자 재확인'
    case 'patient.follow_wait_started':
      return p.reason === 'sensor_timeout'
        ? '환자 추적 정보가 끊겨 안전 대기합니다.'
        : '환자가 멀어져 현재 위치에서 기다립니다.'
    case 'patient.follow_wait_ended':
      return '환자가 복귀해 안내를 다시 시작합니다.'
    // 군집 조정. 로봇이 스스로 양보한 것이라 장애가 아니다 — 문구도 그렇게
    // 읽혀야 한다. "멈춤" 이 아니라 "지나갈 때까지 기다린다" 다.
    case 'fleet.yield_started':
      return p.peer
        ? `${p.peer} 이(가) 좁은 구간을 지날 때까지 기다립니다.`
        : '다른 로봇이 지날 때까지 기다립니다.'
    case 'fleet.yield_ended':
      return p.reason === 'deadman'
        // 관제와 끊긴 것이라 그냥 '재개' 로 적으면 장애가 묻힌다.
        ? '관제 조정이 끊겨 스스로 판단해 안내를 계속합니다.'
        : '길이 비어 안내를 다시 시작합니다.'
    case 'person_follow.state_changed':
      return `환자 추적 상태 변경: ${p.state ?? '알 수 없음'}`
    case 'person_follow.inference_unavailable':
      return 'YOLO 연결이 끊겨 QR 거리만 사용합니다.'
    case 'person_follow.inference_restored':
      return 'YOLO 연결이 복구되었습니다.'
    case 'fire.detected':
      return '화재 감지'
    case 'fire.evacuation_started':
      return '화재 대피소 이동 시작'
    case 'fire.evacuation_succeeded':
      return '화재 대피소 도착'
    case 'fire.evacuation_failed':
      return `화재 대피 실패: ${p.reason ?? '알 수 없는 이유'}`
    case 'fire.evacuation_canceled':
      return '운영자 요청으로 화재 대피 주행 중단'
    case 'fire.inference_unavailable':
      return '화재 감지 서버 연결 끊김'
    case 'fire.inference_restored':
      return '화재 감지 서버 연결 복구'
    case 'fire.alarm_reset':
      return '화재 경보 초기화'
    case 'nav.goal_sent':
      return `${p.visit_name ?? '목적지'} 로 이동 시작`
    case 'nav.goal_succeeded':
      return `${p.visit_name ?? '목적지'} 도착`
    case 'nav.goal_aborted':
      if (p.reason === 'goal_occupied' || Number(p.error_code) === 206) {
        return `${p.visit_name ?? '목적지'} 목표 위치가 장애물로 막혀 이동을 중단했습니다.`
      }
      return `${p.visit_name ?? '목적지'} 이동 실패 (코드 ${p.error_code ?? '?'})`
    case 'nav.waiting_spot_failed':
      if (p.reason === 'goal_occupied' || Number(p.error_code) === 206) {
        return `${p.visit_name ?? '목적지'} 대기 위치가 장애물로 막혀 있습니다.`
      }
      return `${p.visit_name ?? '목적지'} 대기 위치 이동에 실패했습니다.`
    case 'nav.stuck':
      return '경로 이탈 감지'
    case 'dock.return_started':
      return `${p.station_name ?? '충전소'} 복귀 시작`
    case 'dock.return_succeeded':
      return `${p.station_name ?? '충전소'} 복귀 완료`
    case 'dock.return_failed':
      if (p.reason === 'goal_occupied' || Number(p.error_code) === 206) {
        return `${p.station_name ?? '충전소'} 진입 위치가 장애물로 막혀 있습니다.`
      }
      return `${p.station_name ?? '충전소'} 복귀 실패 (코드 ${p.error_code ?? '?'})`
    case 'waypoint.test_started':
      return `${p.waypoint_name ?? 'Waypoint'} 시험 주행 시작`
    case 'waypoint.test_succeeded':
      return `${p.waypoint_name ?? 'Waypoint'} 시험 주행 완료`
    case 'waypoint.test_failed':
      if (p.reason === 'goal_occupied' || Number(p.error_code) === 206) {
        return `${p.waypoint_name ?? 'Waypoint'} 목표 위치가 장애물로 막혀 시험 주행을 중단했습니다.`
      }
      return `${p.waypoint_name ?? 'Waypoint'} 시험 주행 실패 (코드 ${p.error_code ?? '?'})`
    case 'session.started':
      return `안내 시작: ${p.patient_id ?? ''}`
    case 'session.ready':
      return `${p.current_visit ?? '첫 목적지'} 안내 준비 완료`
    case 'session.start_rejected':
      return `안내 시작 거부: ${p.reason ?? '알 수 없는 이유'}`
    case 'session.step_completed':
      return `단계 ${p.step_order ?? '?'} 완료 (${p.source ?? '?'})`
    case 'session.ended':
      return `안내 종료: ${p.end_reason ?? ''}`
    case 'robot.battery_low':
      return `배터리 부족 (${p.percent ?? '?'}%)`
    case 'robot.battery_recovered':
      return `배터리 회복 (${p.percent ?? '?'}%)`
    case 'robot.comm_lost':
      return '통신 두절'
    case 'robot.comm_restored':
      return `통신 복구 (${p.offline_sec ?? '?'}초 중단)`
    case 'robot.paused':
      return `일시정지: ${p.reason ?? ''}`
    case 'robot.resumed':
      return `운행 재개: ${p.reason ?? ''}`
    case 'robot.mode_mismatch':
      return `주행 모드 불일치: 요청 ${p.requested ?? '?'} / 적용 ${p.applied ?? '?'}`
    case 'robot.mode_recovered':
      return `주행 모드 일치 복구: ${p.applied ?? '?'}`
    case 'system.unknown_event_code':
      return `미등록 이벤트 코드: ${p.received_code ?? ''}`
    default:
      return code
  }
}

export function toNotification(event: EventOut): NotificationEvent {
  return {
    id: event.event_id,
    level: event.level,
    message: messageFor(event.event_code, event.payload),
    created_at: event.occurred_at,
  }
}
