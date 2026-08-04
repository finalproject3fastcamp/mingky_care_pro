"""전송 대기 이벤트를 담는 로컬 큐.

SQLite 를 쓰는 이유는 셋이다.

  - 파이썬 표준 라이브러리라 의존성이 안 늘어난다
  - 트랜잭션이 있어서 '보냈는데 못 지운' 상태가 안 생긴다
  - 파일이라 로봇이 재부팅해도 남는다.
    메모리 큐면 네트워크 두절 중 재부팅 시 그동안의 이벤트를 통째로 잃는다

이 큐가 있어야 event_id 를 UUID 로 둔 설계가 실제로 값을 한다.
두절 동안 쌓아뒀다 몰아 보내고, 중복은 서버가 걸러낸다.
"""

import json
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    body        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_occurred ON pending (occurred_at);
"""


class QueueStore:
    """스레드 안전한 파일 기반 큐.

    ROS 콜백 스레드가 put() 하고, 전송 스레드가 take()/drop() 한다.
    """

    def __init__(self, path: str, max_rows: int = 50_000):
        self._path = Path(path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_rows = max_rows
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def put(self, event: dict) -> None:
        """큐에 한 건 넣는다. 같은 event_id 가 이미 있으면 무시한다."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO pending (event_id, occurred_at, body) "
                "VALUES (?, ?, ?)",
                (event["event_id"], event["occurred_at"],
                 json.dumps(event, ensure_ascii=False)),
            )
            # 상한을 넘으면 오래된 것부터 버린다.
            # 디스크가 차서 로봇이 멈추는 것보다 낫다.
            self._conn.execute(
                "DELETE FROM pending WHERE id IN ("
                "  SELECT id FROM pending ORDER BY id DESC LIMIT -1 OFFSET ?)",
                (self._max_rows,),
            )
            self._conn.commit()

    def take(self, limit: int) -> list[tuple[int, dict]]:
        """보낼 배치를 꺼낸다. 지우지는 않는다.

        전송이 성공한 뒤에 drop() 해야 '보냈다고 지웠는데 실제로는 실패' 를 막는다.
        발생 순으로 꺼내 서버가 순서 정렬에 쓰는 비용을 줄인다.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, body FROM pending ORDER BY occurred_at, id LIMIT ?",
                (limit,),
            ).fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    def drop(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM pending WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT count(*) FROM pending").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
