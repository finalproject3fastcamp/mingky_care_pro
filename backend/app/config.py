import os
from pathlib import Path

from dotenv import load_dotenv

DATABASE_ENV_PATH = Path(__file__).resolve().parents[2] / "database" / ".env"
load_dotenv(DATABASE_ENV_PATH)

_HOST = os.environ.get("POSTGRES_HOST", "localhost")
_PORT = os.environ["POSTGRES_PORT"]
_USER = os.environ["POSTGRES_USER"]
_PASSWORD = os.environ["POSTGRES_PASSWORD"]
_DB = os.environ["POSTGRES_DB"]

DATABASE_URL = f"postgresql://{_USER}:{_PASSWORD}@{_HOST}:{_PORT}/{_DB}"

# --- 로봇 생존 감시 -----------------------------------------------------------
#
# 실측 전 잠정값이다. heartbeat 3 회 연속 유실이면 두절로 본다.
# 감지가 Nav2 목표 하나 소요 시간보다 길면, 로봇이 한 구간을 통째로 못 간 뒤에야
# 알게 된다. 주행 실측이 나오면 그 절반 이하로 다시 잡을 것.
HEARTBEAT_OFFLINE_AFTER_SEC = float(os.environ.get("HEARTBEAT_OFFLINE_AFTER_SEC", 15))

# 판정 주기. 두절 감지는 최대 이 값만큼 늦어진다.
HEARTBEAT_CHECK_INTERVAL_SEC = float(os.environ.get("HEARTBEAT_CHECK_INTERVAL_SEC", 5))
