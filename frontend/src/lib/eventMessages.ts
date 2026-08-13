import type { EventOut } from '../types/events'
import type { NotificationEvent } from '../types/monitoring'

// event_code → 사람이 읽는 메시지. 정본은 config/event_codes.yaml 이다.
// 여기 없는 코드는 event_code 문자열을 그대로 보여준다 (미등록도 눈에 띈다).
function messageFor(code: string, payload: Record<string, unknown>): string {
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
    case 'fire.detected':
      return '화재 감지'
    case 'fire.evacuation_started':
      return '화재 대피소 이동 시작'
    case 'fire.evacuation_succeeded':
      return '화재 대피소 도착'
    case 'fire.evacuation_failed':
      return `화재 대피 실패: ${p.reason ?? '알 수 없는 이유'}`
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
      return `${p.visit_name ?? '목적지'} 이동 실패 (코드 ${p.error_code ?? '?'})`
    case 'nav.stuck':
      return '경로 이탈 감지'
    case 'dock.return_started':
      return `${p.station_name ?? '충전소'} 복귀 시작`
    case 'dock.return_succeeded':
      return `${p.station_name ?? '충전소'} 복귀 완료`
    case 'dock.return_failed':
      return `${p.station_name ?? '충전소'} 복귀 실패 (코드 ${p.error_code ?? '?'})`
    case 'waypoint.test_started':
      return `${p.waypoint_name ?? 'Waypoint'} 시험 주행 시작`
    case 'waypoint.test_succeeded':
      return `${p.waypoint_name ?? 'Waypoint'} 시험 주행 완료`
    case 'waypoint.test_failed':
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
