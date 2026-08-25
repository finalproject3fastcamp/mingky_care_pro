FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/mingky/backend \
    EVENT_CODES_FILE=/srv/mingky/config/event_codes.yaml

WORKDIR /srv/mingky

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app backend/app
COPY omx/web/policies.json omx/web/policies.json
COPY omx/web/count_tray.py omx/web/count_tray.py
# 파일 하나가 아니라 디렉터리 전체다. 서버가 읽는 정본이 다섯 개로 늘었고
# (event_codes · duplicate_node_severity · topic_watch · servo_limits ·
# alert_routes), 하나씩 적으면 새 파일을 추가한 사람이 여기를 잊는다.
# 그때 증상은 에러가 아니라 **판정이 조용히 기본값으로 도는 것**이라 아무도
# 못 알아챈다 — duplicate_node_severity.yaml 이 실제로 그 상태였다.
COPY config config
COPY database/seeds database/seeds
# 대시보드가 로봇 위치를 그릴 바탕 지도. Nav2 가 쓰는 것과 같은 파일이다.
COPY mingky_ros/mingky_bringup/map map
COPY mingky_ros/mingky_bringup/config/waypoints waypoints
# 구간 지도 (군집 조정). 없으면 조정이 꺼진 채로 돈다.
COPY mingky_ros/mingky_bringup/config/fleet fleet

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
