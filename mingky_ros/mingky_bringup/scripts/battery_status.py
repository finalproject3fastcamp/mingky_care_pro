#!/usr/bin/env python3
"""Print the latest ROS battery sample without touching ADC or LCD hardware."""

import argparse
import math
import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32


class BatteryStatusReader(Node):
    """Collect one voltage and percentage sample from the shared ROS topics."""

    def __init__(self) -> None:
        super().__init__('battery_status_cli')
        self.voltage: float | None = None
        self.percent: float | None = None
        self.create_subscription(
            Float32, '/battery/voltage', self._on_voltage, 10)
        self.create_subscription(
            Float32, '/battery/percent', self._on_percent, 10)

    def _on_voltage(self, msg: Float32) -> None:
        self.voltage = float(msg.data)

    def _on_percent(self, msg: Float32) -> None:
        self.percent = float(msg.data)

    @property
    def complete(self) -> bool:
        return self.voltage is not None and self.percent is not None


def format_status(voltage: float, percent: float) -> str:
    """Return a terminal-only status compatible with the former command."""
    bounded_percent = int(round(max(0.0, min(100.0, percent))))
    condition = 'Battery LOW!!' if voltage <= 6.8 else 'Battery OK!'
    return (
        f'현재 배터리 상태 : {bounded_percent}% ({voltage:.2f}V)\n'
        f'{condition}'
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='LCD와 ADC를 건드리지 않고 ROS 배터리 상태를 확인합니다.')
    parser.add_argument(
        '--timeout', type=float, default=7.0,
        help='두 배터리 토픽을 기다릴 최대 시간(초, 기본 7초)')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print('--timeout은 0보다 큰 유한한 값이어야 합니다.', file=sys.stderr)
        return 2

    rclpy.init()
    node = BatteryStatusReader()
    deadline = time.monotonic() + args.timeout
    try:
        while rclpy.ok() and not node.complete:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                missing = []
                if node.voltage is None:
                    missing.append('/battery/voltage')
                if node.percent is None:
                    missing.append('/battery/percent')
                print(
                    '배터리 정보를 받지 못했습니다: ' + ', '.join(missing),
                    file=sys.stderr,
                )
                print(
                    'mingky-battery-pub.service 또는 통합 시스템 상태를 '
                    '확인하세요.',
                    file=sys.stderr,
                )
                return 1
            rclpy.spin_once(node, timeout_sec=min(remaining, 0.5))

        if not node.complete:
            return 1
        print(format_status(node.voltage, node.percent))
        return 0
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
