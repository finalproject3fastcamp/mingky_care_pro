FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/mingky/backend \
    EVENT_CODES_FILE=/srv/mingky/config/event_codes.yaml

WORKDIR /srv/mingky

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app backend/app
COPY config/event_codes.yaml config/event_codes.yaml
COPY database/seeds database/seeds
# 대시보드가 로봇 위치를 그릴 바탕 지도. Nav2 가 쓰는 것과 같은 파일이다.
COPY mingky_ros/mingky_bringup/map map

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
