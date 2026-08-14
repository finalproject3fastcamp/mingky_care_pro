# Migrations

PostgreSQL 스키마 변경 파일을 실행 순서대로 관리합니다.

- `001_initial_schema.sql`: 환자, 증상, 검사 순서, 약 정보 스키마
- `002_drop_patient_age.sql`: 환자 나이 컬럼 제거
- `003_sessions_and_events.sql`: 로봇 마스터, 안내 세션, 방문 단계, 배터리 로그, 이벤트 로그
- `004_battery_voltage.sql`: 배터리 로그 전압 컬럼
- `005_patient_photos.sql`: 환자 프로필 사진(BYTEA) 테이블
- `006_session_failure_reasons.sql`: 로봇 두절·시스템 장애 안내 종료 사유
- `007_fire_session_end_reason.sql`: 화재 대피 안내 종료 사유
- `008_events_unknown_code_index.sql`: 미등록 event_code 집계용 부분 인덱스
