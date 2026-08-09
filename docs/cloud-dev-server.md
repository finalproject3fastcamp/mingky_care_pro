# 클라우드 개발 서버

팀원이 공용으로 쓰는 관제 겸 개발 서버입니다.
**관제 스택이 실제로 돌고 있는 서버이기도 합니다.** 아래 주의사항을 먼저 읽으세요.

## 접속

```bash
ssh <계정명>@mingkycarepro.site
```

비밀번호 로그인은 꺼져 있습니다. SSH 키로만 들어갑니다.

| | |
| --- | --- |
| 호스트 | `mingkycarepro.site` (= `161.118.208.109`) |
| OS | Ubuntu 24.04 LTS (aarch64) |
| ROS | Jazzy (`/opt/ros/jazzy`) |
| 자원 | 4 OCPU · 24GB · 디스크 100GB (전원이 나눠 씁니다) |

## 계정 발급

키를 만들고 **공개키(`.pub`)만** 관리자에게 보냅니다. 개인키는 절대 보내지 마세요.

```bash
ssh-keygen -t ed25519 -C "<본인이름>"
cat ~/.ssh/id_ed25519.pub      # 이 내용을 전달
```

관리자는 이렇게 만듭니다.

```bash
sudo mingky-adduser <계정명> <공개키파일>
```

## ROS_DOMAIN_ID

**사람마다 다른 도메인을 씁니다.** 같은 서버에서 도메인이 겹치면 남이 띄운 노드가
내 `ros2 node list` 에 섞이고, 내 토픽이 남의 실행에 들어갑니다.

계정을 만들 때 자동으로 배정되며 `/etc/mingky/ros-domains.conf` 에 기록됩니다.
직접 `export` 하지 않아도 됩니다.

```bash
echo $ROS_DOMAIN_ID          # 본인 번호가 나와야 합니다
```

예약값이라 쓰면 안 되는 번호가 있습니다.

| 번호 | 주인 |
| --- | --- |
| 20 | pinky2 |
| 21 | pinky1 |
| 25 | 관제컴퓨터 |
| 30~98 | 팀원 (자동 배정) |

`/etc/environment` 에 넣는 로봇 쪽 방식(`docs/infra-setup.md`)과 다릅니다.
그쪽은 기기 하나에 값 하나면 되지만, 이 서버는 사람마다 달라야 하기 때문입니다.
설정은 각자 `.bashrc` **최상단**에서 읽습니다. Ubuntu `.bashrc` 는 상단에서
비대화형 셸을 조기 반환하므로 그 아래에 두면 `ssh 서버 '명령'` 이나 systemd 에
적용되지 않습니다.

확인은 비대화형으로 해야 의미가 있습니다.

```bash
ssh <계정명>@mingkycarepro.site 'echo $ROS_DOMAIN_ID'
```

## 공용 워크스페이스

```
/home/ubuntu/mingky_care_pro
```

`mingky` 그룹으로 모두가 읽고 씁니다. 새로 만든 파일도 setgid 로 그룹이 유지됩니다.

### 동시 빌드 금지

`colcon build` 를 두 사람이 동시에 돌리면 `build/`, `install/` 산출물이 섞입니다.
빌드 전에 팀 채널에 알리거나, 개인 clone 을 따로 두고 거기서 빌드하세요.

```bash
git clone <저장소> ~/mingky_care_pro     # 개인 작업용
```

### ⚠️ 이 워크스페이스는 운영 스택의 소스입니다

`deploy/compose.yaml` 이 아래를 컨테이너에 바인드 마운트합니다.

```
../database/migrations
../database/seeds/001_initial_data.sql
../database/seeds/002_robots.sql
```

즉 **공용 워크스페이스에서 브랜치를 갈아타면 운영 DB 초기화 소스가 같이 바뀝니다.**
지금 떠 있는 컨테이너는 영향받지 않지만, 다음 번 컨테이너 재생성 때 반영됩니다.

브랜치를 옮기거나 실험할 때는 개인 clone 을 쓰는 편이 안전합니다.

## sudo

`mingky` 그룹 전원이 비밀번호 없이 전체 sudo 를 씁니다. 편한 만큼 위험합니다.

**건드리기 전에 확인이 필요한 것**

| 대상 | 이유 |
| --- | --- |
| `deploy/` 의 docker 컨테이너 | 관제 스택이 실제로 서비스 중입니다 |
| `/etc/nginx/` | `mingkycarepro.site` 와 `myong12.site` 를 함께 서빙합니다 |
| `/etc/letsencrypt/` | 인증서 자동 갱신이 걸려 있습니다 |
| `/etc/mingky/` | 도메인 배정 맵 |
| iptables / OCI 보안 목록 | 포트를 잘못 닫으면 SSH 가 끊깁니다 |

`deploy/.env` 에는 DB 비밀번호가 있습니다. 권한은 `600` 이며 그대로 두세요.

## 하지 않는 것

**서버에서 rviz2 를 띄우지 마세요.** GPU 가 없어 소프트웨어 렌더링이고, 화면을
X11/VNC 로 인터넷 너머 보내면 느린 데다 4 OCPU 를 나눠 쓰는 모두가 느려집니다.
게다가 이 서버에는 로봇 토픽이 오지 않으므로 **볼 것이 없습니다.**

로봇을 시각화하려면 Foxglove 를 쓰거나(`mingky_bringup/README.md`),
rviz2 는 본인 노트북에서 띄웁니다.

## 관제 스택 다루기

```bash
cd /home/ubuntu/mingky_care_pro
./deploy/deploy.sh status
./deploy/deploy.sh logs
```

서비스 주소

| | |
| --- | --- |
| 의료진 화면 | <https://mingkycarepro.site/medical> |
| 엔지니어 화면 | <https://mingkycarepro.site/engineer> |
| API 상태 | <https://mingkycarepro.site/api/health> |

## 참고

- [`infra-setup.md`](infra-setup.md) — 로봇·로컬망 구성
- [`robot-onboarding.md`](robot-onboarding.md) — 로봇을 처음 쓸 때
- [`../deploy/README.md`](../deploy/README.md) — 관제 서버 배포
