#!/usr/bin/env python3
"""배터리가 기준 전압 이하로 내려가면 부저를 3번 울리고 종료한다.

로봇(핑키) 위에서 실행할 것. 부저가 로봇의 하드웨어라서 PC에서는 동작하지
않는다.

    source ~/mingky_care_pro/install/local_setup.bash
    python3 battery_beep_standalone.py

'standalone' 은 launch 그래프 밖에서 손으로 돌린다는 뜻이다. battery_guard
노드와 파라미터를 공유하지 않으므로, 기준을 바꿀 때는 양쪽을 같이 봐야 한다.
(mingky_battery_guard/README.md 참고)

전압은 battery/voltage 토픽에서 받는다. 예전에는 pinkylib 으로 I2C 를 직접
열었는데, 지금은 adc_reader 가 그 장치의 단독 소유자라 같이 열면 양쪽 값이
함께 망가진다 (battery_source 참고). 그래서 ROS 없이는 돌지 않는다.

판정은 전압으로 한다. 퍼센트는 화면에 곁들이는 표시용이다.
퍼센트는 6.8V 아래와 7.6V 위를 전부 0%/100% 로 뭉개기 때문에, 정작
위험한 구간에서 값이 구분되지 않는다.

    percent = (V - 6.8) / (7.6 - 6.8) * 100      (pinkylib/battery.py)
"""

from collections import deque
import subprocess

from mingky_battery_guard.battery_guard import percent_from_voltage
from mingky_battery_guard.battery_source import VoltageSource
import rclpy

LOW_VOLTAGE = 7.12                            # 이 전압 이하를 '낮음'으로 센다
BUZZER = '/home/pinky/ap/battery_buzzer.py'   # 기존 부저 스크립트를 그대로 사용
POLL_SEC = 10                                 # 몇 초마다 확인할지
WINDOW = 6                                    # 최근 몇 회를 볼지 (= 60초)
CONFIRM = 3                                   # 그 중 몇 회 낮아야 인정할지

# 첫 표본을 기다리는 시간. 발행 주기 5초에 여유를 둔다.
FIRST_SAMPLE_SEC = 8.0


def beep():
    """기존 부저 스크립트의 danger 패턴(784Hz 3번)을 그대로 쓴다."""
    rc = subprocess.call(['python3', BUZZER, 'beep', 'danger'])
    if rc == 3:
        print('부저가 사용 중이라 울리지 못했습니다.', flush=True)
    elif rc != 0:
        print(f'부저 실행 실패 (종료코드 {rc})', flush=True)
    else:
        print('부저 3번 울림.', flush=True)


def watch(source):
    """저전압이 창 안에서 CONFIRM 회 잡히면 부저를 울리고 끝낸다."""
    print(f'배터리 감시 시작 ({LOW_VOLTAGE:.2f}V 이하가 '
          f'최근 {WINDOW}회 중 {CONFIRM}회면 부저 3번)', flush=True)

    if not source.wait_for_sample(FIRST_SAMPLE_SEC):
        print(f'전압을 받지 못했습니다 — {source.advice()}', flush=True)
        print('계속 기다립니다.', flush=True)

    # 저전압 여부를 '연속'이 아니라 '최근 창 안의 횟수'로 센다.
    # 연속 카운터는 기준치 위를 한 번만 봐도 0 으로 초기화되는데, 방전이
    # 진행된 배터리는 주행하면 처지고 멈추면 회복하기를 반복하므로 연속이
    # 절대 쌓이지 않는다. battery_guard 가 같은 이유로 창 방식을 쓴다.
    low_window = deque(maxlen=WINDOW)

    while rclpy.ok():
        volt = source.voltage()

        # 값이 없으면 세지 않는다. 발행이 끊긴 걸 '높음'으로도 '낮음'으로도
        # 치면 안 된다. 마지막 값을 계속 쓰는 것도 같은 이유로 안 된다.
        if volt is None:
            print(f'전압 없음 — {source.advice()}', flush=True)
        else:
            low_window.append(volt <= LOW_VOLTAGE)
            low_count = sum(low_window)
            print(f'배터리 {volt:.3f}V ({percent_from_voltage(volt):.0f}%) — '
                  f'낮음 {low_count}/{CONFIRM} (최근 {len(low_window)}회 중)',
                  flush=True)

            if low_count >= CONFIRM:
                print(f'배터리 {volt:.3f}V — 기준 도달', flush=True)
                beep()
                print('끝.', flush=True)
                return

        # 확인 간격을 줄이면 모터 부하로 처진 한 번의 강하를 여러 번으로
        # 세게 된다. 표본은 항상 같은 간격으로 뽑는다. sleep 대신 spin 인
        # 이유는 기다리는 동안에도 메시지를 받아야 해서다.
        source.spin_for(POLL_SEC)


def main():
    rclpy.init()
    source = VoltageSource('battery_beep_standalone')
    try:
        watch(source)
    except KeyboardInterrupt:
        print('\n중단됨', flush=True)
    finally:
        source.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
