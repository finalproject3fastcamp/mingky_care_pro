# 대학병원 안내·조제 로봇 (Mingky Care)

Pinky 자율주행 로봇과 고정형 OMX(OpenManipulator-X) 로봇팔을 연동하여
환자의 병원 이용 전 과정을 지원하는 AI 기반 스마트 병원 서비스 프로젝트입니다.

## 1. 프로젝트 개요

대학병원은 진료과와 검사실이 다양하고 건물이 복잡하여 초진 환자나 고령 환자가
진료 일정에 따라 여러 장소를 방문하는 데 어려움을 겪는다. 또한 약국에서는
반복적인 약품 분류 및 전달 업무가 지속적으로 발생한다.

본 프로젝트는 Pinky 자율주행 로봇과 고정형 OMX 로봇팔을 연동하여
환자의 병원 이용 전 과정을 지원하는 것을 목표로 한다.

- **Pinky** — 환자의 당일 진료 일정에 맞춰 검사실, 진료실, 약국 등 여러 목적지를 순차적으로 안내
- **OMX** — 모방학습(Imitation Learning)을 활용해 처방된 약품을 Pick & Place 방식으로 트레이에 적재
- **웹 대시보드** — 로봇의 상태, 환자의 진료 진행 현황, 이동 경로 및 작업 로그를 실시간으로 확인

## 2. 프로젝트 목표

- 대학병원 환자의 복잡한 진료 동선을 자동으로 안내하여 이동 편의성을 향상한다.
- 자율주행과 모방학습을 하나의 의료 서비스로 통합한다.
- ROS2 기반의 자율주행 로봇 시스템을 구축한다.
- 웹 기반의 실시간 모니터링 및 관리 시스템을 구현한다.
- 다양한 병원 환경과 시나리오에서 시스템 성능을 실험하고 분석한다.

## 3. 사용자 시나리오

1. 환자가 접수 후 QR 코드(또는 예약 정보)를 로봇에 제시하면 당일 진료 일정을 확인한다.
2. Pinky가 진료 일정에 따라 X-ray실, CT실, 진료실, 물리치료실 등 다음 목적지까지 순차적으로 안내한다.
3. 각 검사가 완료되면 시스템이 다음 검사 또는 진료 장소를 자동으로 확인하고 Pinky가 다음 목적지까지 안내한다.
4. 모든 진료가 끝나면 Pinky가 환자를 원내 약국까지 안내하고, OMX가 모방학습을 통해 처방된 약을 Pick & Place 방식으로 조제한다.
5. 환자가 약을 수령하면 Pinky는 충전소로 복귀하고, 시스템은 이동 경로·진료 진행 상태·작업 로그를 대시보드에 기록한다.

```
접수 → X-ray → CT → 진료실 → 물리치료 → 약국
```

## 4. 레포지토리 구조

```
.
├── pinky/                         # Pinky 기본 ROS2 패키지 (pinklab-art/pinky_pro 기반)
├── omx/                           # OMX 제어·모방학습
├── mingky_ros/                    # 프로젝트 전용 ROS2 패키지 모음
│   ├── mingky_interfaces/         # 공통 msg, srv, action 정의
│   ├── mingky_qr_reader/          # 후방 웹캠 기반 QR 인식
│   ├── mingky_guide_manager/      # 환자 안내 절차와 상태 관리
│   ├── mingky_navigation_manager/ # 엔지니어용 waypoint 시험 주행 중재
│   ├── mingky_event_gateway/      # 이벤트 전달 (로컬 큐 + 재시도)
│   ├── mingky_battery_guard/      # 배터리 저전압 감시와 비상정지
│   ├── mingky_smart_recovery/     # LiDAR 기반 적응형 주행 복구 후보 생성
│   ├── mingky_aruco_detector/     # ArUco 마커 검출 및 자세·거리 추정
│   ├── mingky_teleop/             # 텔레옵 안전 게이트와 주행 모드 관리
│   └── mingky_bringup/            # 프로젝트 통합 launch 및 설정
├── backend/                       # FastAPI 수집·조회 서버
├── frontend/                      # React 관제 대시보드 (의료진·엔지니어)
├── database/                      # PostgreSQL 스키마와 초기 데이터
├── config/                        # 서비스 간 공유 설정 (event_codes.yaml)
├── deploy/                        # 관제 서버 Docker Compose 배포와 인프라 스크립트
├── tools/                         # 진단·캘리브레이션·YOLO 검출 등 보조 도구
├── docs/                          # 설계 문서와 다이어그램
└── README.md
```

각 파트의 상세 사용법과 오픈소스 출처는 하위 README를 참고한다.

| 파트       | 문서                                         | 기반 오픈소스                                                     |
| ---------- | -------------------------------------------- | ----------------------------------------------------------------- |
| Pinky      | [pinky/README.md](pinky/README.md)           | [pinklab-art/pinky_pro](https://github.com/pinklab-art/pinky_pro) |
| OMX        | [omx/README.md](omx/README.md)               | [huggingface/lerobot](https://github.com/huggingface/lerobot)     |
| Mingky ROS | [mingky_ros/README.md](mingky_ros/README.md) | 프로젝트 전용 패키지                                              |
| Backend    | [backend/README.md](backend/README.md)       | FastAPI · asyncpg                                                 |
| Frontend   | [frontend/README.md](frontend/README.md)     | React · Vite · TypeScript                                         |
| Deploy     | [deploy/README.md](deploy/README.md)         | Docker Compose · Nginx                                            |

운영·디버깅 문서

| 문서                                                         | 내용                                                  |
| ------------------------------------------------------------ | ----------------------------------------------------- |
| [docs/monitoring-spec.md](docs/monitoring-spec.md)           | 관제 기능 스펙과 기술 스택 결정 배경                  |
| [docs/system-communication.md](docs/system-communication.md) | 프론트·백엔드·로봇 통신 원칙과 데이터 흐름            |
| [docs/qr-scan-flow.md](docs/qr-scan-flow.md)                 | QR 인식 → 진료 일정 로드 → 안내 세션 시작 흐름        |
| [config/event_codes.yaml](config/event_codes.yaml)           | **이벤트 코드 정본.** 발행 가능한 목록과 payload 형태 |
| **[docs/robot-onboarding.md](docs/robot-onboarding.md)**     | **로봇을 처음 쓰는 사람이 따라 하는 순서**            |
| [docs/team-robot-access.md](docs/team-robot-access.md)       | 관제 서버 경유 로봇 SSH 접속 설정                     |
| [docs/cloud-dev-server.md](docs/cloud-dev-server.md)         | 공용 관제·개발 서버 접속과 주의사항                   |
| [docs/infra-setup.md](docs/infra-setup.md)                   | 네트워크·도메인·시간 동기화·로봇 복구                 |
| [docs/nav2-debugging.md](docs/nav2-debugging.md)             | 주행 문제 진단 순서와 파라미터 튜닝                   |

## 5. 개발 환경

- Ubuntu 24.04 / ROS2 Jazzy
- Pinky Pro (SLAM Toolbox, Nav2)
- OpenManipulator-X 리더 - 팔로워 암 (Dynamixel SDK)

## 6. 팀원 역할

<table>
  <tr>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/soojjung">
        <img src="https://github.com/soojjung.png" width="120" height="120" alt="정수진"/><br/>
        <b>정수진</b>
      </a>
      <br/>
      <sub>의료진 대시보드<br/>QR 스캔 파이프라인<br/>로봇 arming</sub>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/vanillaturtlechips">
        <img src="https://github.com/vanillaturtlechips.png" width="120" height="120" alt="이명일"/><br/>
        <b>이명일</b>
      </a>
      <br/>
      <sub>엔지니어 대시보드<br/>백엔드 · 이벤트 게이트웨이<br/>teleop · safety · 인프라</sub>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/YANGJONGSU">
        <img src="https://github.com/YANGJONGSU.png" width="120" height="120" alt="양종수"/><br/>
        <b>양종수</b>
      </a>
      <br/>
      <sub>OMX 조제 (로봇팔)<br/>모방학습<br/>초기 저장소 세팅</sub>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/wmkimDev">
        <img src="https://github.com/wmkimDev.png" width="120" height="120" alt="김원민"/><br/>
        <b>김원민</b>
      </a>
      <br/>
      <sub>Mingky ROS 골격<br/>DB · waypoint · ArUco<br/>deploy · waypoint UI</sub>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/153yjw">
        <img src="https://github.com/153yjw.png" width="120" height="120" alt="윤정우"/><br/>
        <b>윤정우</b>
      </a>
      <br/>
      <sub>배터리 저전압 감시<br/>충전소 복귀<br/>&nbsp;</sub>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/kimyunseo">
        <img src="https://github.com/kimyunseo.png" width="120" height="120" alt="김윤서"/><br/>
        <b>김윤서</b>
      </a>
      <br/>
      <sub>YOLO 기반<br/>환자 인형 검출<br/>&nbsp;</sub>
    </td>
  </tr>
</table>

## 7. 기대 효과

- 대학병원 내 환자의 복잡한 진료 동선을 자동으로 안내하여 이동 편의성을 향상한다.
- 의료진의 반복적인 환자 안내 및 약품 전달 업무를 일부 자동화한다.
- 자율주행과 모방학습을 결합한 통합 AI 의료 서비스의 가능성을 검증한다.
- 실제 병원 환경을 고려한 서비스 시나리오를 구현하고 검증한다.
- ROS2, 컴퓨터 비전, 모방학습, 웹 서비스를 통합한 실무형 AI 로봇 프로젝트를 수행한다.

## 라이선스

`pinky`는 [pinklab-art/pinky_pro](https://github.com/pinklab-art/pinky_pro)의 ROS2 패키지를 기반으로 하며
Apache License 2.0을 따른다. ([pinky/LICENSE](pinky/LICENSE))
