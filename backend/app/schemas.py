from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class QrScanRequest(BaseModel):
    patient_id: str = Field(min_length=1, max_length=20)
    # 어느 로봇이 스캔했는지. guidance_sessions.robot_id 가 NOT NULL 이라
    # 이 값이 없으면 세션을 만들 수 없다.
    robot_id: str = Field(min_length=1, max_length=20)
    # 환자가 착용한 ArUco 마커. 아직 안 쓰면 생략한다.
    marker_id: int | None = Field(default=None, ge=0, le=49)


class Patient(BaseModel):
    patient_id: str
    name: str
    gender: str
    birth_date: date
    condition_name: str


class ScheduleStep(BaseModel):
    """session_steps 한 행. examination_steps(마스터)가 아니라 스냅샷이다.

    마스터를 그대로 내보내면 나중에 검사 순서가 바뀔 때 과거 안내 기록의
    일정까지 소급해서 달라진다.
    """

    step_order: int
    visit_name: str
    arrived_at: datetime | None = None
    completed_at: datetime | None = None


class TodaySchedule(BaseModel):
    # 로봇은 이 값을 이후 모든 이벤트에 달고 다닌다.
    session_id: int
    patient: Patient
    steps: list[ScheduleStep]
    current_step_order: int


class EventIn(BaseModel):
    """로봇이 보내는 이벤트 한 건. mingky_interfaces/msg/Event 와 1:1."""

    event_id: UUID
    robot_id: str = Field(max_length=20)
    # 0 은 세션과 무관한 이벤트. DB 시퀀스가 1부터라 충돌하지 않는다.
    session_id: int = 0
    occurred_at: datetime
    level: Literal["info", "warning", "error"]
    event_code: str = Field(max_length=50)
    source_node: str = Field(default="", max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    received: int
    inserted: int
    duplicates: int
    state_updates: int
    # 미등록 코드는 거부하지 않고 적재한 뒤 여기 담아 돌려준다.
    # 게이트웨이가 같은 배치를 무한 재전송하지 않도록 HTTP 는 200 이다.
    unknown_codes: list[str] = Field(default_factory=list)
    # 타입 오배선 (robot_type_mismatch) 발생 코드.
    type_mismatches: list[str] = Field(default_factory=list)
    # 이벤트는 적재됐으나 DB 제약에 걸려 상태 갱신을 못 한 코드.
    # 시계가 어긋난 로봇이 보내면 여기에 쌓인다.
    rejected_updates: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 조회 응답


class PatientSummary(BaseModel):
    """대시보드에 띄우는 환자 정보.

    age 는 컬럼이 아니다. 002 에서 지웠고 birth_date 에서 계산한다.
    저장해두면 시간이 지나면서 생년월일과 갈라진다.
    """

    patient_id: str
    name: str
    gender: str
    birth_date: date
    age: int
    condition_name: str


class SessionStep(BaseModel):
    step_order: int
    visit_name: str
    arrived_at: datetime | None = None
    completed_at: datetime | None = None
    completed_source: str | None = None


class SessionOut(BaseModel):
    session_id: int
    robot_id: str
    marker_id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None
    patient: PatientSummary
    steps: list[SessionStep]
    # 진행 중인 세션만 값이 있다. 끝난 세션에는 '현재' 가 없다.
    current_step_order: int | None = None
    current_visit: str | None = None


class OrderIn(BaseModel):
    """대시보드가 로봇에 내리는 명령.

    command 를 문자열로 열어두지 않고 좁힌 이유는, 로봇이 모르는 명령을
    받았을 때 조용히 무시하는 상황을 만들지 않기 위해서다.
    """

    command: Literal[
        "goto", "goto_pose", "start_session", "start_guidance",
        "cancel_guidance", "set_mode",
        "localize", "system_start", "system_stop", "system_restart",
        "fire_alarm_reset", "set_navigation_speed", "set_low_obstacle_mode",
    ]
    # goto 면 waypoint 이름, goto_pose 면 임시 좌표 JSON,
    # start_session 이면 patient_id, start_guidance/cancel_guidance 면 session_id,
    # set_mode 면 auto | manual | estop, set_navigation_speed 면 m/s 숫자,
    # set_low_obstacle_mode 면 disabled | sidestep,
    # 나머지 제어 명령은 run.
    #
    # 모드는 로봇이 정본을 갖는다. 여기서 보내는 것은 요청이고, 반영 여부는
    # robot.mode_changed 이벤트로 확인한다. 통신이 끊겨도 로봇이 스스로
    # 안전한 상태를 지켜야 하므로 서버가 상태를 소유하지 않는다.
    argument: str = Field(min_length=1, max_length=200)


class OrderOut(BaseModel):
    order_id: UUID
    robot_id: str
    command: str
    argument: str
    created_at: datetime


class OrderAck(BaseModel):
    order_id: UUID


class EventOut(BaseModel):
    event_id: UUID
    robot_id: str | None = None
    session_id: int | None = None
    occurred_at: datetime
    received_at: datetime
    level: str
    event_code: str
    source_node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[EventOut]


class SessionEndingContextOut(BaseModel):
    """세션이 왜 그렇게 끝났는지 — 종료 직전 창의 이벤트.

    `end_reason` 만으로는 "배터리 부족으로 끝났다" 까지만 알 수 있고, 그게
    갑자기 벌어진 일인지 40초 전부터 예고돼 있었는지는 알 수 없다. 둘은
    대응이 다르다 — 후자면 임계값이 늦은 것이다.

    문구는 만들지 않고 사실만 돌려준다. 의료진 화면은 한 줄 요약으로,
    엔지니어 화면은 타임라인 전체로 같은 데이터를 다르게 그린다.
    화면마다 어휘가 다르므로 문장은 각 화면이 만든다.
    """

    session_id: int
    ended_at: datetime | None = None
    end_reason: str | None = None
    # 창 안에서 가장 이른 경고/오류. 인과의 시작점이다.
    lead_event_code: str | None = None
    lead_event_at: datetime | None = None
    # 그 경고가 종료보다 몇 초 앞섰는가.
    lead_sec: int | None = None
    # 엔지니어 타임라인용. 발생 순(ASC)이다 — 인과는 시간 순으로 읽는다.
    events: list[EventOut] = Field(default_factory=list)


class BatteryForecastOut(BaseModel):
    """전압 추이로 낸 충전/방전 예상.

    seconds 는 신뢰할 만할 때만 채운다. 부하가 출렁이는 구간의 기울기로
    시간을 내면 "3분 남음" 이 다음 표본에 "47분 남음" 이 된다.
    틀린 시간은 없는 시간보다 나쁘다 — 의료진이 그 숫자로 일정을 잡는다.
    """

    robot_id: str
    direction: Literal["charging", "discharging", "idle", "unknown"] = "unknown"
    seconds: int | None = None
    slope_v_per_hour: float | None = None
    # 적합도. 낮으면 부하가 출렁이는 중이다.
    r_squared: float | None = None
    sample_count: int = 0
    # 시간을 못 낸 이유. 화면이 아니라 엔지니어가 읽는다.
    reason: str | None = None


class UnknownCodeOut(BaseModel):
    """config/event_codes.yaml 에 없는 코드가 얼마나 들어왔는가.

    ingest 는 모르는 코드도 버리지 않고 원본을 그대로 적재한 뒤
    system.unknown_event_code 마커를 함께 남긴다(규칙 4). 그래서 별도
    수집 테이블 없이 마커를 집계하면 된다.

    이 목록이 비어 있지 않다는 것은 로봇이 보내는 이벤트를 서버가
    해석하지 못하고 있다는 뜻이다. 적재는 됐지만 상태 갱신은 일어나지
    않았으므로(ingest 는 known 인 것만 _apply_state 한다) 화면·판정에서
    통째로 빠져 있다.
    """

    event_code: str
    robot_id: str | None = None
    count: int
    first_seen: datetime
    last_seen: datetime


class ServoFault(BaseModel):
    """Dynamixel 이 직접 보고한 하드웨어 에러 (§4.4).

    해소를 알리는 이벤트가 없다. 그래서 occurred_at 을 반드시 같이 내려보낸다 —
    화면이 "3일 전 결함" 과 "방금 결함" 을 같은 빨강으로 그리면 안 된다.
    """

    joint: str
    fault_bits: int | None = None
    temp_c: float | None = None
    occurred_at: datetime


class ServoReadingIn(BaseModel):
    """Dynamixel 하나가 보고한 표본 (§4.4 · 로드맵 11).

    전부 optional 이다. U2D2 응답이 부분적으로 실패해도 읽은 것만 보내는 쪽이,
    한 항목 때문에 표본 전체를 버리는 것보다 낫다. 다만 하나도 없으면 DB 가
    거부한다(012 의 CHECK).

    hardware_error 의 0 과 None 은 다른 사실이다 — 전자는 '정상이라고
    읽었다', 후자는 '못 읽었다' 이고, 둘을 같게 저장하면 통신이 죽은 서보가
    정상으로 집계된다.
    """

    joint: str = Field(min_length=1, max_length=30)
    temp_c: float | None = Field(default=None, ge=-20, le=150)
    # 부호가 있다. 방향에 따라 음수이고 절대값이 부하다.
    current_ma: float | None = Field(default=None, ge=-10000, le=10000)
    voltage_v: float | None = Field(default=None, ge=0, le=30)
    hardware_error: int | None = Field(default=None, ge=0, le=255)


class ServoSampleIn(BaseModel):
    """한 번의 스캔에서 읽은 조인트들. 팔 하나가 주기적으로 올린다."""

    # 상한은 payload 방어다. OMX 는 조인트 5개 + 그리퍼다.
    servos: list[ServoReadingIn] = Field(min_length=1, max_length=20)


class ServoReadingOut(BaseModel):
    """조인트 하나의 최신값과 추세 (§4.4).

    **지금 뜨거운 것과 오르는 중인 것은 다른 사실이다.** 40℃ 인데 회차마다
    오르는 조인트가, 55℃ 에서 평평한 조인트보다 나쁜 신호다. 그래서 state 와
    rising 을 따로 둔다 — 하나로 뭉개면 예지보전 신호가 사라진다.
    """

    joint: str
    recorded_at: datetime
    temp_c: float | None = None
    current_ma: float | None = None
    voltage_v: float | None = None
    hardware_error: int | None = None

    # fault   하드웨어 에러 비트가 섰다
    # hot     임계 초과. 이벤트가 나간다
    # warm    경고선 초과. 표시만 한다
    # ok      정상
    # unknown 온도를 못 읽었다 (에러 비트도 없으면 판정 불가)
    state: Literal["fault", "hot", "warm", "ok", "unknown"]
    warn_temp_c: float | None = None
    hot_temp_c: float | None = None

    # 온도 추세. 신뢰할 만할 때만 채운다 — 표본이 모자라거나 적합이 나쁘면
    # None 이다. 틀린 추세는 없는 추세보다 나쁘다(battery_forecast 와 같은 규칙).
    slope_c_per_hour: float | None = None
    rising: bool = False
    sample_count: int = 0


class ServoHealthOut(BaseModel):
    """팔 한 대의 서보 상태 (§4.4 · 로드맵 11).

    로봇 목록에 얹지 않는다. 저쪽은 3초 폴링인데 추세는 몇 시간 창의 집계라
    같이 두면 그 쿼리가 3초마다 돈다 — 배터리 예상(`/battery-forecast`)을
    따로 뺀 것과 같은 이유다.
    """

    robot_id: str
    window_min: int
    # 나쁜 것부터 정렬돼 온다. 화면이 다시 정렬하지 않는다.
    servos: list[ServoReadingOut] = Field(default_factory=list)


class ManipulatorDetail(BaseModel):
    """팔 전용 지표 (§7.3 의 `detail`). app/dispense.py 가 events 에서 만든다.

    저장된 컬럼이 아니다. 같은 사실을 컬럼으로 한 번 더 들고 있으면 events 와
    갈라지고, 그때 어느 쪽이 맞는지 판단할 근거가 없다.
    """

    # ---- 지금 상태 (창과 무관하다)
    #
    # 어제 되던 pick 이 오늘 안 되는 원인은 대개 코드가 아니라 체크포인트다.
    # 팔의 '무엇이 돌고 있나' 는 git SHA 만으로 답이 안 된다 (§4.4).
    policy_checkpoint_id: str | None = None
    policy_dataset_revision: str | None = None
    policy_loaded_at: datetime | None = None
    # 시작만 하고 아직 끝나지 않은 사이클. 없으면 유휴다.
    active_dispense_id: str | None = None
    active_started_at: datetime | None = None
    # 사람이 홈 복귀를 지시해야 다음 사이클이 시작된다 (§4.4).
    homing_required: bool = False
    homing_reason: str | None = None
    last_servo_fault: ServoFault | None = None

    # ---- 직전 N 사이클 이동창 (§7.2)
    window: int
    # 창을 못 채운 표본. 3건에 1건 실패면 66.7% 지만 신뢰구간이 창만큼 넓다.
    window_cycles: int
    sample_complete: bool
    cycles_completed: int
    cycles_aborted: int
    last_cycle_ended_at: datetime | None = None
    pick_succeeded: int
    pick_failed: int
    # 재시도로 성공한 건. 한 번에 집은 것과 세 번 만에 집은 것을 구분하지
    # 못하면 성공률이 정책 품질을 반영하지 못한다.
    pick_retried: int
    # 표본이 0 이면 성공률은 존재하지 않는다. 0.0 과 구분해야 한다.
    pick_success_rate: float | None = None
    cycle_time_p50_ms: int | None = None
    cycle_time_p95_ms: int | None = None


class TopicSampleIn(BaseModel):
    """로봇이 보고하는 토픽 하나의 사실 (§7.2, 로드맵 9).

    판정은 담지 않는다. "몇 초 전에 마지막으로 받았고 최근 주기가 몇 Hz 였는가"
    까지가 로봇의 몫이고, 그게 정상인지는 서버가 config/topic_watch.yaml 로
    정한다 — 임계를 바꾸려고 로봇을 재배포하지 않는다.

    age_sec 은 한 번도 못 받았어도 None 이 아니다. 감시를 시작한 시각부터
    잰다 — 그래야 '부팅 이후 라이다가 한 번도 안 뜬' 경우가 시간이 갈수록
    나빠지는 값으로 드러난다. None 은 구버전 게이트웨이 대비로만 열어둔다.
    """

    age_sec: float | None = Field(default=None, ge=0)
    # 경과 시간만으로는 못 잡는 장애가 있다. 라이다가 USB 대역 부족으로
    # 10Hz → 3Hz 가 되면 나이는 0.33초라 어떤 임계에도 안 걸린다.
    hz: float | None = Field(default=None, ge=0)


class TopicAgeOut(BaseModel):
    """토픽 하나의 나이와 그 판정 (§7.2).

    판정을 프론트가 다시 하지 않게 state 까지 실어 보낸다. 임계가 서버 설정
    파일에 있는데 화면이 자기 숫자로 색을 칠하면 두 곳이 조용히 갈라진다.
    """

    topic: str
    age_sec: float | None = None
    hz: float | None = None
    expected_hz: float | None = None
    # fresh    정상
    # slow     늦다 (warn 초과 또는 측정 Hz 가 기대의 min_hz_ratio 미만)
    # stale    사실상 끊겼다
    # idle     always_on 이 아닌 토픽이 안 오는 중. 대기 로봇의 정상 상태다
    # missing  로봇이 나이를 못 냈다 (구버전 게이트웨이)
    # unwatched 정본에 있는데 로봇이 보고하지 않는다 — 감시 노드가 안 보고 있다
    # unrated  로봇이 보고했는데 정본에 임계가 없다. 표시만 하고 판정하지 않는다
    state: Literal[
        "fresh", "slow", "stale", "idle", "missing", "unwatched", "unrated"]
    always_on: bool = False
    # 이 토픽이 끊기면 무엇이 안 되는가. 화면이 문구를 만들지 않게 서버가 준다.
    why: str = ""


class RobotBase(BaseModel):
    """로봇 종류와 무관하게 성립하는 것만 (§4.3 의 공통 축).

    생존·형상·리소스는 mobile 이든 manipulator 든 같은 질문이다. 나머지는
    타입별 모델로 내려간다 — 배터리를 여기 두면 팔의 배터리가 항상 null 로
    나가고, 화면은 그 null 을 '아직 보고 안 함' 과 구분하지 못한다.
    """

    robot_id: str
    display_name: str
    domain_id: int | None = None
    is_active: bool
    # 생존 여부는 DB 가 아니라 백엔드 메모리에서 온다(app/heartbeat.py).
    # unknown 은 '한 번도 heartbeat 를 안 보낸 로봇' 이다. offline 과 다르다 —
    # OMX 는 관제 PC 에 USB 직결이라 잃을 네트워크 링크가 없다.
    last_seen_at: datetime | None = None
    link_state: Literal["online", "offline", "unknown"] = "unknown"
    runtime_reported_at: datetime | None = None
    # 로봇이 보고한 자원·큐 상태. 전부 인메모리(robot_runtime)이고 DB 에
    # 저장하지 않는다. 구버전 게이트웨이는 안 보내므로 None 이 정상이다.
    cpu_total_pct: float | None = None
    queue_pending: int | None = None
    max_node_cpu_pct: float | None = None
    max_node_cpu_name: str | None = None
    inventory_hash: str | None = None


class MobileRobotOut(RobotBase):
    """주행 로봇(핑키). 안내 세션·배터리·Nav2 가 여기 있다."""

    robot_type: Literal["mobile"] = "mobile"
    # 배터리는 저장된 마지막 표본이다. 실시간 값이 아니다.
    battery_voltage: float | None = None
    battery_percent: int | None = None
    battery_recorded_at: datetime | None = None
    active_session_id: int | None = None
    active_patient_id: str | None = None
    last_session_ended_at: datetime | None = None
    last_session_end_reason: str | None = None
    # 의료진이 이 로봇을 활성화한 시각. NULL = 대기 중.
    # DB 컬럼이 아니라 app/arming.py 인메모리 레지스트리에서 조립한다.
    armed_at: datetime | None = None
    # ROS 2 라이프사이클이다. OMX 는 LeRobot 시리얼 직결이라 이 축이 없다.
    system_state: Literal[
        "active", "activating", "deactivating", "inactive", "failed", "unknown"
    ] = "unknown"
    localization_active: bool = False
    fire_alarm_active: bool | None = None
    returning_to_dock: bool = False
    navigation_speed_mps: float | None = Field(default=None, ge=0.05, le=0.25)
    low_obstacle_mode: Literal["disabled", "sidestep"] | None = None
    guide_robot_state: Literal[
        "idle", "moving", "waiting", "charging", "battery_low",
        "comm_lost", "paused", "returning_to_dock",
    ] | None = None
    # 토픽 주기 감시 (§7.2). 공통 축이 아니라 여기 두는 이유는 ROS 개념이라서다
    # — OMX 는 LeRobot 시리얼 직결이라 토픽 자체가 없고, 팔에 빈 목록을 내려
    # 보내면 화면이 "감시 중인데 아무것도 안 온다" 로 읽는다.
    # 나쁜 것부터 정렬돼 온다. 화면이 다시 정렬하지 않는다.
    topics: list[TopicAgeOut] = Field(default_factory=list)


class ManipulatorRobotOut(RobotBase):
    """조제 로봇(OMX). arming 도 세션도 배터리도 없다.

    없는 필드를 null 로 채워 보내지 않는 것이 이 분리의 전부다. mobile 전용
    필드가 응답에 없으면 프론트의 discriminated union 이 컴파일 타임에
    "팔에 배터리 카드를 렌더" 를 막는다 (§7.3).
    """

    robot_type: Literal["manipulator"] = "manipulator"
    detail: ManipulatorDetail


# robot_type 이 discriminant 다. FastAPI 가 이 어노테이션으로 스키마를
# 갈라주고, 프론트의 `type Robot = MobileRobot | ManipulatorRobot` 과 1:1 이 된다.
RobotOut = Annotated[
    MobileRobotOut | ManipulatorRobotOut, Field(discriminator="robot_type")
]


class RobotArmingOut(BaseModel):
    """로봇 QR 노드가 폴링하는 최소 상태."""

    robot_id: str
    armed: bool
    armed_at: datetime | None = None


class RobotHeartbeatIn(BaseModel):
    """상시 게이트웨이가 함께 보고하는 통합 실행 상태.

    5초 주기 × 로봇 수라 payload 를 키우면 안 된다. 여기에는 작고 자주
    바뀌는 것만 싣고, 크고 거의 안 바뀌는 것(노드 목록·커밋)은
    RobotInventoryIn 으로 따로 보낸다. 해시만 실어 서버가 자기가 아는
    것과 다른지 판단하게 한다.

    신규 필드는 전부 기본값 있는 optional 이다. 게이트웨이가 먼저 배포되든
    서버가 먼저 배포되든 heartbeat 가 422 로 거부되면 안 된다 — 생존 신호가
    끊기면 멀쩡한 로봇에 comm_lost 가 찍힌다.
    """

    system_state: Literal[
        "active", "activating", "deactivating", "inactive", "failed", "unknown"
    ] = "unknown"
    localization_active: bool = False
    # None은 구버전 게이트웨이이거나 아직 화재 노드의 상태를 받지 못한 경우다.
    fire_alarm_active: bool | None = None
    # 구버전 게이트웨이는 이 필드가 없으므로 None도 정상이다.
    returning_to_dock: bool | None = None
    # Nav2 controller_server에 실제로 반영된 직진 목표속도. 구버전 게이트웨이는
    # 이 값을 보내지 않으므로 None을 허용한다.
    navigation_speed_mps: float | None = Field(default=None, ge=0.05, le=0.25)
    # guide_manager에 실제로 적용된 저상 장애물 회피 전략.
    low_obstacle_mode: Literal["disabled", "sidestep"] | None = None
    # Guide Manager의 현재 로봇 상태. 구버전 게이트웨이는 보내지 않는다.
    guide_robot_state: Literal[
        "idle", "moving", "waiting", "charging", "battery_low",
        "comm_lost", "paused", "returning_to_dock",
    ] | None = None

    # 게이트웨이가 계산한 인벤토리 지문. 서버가 아는 값과 다르면 본문을 요구한다.
    inventory_hash: str | None = Field(default=None, max_length=64)
    # 로봇 전체 CPU. 노드별 합이 아니라 /proc/stat 에서 읽은 값이다.
    cpu_total_pct: float | None = Field(default=None, ge=0, le=100)
    # 게이트웨이 전송 대기 건수. 상한 근처면 이미 데이터가 버려지는 중이다.
    queue_pending: int | None = Field(default=None, ge=0)
    # 가장 많이 먹는 프로세스 하나. 상세는 인벤토리에서 본다.
    # 코어가 여러 개면 100 을 넘을 수 있으므로 상한을 두지 않는다.
    max_node_cpu_pct: float | None = Field(default=None, ge=0)
    max_node_cpu_name: str | None = Field(default=None, max_length=100)
    # 토픽별 마지막 수신 경과와 최근 주기 (§7.2). 감시 노드가 콜백에서 시각만
    # 갱신하고 게이트웨이가 여기 싣는다. `ros2 topic hz` 를 서브프로세스로
    # 돌리지 않는다 — 호출마다 노드를 새로 띄우는 비용이 5초 주기에 안 맞는다.
    #
    # 개수를 제한하는 이유는 payload 방어다. 로봇이 토픽 200개를 실어 보내면
    # 5초 × 로봇 수로 계속 들어온다. 정본(config/topic_watch.yaml)이 네 개고,
    # 여유를 둬도 이 상한을 넘을 이유가 없다.
    topics: dict[str, TopicSampleIn] = Field(default_factory=dict, max_length=20)


class RobotHeartbeatOut(BaseModel):
    """heartbeat 응답.

    본문 없이 204 를 돌려주던 경로다. 서버가 로봇에게 요구할 게 생겨서
    본문이 붙었다 — 인벤토리 해시를 모르면 본문을 다시 달라고 한다.
    서버가 재시작해 메모리를 잃었거나 로봇의 전송이 유실된 경우다.
    """

    need_inventory: bool = False


class NodeGraphInfo(BaseModel):
    """rclpy 그래프에서 직접 얻은 노드. **중복 판정의 유일한 근거다.**

    프로세스 매칭 성공 여부와 무관하게 항상 정확하다. 같은 (이름,
    네임스페이스)가 두 번 뜨면 count 가 2 다.
    """

    name: str
    namespace: str = "/"
    count: int = Field(ge=1)


class ProcessInfo(BaseModel):
    """/proc 스캔 결과. 노드 이름은 **추정이다.**

    rclpy 는 노드→PID 매핑을 주지 않고 공식 API 도 없다. 컴포지션
    컨테이너를 쓰면 한 PID 에 노드가 여럿이라 1:1 도 아니다. 그래서
    matched_node_names 는 cmdline 의 `__node:=` 리매핑에서 추정하고,
    실패하면 실행 파일 이름으로 대신한다. 중복 판정에는 쓰지 않는다.
    """

    pid: int
    install_path: str
    workspace_path: str | None = None
    matched_node_names: list[str] = Field(default_factory=list)
    cpu_pct: float | None = None
    # 누적 CPU 초. 순간 100% 는 정상일 수 있지만 11시간 누적은 아니다.
    cpu_seconds_total: float | None = None


class WorkspaceInfo(BaseModel):
    """노드가 실제로 실행된 워크스페이스와 그 커밋.

    dirty 를 따로 보는 이유는, 커밋 안 된 변경이 있는 워크스페이스는
    커밋 해시만으로 재현이 불가능하기 때문이다.
    """

    path: str
    commit: str | None = None
    branch: str | None = None
    dirty: bool = False
    process_count: int = 0


class RobotInventoryIn(BaseModel):
    """변할 때만 오는 층. 노드 목록과 커밋은 몇 시간에 한 번 바뀐다."""

    inventory_hash: str = Field(max_length=64)
    node_graph: list[NodeGraphInfo] = Field(default_factory=list)
    processes: list[ProcessInfo] = Field(default_factory=list)
    workspaces: list[WorkspaceInfo] = Field(default_factory=list)
    ros_domain_id: int | None = None
    # 지금 map_server 가 실제로 실은 맵 (§7.2 형상 패널). 디스크의 yaml 이
    # 아니라 /map 격자에서 뜬 지문이라, 런치 인자와 실제가 갈린 경우가 드러난다.
    # 이름은 사람이 읽을 라벨일 뿐이고 판정은 지문으로 한다.
    map_name: str | None = Field(default=None, max_length=100)
    map_hash: str | None = Field(default=None, max_length=64)
    reported_at: datetime | None = None


class DuplicateNodeOut(BaseModel):
    """중복으로 뜬 노드와 그 심각도.

    심각도는 서버가 정한다(config/duplicate_node_severity.yaml). 로봇에
    박아두면 임계를 바꾸려고 재배포해야 한다.
    """

    name: str
    namespace: str = "/"
    count: int
    severity: Literal["error", "warning"]
    reason: str


class RobotInventoryOut(BaseModel):
    """엔지니어 화면이 읽는 인벤토리.

    판정 결과(중복 경고, 워크스페이스 혼재)를 함께 내려준다. 프론트가
    같은 판정을 다시 구현하면 두 곳이 어긋난다.
    """

    robot_id: str
    inventory_hash: str
    reported_at: datetime
    node_graph: list[NodeGraphInfo] = Field(default_factory=list)
    processes: list[ProcessInfo] = Field(default_factory=list)
    workspaces: list[WorkspaceInfo] = Field(default_factory=list)
    ros_domain_id: int | None = None
    map_name: str | None = None
    map_hash: str | None = None
    duplicates: list[DuplicateNodeOut] = Field(default_factory=list)
    # 정상 배치에서는 워크스페이스가 하나여야 한다. 둘 이상이면 서로 다른
    # 코드가 한 로봇에서 같이 도는 것이고, 그 상태로는 무엇을 고쳐야 하는지
    # 알 수 없다.
    mixed_workspaces: bool = False


class BatterySampleIn(BaseModel):
    """로봇 게이트웨이가 주기적으로 보내는 최신 배터리 표본."""

    voltage: float | None = Field(
        default=None, ge=0, le=12, allow_inf_nan=False)
    battery_percent: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def has_reading(self):
        if self.voltage is None and self.battery_percent is None:
            raise ValueError("voltage or battery_percent is required")
        return self


class QrObservationIn(BaseModel):
    """로봇 후방 카메라의 최신 QR 거리 관측값."""

    visible: bool
    distance: float | None = Field(
        default=None, gt=0, le=10, allow_inf_nan=False)
    follow_state: Literal[
        "inactive", "normal", "slow", "waiting",
    ] | None = None
    follow_distance: float | None = Field(
        default=None, gt=0, le=10, allow_inf_nan=False)
    follow_source: Literal[
        "none", "qr", "visual", "stale", "unknown",
    ] | None = None
    qr_visible: bool = False
    visual_visible: bool = False

    @model_validator(mode="after")
    def visible_has_distance(self):
        if self.visible and self.distance is None:
            raise ValueError("visible QR observation requires distance")
        if not self.visible:
            self.distance = None
        return self


class QrObservationOut(BaseModel):
    robot_id: str
    visible: bool = False
    distance: float | None = None
    follow_state: Literal[
        "inactive", "normal", "slow", "waiting",
    ] | None = None
    follow_distance: float | None = None
    follow_source: Literal[
        "none", "qr", "visual", "stale", "unknown",
    ] | None = None
    qr_visible: bool = False
    visual_visible: bool = False
    observed_at: datetime | None = None


class SloSessionOut(BaseModel):
    """판정이 끝난 세션 한 건.

    failures 가 비어 있으면 성공이다. 별도의 success 불린을 두지 않는 이유는
    둘이 어긋난 응답을 만들 수 있기 때문이다 — 목록이 곧 판정이다.
    """

    session_id: int
    robot_id: str
    started_at: datetime
    ended_at: datetime
    end_reason: str
    # abnormal_end · manual_stop · operator_order (app/slo.py 의 상수)
    failures: list[str]


class SloWindowOut(BaseModel):
    """직전 N세션 이동창의 완주율과 오차 예산 (§1.1 · §1.2).

    completion_rate 만 내려주지 않는다. 숫자 하나로는 "왜 나빠졌는가" 로
    넘어갈 수 없어서, 실패한 세션 목록을 같이 실어 화면이 바로 원인 추적
    도구가 되게 한다.
    """

    window: int
    sessions_judged: int
    # 표본이 창을 채웠는가. 12세션에 1회 실패면 91.7% 지만 신뢰구간이 창만큼
    # 넓다. 화면이 "표본 부족" 을 표시할 수 있어야 한다.
    sample_complete: bool
    success: int
    failure: int
    # 표본이 0 이면 완주율은 존재하지 않는다. 0.0 과 구분해야 한다.
    completion_rate: float | None
    target: float
    budget_total: int
    budget_used: int
    # 잔량 0 과 소진은 다른 상태다. 50세션에서 실패 5건은 정확히 90% 로
    # 목표 안이고, 6건부터 위반이다.
    budget_remaining: int
    budget_exhausted: bool
    failed_sessions: list[SloSessionOut]


class RobotConfigOut(BaseModel):
    """로봇 한 대가 지금 무엇으로 돌고 있는가 (§7.2 형상 패널).

    "데모가 어제는 됐는데 오늘 안 된다" 의 원인 대부분이 여기 있다. 그런데
    지금까지 이 세 가지를 한 화면에서 볼 곳이 없었다 — 커밋은 인벤토리
    안쪽에, 맵은 아무 데도, 체크포인트는 조제 패널에 흩어져 있었다.

    타입별로 채워지는 칸이 다르다. mobile 은 코드·맵, manipulator 는
    코드가 아니라 **정책 체크포인트와 데이터셋 revision** 이 버전이다(§4.4).
    """

    robot_id: str
    robot_type: str
    display_name: str
    # 형상 보고를 마지막으로 받은 시각. None 이면 한 번도 보고하지 않았다 —
    # OMX 는 게이트웨이가 아직 없어 정상적으로 None 이다(로드맵 6).
    reported_at: datetime | None = None

    # ---- mobile (ROS 워크스페이스와 맵)
    commit: str | None = None
    branch: str | None = None
    # 커밋 안 된 변경이 있으면 커밋 해시만으로 재현이 불가능하다.
    dirty: bool = False
    workspace_path: str | None = None
    map_name: str | None = None
    # 판정은 이름이 아니라 지문으로 한다. 같은 이름의 다른 맵이 실제로 있다.
    map_hash: str | None = None

    # ---- manipulator (§4.4 — 버전의 의미가 다르다)
    policy_checkpoint_id: str | None = None
    policy_dataset_revision: str | None = None
    policy_loaded_at: datetime | None = None


class ConfigMismatchOut(BaseModel):
    """4대 중 무엇이 갈렸는가 (§9.2 의 '로봇 4대 SHA 동일' 강제).

    타입 안에서만 비교한다. OMX 의 코드 SHA 와 핑키의 코드 SHA 를 나란히
    놓는 것은 의미가 없다 — 다른 저장소의 다른 수명주기다(§4.4).
    """

    axis: Literal["commit", "map", "policy", "dataset"]
    robot_type: Literal["mobile", "manipulator"]
    # 값 → 그 값으로 도는 로봇들. 몇 대 몇으로 갈렸는지가 바로 보여야 한다.
    values: dict[str, list[str]]
    # 비교에서 빠진 로봇. 이 판정이 몇 대를 본 것인지 같이 말하지 않으면
    # "2대가 같다" 가 "4대가 같다" 로 읽힌다.
    unreported: list[str] = Field(default_factory=list)


class FleetConfigOut(BaseModel):
    robots: list[RobotConfigOut]
    # 갈린 축만 담는다. 비어 있으면 4대가 같은 형상으로 돌고 있다는 뜻이다.
    mismatches: list[ConfigMismatchOut] = Field(default_factory=list)


class ControlAuditOut(BaseModel):
    """제어 개입 한 건. actor 가 없으면 익명 기록이다."""

    audit_id: int
    occurred_at: datetime
    robot_id: str
    session_id: int | None = None
    action: str
    argument: str | None = None
    actor: str | None = None
    actor_source: str
    # 이 명령이 §1.1 판정 대상인지. 프론트가 목록을 다시 필터링하면서 백엔드와
    # 다른 집합을 쓰게 되는 것을 막는다.
    intervention: bool


class ControlAuditPage(BaseModel):
    total: int
    # actor 가 비어 있는 행. 이 비율이 오르면 클라이언트가 헤더를 빠뜨리고 있다.
    anonymous: int
    limit: int
    items: list[ControlAuditOut]
