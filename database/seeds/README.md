# Seeds

초기 데이터를 관리합니다.

- `001_initial_data.sql`: 환자, 증상, 검사 순서, 약 데이터
- `002_robots.sql`: 로봇 초기 데이터

## 환자 프로필 사진

`assets/patient_photos/`의 JPEG 파일을 `patient_photos` 테이블에 넣습니다.
마이그레이션 적용 후 저장소 루트에서 실행합니다. 같은 환자 사진은 덮어써서
여러 번 실행해도 중복되지 않습니다.

```bash
python3 database/seeds/load_patient_photos.py
```
