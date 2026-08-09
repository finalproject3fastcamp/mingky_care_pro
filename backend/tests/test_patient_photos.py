import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import patients


class FakeConnection:
    def __init__(self, row):
        self.row = row

    async def fetchrow(self, query, patient_id):
        assert "LEFT JOIN patient_photos" in query
        assert patient_id == "p001"
        return self.row


class AcquireContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, row):
        self.connection = FakeConnection(row)

    def acquire(self):
        return AcquireContext(self.connection)


def request(if_none_match=None):
    headers = []
    if if_none_match is not None:
        headers.append((b"if-none-match", if_none_match.encode()))
    return Request({"type": "http", "method": "GET", "headers": headers})


def test_get_photo_returns_private_image_response(monkeypatch):
    image_data = b"\xff\xd8\xffprofile\xff\xd9"
    row = {
        "patient_id": "p001",
        "content_type": "image/jpeg",
        "image_data": image_data,
    }
    monkeypatch.setattr(patients, "get_pool", lambda: FakePool(row))

    response = asyncio.run(patients.get_photo("p001", request()))

    assert response.status_code == 200
    assert response.body == image_data
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["etag"].startswith('"')


def test_get_photo_returns_not_modified_for_matching_etag(monkeypatch):
    image_data = b"\xff\xd8\xffprofile\xff\xd9"
    row = {
        "patient_id": "p001",
        "content_type": "image/jpeg",
        "image_data": image_data,
    }
    monkeypatch.setattr(patients, "get_pool", lambda: FakePool(row))
    first = asyncio.run(patients.get_photo("p001", request()))

    response = asyncio.run(
        patients.get_photo("p001", request(first.headers["etag"]))
    )

    assert response.status_code == 304
    assert response.body == b""
    assert response.headers["cache-control"] == "private, max-age=3600"


@pytest.mark.parametrize(
    ("row", "detail"),
    [
        (None, "patient not found"),
        (
            {"patient_id": "p001", "content_type": None, "image_data": None},
            "patient photo not found",
        ),
    ],
)
def test_get_photo_returns_not_found(monkeypatch, row, detail):
    monkeypatch.setattr(patients, "get_pool", lambda: FakePool(row))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(patients.get_photo("p001", request()))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == detail
