import { api } from './api'

// 약국 조제 API 클라이언트.
//
// 데이터 파일이 한글 키로 되어 있어(prescriptions.json 등) 응답 스키마도 한글이다.
// 백엔드가 원본을 통째로 넘겨주므로 프론트도 같은 필드명으로 받는다 —
// 중간에 영어로 리매핑하면 정본이 갈라진다.

export type Color = 'red' | 'yellow' | 'green'

export interface Drug {
  이름: string
  성분: string
  색이름: string
}

export interface Prescription {
  코드: string
  병명: string
  조합: Color[]
  설명: string
  복용: string
}

export interface PrescriptionsResponse {
  약품: Record<Color, Drug>
  처방: Prescription[]
}

export interface Patient {
  id: string
  이름: string
  생년: string
  성별: string
  병명: string
  처방코드: string
  담당의: string
  특이사항: string
  처방: Prescription | null
}

export interface Policy {
  id: string
  이름: string
  부제: string
  추천: boolean
  실기: string
  설명: string
}

export interface TrayReading {
  모드: '시뮬레이션' | '실제'
  개수?: Record<Color, number>
  오류?: string
}

export interface DispenseBody {
  환자: {
    이름: string
    id: string
    생년: string
    성별: string
    병명: string
  }
  처방코드: string
  정책: string
  조합?: Color[]
  병명?: string
}

export interface DispenseResponse {
  job: string
  처방: Prescription
  정책: string
}

// SSE 이벤트. 종류별로 필드가 다르므로 discriminated union 이 자연스럽다.
export type ProgressEvent =
  | { 종류: '시작'; job: string; 총단계: number }
  | { 종류: '단계시작'; 순번: number; 색: Color; 약: string; 색이름: string }
  | { 종류: '단계끝'; 순번: number; 색: Color; 성공: boolean; 메모: string }
  | { 종류: '조제완료'; job: string; 시각: string }
  | { 종류: '포장시작'; job: string }
  | { 종류: '포장단계'; 이름: string }
  | { 종류: '완료'; job: string; 시각: string }
  | { 종류: '중단'; 이유: string }
  | { 종류: '중단요청' }
  | { 종류: '리셋' }
  | { 종류: '알림'; 글: string; 급?: 'warn' | 'bad' | 'ok' | '' }

export async function getPrescriptions(): Promise<PrescriptionsResponse> {
  const { data } = await api.get<PrescriptionsResponse>('/pharmacy/prescriptions')
  return data
}

export async function searchPatients(q: string): Promise<Patient[]> {
  const { data } = await api.get<{ 환자: Patient[] }>('/pharmacy/patients', {
    params: { q },
  })
  return data.환자
}

// 무작위는 트레이 상태에 의존한다. 실제 모드에서 카메라가 검은 화면이면 503,
// 트레이가 비었으면 400 — axios 는 그 응답의 data 를 error.response.data 로 준다.
export async function getRandomPrescriptions(): Promise<
  { 처방: Prescription[]; 트레이: Record<Color, number> }
> {
  const { data } = await api.get<{
    처방: Prescription[]
    트레이: Record<Color, number>
  }>('/pharmacy/random-prescriptions')
  return data
}

export async function getPolicies(): Promise<{ 정책: Policy[]; 기본: string }> {
  const { data } = await api.get<{ 정책: Policy[]; 기본: string }>(
    '/pharmacy/policies',
  )
  return data
}

export async function getTray(): Promise<TrayReading> {
  const { data } = await api.get<TrayReading>('/pharmacy/tray')
  return data
}

export async function startDispense(body: DispenseBody): Promise<DispenseResponse> {
  const { data } = await api.post<DispenseResponse>('/pharmacy/dispense', body)
  return data
}

export async function stopDispense(): Promise<void> {
  await api.post('/pharmacy/stop')
}

export async function startPack(): Promise<void> {
  await api.post('/pharmacy/pack')
}

export async function resetPharmacy(): Promise<void> {
  await api.post('/pharmacy/reset')
}

// SSE 는 axios 로 못 태운다 (스트림). EventSource 는 상대경로를 그대로 받아
// 현재 origin 에 붙이므로 vite 프록시(/api → :8000) 를 그대로 탄다.
export function progressUrl(): string {
  const base = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')
  return `${base}/pharmacy/progress`
}
