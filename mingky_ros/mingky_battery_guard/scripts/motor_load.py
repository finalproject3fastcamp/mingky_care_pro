#!/usr/bin/env python3
"""배터리 전압을 떨어뜨리기 위해 모터에 부하를 준다 (테스트용).

제자리 회전이라 로봇이 이동하지 않는다. 책상 위에서도 비교적 안전하지만,
바퀴가 도니까 바닥에 내려놓거나 바퀴가 뜨게 잡고 실행하는 것을 권한다.

    python3 motor_load.py          # 기본 15초
    python3 motor_load.py 30       # 30초

Ctrl+C 로 즉시 멈춘다. 어떤 경우에도 끝날 때 모터를 정지시킨다.

전압은 battery/voltage 토픽에서 받는다. I2C 를 직접 열면 상시 도는
adc_reader 와 리더가 둘이 되어 양쪽 값이 함께 망가진다 (battery_source 참고).
그래서 워크스페이스를 source 한 뒤 실행해야 한다.

    source ~/mingky_care_pro/install/local_setup.bash

전압 표시는 발행 주기(5초)를 따르므로 모터가 도는 동안 띄엄띄엄 찍힌다.
"""

import sys
import time

from mingky_battery_guard.battery_guard import percent_from_voltage
from mingky_battery_guard.battery_source import VoltageSource
from pinkylib import Motor
import rclpy

# move() 는 -100~100(%) 를 받아 내부에서 ±300 으로 변환한다. 100이 최대.
SPEED = 100
DEFAULT_SEC = 15

# 모터에 명령을 다시 넣는 간격. 전압 발행 주기와는 무관하다.
MOVE_TICK_SEC = 0.5
# 첫 표본을 기다리는 시간. 발행 주기 5초에 여유를 둔다.
FIRST_SAMPLE_SEC = 8.0


def show(source, label):
    """최신 전압을 한 줄 찍는다. 값이 없으면 이유를 대신 찍는다."""
    volt = source.voltage()
    if volt is None:
        print(f'{label}: 값 없음 — {source.advice()}', flush=True)
        return
    print(f'{label}: {volt:.3f}V  {percent_from_voltage(volt):.2f}%', flush=True)


def spin_motors(source, duration):
    """제자리 회전을 duration 초 돌린다. 새 전압이 올 때마다 찍는다."""
    motor = Motor()
    try:
        motor.enable_motor()
        print(f'제자리 회전 {duration}초 시작 (Ctrl+C 로 중단)', flush=True)

        seen = source.samples
        end = time.time() + duration
        while time.time() < end:
            motor.move(SPEED, -SPEED)      # 좌우 반대 -> 제자리 회전
            # sleep 대신 spin. 자는 동안에도 전압을 받아야 한다.
            source.spin_for(MOVE_TICK_SEC)
            if source.samples > seen:
                seen = source.samples
                show(source, '  부하 중')

    except KeyboardInterrupt:
        print('\n중단됨', flush=True)
    finally:
        # 무슨 일이 있어도 모터를 멈춘다
        try:
            motor.stop()
        except Exception:
            pass
        try:
            motor.disable_motor()
        except Exception:
            pass
        try:
            motor.close()
        except Exception:
            pass
        print('모터 정지.', flush=True)


def run(source, duration):
    if not source.wait_for_sample(FIRST_SAMPLE_SEC):
        print(f'배터리 전압을 받지 못했습니다 — {source.advice()}', flush=True)
        print('전압 표시 없이 진행합니다.', flush=True)
    else:
        show(source, '시작 전 배터리')

    spin_motors(source, duration)

    # 멈춘 직후의 값이 필요하다. 들고 있던 값은 부하 중에 받은 것이다.
    source.wait_for_sample(FIRST_SAMPLE_SEC)
    show(source, '끝난 뒤 배터리')


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SEC

    rclpy.init()
    source = VoltageSource('motor_load_battery')
    try:
        run(source, duration)
    finally:
        source.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
