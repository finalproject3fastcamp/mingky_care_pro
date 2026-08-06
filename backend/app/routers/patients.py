"""환자 프로필 사진 조회 API."""

import hashlib

from fastapi import APIRouter, HTTPException, Request, Response

from ..db import get_pool

router = APIRouter(prefix="/patients", tags=["patients"])

_PHOTO_SQL = """
    SELECT p.patient_id, pp.content_type, pp.image_data
    FROM patients p
    LEFT JOIN patient_photos pp USING (patient_id)
    WHERE p.patient_id = $1
"""


@router.get("/{patient_id}/photo")
async def get_photo(patient_id: str, request: Request) -> Response:
    pool = get_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(_PHOTO_SQL, patient_id)

    if row is None:
        raise HTTPException(status_code=404, detail="patient not found")
    if row["image_data"] is None:
        raise HTTPException(status_code=404, detail="patient photo not found")

    image_data = bytes(row["image_data"])
    etag = f'"{hashlib.sha256(image_data).hexdigest()}"'
    headers = {
        "Cache-Control": "private, max-age=3600",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    return Response(
        content=image_data,
        media_type=row["content_type"],
        headers=headers,
    )
