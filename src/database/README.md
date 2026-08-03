# Database

PostgreSQL 로컬 개발 환경과 스키마 변경 이력을 관리하는 디렉터리입니다.

## 로컬 실행

```bash
cd src/database
cp .env.example .env
docker compose up -d
docker compose ps
```

종료할 때는 다음 명령을 사용합니다.

```bash
docker compose down
```

## 디렉터리

- `migrations/`: 버전이 관리되는 스키마 변경 파일
- `seeds/`: 초기 데이터 파일

`.env`, PostgreSQL 데이터 파일, 덤프와 백업 파일은 Git에 포함하지 않습니다.
