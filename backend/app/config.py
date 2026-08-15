import os
from pathlib import Path

from dotenv import load_dotenv

DATABASE_ENV_PATH = Path(__file__).resolve().parents[2] / "database" / ".env"
load_dotenv(DATABASE_ENV_PATH)

_REQUIRED_DB_KEYS = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
                     "POSTGRES_PORT")


def database_url() -> str:
    """DB 접속 문자열. import 시점이 아니라 호출 시점에 환경 변수를 읽는다.

    ## 왜 상수가 아닌가

    모듈 최상단에서 읽으면 `POSTGRES_PORT` 하나가 없다는 이유로 app.config 를
    거치는 모든 것이 KeyError 로 죽는다. DB 를 쓰지도 않는 테스트까지 수집
    단계에서 무너진다 — 13개 모듈 중 10개가 그랬다. database/.env 는
    .gitignore 에 있어서 CI 러너에는 아예 없으므로, 이대로면 CI 를 세울 수 없다.

    ## 왜 기본값을 주지 않는가

    기본값을 깔면 오타 하나로 엉뚱한 DB 에 조용히 붙는다. 관제 데이터를 다루는
    쪽에서 그건 KeyError 보다 나쁘다. 실패는 그대로 두고 **실패 시점만** import
    에서 connect 로 옮긴다.
    """
    missing = [key for key in _REQUIRED_DB_KEYS if not os.environ.get(key)]
    if missing:
        raise RuntimeError(
            f"DB 환경 변수가 없습니다: {', '.join(missing)}. "
            f"{DATABASE_ENV_PATH} 를 만들거나 환경 변수로 넘기세요.")

    host = os.environ.get("POSTGRES_HOST", "localhost")
    return (f"postgresql://{os.environ['POSTGRES_USER']}"
            f":{os.environ['POSTGRES_PASSWORD']}"
            f"@{host}:{os.environ['POSTGRES_PORT']}"
            f"/{os.environ['POSTGRES_DB']}")

# --- 로봇 생존 감시 -----------------------------------------------------------
#
# 실측 전 잠정값이다. heartbeat 3 회 연속 유실이면 두절로 본다.
# 감지가 Nav2 목표 하나 소요 시간보다 길면, 로봇이 한 구간을 통째로 못 간 뒤에야
# 알게 된다. 주행 실측이 나오면 그 절반 이하로 다시 잡을 것.
HEARTBEAT_OFFLINE_AFTER_SEC = float(os.environ.get("HEARTBEAT_OFFLINE_AFTER_SEC", 15))

# 판정 주기. 두절 감지는 최대 이 값만큼 늦어진다.
HEARTBEAT_CHECK_INTERVAL_SEC = float(os.environ.get("HEARTBEAT_CHECK_INTERVAL_SEC", 5))

# 순간적인 Wi-Fi 손실이나 통합 서비스 재시작을 안내 취소로 오판하지 않도록
# 통신 두절/시스템 장애가 이 시간 이상 연속될 때만 활성 세션을 종료한다.
SESSION_FAILURE_CANCEL_AFTER_SEC = float(
    os.environ.get("SESSION_FAILURE_CANCEL_AFTER_SEC", 30))
