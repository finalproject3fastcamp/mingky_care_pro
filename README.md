# 대학병원 안내·조제 로봇 (Mingky Care)

Pinky 자율주행 로봇과 고정형 OMX(OpenManipulator-X) 로봇팔을 연동하여
환자의 병원 이용 전 과정을 지원하는 AI 기반 스마트 병원 서비스 프로젝트입니다.

> **라이브 데모:** [https://mingkycarepro.site/medical](https://mingkycarepro.site/medical) — 의료진 대시보드
>
> **시연 영상**
> - [OMX 로봇팔](https://youtu.be/ukF5k4bYa9o)
> - [Pinky 자율주행](https://youtu.be/plwKbx3PGU8)

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
├── pinky/         # Pinky 기본 ROS2 패키지 (pinklab-art/pinky_pro 기반)
├── omx/           # OMX 제어 · 모방학습
├── mingky_ros/    # 프로젝트 전용 ROS2 패키지 모음
│   ├── mingky_guide_manager/   # 환자 안내 절차와 상태 관리
│   ├── mingky_qr_reader/       # 후방 웹캠 기반 QR 인식
│   ├── mingky_event_gateway/   # 이벤트 전달 (로컬 큐 + 재시도)
│   ├── mingky_bringup/         # 프로젝트 통합 launch 및 설정
│   └── ...                     # 그 외 teleop · battery · aruco 등
├── backend/       # FastAPI 수집·조회 서버
├── frontend/      # React 관제 대시보드
├── database/      # PostgreSQL 스키마와 초기 데이터
├── config/        # 서비스 간 공유 설정 (event_codes.yaml)
├── deploy/        # 관제 서버 배포 · 인프라 스크립트
├── tools/         # 진단 · 캘리브레이션 · 보조 도구 (fake_robot 하네스 포함)
└── docs/          # 설계 문서와 다이어그램
```

## 5. 문서

> **[GitHub Wiki](https://github.com/finalproject3fastcamp/mingky_care_pro/wiki)** — 회의록, 실험 기록, 데모 영상 등 프로젝트 히스토리 아카이브

파트별 상세 사용법과 오픈소스 출처는 하위 README를 참고한다.

| 파트                                         | 기반 오픈소스                                                     |
| -------------------------------------------- | ----------------------------------------------------------------- |
| [pinky/README.md](pinky/README.md)           | [pinklab-art/pinky_pro](https://github.com/pinklab-art/pinky_pro) |
| [omx/README.md](omx/README.md)               | [huggingface/lerobot](https://github.com/huggingface/lerobot)     |
| [mingky_ros/README.md](mingky_ros/README.md) | 프로젝트 전용 패키지                                              |
| [backend/README.md](backend/README.md)       | FastAPI · asyncpg                                                 |
| [frontend/README.md](frontend/README.md)     | React · Vite · TypeScript                                         |
| [deploy/README.md](deploy/README.md)         | Docker Compose · Nginx                                            |

핵심 운영 문서

- [docs/robot-onboarding.md](docs/robot-onboarding.md) — 로봇을 처음 쓰는 사람이 따라 하는 순서
- [tools/fake_robot/README.md](tools/fake_robot/README.md) — **로봇 없이 개발하기.** ROS 없이 HTTP 로 로봇을 흉내내는 하네스
- [config/event_codes.yaml](config/event_codes.yaml) — 이벤트 코드 정본 (발행 가능한 목록과 payload 형태)
- [docs/system-communication.md](docs/system-communication.md) — 프론트·백엔드·로봇 통신 원칙과 데이터 흐름
- [docs/monitoring-spec.md](docs/monitoring-spec.md) — 관제 기능 스펙과 기술 스택 결정 배경
- [docs/omx-imitation-learning.md](docs/omx-imitation-learning.md) — OMX 모방학습을 어떻게 돌렸는지 (데이터·학습·평가와 겪은 문제)
- [docs/infra-setup.md](docs/infra-setup.md) · [docs/nav2-debugging.md](docs/nav2-debugging.md) — 인프라 · 주행 진단

## 6. 기술 스택

| 영역          | 기술                                                                           |
| ------------- | ------------------------------------------------------------------------------ |
| OS · 미들웨어 | Ubuntu 24.04 · ROS2 Jazzy · Fast DDS                                           |
| 자율주행      | Pinky Pro · Nav2 (MPPI · AMCL · twist_mux) · SLAM Toolbox                      |
| 로봇팔        | OpenManipulator-X (리더-팔로워) · Dynamixel SDK · LeRobot · SmolVLA (모방학습) |
| 비전          | OpenCV · ArUco · YOLO · pyzbar · Picamera2                                     |
| 백엔드        | FastAPI · Uvicorn · asyncpg · Pydantic · SSE                                   |
| 프론트엔드    | React 19 · Vite · TypeScript · React Router · Axios · Three.js · anime.js      |
| 데이터베이스  | PostgreSQL                                                                     |
| 품질 · CI     | GitHub Actions · pytest · oxlint                                               |
| 인프라 · 배포 | Docker Compose · Nginx                                                         |

## 7. 팀원 역할

<table>
  <tr>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/soojjung">
        <img src="https://github.com/soojjung.png?size=240" width="120" height="120" alt="정수진"/><br/>
        <b>정수진</b>
      </a>
      <br/>
      <div align="left"><sub>∙ 의료진, 약국 대시보드<br/>∙ QR 파이프라인<br/>∙ OMX 포장 
      </sub></div>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/vanillaturtlechips">
        <img src="https://github.com/vanillaturtlechips.png?size=240" width="120" height="120" alt="이명일"/><br/>
        <b>이명일</b>
      </a>
      <br/>
      <div align="left"><sub>∙ 엔지니어 대시보드<br/>∙ 이벤트 로그 수집<br/>∙ Observability<br/>∙ 백엔드 · 이벤트 게이트웨이</sub></div>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/YANGJONGSU">
        <img src="https://github.com/YANGJONGSU.png?size=240" width="120" height="120" alt="양종수"/><br/>
        <b>양종수</b>
      </a>
      <br/>
      <div align="left"><sub>∙ OMX 조제<br/>∙ 약국 대시보드<br/>∙ 화재 자동 대피<br/>∙ AMCL 재탐색</sub></div>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/wmkimDev">
        <img src="https://github.com/wmkimDev.png?size=240" width="120" height="120" alt="김원민"/><br/>
        <b>김원민</b>
      </a>
      <br/>
      <div align="left"><sub>∙ Mingky ROS 골격<br/>∙ DB · waypoint<br/>∙ Nav2 · MPPI<br/>∙ 저상 장애물 회피</sub></div>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/153yjw">
        <img src="https://github.com/153yjw.png?size=240" width="120" height="120" alt="윤정우"/><br/>
        <b>윤정우</b>
      </a>
      <br/>
      <div align="left"><sub>∙ 3D 병원 지도<br/>∙ 배터리 저전압 감시 및 충전소 복귀<br/>∙ 환자 이탈 시 충전소 자동 복귀</sub></div>
    </td>
    <td align="center" valign="top" width="160">
      <a href="https://github.com/kimyunseo0902-commits">
        <img src="https://github.com/kimyunseo0902-commits.png?size=240" width="120" height="120" alt="김윤서"/><br/>
        <b>김윤서</b>
      </a>
      <br/>
      <div align="left"><sub>∙ YOLO 환자 인형 검출<br/>∙ 저상 장애물 우회</sub></div>
    </td>
  </tr>
</table>

## 라이선스

`pinky`는 [pinklab-art/pinky_pro](https://github.com/pinklab-art/pinky_pro)의 ROS2 패키지를 기반으로 하며
Apache License 2.0을 따른다. ([pinky/LICENSE](pinky/LICENSE))
