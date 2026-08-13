"""불꽃(fire)이 전방 카메라에 잡히면 로봇을 대피 지점으로 이동시킨다.

아직 실험 단계다 (2026-08-12 시작). 정식 기능이 되기 전까지 일부러 좁혀둔
범위가 있다:

  - `fire` 클래스만 본다. `smoke` 는 오탐(노을·조명 등)과 잘 안 갈라져서
    일단 빼뒀다 — 검증 데이터 기준 fire 클래스는 오탐 0건, smoke 클래스는
    비슷한 신뢰도 대역에서 오탐이 났다.
  - 대피 목적지 좌표는 입원병동-CT실 중앙(벽에서 0.175m, 지도 기준 계산값,
    아래 shelter_x/y/yaw 선언부 참고)이다. 실제 대피소 위치가 달라지면
    이 세 파라미터만 바꾸면 된다.
  - 관제 대시보드 이벤트(event_codes.yaml)는 아직 연동 안 했다. 로그만
    남긴다 — 공용 설정 파일은 이 실험이 실제로 되는 걸 확인한 뒤에 상의하고
    건드릴 것.

## 이 노드는 로봇(핑키) 위에서 돈다 -- YOLO 추론만 AI 노트북에 맡긴다

처음엔 이 노드 자체를 통째로 AI 노트북에서 돌리고, ROS2(DDS)로 핑키와
직접 통신하게 하려고 했다. 그런데 실제 와이파이(FASTCAMPUS 공용망)에서
테스트해보니 **UDP가 기기 간(피어투피어)으로 막혀있었다** -- ROS2 기본
디스커버리가 UDP 멀티캐스트/유니캐스트를 쓰는데, 순수 UDP 패킷을 직접
주고받는 테스트가 전부 타임아웃났다. 반면 TCP(SSH, HTTP)는 같은 두 기기
사이에서 문제없이 통했다 (직접 확인함: AI 노트북 → 핑키 SSH 핸드셰이크,
HTTP GET 둘 다 성공).

그래서 구조를 뒤집었다: **이 노드는 핑키 위에서 도는 평범한 ROS2 노드**로
남기고(카메라 구독도, Nav2 명령도 전부 로컬이라 문제 없음), **YOLO
추론 하나만 HTTP로 AI 노트북에 위탁**한다. `infer_server_url` 로 지정한
주소에 압축된 프레임(JPEG bytes)을 그대로 POST 하면, AI 노트북의 추론
서버(별도 스크립트, ROS2 아님, 그냥 Flask)가 GPU로 YOLO 돌리고
`{"fire": true/false}` 만 돌려준다. ROS2/DDS가 두 기기 사이를 오갈 필요가
아예 없어져서, 와이파이의 UDP 차단과 완전히 무관해진다.

카메라 프레임은 `qr_reader_node.py` 에 새로 추가한
`/front_camera/image_raw/compressed` (sensor_msgs/CompressedImage) 를
구독한다 -- 후방 카메라(`/rear_camera/image_raw`)가 이미 쓰던 것과 같은
패턴이다. 이 노드가 핑키 위에서 돌기 때문에 이 구독은 완전히 로컬이라
네트워크 문제와 무관하다.

## 왜 navigation_manager 의 ~/goto 를 안 쓰는가

처음엔 이미 있는 navigation_manager(엔지니어용 Waypoint 시험 주행)에
목적지 이름만 던지면 될 줄 알았는데, 코드를 보니 `_start_test()`가
`_clinical_active`(환자 안내 중)면 요청 자체를 거부하도록 돼 있다 —
엔지니어 시험 주행이 실제 환자 안내에 방해되면 안 된다는, 이 기능과는
정반대 우선순위다. 화재는 환자 안내 중이어도 최우선으로 끼어들어야 하므로,
mingky_localize 에서 이미 검증한 패턴을 그대로 가져왔다: 이 노드가 독자적인
NavigateToPose 액션 클라이언트를 갖고, 이동 전에 CancelGoal 로 기존 목표를
(누가 보낸 것이든) 강제로 취소한 뒤 대피 목표를 보낸다.
"""

import collections
import math
import threading
import time

import rclpy
import requests
from action_msgs.srv import CancelGoal
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data,
)
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, Trigger

MODE_TOPIC = "/mode"
CANCEL_NAV_SERVICE = "/navigate_to_pose/_action/cancel_goal"
TEST_TRIGGER_SERVICE = "fire_evac/trigger_test"
GUIDE_EVAC_SERVICE = "/guide_manager/fire_evacuation"
# lcd_status_node(mingky_lcd_status)가 이 토픽을 구독해서 True 인 동안
# GuideState 화면 대신 "긴급 상황" 화면으로 강제 전환한다. GuideState 는
# guide_manager 만 발행한다는 규칙이 있어서(GuideState.msg 참고) 새 상태를
# 얹지 않고 별도 토픽으로 뺐다.
EVAC_ACTIVE_TOPIC = "/fire_evac/active"
AUTO_MODE = "auto"


def _latched(depth: int = 1) -> QoSProfile:
    return QoSProfile(
        depth=depth,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        reliability=ReliabilityPolicy.RELIABLE,
    )


def _yaw_to_quat(yaw: float) -> tuple[float, float]:
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def _detections_confirmed(results, required: int) -> bool:
    """Return whether enough frames in the rolling window detected fire."""
    return sum(bool(result) for result in results) >= required


class FireEvacNode(Node):

    def __init__(self, **kwargs):
        super().__init__("fire_evac_node", **kwargs)

        self.declare_parameter("robot_id", "pinky-01")
        # qr_reader_node 가 발행하는 전방 카메라 압축 이미지 토픽.
        self.declare_parameter("image_topic", "/front_camera/image_raw/compressed")
        # 이 토픽에 새 프레임이 이 시간(초)보다 오래 안 오면 카메라/네트워크가
        # 끊겼다고 보고 감지를 쉰다 (오래된 프레임으로 계속 판단하지 않으려는
        # 것 -- mingky_localize 에서 라이다 신선도 체크한 것과 같은 이유).
        self.declare_parameter("frame_max_age_sec", 2.0)
        # AI 노트북에서 도는 추론 서버 주소 (mingky_fire_evac/infer_server.py,
        # ROS2 아님, 그냥 Flask). 필수값이라 기본값을 비워두고, 없으면 바로
        # 에러를 낸다 (qr_reader 의 robot_id 필수 파라미터와 같은 패턴).
        self.declare_parameter("infer_server_url", "")
        # 추론 요청 자체가 응답 없이 오래 걸리면(네트워크 끊김, 서버 다운
        # 등) 감지 루프가 거기서 계속 멈춰있으면 안 되므로 타임아웃을 짧게
        # 둔다. 실패하면 이번 프레임은 그냥 건너뛴다 (아래 _detect_fire 참고).
        self.declare_parameter("infer_timeout_sec", 2.0)
        self.declare_parameter("nav_result_timeout_sec", 120.0)
        self.declare_parameter("conf_threshold", 0.3)
        # 최근 window_size 프레임 중 required_detections 프레임 이상에서
        # fire 가 감지돼야 확정한다. 순간적인 오탐(반사광 한 프레임 등)을
        # 거르기 위한 것 -- 저번에 실제 사진으로 검증했을 때 이 정도 비율이면
        # 랜덤노이즈/노을 같은 극단적 오탐 케이스가 아닌 이상 통과 안 한다.
        self.declare_parameter("window_size", 7)
        self.declare_parameter("required_detections", 5)
        # 입원병동(ward_goal)과 CT실(ct_room_goal) 중앙점 (2026-08-12).
        # ward_goal(2.729600, 1.262527) 과 ct_room_goal(2.586657, 1.720020)
        # 의 평균이다. 지도 이미지(yun_map_highres_clean.pgm)로 사방 벽까지
        # 거리를 직접 재보니 이 지점이 이미 동쪽 벽에서 0.175m 거리라 --
        # "벽에 가깝게" 요구사항을 이 좌표 자체가 자연스럽게 만족한다
        # (프로젝트 벽 최소 거리 기준 0.15m 보다 여유 있게 안전).
        #
        # 로봇을 이 자리에 세우고 auto_localize 재탐색으로 실측한 값
        # (/amcl_pose, 2026-08-13).
        self.declare_parameter("shelter_x", 2.643918)
        self.declare_parameter("shelter_y", 1.435542)
        self.declare_parameter("shelter_yaw", 0.422299)

        get = self.get_parameter
        self.robot_id = str(get("robot_id").value)
        self.image_topic = str(get("image_topic").value)
        self.frame_max_age_sec = float(get("frame_max_age_sec").value)
        self.infer_server_url = str(get("infer_server_url").value)
        if not self.infer_server_url:
            raise RuntimeError(
                "infer_server_url 파라미터가 필요합니다 (AI 노트북의 추론 서버 주소).")
        self.infer_timeout_sec = float(get("infer_timeout_sec").value)
        self.nav_result_timeout_sec = max(
            1.0, float(get("nav_result_timeout_sec").value))
        self.conf_threshold = float(get("conf_threshold").value)
        self.window_size = int(get("window_size").value)
        self.required_detections = int(get("required_detections").value)
        if self.window_size <= 0:
            raise ValueError("window_size는 1 이상이어야 합니다.")
        if not 1 <= self.required_detections <= self.window_size:
            raise ValueError(
                "required_detections는 1 이상 window_size 이하여야 합니다.")
        self.shelter = (
            float(get("shelter_x").value),
            float(get("shelter_y").value),
            float(get("shelter_yaw").value),
        )

        self.mode = None
        self._evacuating = False
        self._recent: collections.deque = collections.deque(maxlen=self.window_size)
        self._stop = False
        self._latest_jpeg = None        # bytes | None (원본 압축 프레임, 재인코딩 없이 그대로 전송)
        self._latest_frame_at = None    # time.monotonic() | None
        self._last_processed_at = None  # 마지막으로 추론에 쓴 프레임 시각

        self.create_subscription(String, MODE_TOPIC, self._on_mode, _latched())
        # 콜백은 디코드만 하고 바로 리턴한다. YOLO 추론(느림)은 여기서 하면
        # 안 된다 -- rclpy.spin(self) 을 도는 메인 스레드가 막혀서 다른
        # 콜백(모드 변경 등)도 다 같이 밀린다. 실제 추론은 별도 스레드
        # (_watch_loop)가 이 콜백이 저장해둔 최신 프레임을 가져다 쓴다.
        self.create_subscription(
            CompressedImage, self.image_topic, self._on_image,
            qos_profile_sensor_data)
        self.evac_active_pub = self.create_publisher(Bool, EVAC_ACTIVE_TOPIC, _latched())
        self.evac_active_pub.publish(Bool(data=False))
        self.cancel_nav_client = self.create_client(CancelGoal, CANCEL_NAV_SERVICE)
        self.guide_evac_client = self.create_client(SetBool, GUIDE_EVAC_SERVICE)
        self.nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        # CV 파이프라인 없이도 "감지됐다고 치고" 이동 로직만 따로 테스트할 수
        # 있게 만든 서비스. 카메라 앞에 매번 불을 갖다 댈 필요 없이 Nav2
        # 취소/목표전송 부분만 검증할 때 쓴다.
        self.create_service(Trigger, TEST_TRIGGER_SERVICE, self._on_test_trigger)

        self.get_logger().info(
            f"추론 서버: {self.infer_server_url}. 감지 스레드 시작.")
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def destroy_node(self):
        self._stop = True
        super().destroy_node()

    # ------------------------------------------------------------ 구독 콜백

    def _on_mode(self, msg: String):
        self.mode = msg.data

    def _on_image(self, msg: CompressedImage):
        # 디코드하지 않고 압축된 바이트 그대로 들고 있는다 -- 이 프레임을
        # 다시 볼 사람은 우리가 아니라 AI 노트북(HTTP로 그대로 전달)이라,
        # 여기서 디코드했다가 다시 인코드하는 건 낭비다.
        self._latest_jpeg = bytes(msg.data)
        self._latest_frame_at = time.monotonic()

    def _set_evacuating(self, value: bool):
        """_evacuating 플래그를 바꾸고, 그때마다 LCD 가 볼 토픽도 같이 갱신한다.

        둘이 따로 놀면(플래그만 바뀌고 토픽을 깜빡하면) 실제로는 대피 중인데
        화면은 평소 안내 화면 그대로인 상황이 생긴다. 그래서 이 메서드
        하나로만 바꾸게 강제한다.
        """
        self._evacuating = value
        self.evac_active_pub.publish(Bool(data=value))

    def _on_test_trigger(self, request, response):
        if self._evacuating:
            response.success = False
            response.message = "이미 대피 이동 중입니다."
            return response
        self.get_logger().warn("수동 테스트 트리거 — 감지 없이 바로 대피 이동 시작합니다.")
        self._set_evacuating(True)
        threading.Thread(target=self._start_evacuation, daemon=True).start()
        response.success = True
        response.message = "대피 이동을 시작했습니다."
        return response

    # ------------------------------------------------------------ 감지 루프
    #
    # HTTP 요청(AI 노트북 추론 서버 호출)은 블로킹이고 네트워크 상황에 따라
    # 늦어질 수 있어서 rclpy.spin(self) 을 도는 메인 스레드(구독 콜백을
    # 처리하는 그 스레드)에서 직접 하면 안 된다 (mingky_localize 의
    # _run_sequence 와 같은 이유). 그래서 별도 스레드에서, "_on_image 콜백이
    # 마지막으로 저장해둔 프레임"을 폴링해서 처리한다.

    def _watch_loop(self):
        while rclpy.ok() and not self._stop:
            time.sleep(0.1)
            if self.mode != AUTO_MODE or self._evacuating:
                # 수동 조작 중이거나 이미 대피 이동 중이면 새로 트리거할
                # 필요가 없다. 창(window)에 쌓이는 걸 막으려고 비운다.
                self._recent.clear()
                continue

            jpeg, frame_at = self._latest_jpeg, self._latest_frame_at
            if jpeg is None or frame_at is None:
                continue
            if time.monotonic() - frame_at > self.frame_max_age_sec:
                # qr_reader 쪽 발행이 끊겼거나(구독자 수 0으로 판단해 꺼짐,
                # 노드가 죽음 등) 네트워크가 끊긴 상태. 오래된 프레임으로
                # 계속 판단하지 않는다.
                continue
            if frame_at == self._last_processed_at:
                continue  # 아직 새 프레임이 안 왔다
            self._last_processed_at = frame_at

            self._recent.append(self._detect_fire(jpeg))
            if _detections_confirmed(
                    self._recent, self.required_detections):
                self.get_logger().warn(
                    f"불꽃 반복 감지 ({sum(self._recent)}/{self.window_size}"
                    "프레임) — 대피 이동을 시작합니다.")
                self._set_evacuating(True)
                threading.Thread(target=self._start_evacuation, daemon=True).start()

    def _detect_fire(self, jpeg_bytes: bytes) -> bool:
        """AI 노트북의 추론 서버에 프레임을 보내고 fire 감지 여부를 받는다.

        네트워크/서버 문제로 요청이 실패해도 이 노드를 죽이면 안 된다 --
        이번 프레임만 "미감지"로 취급하고 다음 프레임에서 다시 시도한다
        (최근 프레임 확인 로직이 어차피 한두 번 실패는 감당하게 돼있다).
        """
        try:
            resp = requests.post(
                self.infer_server_url,
                files={"image": ("frame.jpg", jpeg_bytes, "image/jpeg")},
                data={"conf": str(self.conf_threshold)},
                timeout=self.infer_timeout_sec,
            )
            resp.raise_for_status()
            return bool(resp.json().get("fire", False))
        except requests.RequestException as exc:
            self.get_logger().warn(f"추론 서버 호출 실패: {exc}", throttle_duration_sec=5.0)
            return False

    # ------------------------------------------------------------ 대피 이동

    def _start_evacuation(self):
        safe_to_clear = True
        try:
            self._set_guide_evacuation(True)
            self._cancel_active_nav_goal()

            x, y, yaw = self.shelter
            goal = NavigateToPose.Goal()
            goal.pose.header.frame_id = "map"
            goal.pose.header.stamp = self.get_clock().now().to_msg()
            goal.pose.pose.position.x = x
            goal.pose.pose.position.y = y
            qz, qw = _yaw_to_quat(yaw)
            goal.pose.pose.orientation.z = qz
            goal.pose.pose.orientation.w = qw

            if not self.nav_client.wait_for_server(timeout_sec=5.0):
                self.get_logger().error("navigate_to_pose 액션 서버가 없습니다.")
                return

            send_future = self.nav_client.send_goal_async(goal)
            goal_handle = self._wait_for_future(send_future, timeout_sec=5.0)
            if goal_handle is None or not goal_handle.accepted:
                self.get_logger().error("대피 목표가 거부됐습니다.")
                return

            self.get_logger().info(f"대피 목표 전송됨: x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}")
            result_future = goal_handle.get_result_async()
            result = self._wait_for_future(
                result_future, timeout_sec=self.nav_result_timeout_sec)
            if result is None:
                self.get_logger().error(
                    "대피 이동 결과 타임아웃 — 대피 목표를 취소합니다.")
                cancel_future = goal_handle.cancel_goal_async()
                cancel_response = self._wait_for_future(cancel_future, timeout_sec=5.0)
                if cancel_response is None:
                    # 실제 로봇이 계속 움직일 가능성이 있으므로 평상 상태로
                    # 돌아가지 않는다. 운영자가 Nav2/비상정지를 확인해야 한다.
                    safe_to_clear = False
                    self.get_logger().fatal(
                        "대피 목표 취소 응답이 없습니다. 대피 상태를 유지합니다.")
                    return
                terminal = self._wait_for_future(result_future, timeout_sec=5.0)
                if terminal is None:
                    safe_to_clear = False
                    self.get_logger().fatal(
                        "대피 목표 취소 후 종료 결과가 없습니다. 대피 상태를 유지합니다.")
                    return
                self.get_logger().warn(
                    f"대피 목표 취소 완료. status={terminal.status}")
            else:
                self.get_logger().info(f"대피 이동 종료. status={result.status}")
        finally:
            if safe_to_clear:
                self._set_guide_evacuation(False)
                self._set_evacuating(False)
                self._recent.clear()

    def _set_guide_evacuation(self, active: bool) -> bool:
        """Guide Manager가 기존 안내 상태를 정리한 뒤 대피를 시작하게 한다."""
        if not self.guide_evac_client.wait_for_service(timeout_sec=3.0):
            self.get_logger().error(
                "Guide Manager 화재 대피 서비스가 없습니다. "
                "안내 상태 정리 없이 대피를 계속합니다.")
            return False
        future = self.guide_evac_client.call_async(SetBool.Request(data=active))
        response = self._wait_for_future(future, timeout_sec=3.0)
        if response is None or not response.success:
            self.get_logger().error(
                "Guide Manager 화재 대피 상태 전환에 실패했습니다. "
                "대피를 우선해 계속 진행합니다.")
            return False
        return True

    def _cancel_active_nav_goal(self):
        """Nav2 의 지금 목표를 강제로 전부 취소한다 (mingky_localize 와 동일 패턴).

        goal_id/시각이 둘 다 비어있는 CancelGoal 요청은 "모든 목표 취소" 로
        정의돼 있어서, 지금 목표를 누가 보냈는지(guide_manager 든
        navigation_manager 든) 몰라도 이거 하나로 정리된다.
        """
        if not self.cancel_nav_client.service_is_ready():
            return
        future = self.cancel_nav_client.call_async(CancelGoal.Request())
        self._wait_for_future(future, timeout_sec=2.0)

    @staticmethod
    def _wait_for_future(future, timeout_sec: float):
        """call_async/send_goal_async 의 결과를 기다린다.

        spin_until_future_complete 는 이미 spin 중인 노드 안에서 부르면
        충돌한다 (mingky_localize 에서 겪은 문제와 동일). 메인 스레드의
        rclpy.spin(self) 가 계속 콜백을 처리해주고 있으니, 워커 스레드에서는
        그냥 time.sleep 으로 완료를 기다리기만 하면 된다.
        """
        deadline = time.monotonic() + timeout_sec
        while not future.done() and rclpy.ok() and time.monotonic() < deadline:
            time.sleep(0.05)
        return future.result() if future.done() else None


def main():
    rclpy.init()
    node = FireEvacNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
