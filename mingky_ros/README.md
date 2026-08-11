# Mingky Care ROS2 packages

병원 안내 서비스에 필요한 프로젝트 전용 ROS2 패키지를 관리한다.
Pinky 기본 드라이버와 내비게이션 패키지는 `../pinky/`에서 별도로 관리한다.

## Packages

- `mingky_interfaces`: 프로젝트 공통 `msg`, `srv`, `action` 정의
- `mingky_qr_reader`: 후방 웹캠 기반 QR 인식
- `mingky_guide_manager`: 환자 안내 절차와 상태 관리
- `mingky_navigation_manager`: 세션과 무관한 Waypoint 시험 주행과 목표 중재
- `mingky_event_gateway`: 이벤트를 관제 서버로 전달 (로컬 큐 + 재시도)
- `mingky_bringup`: 프로젝트 패키지 통합 실행과 설정

현재는 패키지 골격만 제공한다. 구체적인 인터페이스, 노드, launch 파일과
설정값은 기능이 확정되는 시점에 추가한다.

## Build

저장소 루트에서 Pinky 기본 패키지와 프로젝트 패키지를 함께 빌드한다.

```bash
colcon build --base-paths pinky mingky_ros
```
