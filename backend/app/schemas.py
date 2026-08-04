from datetime import date

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
