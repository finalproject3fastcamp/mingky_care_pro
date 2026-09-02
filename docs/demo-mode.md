# 데모 모드 — 실기 없이 관제를 살려 둔다

프로젝트가 끝나고 Pinky 1·2 와 OMX 가 회수된 뒤에도
[mingkycarepro.site](https://mingkycarepro.site/medical) 를 보여줄 수 있게 하는
절차다. 운영 방법은 [`tools/demo_stack/README.md`](../tools/demo_stack/README.md)
에 있고, 이 문서는 **전환 결정과 되돌리는 길**을 남긴다.

## 무엇을 바꾸고 무엇을 안 바꾸나

바꾸지 않는 것부터. **프론트엔드·nginx·compose·백엔드 코드는 손대지 않는다.**

로봇↔서버 경계가 순수 HTTP·WebSocket 이라(monitoring-spec.md §3.2) 서버는
포트 뒤에 무엇이 있는지 묻지 않는다. 로봇이 서 있던 자리에 대신 서면 그만이고,
그래서 이 전환은 **코드 교체가 아니라 프로세스 교체**다.

| 실기가 하던 일 | 지금 하는 것 | 어디 |
| --- | --- | --- |
| heartbeat·세션·이벤트 (HTTP) | `fake_robot.py --loop` | `mingky-demo-scenario` |
| 좌표·라이다·파티클·주행 모드 (WS) | `fake_teleop.py` | `mingky-demo-teleop` |
| 카메라 MJPEG (ssh 역터널 `1880x`) | `mock_camera.py` | `mingky-demo-camera` |
| OMX 조제·포장 (원격 러너) | **백엔드의 기존 SIM 분기** | 코드 없음. env 만 |

마지막 줄이 요점이다. 조제는 새로 만든 것이 하나도 없다 — `PHARMACY_REAL=0`
과 `PACK_REAL=0` 이 원래 기본값이고, SIM 경로가 처음부터 있었다
([`backend/app/pharmacy.py`](../backend/app/pharmacy.py)).

## 순서

### 1. 백업 — git 에 없는 것부터

코드는 이미 안전하다. 실기 운용 마지막 상태는 `real-robot-final` 태그가
가리킨다. 위험한 것은 저장소에 **한 번도 들어간 적 없는** 쪽이다.

```bash
sudo ./deploy/backup-server-state.sh
```

이 스크립트가 묶는 것:

| 무엇 | 왜 |
| --- | --- |
| PostgreSQL 덤프 | 실기로 돌린 진짜 세션·이벤트. 재현이 불가능하다 |
| `/etc/mingky/*.env` | 저장소에는 `.example` 만 있다 |
| nginx 사이트 설정 | 역터널 포트와 Foxglove 토큰이 박혀 있다 |
| `mingky-*` · `fg-*` systemd 유닛 | 실기가 어떤 서비스로 돌았는지의 유일한 기록 |
| `deploy/.env` | DB 비밀번호와 OMX 러너 주소 |

**DB 를 먼저 떠야 하는 이유가 따로 있다.** 데모 하네스는 같은 DB 에 계속
쓴다. 지금 안 떠 두면 진짜 주행 기록과 가짜가 한 테이블에 섞인다.
`source_node` 로 갈라낼 수는 있지만(`fake_robot` · `fake_teleop` 으로 고정)
그건 사후 구분이지 보존이 아니다.

산출물에는 비밀번호와 토큰이 들어 있다. 저장소에 넣지 마라.

### 2. OMX 원격 러너를 끊는다

`deploy/.env` 에서 두 줄을 비운다. OMX 박스가 회수됐으므로 역터널 포트
(`host.docker.internal:2203x`)에 아무도 없다.

`.env.example` 의 기본값은 빈 값이지만 **운영 서버는 그렇지 않았다.** 실기를
붙이며 네 줄이 다 켜져 있었다(2026-09-02 확인).

```diff
-MINGKY_OMX_DISPENSE_URL=http://172.18.0.1:22131
-MINGKY_OMX_PACK_URL=http://172.18.0.1:22132
-PHARMACY_REAL=1
-PACK_REAL=1
+MINGKY_OMX_DISPENSE_URL=
+MINGKY_OMX_PACK_URL=
+PHARMACY_REAL=0
+PACK_REAL=0
```

URL 이 비면 원격 프록시를 안 찾고, `PHARMACY_REAL=0` 이라야 `_dispense_worker`
가 SIM 분기로 간다 (`compose.yaml` 주석). **넷 다 되돌려야 한다** — URL 만
비우고 `PHARMACY_REAL=1` 을 두면 없는 러너를 계속 부른다.

바꾸기 전 원본을 옆에 남겨 둔다.

```bash
cp deploy/.env deploy/.env.real-robot-$(date +%Y%m%d)
```

그리고 **`restart` 가 아니라 `up -d`** 로 올려야 한다. `docker compose restart`
는 컨테이너를 그대로 다시 띄울 뿐 `.env` 를 다시 읽지 않아서, 바꾼 값이 반영되지
않는다.

```bash
docker compose --env-file deploy/.env --file deploy/compose.yaml \
  up -d --no-build backend
docker compose --env-file deploy/.env --file deploy/compose.yaml \
  exec -T backend printenv PHARMACY_REAL PACK_REAL
```

`--no-build` 라 이미지를 다시 굽지 않고 backend 컨테이너만 새 환경으로 바꾼다.
프론트엔드와 DB 는 건드리지 않으므로 화면이 끊기지 않는다.

### 3. 데모 스택을 세운다

```bash
sudo apt install -y ffmpeg python3-venv
sudo curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp
sudo ./tools/demo_stack/install.sh
```

카메라 영상은 유튜브가 클라우드 IP 를 막아 서버에서 직접 못 받는다. 사람이
쓰는 회선에서 받아 `/opt/mingky-demo/.work/` 에 넣고 다시 돌린다.

자세한 것은 [`tools/demo_stack/README.md`](../tools/demo_stack/README.md).

## 되돌리기

```bash
sudo systemctl disable --now \
  mingky-demo-scenario mingky-demo-teleop mingky-demo-camera
```

실기 경로는 지운 적이 없다. 데모 스택은 전부 `tools/` 아래 **추가**이고,
로봇 쪽 코드·설정·systemd 유닛은 `real-robot-final` 그대로다.

한 가지 순서가 중요하다. **카메라 서비스는 실기를 붙이기 전에 반드시 먼저
꺼야 한다.** `mock_camera` 가 `1880x` 를 잡고 있으면 로봇의 역터널이
`ExitOnForwardFailure` 로 죽는다
([`deploy/robot/systemd/mingky-camera-tunnel.service`](../deploy/robot/systemd/mingky-camera-tunnel.service)).

teleop 은 그렇지 않다 — 진짜 로봇이 붙으면 서버가 옛 소켓을 먼저 닫으므로
([`routers/teleop.py`](../backend/app/routers/teleop.py) 의 `robot_socket`)
데모를 미처 못 껐어도 실기가 이긴다.

## 알아둘 것

- **가짜인 것을 숨기지 않는다.** 이벤트의 `source_node` 가 `fake_robot` ·
  `fake_teleop` 으로 고정이다. 타임라인에서 가짜가 섞인 것을 알아볼 수 있어야
  나중에 조사가 된다.
- **재시작이 곧 복구다.** 반복 재생은 기동할 때와 회차가 실패할 때
  `GET /sessions/active` 를 읽어 **서버에 열려 있는** 세션을 `aborted` 로 닫는다
  (`Harness._recover`). 이 정리가 없으면 열린 세션이 다음 회차의 arming 을 계속
  막아 데모가 영구히 선다 — 실제로 배포 중에 서비스를 재시작하다 세션이 남았고,
  1250 회차를 같은 409 로 헛돌았다. 그래서 자기 기억이 아니라 서버를 기준으로
  닫는다. 뭔가 이상하면 **일단 재시작해 보는 것이 맞다.**

  ```bash
  sudo systemctl restart mingky-demo-scenario
  sudo journalctl -u mingky-demo-scenario -n 20 --no-pager -o cat
  ```
- **카메라는 실제 시연 영상이다.** [Pinky 자율주행 시연](https://youtu.be/plwKbx3PGU8)
  에서 구운 프레임을 돌린다. 영상을 못 받으면 합성 화면으로 떨어지고, 그때는
  화면에 `SIMULATED` 가 찍힌다.
