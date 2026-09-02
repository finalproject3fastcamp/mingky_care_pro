# 데모 스택

실기가 회수된 뒤에도 [mingkycarepro.site](https://mingkycarepro.site/medical) 가
살아 있게 한다. **프론트엔드도 nginx 도 compose 도 고치지 않는다.**

## 왜 이렇게 되나

로봇↔서버 경계가 순수 HTTP·WebSocket 이다 ([`monitoring-spec.md`](../../docs/monitoring-spec.md)
§3.2). 서버는 포트 뒤에 무엇이 있는지 묻지 않으므로, 로봇이 서 있던 자리에
대신 서면 그만이다. 대시보드는 진짜와 구분하지 못한다.

```
     실기가 있을 때                        지금
  ┌──────────────┐                  ┌──────────────────┐
  │ Pinky 1·2    │─ HTTP  ─┐        │ fake_robot --loop│─ HTTP  ─┐
  │ (ROS2)       │─ WS    ─┤        │ fake_teleop      │─ WS    ─┤
  │ 카메라 8091/2│─ ssh -R ┤        │ mock_camera      │─ :1880x ┤
  └──────────────┘         ▼        └──────────────────┘         ▼
                    [ 관제 서버 ]                          [ 관제 서버 ]
                    변한 것이 없다 ──────────────────────────────┘
```

## 무엇이 무엇을 대신하나

| 서비스 | 대신하는 것 | 없으면 화면에서 |
| --- | --- | --- |
| `mingky-demo-scenario` | 로봇의 heartbeat·세션·이벤트 | 15초 뒤 로봇 4대가 전부 `comm_lost` 로 빨개진다 |
| `mingky-demo-teleop` | teleop 소켓의 **로봇 쪽** | **3D 병원 맵에 로봇이 아예 안 그려진다** (`h.robot.visible = !!pose`). 라이다·파티클·경로도 전부 빈다. 조작 패드와 주행 모드 버튼이 먹지 않는다 |
| `mingky-demo-camera` | 카메라 역터널(`1880x`) | 카메라 대시보드와 지도 후방캠이 502 |

### 지도와 타임라인은 같은 것을 본다

`fake_teleop` 은 정해진 순서표대로 돌지 않는다. `GET /sessions/active` 의
`current_visit` 를 따라간다 — 타임라인이 'X-ray 도착' 을 찍으면 지도의 로봇도
X-ray 실로 간다. 안내가 없으면 충전소로 돌아간다.

시나리오 파일이 아니라 **서버가 아는 현재 단계**를 보는 것이 요점이다.
시나리오를 바꿔도, 나중에 진짜 로봇이 세션을 만들어도 그대로 맞는다.
검사 이름을 waypoint 로 옮기는 것은 waypoint 정본의 `visit_waypoints` 가 한다.

### 조제는 새로 만든 것이 없다

조제(OMX)는 **아무것도 안 만들어도 된다.** 백엔드에 이미 SIM 분기가 있고 그게
기본값이다 (`PHARMACY_REAL=0` · `PACK_REAL=0`, [`backend/app/pharmacy.py`](../../backend/app/pharmacy.py)).
`deploy/.env` 에서 `MINGKY_OMX_DISPENSE_URL` · `MINGKY_OMX_PACK_URL` 만 비우면
원격 러너를 안 찾고 SIM 으로 돈다.

## 설치

```bash
# python3-venv 는 기본으로 안 깔려 있다. 관제 서버는 백엔드를 도커로 돌려서
# 호스트에 venv 가 필요했던 적이 없기 때문이다.
sudo apt install -y ffmpeg python3-venv

# yt-dlp 은 apt 판이 낡아 유튜브를 자주 못 받는다. 공식 단일 실행 파일을 쓴다.
sudo curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp

sudo ./tools/demo_stack/install.sh
```

> **유튜브가 클라우드 IP 를 막는다.** 서버에서 받으면 대개
> `Sign in to confirm you are not a bot` 이 뜨고 합성 화면으로 떨어진다.
> 그때는 사람이 쓰는 회선에서 받아 `/opt/mingky-demo/.work/` 에 넣고 다시
> 돌린다. 확장자는 아무거나 된다 (`fetch_demo_frames.sh` 머리말).

설치가 하는 일은 여섯 단계다 — 시스템 사용자, `/opt/mingky-demo` 로 소스 복사,
venv, `/etc/mingky/demo.env`, 카메라 프레임 굽기, systemd 등록.

설치 뒤 `/etc/mingky/demo.env` 의 `MINGKY_DEMO_BASE_URL` 이 맞는지 본다.
기본값은 프론트엔드 nginx 를 거치는 `http://127.0.0.1:8080/api` 다 —
compose 가 backend 포트를 밖으로 안 내보내기 때문이다.

## 확인

```bash
systemctl status 'mingky-demo-*'
journalctl -u mingky-demo-scenario -f
journalctl -u mingky-demo-teleop -f

curl -s http://127.0.0.1:18801/health          # 카메라
curl -s http://127.0.0.1:8080/api/robots | head  # 로봇 4대의 link_state
```

화면에서 볼 것:

- `/medical` — 지도 위 핑키 2대가 움직이고, 타임라인에 안내 이벤트가 흐른다
- `/engineer` — 라이다·파티클·경로 레이어, 조작 패드, 주행 모드
- `/camera` — 전방·후방 스트림

## 끄기 / 실기 복귀

```bash
sudo systemctl disable --now mingky-demo-scenario mingky-demo-teleop mingky-demo-camera
```

이 세 줄이 전부다. 실기 경로는 **지운 적이 없다** — 데모 스택은 전부 `tools/`
아래 추가이고, 실기가 쓰던 코드·설정·유닛은 그대로 있다.

진짜 로봇이 teleop 소켓에 붙으면 서버가 옛 소켓을 먼저 닫으므로
([`routers/teleop.py`](../../backend/app/routers/teleop.py) 의 `robot_socket`),
`mingky-demo-teleop` 을 미처 못 껐어도 실기가 이긴다. 카메라는 그렇지 않다 —
역터널이 `1880x` 를 잡으려다 `ExitOnForwardFailure` 로 죽는다. **카메라 서비스는
실기를 붙이기 전에 반드시 먼저 꺼야 한다.**

## 조정

| 하고 싶은 것 | 어디를 |
| --- | --- |
| 다른 시나리오를 돌린다 | `demo.env` 의 `MINGKY_DEMO_SCENARIO` |
| 회차 사이를 늘린다 | `demo.env` 의 `MINGKY_DEMO_LOOP_DELAY` |
| 대기 자리를 바꾼다 | `fake_teleop.py` 의 `HOME` (이름은 waypoint 정본 기준) |
| 주행 속도 | `fake_teleop.py` 의 `CRUISE_MPS` |
| 카메라 영상 | `MINGKY_DEMO_VIDEO_URL` 을 주고 `fetch_demo_frames.sh` 재실행 |

장애 화면을 보여주고 싶으면 시나리오만 바꾸면 된다. 이미 있는 것들:
`servo_overheat.yaml`(서보 과열), `topic_stale.yaml`(라이다 두절),
`manipulator_cycle_aborted.yaml`(조제 포기), `fleet_config_split.yaml`(형상 갈림).

## 가짜인 것을 숨기지 않는다

이벤트의 `source_node` 는 `fake_robot` · `fake_teleop` 으로 고정이다. 타임라인
에서 가짜가 섞여 들어온 것을 알아볼 수 있어야 나중에 조사가 된다. 실기 기록과
데모 기록이 같은 DB 에 쌓이므로, **데모를 켜기 전에**
[`deploy/backup-server-state.sh`](../../deploy/backup-server-state.sh) 로
실기 기록을 떠 두는 것을 권한다.

## 파일

| 파일 | 무엇 |
| --- | --- |
| `install.sh` | 서버에 세운다 |
| `fetch_demo_frames.sh` | 시연 영상 → JPEG. 설치 때 한 번 |
| `mock_camera.py` | MJPEG 서버. stdlib 만 쓴다 |
| `fake_teleop.py` | 가상 주행체. `websockets` · `PyYAML` |
| `demo.env.example` | 설정 정본 |
| `systemd/` | 유닛 3개 |

시나리오 재생은 새로 만들지 않았다. [`tools/fake_robot`](../fake_robot) 의
하네스에 `--loop` 를 더한 것뿐이다.
