# QR 스캔으로 백엔드가 세션을 생성했음을 로봇 내부에 알린다.
# 발행자: mingky_qr_reader (POST /qr/scan 응답 파싱 후)
# 구독자: mingky_guide_manager (세션 상태 소유자)
#
# visit_names 는 step_order 오름차순으로 담는다. 인덱스 (current_step_order - 1)
# 이 현재 진행해야 할 단계다. 백엔드 응답의 steps 배열을 그대로 옮겨온 형태다.

# guidance_sessions.session_id. 0 이면 유효하지 않은 세션.
int64 session_id

# 이후 발행하는 모든 이벤트가 이 세션을 참조한다.
string patient_id

# 1-based. 재스캔으로 기존 세션을 되받은 경우엔 진행 중이던 단계가 들어온다.
int32 current_step_order

# session_steps.visit_name 을 step_order 순으로 나열한 것.
string[] visit_names
