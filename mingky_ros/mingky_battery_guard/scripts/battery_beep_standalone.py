#!/usr/bin/env python3
"""배터리가 40% 이하가 되면 부저를 3번 울리고 종료한다.

로봇(핑키) 위에서 실행할 것. 부저와 배터리 둘 다 로봇의 하드웨어라서
PC에서는 동작하지 않는다.

    python3 battery_beep.py

퍼센트 계산은 pinkylib 와 동일하다.
    percent = (V - 6.8) / (7.6 - 6.8) * 100
    40% = 7.12V
"""

import subprocess
import time

from pinkylib import Battery

THRESHOLD = 40.0                              # 이 % 이하면 울린다
BUZZER = '/home/pinky/ap/battery_buzzer.py'   # 기존 부저 스크립트를 그대로 사용
POLL_SEC = 10                                 # 몇 초마다 확인할지
CONFIRM = 2                                   # 연속 몇 번 낮아야 인정할지


def read_percent():
    """읽을 때만 I2C를 열고 바로 닫는다. battery_buzzer.py 와 같은 방식."""
    battery = None
    try:
        battery = Battery()
        return battery.battery_percentage()
    except Exception as e:
        print(f'배터리 읽기 실패: {e}', flush=True)
        return None
    finally:
        if battery is not None:
            try:
                battery.close()
            except Exception:
                pass


def beep():
    """기존 부저 스크립트의 danger 패턴(784Hz 3번)을 그대로 쓴다."""
    rc = subprocess.call(['python3', BUZZER, 'beep', 'danger'])
    if rc == 3:
        print('부저가 사용 중이라 울리지 못했습니다.', flush=True)
    elif rc != 0:
        print(f'부저 실행 실패 (종료코드 {rc})', flush=True)
    else:
        print('부저 3번 울림.', flush=True)


def main():
    print(f'배터리 감시 시작 ({THRESHOLD}% 이하면 부저 3번)', flush=True)
    low_count = 0

    while True:
        pct = read_percent()

        if pct is None:
            time.sleep(POLL_SEC)
            continue

        print(f'배터리 {pct:.1f}%', flush=True)

        if pct > THRESHOLD:
            low_count = 0
            time.sleep(POLL_SEC)
            continue

        # 모터가 돌면 전압이 순간 처져서 %가 확 떨어져 보인다.
        # (1% = 8mV 밖에 안 된다) 그래서 연속 2번일 때만 인정한다.
        low_count += 1
        if low_count < CONFIRM:
            print(f'  낮음 {low_count}/{CONFIRM} — 한 번 더 확인', flush=True)
            time.sleep(1)
            continue

        print(f'배터리 {pct:.1f}% — 기준 도달', flush=True)
        beep()
        print('끝.', flush=True)
        return


if __name__ == '__main__':
    main()
