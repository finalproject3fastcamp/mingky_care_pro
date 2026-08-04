# 로봇에서 발생한 이벤트 한 건.
#
# 의료진 대시보드와 엔지니어 대시보드가 같은 소스를 보도록 하는 공통 인터페이스이며,
# 게이트웨이를 거쳐 PostgreSQL events 테이블에 그대로 적재된다.
# 발행 가능한 event_code 목록과 payload 형태는 config/event_codes.yaml 이 정본이다.

uint8 LEVEL_INFO=0
uint8 LEVEL_WARNING=1
uint8 LEVEL_ERROR=2

# UUID 문자열. 발행하는 노드가 생성한다.
# 네트워크 두절 후 재전송될 때 서버가 ON CONFLICT DO NOTHING 으로 걸러내므로
# 같은 이벤트를 여러 번 보내도 안전하다.
string event_id

# robots.robot_id 와 같은 값. 예: 'pinky-01'
string robot_id

# guidance_sessions.session_id.
# 0 은 세션과 무관한 이벤트를 뜻한다.
# DB 시퀀스가 1 부터 시작하므로 0 은 실제 세션과 겹치지 않는다.
int64 session_id

# 이벤트가 실제로 일어난 벽시계 시각.
# header.stamp 를 쓰지 않는 이유는 use_sim_time 이 켜진 환경에서
# 시뮬레이션 시간이 들어가 DB 에 잘못된 시각이 저장되기 때문이다.
builtin_interfaces/Time occurred_at

# 위 LEVEL_* 상수 중 하나.
uint8 level

# 이벤트 종류. config/event_codes.yaml 에 등록된 값만 사용한다.
# 수집 서버가 그 목록으로 검증하므로 미등록 값은 오류로 드러난다.
string event_code

# 이벤트를 발행한 노드 이름. 엔지니어 대시보드의 노드별 필터에 쓰인다.
string source_node

# event_code 별로 형태가 다른 부가 정보를 담은 JSON 문자열.
# 형태는 config/event_codes.yaml 이 정의한다.
string payload
