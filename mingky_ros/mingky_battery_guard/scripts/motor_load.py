#!/usr/bin/env python3
"""배터리 전압을 떨어뜨리기 위해 모터에 부하를 준다 (테스트용).

제자리 회전이라 로봇이 이동하지 않는다. 책상 위에서도 비교적 안전하지만,
바퀴가 도니까 바닥에 내려놓거나 바퀴가 뜨게 잡고 실행하는 것을 권한다.

    python3 motor_load.py          # 기본 15초
    python3 motor_load.py 30       # 30초

Ctrl+C 로 즉시 멈춘다. 어떤 경우에도 끝날 때 모터를 정지시킨다.
"""

import sys
import time

from pinkylib import Battery, Motor

# move() 는 -100~100(%) 를 받아 내부에서 ±300 으로 변환한다. 100이 최대.
SPEED = 100
DEFAULT_SEC = 15


def read_battery():
    b = None
    try:
        b = Battery()
        return b.get_voltage(), b.battery_percentage()
    except Exception:
        return None, None
    finally:
        if b is not None:
            try:
                b.close()
            except Exception:
                pass


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SEC

    v, p = read_battery()
    if v is not None:
        print(f'시작 전 배터리: {v:.3f}V  {p:.2f}%', flush=True)

    motor = Motor()
    try:
        motor.enable_motor()
        print(f'제자리 회전 {duration}초 시작 (Ctrl+C 로 중단)', flush=True)

        end = time.time() + duration
        while time.time() < end:
            motor.move(SPEED, -SPEED)      # 좌우 반대 -> 제자리 회전
            time.sleep(0.5)
            v, p = read_battery()
            if v is not None:
                print(f'  {v:.3f}V  {p:.2f}%', flush=True)

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

    v, p = read_battery()
    if v is not None:
        print(f'끝난 뒤 배터리: {v:.3f}V  {p:.2f}%', flush=True)


if __name__ == '__main__':
    main()
