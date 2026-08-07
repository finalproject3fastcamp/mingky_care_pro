import type { Patient } from '../types/monitoring'

interface Props {
  patient: Patient
  /** 이 환자를 안내 중인 로봇. 세션이 있을 때만 표시한다. */
  robotId?: string
  /** 세션 시작 시각 (ISO). 세션이 있을 때만 표시한다. */
  startedAt?: string
}

// 백엔드는 UTC 로 내려주고, 화면은 브라우저 로컬 시간으로 보여준다.
// 병원 내부 사용이라 24시간 형식이 낫다 (오전·오후 혼선 방지).
const timeFormatter = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

export function PatientInfoCard({ patient, robotId, startedAt }: Props) {
  return (
    <div className="card">
      <div className="card-title">환자 정보</div>
      <div className="patient-name">{patient.name}</div>
      <dl className="status-grid patient-details">
        <dt>환자 ID</dt>
        <dd className="mono">{patient.patient_id}</dd>
        <dt>성별</dt>
        <dd>{patient.gender}</dd>
        <dt>나이</dt>
        <dd>만 {patient.age}세</dd>
        <dt>생년월일</dt>
        <dd className="mono">{patient.birth_date}</dd>
        <dt>질환</dt>
        <dd>{patient.condition_name}</dd>
      </dl>
      {(robotId || startedAt) && (
        <div className="patient-session-meta">
          {robotId && <span className="mono">{robotId}</span>}
          {robotId && startedAt && <span aria-hidden> · </span>}
          {startedAt && <span>{timeFormatter.format(new Date(startedAt))} 시작</span>}
        </div>
      )}
    </div>
  )
}
