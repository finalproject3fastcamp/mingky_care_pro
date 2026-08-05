import type {
  ExamStep,
  NotificationEvent,
  Patient,
  RobotStatus,
  TodaySchedule,
} from '../types/monitoring'

// database/seeds/001_initial_data.sql 기준.
// 백엔드가 birth_date 에서 age 를 계산해 내려주므로(002 에서 컬럼 제거)
// mock 도 동일하게 저장하지 않고 반환 직전에 계산한다.
type PatientRecord = Omit<Patient, 'age'>

const patients: Record<string, PatientRecord> = {
  p001: {
    patient_id: 'p001',
    name: '윤동수',
    gender: '남자',
    birth_date: '1953-01-15',
    condition_name: '퇴행성 무릎 관절염',
  },
  p002: {
    patient_id: 'p002',
    name: '권민수',
    gender: '남자',
    birth_date: '1976-08-22',
    condition_name: '단순 팔 골절',
  },
  p003: {
    patient_id: 'p003',
    name: '김지우',
    gender: '여자',
    birth_date: '2005-04-09',
    condition_name: '십자인대 파열',
  },
}

// PostgreSQL 의 date_part('year', age(birth_date)) 와 동일한 만 나이.
function computeAge(birthDate: string, today = new Date()): number {
  const born = new Date(birthDate)
  let age = today.getFullYear() - born.getFullYear()
  const m = today.getMonth() - born.getMonth()
  if (m < 0 || (m === 0 && today.getDate() < born.getDate())) age -= 1
  return age
}

function toPatient(record: PatientRecord): Patient {
  return { ...record, age: computeAge(record.birth_date) }
}

const stepsByPatient: Record<string, ExamStep[]> = {
  p001: [
    { examination_step_id: 1, step_order: 1, examination_name: 'X-ray' },
    { examination_step_id: 2, step_order: 2, examination_name: '임상병리실' },
    { examination_step_id: 3, step_order: 3, examination_name: '물리치료실' },
  ],
  p002: [
    { examination_step_id: 4, step_order: 1, examination_name: 'X-ray' },
    { examination_step_id: 5, step_order: 2, examination_name: 'CT' },
  ],
  p003: [
    { examination_step_id: 6, step_order: 1, examination_name: 'X-ray' },
    { examination_step_id: 7, step_order: 2, examination_name: 'MRI' },
    { examination_step_id: 8, step_order: 3, examination_name: '물리치료실' },
  ],
}

const currentStepByPatient: Record<string, number> = {
  p001: 2,
  p002: 1,
  p003: 2,
}

const status: RobotStatus = {
  state: '안내중',
  battery: 74,
  current_destination: '임상병리실',
  eta_seconds: 42,
}

const notifications: NotificationEvent[] = [
  {
    id: 'n1',
    level: 'info',
    message: '환자 확인 완료: 윤동수 (p001)',
    created_at: new Date(Date.now() - 60_000).toISOString(),
  },
  {
    id: 'n2',
    level: 'warning',
    message: '배터리 74% — 검사 종료 후 충전 권장',
    created_at: new Date(Date.now() - 30_000).toISOString(),
  },
]

const delay = <T>(value: T, ms = 200): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), ms))

export const mockApi = {
  getTodaySchedule: (patientId: string): Promise<TodaySchedule | null> => {
    const patient = patients[patientId]
    const steps = stepsByPatient[patientId]
    if (!patient || !steps) return delay(null)
    return delay({
      patient: toPatient(patient),
      steps,
      current_step_order: currentStepByPatient[patientId] ?? 1,
    })
  },
  getRobotStatus: () => delay(status),
  getNotifications: () => delay(notifications),
}
