# 인프라 설치 파일

로봇과 클라우드 서버에 **손으로 만들어져 있던** 설정들이다. 저장소 밖에만
있으면 기기를 다시 깔 때 무엇이 있어야 하는지 알 수 없어서 여기로 옮겼다.

`compose.yaml`·`deploy.sh` 는 백엔드/프런트 컨테이너 배포용으로, 이 문서와
별개다. 이 문서는 그 아래 깔린 **연결 자체**를 다룬다.

---

## 무엇이 어디에 있나

```
deploy/robot/          로봇(핑키)에 들어가는 것
  systemd/*.service      부팅 시 뜨는 8 개
  bin/foxglove-remote.sh
  robot.env.example      로봇의 정체(MINGKY_ROBOT_ID)
  install.sh             ← 로봇에서 실행

deploy/cloud/          관제 서버에 들어가는 것
  bin/mingky-adduser     팀원 계정 생성
  ros-domain.sh          로그인 시 ROS_DOMAIN_ID 자동 지정
  nginx/                 리버스 프록시·WebSocket 업그레이드
  10-tunnel-keepalive.conf
```

---

## 연결 구조

로봇이 **밖으로** SSH 터널을 건다. 기관 Wi-Fi 안에 있어도, 공유기가 없어도
관제가 로봇에 닿는 이유다.

```
  노트북 ──→ 관제 서버 ──(로봇이 미리 뚫어둔 터널)──→ 핑키
                 :22021 → pinky1
                 :22022 → pinky2
```

**포트는 로봇마다 달라야 한다.** `install.sh` 가 robot-id 번호에서 유도해
`/etc/mingky/robot.env` 에 적는다. 유닛 파일에는 번호가 없다.

```
pinky-01   SSH 22021   Foxglove 18765   Camera front/rear 18801/18802
pinky-02   SSH 22022   Foxglove 18766   Camera front/rear 18803/18804
pinky-03   SSH 22023   Foxglove 18767
```

서버의 `authorized_keys` 가 키별로 `permitlisten` 을 걸어 두므로 값이 틀리면
서버가 바인딩을 거부한다. 유닛에 `ExitOnForwardFailure=yes` 가 있어 터널이
뜨지 않고 재시작만 반복하며, **그 로봇은 접근 불가가 된다.**

로봇 IP 를 알 필요가 없고, 로봇이 망을 옮겨도 그대로 된다.

---

## 로봇 설치

```bash
cd ~/mingky_care_pro/deploy/robot
sudo ./install.sh pinky-01        # 2호기는 pinky-02
```

### 부팅과 함께 뜨는 것

| 유닛 | 역할 |
| --- | --- |
| `mingky-ssh-tunnel` | 위 터널을 여는 쪽. **이게 죽으면 로봇에 접근 못 한다** |
| `mingky-gateway` | 이벤트·heartbeat 상향, 명령 하향 |
| `mingky-battery-pub` | 배터리 발행 |
| `mingky-teleop-bridge` | 대시보드 실시간 조작·위치 |
| `mingky-camera-tunnel` | 전·후방 저FPS MJPEG를 관제 서버로 역터널 |
| `fg-bridge` `fg-tunnel` | Foxglove 관측 (필요할 때만) |
| `fg-teleop` | 주행 모드 관리·원격 조작 속도 상한 (상시) |

### 개인 브랜치 실기 시험

평상시 모든 유닛은 공용 운영 경로 `/home/pinky/mingky_care_pro`를 사용한다.
개인 브랜치를 시험할 때는 저장소의 전환 헬퍼를 개인 홈에 설치해 사용한다.
손으로 drop-in을 추가하면 다른 팀원의 파일과 함께 적용되어 마지막 파일이
실행 경로를 다시 덮을 수 있다.

```bash
install -m 755 deploy/robot/bin/mingky-private-runtime \
  /home/pinky/wmk/mingky-private-runtime

# 개인 경로 전환. 실행 중이던 서비스만 다시 올린다.
/home/pinky/wmk/mingky-private-runtime start

# 실제 ExecStart와 상태 확인
/home/pinky/wmk/mingky-private-runtime status

# 시험 종료 후 공용 경로 복귀
/home/pinky/wmk/mingky-private-runtime stop
```

`MINGKY_PRIVATE_REPO` 환경변수로 다른 개인 경로를 지정할 수 있다. 헬퍼는
기존 개인 drop-in을 삭제하지 않고 `service-overrides-backup`에 백업한다.
네 서비스를 완전히 내린 뒤 제어 모드 → 게이트웨이/브리지 → 통합
시스템 순으로 올린다. `active` 표시만 믿지 않고 `/mode`와
`/guide_manager/state` 실데이터를 받아야 성공한다.

```bash
sudo rm /etc/systemd/system/fg-teleop.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart fg-teleop.service
systemctl cat fg-teleop.service
```

`mode_manager`와 `teleop_limiter`는 같은 `fg-teleop.service`에서 함께 실행되므로
둘을 서로 다른 워크스페이스에서 따로 실행하지 않는다.

**`mingky-battery-pub` 를 따로 둔 이유가 있다.** 배터리 퍼블리셔는 원래
`bringup_robot.launch.xml` 안에 있어서 주행 스택을 띄워야만 돌았다. 그런데
의료진이 대시보드에서 로봇을 고르려면 배터리가 40% 이상이어야 하고, 그건
주행 **전** 상황이다. 값이 없으면 백엔드가 `409` 로 막아 아무것도 시작할 수
없다 — 로봇을 쓰려면 배터리 값이 필요한데 배터리 값은 로봇을 써야 나오는
구조였다. 그래서 주행과 분리했다.

주행 스택(Nav2·라이다·모터)은 자동이 아니다. 필요할 때 띄운다.

### 스크립트가 못 하는 두 가지

**터널 키.** `/home/pinky/.ssh/id_ed25519_tunnel` 과, 클라우드
`~ubuntu/.ssh/authorized_keys` 의 대응 항목이 필요하다. 로봇이 자기 포트만
열도록 제한한다 — 이게 없으면 로봇 하나가 아무 포트나 열 수 있다.

```
restrict,port-forwarding,permitlisten="127.0.0.1:22021",permitlisten="127.0.0.1:18765",permitlisten="127.0.0.1:18801",permitlisten="127.0.0.1:18802" ssh-ed25519 AAAA... fgtunnel-pinky-01
```

**Wi-Fi 자동 접속.** 저장소로 옮길 수 없는 기기별 설정이다.

```bash
nmcli -f NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY con show
```

쓰는 망이 `yes` / `100` 이어야 한다. 실제로 이걸 놓쳐 사고가 났다 — 새 망의
autoconnect 가 꺼져 있고 이미 없어진 `mingky` 가 우선순위 100 이라, 재부팅한
로봇이 없는 망만 찾다 실패하고 자기 AP 를 띄웠다. 원격으로는 손쓸 방법이
없어 사람이 직접 가서 복구해야 했다.

---

## 클라우드 설치

```bash
sudo install -m 755 deploy/cloud/bin/mingky-adduser /usr/local/sbin/
sudo install -d -m 755 /etc/mingky
sudo install -m 755 deploy/cloud/ros-domain.sh /etc/mingky/
sudo cp deploy/cloud/ros-domains.conf.example /etc/mingky/ros-domains.conf
sudo install -m 644 deploy/cloud/10-tunnel-keepalive.conf /etc/ssh/sshd_config.d/
sudo systemctl reload ssh
```

nginx 는 도메인·인증서 경로가 환경마다 달라 `.example` 로 둔다. 실제 파일과
비교해 반영한다. Foxglove 경로의 토큰은 `<FOXGLOVE_TOKEN>` 으로 지워 뒀다.

### 팀원 계정

```bash
sudo mingky-adduser yunseo ~/yunseo.pub
```

계정을 만들고 키를 넣고 `ROS_DOMAIN_ID` 를 다음 번호로 배정한다. **도메인이
겹치면 남이 띄운 노드가 내 `ros2 node list` 에 섞인다.** 20·21·25 는
로봇과 관제컴퓨터가 쓰므로 예약돼 있다.

### `10-tunnel-keepalive.conf` 가 필요한 이유

기본값(`ClientAliveInterval 0`)이면 sshd 가 죽은 세션을 알아채지 못한다.
로봇 Wi-Fi 가 끊기면 서버는 터널 세션을 계속 살아 있다고 믿고 22021 을 붙든
채 놓지 않는다. 로봇이 돌아와 다시 걸어도 포트가 이미 쓰이는 중이라 터널이
서지 않는다 — **로봇은 켜져 있는데 접근이 안 된다.**

---

## 접속이 느릴 때

로봇 접속은 SSH 핸드셰이크를 두 번(노트북→서버, 서버→로봇) 하고 그게 해외를
돈다. 매번 새로 맺으면 명령 하나에 3~4 초가 걸린다. 노트북의 `~/.ssh/config`
맨 위에 연결 재사용을 켠다.

```
Host *
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 10m
```

첫 연결만 느리고 이후 명령은 기존 연결에 얹힌다. 실측 **4.2 초 → 0.6 초**.

로봇 자체는 대개 한가하다(CPU 90% 이상 idle). 느리게 느껴지면 로봇이 아니라
경로를 의심한다.

---

## 팀원용 접속 안내

`docs/team-robot-access.md` 를 그대로 주면 된다.
