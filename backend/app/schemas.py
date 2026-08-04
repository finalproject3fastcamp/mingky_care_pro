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
