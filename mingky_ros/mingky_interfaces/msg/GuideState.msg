# 안내 상태. 상태를 소유하는 노드(mingky_guide_manager)만 발행한다.
#
# 로봇 축과 세션 축을 나눈다. 둘은 동시에 서로 다른 값을 가진다.
# 예) 로봇은 battery_low 인데 환자는 in_room(검사중) 일 수 있다.
# 한 축으로 합치면 표현이 불가능하고, 로봇이 2대라 더 그렇다.
#
# 화면에 보일 한글 라벨은 여기서 정하지 않는다.
# 이 토픽은 기계가 읽는 것이고, 표시 문구는 프론트와 LCD 가 각자 매핑한다.

# --- 로봇 축 : 하드웨어와 주행 상태 ---
string ROBOT_IDLE=idle
string ROBOT_MOVING=moving
string ROBOT_WAITING=waiting
string ROBOT_CHARGING=charging
string ROBOT_BATTERY_LOW=battery_low
string ROBOT_COMM_LOST=comm_lost
string ROBOT_PAUSED=paused
string ROBOT_RETURNING_TO_DOCK=returning_to_dock

# --- 세션 축 : 환자 안내 진행 상태 ---
string SESSION_NONE=none
string SESSION_QR=qr_scanning
string SESSION_CONFIRMED=patient_confirmed
string SESSION_GUIDING=guiding
string SESSION_ARRIVED=arrived
string SESSION_IN_ROOM=in_room
string SESSION_COMPLETED=completed

# robots.robot_id 와 같은 값. 예: 'pinky-01'
string robot_id

# 위 ROBOT_* / SESSION_* 상수 중 하나.
string robot_state
string session_state

# guidance_sessions.session_id. 세션이 없으면 0.
int64 session_id

# 현재 안내 중인 환자와 이동 구간. 세션이 없으면 빈 문자열.
string patient_id
# 첫 목적지로 출발할 때는 빈 문자열. 두 번째 목적지부터는 직전 검사실 이름.
# LCD처럼 늦게 뜨는 구독자도 "X-ray → CT"를 복원할 수 있도록 상태에 포함한다.
string previous_visit
string current_visit

# 배터리. percentage 는 파생값이라 없을 수 있으므로 -1 을 '모름' 으로 쓴다.
float32 battery_voltage
bool returning_to_dock
int16 battery_percent

# 환자를 기다리는 실제 남은 시간. Guide Manager의 단조 시계에서 계산한다.
# 대기 중이 아니면 -1. 화면이 자체 시작 시각을 추측하지 않게 하는 값이다.
float32 patient_wait_remaining_sec
