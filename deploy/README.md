# 관제 서버 수동 배포

Docker가 설치된 Linux PC에서 PostgreSQL, FastAPI, React 대시보드를 함께
실행합니다.

## Docker 설치 — Ubuntu 22.04/24.04

Docker 공식 APT 저장소를 등록하고 Engine과 Compose 플러그인을 설치합니다.

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

현재 사용자가 `sudo` 없이 배포 스크립트를 실행할 수 있도록 Docker 그룹에
추가합니다. 이 그룹에는 관리자 수준 권한이 있으므로 관제 서버 운영 사용자만
추가합니다.

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker run --rm hello-world
docker compose version
```

설치 문제가 있으면 [Docker Engine Ubuntu 공식 문서](https://docs.docker.com/engine/install/ubuntu/)를
확인합니다.

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
./deploy/deploy.sh migrate
./deploy/deploy.sh down
```

`down`은 컨테이너만 종료하고 PostgreSQL Docker Volume은 유지합니다.

## DB 초기화 범위

빈 PostgreSQL Volume을 처음 생성할 때 `database/migrations/`의 모든 파일과
초기 환자·약품·로봇 데이터를 적용합니다. 환자 프로필 사진은 `up` 실행 시
멱등하게 적재합니다.

## 운영 DB에 마이그레이션 적용

이미 데이터가 있는 DB는 볼륨 생성 시점이 지났으므로 위 경로를 타지 않습니다.
새 마이그레이션은 명시적으로 적용합니다.

```bash
./deploy/deploy.sh migrate
```

적용 이력은 `schema_migrations` 테이블이 들고 있어 미적용 파일만 실행됩니다.
여러 번 실행해도 안전합니다.

시드는 건너뜁니다. `001_initial_data.sql`이 환자·약품을 `DO UPDATE`로 덮어쓰기
때문에, 운영자가 화면에서 고쳐 둔 값이 배포 때마다 되돌아갑니다.

이 테이블을 도입하기 전부터 돌던 DB는 `robot_inventory` 테이블의 존재를 근거로
`001~009`가 적용된 것으로 표시됩니다(`000_schema_migrations.sql`). 따로 손댈
것은 없고, 첫 `migrate`에서 `010`부터 실행됩니다.
