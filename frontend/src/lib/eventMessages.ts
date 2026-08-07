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
    case 'nav.goal_sent':
      return `${p.visit_name ?? '목적지'} 로 이동 시작`
    case 'nav.goal_succeeded':
      return `${p.visit_name ?? '목적지'} 도착`
    case 'nav.goal_aborted':
      return `${p.visit_name ?? '목적지'} 이동 실패 (코드 ${p.error_code ?? '?'})`
    case 'nav.stuck':
      return '경로 이탈 감지'
    case 'session.started':
      return `안내 시작: ${p.patient_id ?? ''}`
    case 'session.step_completed':
      return `단계 ${p.step_order ?? '?'} 완료 (${p.source ?? '?'})`
    case 'session.ended':
      return `안내 종료: ${p.end_reason ?? ''}`
    case 'robot.battery_low':
      return `배터리 부족 (${p.percent ?? '?'}%)`
    case 'robot.comm_lost':
      return '통신 두절'
    case 'robot.paused':
      return `일시정지: ${p.reason ?? ''}`
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
