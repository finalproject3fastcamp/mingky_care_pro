from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


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

    command: Literal[
        "goto", "goto_pose", "start_session", "start_guidance", "set_mode",
        "localize", "system_start", "system_stop", "system_restart",
    ]
    # goto 면 waypoint 이름, goto_pose 면 임시 좌표 JSON,
    # start_session 이면 patient_id, start_guidance 면 session_id,
    # set_mode 면 auto | manual | estop, 나머지 제어 명령은 run.
    #
    # 모드는 로봇이 정본을 갖는다. 여기서 보내는 것은 요청이고, 반영 여부는
    # robot.mode_changed 이벤트로 확인한다. 통신이 끊겨도 로봇이 스스로
    # 안전한 상태를 지켜야 하므로 서버가 상태를 소유하지 않는다.
    argument: str = Field(min_length=1, max_length=200)


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


class UnknownCodeOut(BaseModel):
    """config/event_codes.yaml 에 없는 코드가 얼마나 들어왔는가.

    ingest 는 모르는 코드도 버리지 않고 원본을 그대로 적재한 뒤
    system.unknown_event_code 마커를 함께 남긴다(규칙 4). 그래서 별도
    수집 테이블 없이 마커를 집계하면 된다.

    이 목록이 비어 있지 않다는 것은 로봇이 보내는 이벤트를 서버가
    해석하지 못하고 있다는 뜻이다. 적재는 됐지만 상태 갱신은 일어나지
    않았으므로(ingest 는 known 인 것만 _apply_state 한다) 화면·판정에서
    통째로 빠져 있다.
    """

    event_code: str
    robot_id: str | None = None
    count: int
    first_seen: datetime
    last_seen: datetime


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
    last_session_ended_at: datetime | None = None
    last_session_end_reason: str | None = None
    # 의료진이 이 로봇을 활성화한 시각. NULL = 대기 중.
    # DB 컬럼이 아니라 app/arming.py 인메모리 레지스트리에서 조립한다.
    armed_at: datetime | None = None
    # 생존 여부는 DB 가 아니라 백엔드 메모리에서 온다(app/heartbeat.py).
    # unknown 은 '한 번도 heartbeat 를 안 보낸 로봇' 이다. offline 과 다르다 —
    # OMX 는 관제 PC 에 USB 직결이라 잃을 네트워크 링크가 없다.
    last_seen_at: datetime | None = None
    link_state: Literal["online", "offline", "unknown"] = "unknown"
    system_state: Literal[
        "active", "activating", "deactivating", "inactive", "failed", "unknown"
    ] = "unknown"
    localization_active: bool = False
    runtime_reported_at: datetime | None = None


class RobotArmingOut(BaseModel):
    """로봇 QR 노드가 폴링하는 최소 상태."""

    robot_id: str
    armed: bool
    armed_at: datetime | None = None


class RobotHeartbeatIn(BaseModel):
    """상시 게이트웨이가 함께 보고하는 통합 실행 상태."""

    system_state: Literal[
        "active", "activating", "deactivating", "inactive", "failed", "unknown"
    ] = "unknown"
    localization_active: bool = False


class BatterySampleIn(BaseModel):
    """로봇 게이트웨이가 주기적으로 보내는 최신 배터리 표본."""

    voltage: float | None = Field(
        default=None, ge=0, le=12, allow_inf_nan=False)
    battery_percent: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def has_reading(self):
        if self.voltage is None and self.battery_percent is None:
            raise ValueError("voltage or battery_percent is required")
        return self


class QrObservationIn(BaseModel):
    """로봇 후방 카메라의 최신 QR 거리 관측값."""

    visible: bool
    distance: float | None = Field(
        default=None, gt=0, le=10, allow_inf_nan=False)

    @model_validator(mode="after")
    def visible_has_distance(self):
        if self.visible and self.distance is None:
            raise ValueError("visible QR observation requires distance")
        if not self.visible:
            self.distance = None
        return self


class QrObservationOut(BaseModel):
    robot_id: str
    visible: bool = False
    distance: float | None = None
    observed_at: datetime | None = None
