#!/usr/bin/env python3
"""SQLite에 저장된 Nav2 실험 한 건을 읽기 쉬운 텍스트로 요약한다."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def scalar(connection: sqlite3.Connection, query: str, values: tuple[object, ...]) -> object:
    row = connection.execute(query, values).fetchone()
    return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="실험 SQLite 파일")
    parser.add_argument("--run-id", help="지정하지 않으면 DB의 최신 run")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"DB 파일을 찾을 수 없습니다: {args.db}")

    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    run = connection.execute(
        "SELECT * FROM runs WHERE id=?" if args.run_id else "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1",
        (args.run_id,) if args.run_id else (),
    ).fetchone()
    if run is None:
        print("[안내] 기록된 실험이 없습니다.")
        return 1

    run_id = run["id"]
    print(f"실험: {run_id}")
    print(f"상태: {run['status']}  시작: {run['started_at']}  종료: {run['finished_at'] or '-'}")
    print(f"지도: {run['map_path']}")
    print(f"프로파일: {run['profile_path'] or 'baseline'}")
    print(f"Git: {run['git_commit'] or '-'}")
    print()

    goals = connection.execute(
        "SELECT result, COUNT(*) AS count FROM goals WHERE run_id=? GROUP BY result ORDER BY result", (run_id,)
    ).fetchall()
    print("목표 결과:", ", ".join(f"{row['result'] or '진행 중'} {row['count']}" for row in goals) or "없음")
    print("샘플 수:", scalar(connection, "SELECT COUNT(*) FROM samples WHERE run_id=?", (run_id,)))
    min_distance = scalar(connection, "SELECT MIN(scan_min_range) FROM samples WHERE run_id=?", (run_id,))
    min_front = scalar(connection, "SELECT MIN(scan_front_range) FROM samples WHERE run_id=?", (run_id,))
    print(f"최소 LiDAR 거리: 전체 {min_distance if min_distance is not None else '-'}m / 전방 {min_front if min_front is not None else '-'}m")
    print("저장된 실패 스냅샷:", scalar(connection, "SELECT COUNT(*) FROM snapshots WHERE run_id=?", (run_id,)))
    print()

    events = connection.execute(
        "SELECT level, source, event_type, COUNT(*) AS count FROM events WHERE run_id=? GROUP BY level, source, event_type ORDER BY count DESC, source",
        (run_id,),
    ).fetchall()
    print("이벤트 요약:")
    if not events:
        print("  없음")
    for event in events:
        print(f"  [{event['level']}] {event['source']} / {event['event_type']}: {event['count']}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
