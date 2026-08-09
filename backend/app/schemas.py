from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class QrScanRequest(BaseModel):
    patient_id: str = Field(min_length=1, max_length=20)
    # 어느 로봇이 스캔했는지. guidance_sessions.robot_id 가 NOT NULL 이라
    # 이 값이 없으면 세션을 만들 수 없다.
    robot_id: str = Field(min_length=1, max_length=20)
    # 환자가 착용한 ArUco 마커. 아직 안 쓰면 생략한다.
    marker_id: int | None = Field(default=None, ge=0, le=49)


class Patient(BaseModel):
    patient_id: str
    name: str
    gender: str
    birth_date: date
    condition_name: str


class ScheduleStep(BaseModel):
    """session_steps 한 행. examination_steps(마스터)가 아니라 스냅샷이다.

    마스터를 그대로 내보내면 나중에 검사 순서가 바뀔 때 과거 안내 기록의
    일정까지 소급해서 달라진다.
    """

    step_order: int
    visit_name: str
    arrived_at: datetime | None = None
    completed_at: datetime | None = None


class TodaySchedule(BaseModel):
    # 로봇은 이 값을 이후 모든 이벤트에 달고 다닌다.
    session_id: int
    patient: Patient
    steps: list[ScheduleStep]
    current_step_order: int


class EventIn(BaseModel):
    """로봇이 보내는 이벤트 한 건. mingky_interfaces/msg/Event 와 1:1."""

    event_id: UUID
    robot_id: str = Field(max_length=20)
    # 0 은 세션과 무관한 이벤트. DB 시퀀스가 1부터라 충돌하지 않는다.
    session_id: int = 0
    occurred_at: datetime
    level: Literal["info", "warning", "error"]
    event_code: str = Field(max_length=50)
    source_node: str = Field(default="", max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    received: int
    inserted: int
    duplicates: int
    state_updates: int
    # 미등록 코드는 거부하지 않고 적재한 뒤 여기 담아 돌려준다.
    # 게이트웨이가 같은 배치를 무한 재전송하지 않도록 HTTP 는 200 이다.
    unknown_codes: list[str] = Field(default_factory=list)
    # 이벤트는 적재됐으나 DB 제약에 걸려 상태 갱신을 못 한 코드.
    # 시계가 어긋난 로봇이 보내면 여기에 쌓인다.
    rejected_updates: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 조회 응답


class PatientSummary(BaseModel):
    """대시보드에 띄우는 환자 정보.

    age 는 컬럼이 아니다. 002 에서 지웠고 birth_date 에서 계산한다.
    저장해두면 시간이 지나면서 생년월일과 갈라진다.
    """

    patient_id: str
    name: str
    gender: str
    birth_date: date
    age: int
    condition_name: str


class SessionStep(BaseModel):
    step_order: int
    visit_name: str
    arrived_at: datetime | None = None
    completed_at: datetime | None = None
    completed_source: str | None = None


class SessionOut(BaseModel):
    session_id: int
    robot_id: str
    marker_id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None
    patient: PatientSummary
    steps: list[SessionStep]
    # 진행 중인 세션만 값이 있다. 끝난 세션에는 '현재' 가 없다.
    current_step_order: int | None = None
    current_visit: str | None = None


class OrderIn(BaseModel):
    """대시보드가 로봇에 내리는 명령.

    command 를 문자열로 열어두지 않고 좁힌 이유는, 로봇이 모르는 명령을
    받았을 때 조용히 무시하는 상황을 만들지 않기 위해서다.
    """

    command: Literal["goto", "start_session"]
    # goto 면 waypoint 이름, start_session 이면 patient_id.
    argument: str = Field(min_length=1, max_length=100)


class OrderOut(BaseModel):
    order_id: UUID
    robot_id: str
    command: str
    argument: str
    created_at: datetime


class OrderAck(BaseModel):
    order_id: UUID


class EventOut(BaseModel):
    event_id: UUID
    robot_id: str | None = None
    session_id: int | None = None
    occurred_at: datetime
    received_at: datetime
    level: str
    event_code: str
    source_node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[EventOut]


class RobotOut(BaseModel):
    robot_id: str
    robot_type: str
    display_name: str
    domain_id: int | None = None
    is_active: bool
    # 배터리는 저장된 마지막 표본이다. 실시간 값이 아니다.
    battery_voltage: float | None = None
    battery_percent: int | None = None
    battery_recorded_at: datetime | None = None
    active_session_id: int | None = None
    active_patient_id: str | None = None
    # 의료진이 이 로봇을 활성화한 시각. NULL = 대기 중.
    # DB 컬럼이 아니라 app/arming.py 인메모리 레지스트리에서 조립한다.
    armed_at: datetime | None = None
    # 생존 여부는 DB 가 아니라 백엔드 메모리에서 온다(app/heartbeat.py).
    # unknown 은 '한 번도 heartbeat 를 안 보낸 로봇' 이다. offline 과 다르다 —
    # OMX 는 관제 PC 에 USB 직결이라 잃을 네트워크 링크가 없다.
    last_seen_at: datetime | None = None
    link_state: Literal["online", "offline", "unknown"] = "unknown"


class RobotArmingOut(BaseModel):
    """로봇 QR 노드가 폴링하는 최소 상태."""

    robot_id: str
    armed: bool
    armed_at: datetime | None = None
