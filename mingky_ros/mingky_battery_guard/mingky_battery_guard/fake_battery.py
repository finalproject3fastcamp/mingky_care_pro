#!/usr/bin/env python3
"""battery_guard 검증용: 가짜 배터리 값을 순서대로 흘려보내고 경보를 받아 적는다.

battery_guard 는 전압을 1차 소스로 쓰므로 여기서도 전압을 발행한다.
변환식은 pinkylib 와 반드시 같아야 한다. 다르면 guard 가 되돌려 계산한
퍼센트가 여기서 출력하는 값과 어긋나서, 테스트가 통과처럼 보이면서
실제로는 엉뚱한 지점에서 발동한다.
"""

from mingky_interfaces.msg import Event
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

EMPTY_V = 6.8   # 0%    (pinkylib/battery.py 와 동일)
FULL_V = 7.6    # 100%

# 기본 설정(발동 40% / 연속 3회 / 재무장 60% / 표본 5) 기준으로 짠 시나리오.
#   ① 표본 채우며 하강  ② 40% 밑에서 연속 3회 → 발동  ③ 재발동 안 함
#   ④ 60% 위로 충전 → 재무장  ⑤ 다시 떨어지면 또 발동
# 중앙값 필터(median_samples=3) 때문에 판단이 한 샘플 늦다는 점을 감안한 개수다.
SEQ = [
    70.0, 65.0, 60.0, 55.0, 50.0,      # ① 표본 채우기 (하강 중)
    38.0, 37.0, 36.0, 35.0,            # ② 연속 3회 → 여기서 발동
    34.0,                              # ③ 이미 울렸으므로 조용
    45.0, 55.0, 65.0, 70.0, 75.0,      # ④ 충전 → 60% 넘으며 재무장
    39.0, 38.0, 37.0, 36.0,            # ⑤ 다시 연속 3회 → 재발동
]
EXPECTED_ALERTS = 2


def voltage_for(pct):
    """퍼센트 -> 전압. percent_from_voltage() 의 역함수."""
    return EMPTY_V + (pct / 100.0) * (FULL_V - EMPTY_V)


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

        pct = SEQ[self.i]
        v = voltage_for(pct)
        self.vpub.publish(Float32(data=v))
        self.pub.publish(Float32(data=pct))
        print(f'[발행 {self.i + 1:2}] {pct:5.1f}% ({v:.3f}V)', flush=True)
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
