from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class QrScanRequest(BaseModel):
    patient_id: str = Field(min_length=1, max_length=20)


class Patient(BaseModel):
    patient_id: str
    name: str
    gender: str
    birth_date: date
    condition_name: str


class ExamStep(BaseModel):
    examination_step_id: int
    step_order: int
    examination_name: str


class TodaySchedule(BaseModel):
    patient: Patient
    steps: list[ExamStep]
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
