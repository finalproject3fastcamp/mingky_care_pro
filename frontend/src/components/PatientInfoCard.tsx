import type { Patient } from '../types/monitoring'

interface Props {
  patient: Patient
}

export function PatientInfoCard({ patient }: Props) {
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
    </div>
  )
}
