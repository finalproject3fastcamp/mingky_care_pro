#!/usr/bin/env python3
"""battery_guard 검증용: 가짜 배터리 값을 순서대로 흘려보내고 경보를 받아 적는다.

battery_guard 는 전압으로 판정하므로 시나리오도 전압으로 쓴다.
퍼센트 눈금으로 시나리오를 쓰고 전압으로 변환해 발행하면, 임계값이 전압인
지금은 검증하는 대상이 눈금과 어긋난 채로 통과해 버린다. 통과가 아무것도
보증하지 않는 상태가 되므로 눈금을 판정 도메인과 일치시킨다.
"""

from mingky_interfaces.msg import Event
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

EMPTY_V = 6.8   # 퍼센트 0% 지점 (pinkylib/battery.py 와 동일)
FULL_V = 7.6    # 퍼센트 100% 지점

# 기본 설정(발동 7.12V / 창 6회 중 3회 / 재무장 7.28V / 표본 5) 기준 시나리오.
#   ① 표본 채우며 하강  ② 7.12V 밑에서 3회 → 발동  ③ 재발동 안 함
#   ④ 7.28V 위로 충전 → 재무장  ⑤ 다시 떨어지면 또 발동
# 중앙값 필터(median_samples=3) 때문에 판단이 한 샘플 늦다는 점을 감안한 개수다.
SEQ = [
    7.36, 7.32, 7.28, 7.24, 7.20,      # ① 표본 채우기 (하강 중)
    7.104, 7.096, 7.088, 7.08,         # ② 3회 낮음 → 여기서 발동
    7.072,                             # ③ 이미 울렸으므로 조용
    7.16, 7.24, 7.32, 7.36, 7.40,      # ④ 충전 → 7.28V 넘으며 재무장
    7.112, 7.104, 7.096, 7.088,        # ⑤ 다시 3회 낮음 → 재발동
]
EXPECTED_ALERTS = 2


def percent_for(volt):
    """전압 -> 퍼센트. 화면에 곁들여 찍기 위한 표시용이다."""
    return max(0.0, min(100.0, (volt - EMPTY_V) / (FULL_V - EMPTY_V) * 100.0))


class Fake(Node):

    def __init__(self):
        super().__init__('fake_battery')
        self.pub = self.create_publisher(Float32, 'battery/percent', 10)
        self.vpub = self.create_publisher(Float32, 'battery/voltage', 10)

        # 게이트웨이와 같은 자리에서 이벤트를 엿본다.
        self.create_subscription(Event, '/events', self.on_event, 100)

        self.i = 0
        self.alerts = []
        self.create_timer(1.0, self.tick)

    def on_event(self, msg):
        """robot.battery_low 만 센다. 복귀 관련 이벤트는 use_nav2=false 면 안 온다."""
        if msg.event_code != 'robot.battery_low':
            return
        self.alerts.append(msg.payload)
        print(f'  >>> [경보 수신] {msg.event_code} {msg.payload}', flush=True)

    def tick(self):
        if self.i >= len(SEQ):
            ok = len(self.alerts) == EXPECTED_ALERTS
            print('\n===== 결과 =====', flush=True)
            print(f'경보 {len(self.alerts)}회 (예상 {EXPECTED_ALERTS}회) '
                  f'-> {"통과" if ok else "실패"}', flush=True)
            for a in self.alerts:
                print(f'  {a}', flush=True)
            raise SystemExit(0 if ok else 1)

        v = SEQ[self.i]
        pct = percent_for(v)
        self.vpub.publish(Float32(data=v))
        self.pub.publish(Float32(data=pct))
        print(f'[발행 {self.i + 1:2}] {v:.3f}V ({pct:5.1f}%)', flush=True)
        self.i += 1


def main(args=None):
    rclpy.init(args=args)
    node = Fake()
    code = 0
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit) as e:
        code = getattr(e, 'code', 0) or 0
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return code


if __name__ == '__main__':
    main()
