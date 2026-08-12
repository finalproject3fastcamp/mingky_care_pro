#!/usr/bin/env python3
"""battery/voltage 를 구독해 최신 전압을 들고 있는다.

로봇에서 손으로 돌리는 스크립트들이 쓴다. I2C 를 직접 여는 대신 이걸 쓴다.

왜 I2C 를 직접 열면 안 되나
    ADC(0x08)는 '커맨드로 채널 선택 -> 2바이트 읽기' 구조이고, 선택된 채널을
    하나만 기억한다. write 와 read 사이에 다른 프로세스가 채널을 바꾸면 엉뚱한
    채널 값이 배터리 전압으로 들어온다. 통신은 성공하고 예외도 나지 않아서,
    값만 보고는 오염을 알아챌 방법이 없다.

    운영에서는 mingky_sensors 의 adc_reader 가 mingky-battery-pub.service 로
    상시 돌며 이 장치의 단독 소유자 노릇을 한다. 그 옆에서 pinkylib.Battery 를
    열면 리더가 둘이 되어 양쪽 값이 함께 망가진다. 실측으로 중앙값이 0.397V
    낮아지고 산포가 37mV -> 3.22V 로 벌어졌다 (mingky_sensors/README.md).

    adc_reader 는 flock 으로 자기 임계 구역을 지키지만, pinkylib 은 잠금을
    쓰지 않아 그 잠금과 맞물리지 않는다. 그래서 잠금을 흉내 내는 대신
    토픽을 구독한다.

값이 없는 상태를 값으로 취급하지 않는다
    구독은 마지막 값을 계속 들고 있으므로, 발행자가 죽어도 변수에는 옛날 값이
    남는다. 그대로 쓰면 멈춘 값으로 판정하게 되는데, 이건 읽기 실패보다
    위험하다. 실패는 눈에 띄지만 멈춘 값은 정상으로 보이기 때문이다.
    그래서 stale_after_sec 이 지난 값은 없는 것으로 본다.
"""

import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

TOPIC = 'battery/voltage'

# adc_reader 의 발행 주기는 5초다 (mingky_sensors 의 battery_period_sec).
# 세 번 걸러도 안 오면 발행하는 쪽이 죽은 것으로 본다.
STALE_AFTER_SEC = 16.0


class VoltageSource(Node):
    """battery/voltage 구독자. 최신 전압 하나만 들고 있는다."""

    def __init__(self, node_name, topic=TOPIC,
                 stale_after_sec=STALE_AFTER_SEC, **kwargs):
        super().__init__(node_name, **kwargs)
        self.stale_after = float(stale_after_sec)
        self.samples = 0            # 지금까지 받은 표본 수
        self._volt = None
        self._at = 0.0
        self.sub = self.create_subscription(
            Float32, topic, self.on_voltage, 10)

    def on_voltage(self, msg):
        self._volt = float(msg.data)
        self._at = time.monotonic()
        self.samples += 1

    # ------------------------------------------------------------------

    def voltage(self):
        """최신 전압. 아직 못 받았거나 너무 오래된 값이면 None."""
        if self._volt is None:
            return None
        if time.monotonic() - self._at > self.stale_after:
            return None
        return self._volt

    def publisher_count(self):
        """battery/voltage 를 발행하는 노드 수."""
        return self.sub.get_publisher_count()

    def spin_for(self, seconds):
        """그 시간만큼 콜백을 처리한다. time.sleep 자리에 쓴다.

        자는 동안에도 메시지를 받아야 해서 sleep 을 그대로 둘 수 없다.
        """
        end = time.monotonic() + max(0.0, float(seconds))
        while rclpy.ok():
            left = end - time.monotonic()
            if left <= 0.0:
                break
            rclpy.spin_once(self, timeout_sec=left)

    def wait_for_sample(self, timeout_sec):
        """새 표본이 하나 올 때까지 기다린다. 받았으면 True.

        들고 있던 값이 아니라 '새로' 온 것을 기다린다. 모터를 멈춘 직후처럼
        그 시점의 전압이 필요한 자리가 있어서다.
        """
        seen = self.samples
        end = time.monotonic() + float(timeout_sec)
        while rclpy.ok() and self.samples == seen:
            left = end - time.monotonic()
            if left <= 0.0:
                break
            rclpy.spin_once(self, timeout_sec=left)
        return self.samples > seen

    def advice(self):
        """값이 안 올 때 사람에게 보여줄 안내."""
        n = self.publisher_count()
        if n == 0:
            return ('battery/voltage 를 발행하는 노드가 없습니다. '
                    'systemctl status mingky-battery-pub 로 확인하세요.')
        if n > 1:
            return (f'battery/voltage 발행자가 {n}개입니다. I2C 를 동시에 읽으면 '
                    '전압이 조용히 낮아집니다. 하나만 남기세요 '
                    '(주행 런치는 start_battery_publisher:=false).')
        return ('발행자는 하나인데 값이 오지 않습니다. '
                'ros2 topic echo /battery/voltage 로 확인하세요.')
