#!/usr/bin/env python
"""Dynamixel 서보 온도·전압을 JSON 한 줄로 찍는다 — 리포터가 subprocess 로 부른다.

## 왜 별도 프로세스인가

리포터(omx/report/reporter.py)는 관제 백엔드·조제 파이썬과 같은 인터프리터에서
임포트될 수 있어야 해서 표준 라이브러리만 쓴다. dynamixel_sdk 는 il venv 에만
있다 — 그래서 트레이 계수(omx/web/count_tray.py)와 같은 방식을 쓴다. **il venv
파이썬으로 이 스크립트를 띄우고 stdout 의 JSON 한 줄만 읽는다.**

## 계약

마지막 줄에 `SERVO_JSON ` 로 시작하는 한 줄을 찍는다. 앞줄에 로그가 섞여도
리포터가 이 접두어로 골라낸다 (count_tray.py 의 TRAY_JSON 과 같은 철학).

    SERVO_JSON {"servos": [{"joint": "shoulder_lift", "temp_c": 41.0, ...}, ...]}
    SERVO_JSON {"오류": "포트를 열지 못했습니다: /dev/ttyACM0"}

payload 는 backend/app/schemas.py 의 `ServoReadingIn` 과 일치한다. 조인트 하나가
부분적으로 실패해도 읽은 것만 담는다 — 한 레지스터 때문에 표본 전체를 버리지
않는다(ServoReadingIn 의 전 필드 optional 규약).

## 무엇을 읽나

X-시리즈(XL330·XL430·XM430)에서 주소가 동일하고 확실한 레지스터만 읽는다.

    Hardware Error Status   70  (1B)          하드웨어 에러 비트
    Present Input Voltage  144  (2B, 0.1V)    입력 전압
    Present Temperature    146  (1B, ℃)       현재 온도

전류(126)는 **읽지 않는다.** XL330·XM430 에서는 Present Current 지만
XL430-W250 에서는 같은 주소가 Present Load 다. OMX 팔은 두 모델이 섞여 있어
(11~13 이 XL430, 14~16 이 XL330), 126 을 일괄로 current 로 올리면 절반이
전류가 아닌 부하값이 된다. 틀린 전류를 올리는 것보다 비우는 편이 낫다
(ServoReadingIn.current_ma 는 None 을 허용한다).

## 조인트 ↔ ID 맵

omx/src/omx_f_keyboard_teleop.py 의 모터 정의를 그대로 따른다. 여기서 지어내지
않는다.

    shoulder_pan  11 (xl430)   wrist_flex 14 (xl330)
    shoulder_lift 12 (xl430)   wrist_roll 15 (xl330)
    elbow_flex    13 (xl430)   gripper    16 (xl330)

## 사용

    ~/venv/il/bin/python read_servos.py --port /dev/ttyACM0 --baud 1000000
"""

from __future__ import annotations

import argparse
import json
import os
import sys

MARKER = "SERVO_JSON"

# 조인트 ↔ Dynamixel ID (omx_f_keyboard_teleop.py 의 모터 정의).
_JOINTS: list[tuple[str, int]] = [
    ("shoulder_pan", 11),
    ("shoulder_lift", 12),
    ("elbow_flex", 13),
    ("wrist_flex", 14),
    ("wrist_roll", 15),
    ("gripper", 16),
]

# X-시리즈 Protocol 2.0 컨트롤 테이블 (주소가 모델 간 동일한 것만).
_ADDR_HW_ERROR = 70          # 1 byte
_ADDR_VOLTAGE = 144          # 2 byte, 단위 0.1V
_ADDR_TEMPERATURE = 146      # 1 byte, 단위 ℃


def _emit(payload: dict) -> None:
    print(f"{MARKER} {json.dumps(payload, ensure_ascii=False)}", flush=True)


def _read(port, packet, dxl_id: int) -> dict | None:
    """서보 하나의 온도·전압·에러비트. 하나도 못 읽으면 None(표본 제외)."""
    reading: dict = {}

    # 온도 — 주 신호. 이것부터 읽는다.
    temp, comm, err = packet.read1ByteTxRx(port, dxl_id, _ADDR_TEMPERATURE)
    if comm == 0 and err == 0:
        reading["temp_c"] = float(temp)

    # 하드웨어 에러 비트. 0(정상)과 못 읽음(None)은 다른 사실이라 성공했을
    # 때만 담는다.
    hw, comm, err = packet.read1ByteTxRx(port, dxl_id, _ADDR_HW_ERROR)
    if comm == 0 and err == 0:
        reading["hardware_error"] = int(hw)

    # 입력 전압 (0.1V 단위).
    volt, comm, err = packet.read2ByteTxRx(port, dxl_id, _ADDR_VOLTAGE)
    if comm == 0 and err == 0:
        reading["voltage_v"] = round(volt * 0.1, 1)

    return reading or None


def main() -> int:
    ap = argparse.ArgumentParser(description="Dynamixel 서보 온도·전압")
    ap.add_argument(
        "--port", default=os.environ.get("OMX_ARM_PORT", "/dev/ttyACM0"))
    ap.add_argument(
        "--baud", type=int,
        default=int(os.environ.get("OMX_ARM_BAUD", "1000000")))
    args = ap.parse_args()

    try:
        from dynamixel_sdk import PacketHandler, PortHandler  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        _emit({"오류": f"dynamixel_sdk 를 불러오지 못했습니다 — {type(e).__name__}: {e}"})
        return 0

    port = PortHandler(args.port)
    if not port.openPort():
        _emit({"오류": f"포트를 열지 못했습니다: {args.port}"})
        return 0
    if not port.setBaudRate(args.baud):
        port.closePort()
        _emit({"오류": f"보드레이트 설정 실패: {args.baud}"})
        return 0

    packet = PacketHandler(2.0)
    servos = []
    try:
        for joint, dxl_id in _JOINTS:
            reading = _read(port, packet, dxl_id)
            if reading is not None:
                servos.append({"joint": joint, **reading})
    finally:
        port.closePort()

    if not servos:
        _emit({"오류": "서보 응답이 없습니다 — 전원·연결 확인"})
        return 0

    _emit({"servos": servos})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
