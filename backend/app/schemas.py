from datetime import date, datetime
from typing import Any, Literal
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
        "goto", "goto_pose", "start_session", "start_guidance", "set_mode",
        "localize", "system_start", "system_stop", "system_restart",
        "fire_alarm_reset", "set_navigation_speed",
    ]
    # goto 면 waypoint 이름, goto_pose 면 임시 좌표 JSON,
    # start_session 이면 patient_id, start_guidance 면 session_id,
    # set_mode 면 auto | manual | estop, set_navigation_speed 면 m/s 숫자,
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


class RobotOut(BaseModel):
    robot_id: str
    robot_type: str
    display_name: str
    domain_id: int | None = None
    is_active: bool
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
    # 생존 여부는 DB 가 아니라 백엔드 메모리에서 온다(app/heartbeat.py).
    # unknown 은 '한 번도 heartbeat 를 안 보낸 로봇' 이다. offline 과 다르다 —
    # OMX 는 관제 PC 에 USB 직결이라 잃을 네트워크 링크가 없다.
    last_seen_at: datetime | None = None
    link_state: Literal["online", "offline", "unknown"] = "unknown"
    system_state: Literal[
        "active", "activating", "deactivating", "inactive", "failed", "unknown"
    ] = "unknown"
    localization_active: bool = False
    fire_alarm_active: bool | None = None
    navigation_speed_mps: float | None = Field(default=None, ge=0.05, le=0.25)
    runtime_reported_at: datetime | None = None
    # 로봇이 보고한 자원·큐 상태. 전부 인메모리(robot_runtime)이고 DB 에
    # 저장하지 않는다. 구버전 게이트웨이는 안 보내므로 None 이 정상이다.
    cpu_total_pct: float | None = None
    queue_pending: int | None = None
    max_node_cpu_pct: float | None = None
    max_node_cpu_name: str | None = None
    inventory_hash: str | None = None


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
    # Nav2 controller_server에 실제로 반영된 직진 목표속도. 구버전 게이트웨이는
    # 이 값을 보내지 않으므로 None을 허용한다.
    navigation_speed_mps: float | None = Field(default=None, ge=0.05, le=0.25)

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
    observed_at: datetime | None = None
