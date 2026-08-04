from fastapi import APIRouter, HTTPException

from ..db import get_pool
from ..schemas import ExamStep, Patient, QrScanRequest, TodaySchedule

router = APIRouter(prefix="/qr", tags=["qr"])

_PATIENT_SQL = """
    SELECT p.patient_id, p.name, p.gender, p.birth_date, c.condition_name, c.condition_id
    FROM patients p
    JOIN conditions c USING (condition_id)
    WHERE p.patient_id = $1
"""

_STEPS_SQL = """
    SELECT examination_step_id, step_order, examination_name
    FROM examination_steps
    WHERE condition_id = $1
    ORDER BY step_order
"""


@router.post("/scan", response_model=TodaySchedule)
async def scan(payload: QrScanRequest) -> TodaySchedule:
    pool = get_pool()
    async with pool.acquire() as conn:
        patient_row = await conn.fetchrow(_PATIENT_SQL, payload.patient_id)
        if patient_row is None:
            raise HTTPException(status_code=404, detail="patient not found")
        step_rows = await conn.fetch(_STEPS_SQL, patient_row["condition_id"])

    patient = Patient(
        patient_id=patient_row["patient_id"],
        name=patient_row["name"],
        gender=patient_row["gender"],
        birth_date=patient_row["birth_date"],
        condition_name=patient_row["condition_name"],
    )
    steps = [ExamStep(**dict(row)) for row in step_rows]
    return TodaySchedule(patient=patient, steps=steps, current_step_order=1)
