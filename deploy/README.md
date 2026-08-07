# 관제 서버 수동 배포

Docker가 설치된 Linux PC에서 PostgreSQL, FastAPI, React 대시보드를 함께
실행합니다. CI/CD, HTTPS, 인증은 이 구성의 범위에 포함하지 않습니다.

## 최초 실행

```bash
cp deploy/.env.example deploy/.env
```

`deploy/.env`의 `POSTGRES_PASSWORD`를 변경한 다음 실행합니다.

```bash
./deploy/deploy.sh up
```

기본 포트가 `8080`이고 서버 IP가 `192.168.0.10`이라면 다음 주소를 사용합니다.

- 의료진 화면: `http://192.168.0.10:8080/medical`
- 엔지니어 화면: `http://192.168.0.10:8080/engineer`
- API 상태: `http://192.168.0.10:8080/api/health`
- Pinky `backend_url`: `http://192.168.0.10:8080/api`

## 관리 명령

```bash
./deploy/deploy.sh status
./deploy/deploy.sh logs
./deploy/deploy.sh restart
./deploy/deploy.sh down
```

`down`은 컨테이너만 종료하고 PostgreSQL Docker Volume은 유지합니다.

## DB 초기화 범위

빈 PostgreSQL Volume을 처음 생성할 때만 `database/migrations/001~005`와
초기 환자·약품·로봇 데이터를 적용합니다. 환자 프로필 사진은 `up` 실행 시
멱등하게 적재합니다.

이미 데이터가 있는 운영 DB에 새 마이그레이션을 자동 적용하는 기능은 아직
포함하지 않습니다. 현재 구성은 새 PC에 현재 시스템을 처음 배포하는 용도입니다.
