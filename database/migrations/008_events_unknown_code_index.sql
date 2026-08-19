-- 미등록 event_code 집계용 부분 인덱스.
--
-- GET /events/unknown-codes 는 이렇게 훑는다.
--
--     WHERE event_code = 'system.unknown_event_code'
--
-- events 에는 event_code 인덱스가 없다(003 은 occurred_at, robot_id,
-- session_id 만 걸었다). 그래서 이 질의가 **events 전체를 순차 스캔한다.**
-- 화면이 60초마다 물어보고, 대시보드를 여러 명이 열면 그만큼 곱해진다.
-- events 는 append-only 라 이 비용이 계속 커지기만 한다.
--
-- 프론트가 since 없이 부르므로 occurred_at 조건은 걸리지 않는다. 시간으로
-- 좁혀지기를 기대할 수 없다.
--
-- ## 왜 부분 인덱스인가
--
-- 미등록 코드는 드물어야 정상이다. 전체 event_code 에 인덱스를 걸면 흔한
-- 코드(nav.*, qr.*) 때문에 인덱스가 events 만큼 커지는데, 정작 이 질의는
-- 그중 한 값만 본다. 마커 행만 담으면 인덱스가 작게 유지되고 적재 경로에
-- 주는 부담도 그만큼 적다. 003 의 idx_events_session 과 같은 방식이다.
--
-- occurred_at DESC 를 키로 두는 이유는 since 를 주는 호출과 last_seen
-- 정렬을 같은 인덱스로 처리하기 위해서다.

BEGIN;

CREATE INDEX idx_events_unknown_code
    ON events (occurred_at DESC)
    WHERE event_code = 'system.unknown_event_code';

COMMIT;
