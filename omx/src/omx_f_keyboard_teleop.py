#!/usr/bin/env python3
"""OMX-Follower keyboard teleop (python3)."""

from __future__ import annotations

import argparse
import select
import sys
import termios
import time
import tty
from pathlib import Path


def _ensure_lerobot_import() -> None:
    """Allow running from workspace root without installing lerobot as a package."""
    workspace_src = Path(__file__).resolve().parent
    lerobot_src = workspace_src / "lerobot" / "src"
    if lerobot_src.is_dir():
        lerobot_src_str = str(lerobot_src)
        if lerobot_src_str not in sys.path:
            sys.path.insert(0, lerobot_src_str)


_ensure_lerobot_import()

DEFAULT_PORT = "/dev/omx_follower"
EXPECTED_MOTOR_IDS = {11, 12, 13, 14, 15, 16}
_DXL_SYMBOLS = None


def _get_dxl_symbols():
    """Lazy import so `-h` works even when runtime deps are missing."""
    global _DXL_SYMBOLS
    if _DXL_SYMBOLS is None:
        from lerobot.motors import Motor, MotorNormMode
        from lerobot.motors.dynamixel import DriveMode, DynamixelMotorsBus, OperatingMode

        _DXL_SYMBOLS = (Motor, MotorNormMode, DriveMode, DynamixelMotorsBus, OperatingMode)
    return _DXL_SYMBOLS


MOTOR_ORDER = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)

ARM_MOTORS = MOTOR_ORDER[:-1]
GRIPPER_MOTOR = MOTOR_ORDER[-1]

KEY_BINDINGS = {
    "1": ("shoulder_pan", +1),
    "q": ("shoulder_pan", -1),
    "2": ("shoulder_lift", +1),
    "w": ("shoulder_lift", -1),
    "3": ("elbow_flex", +1),
    "e": ("elbow_flex", -1),
    "4": ("wrist_flex", +1),
    "r": ("wrist_flex", -1),
    "5": ("wrist_roll", +1),
    "t": ("wrist_roll", -1),
    "o": ("gripper", +1),
    "p": ("gripper", -1),
}


def _build_bus(port: str) -> DynamixelMotorsBus:
    Motor, MotorNormMode, _, DynamixelMotorsBus, _ = _get_dxl_symbols()
    return DynamixelMotorsBus(
        port=port,
        motors={
            "shoulder_pan": Motor(11, "xl430-w250", MotorNormMode.RANGE_M100_100),
            "shoulder_lift": Motor(12, "xl430-w250", MotorNormMode.RANGE_M100_100),
            "elbow_flex": Motor(13, "xl430-w250", MotorNormMode.RANGE_M100_100),
            "wrist_flex": Motor(14, "xl330-m288", MotorNormMode.RANGE_M100_100),
            "wrist_roll": Motor(15, "xl330-m288", MotorNormMode.RANGE_M100_100),
            "gripper": Motor(16, "xl330-m288", MotorNormMode.RANGE_0_100),
        },
    )


class KeyboardTeleop:
    def __init__(
        self,
        port: str,
        arm_step: int,
        gripper_step: int,
        command_interval: float,
    ) -> None:
        self.bus = _build_bus(port)
        self.arm_step = arm_step
        self.gripper_step = gripper_step
        self.command_interval = command_interval
        self.positions: dict[str, int] = {}
        self.min_limits: dict[str, int] = {}
        self.max_limits: dict[str, int] = {}
        self.last_command_time = 0.0

    def connect_and_configure(self) -> None:
        _, _, DriveMode, _, OperatingMode = _get_dxl_symbols()
        self.bus.connect()
        with self.bus.torque_disabled():
            self.bus.configure_motors(return_delay_time=0)

            for motor in ARM_MOTORS:
                self.bus.write(
                    "Operating_Mode",
                    motor,
                    OperatingMode.EXTENDED_POSITION.value,
                    normalize=False,
                )

            self.bus.write(
                "Operating_Mode",
                GRIPPER_MOTOR,
                OperatingMode.CURRENT_POSITION.value,
                normalize=False,
            )

            self.bus.write("Drive_Mode", GRIPPER_MOTOR, DriveMode.NON_INVERTED.value, normalize=False)

            # Keep the same tuning used in omx_follower configure path.
            self.bus.write("Position_P_Gain", "elbow_flex", 1500, normalize=False)
            self.bus.write("Position_I_Gain", "elbow_flex", 0, normalize=False)
            self.bus.write("Position_D_Gain", "elbow_flex", 600, normalize=False)

        self.bus.enable_torque(num_retry=3)
        self.positions = self.bus.sync_read("Present_Position", normalize=False)
        self.min_limits = self.bus.sync_read("Min_Position_Limit", normalize=False)
        self.max_limits = self.bus.sync_read("Max_Position_Limit", normalize=False)

    def disconnect(self, disable_torque: bool) -> None:
        if self.bus.is_connected:
            self.bus.disconnect(disable_torque=disable_torque)

    def _get_key(self, timeout: float = 0.01) -> str | None:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                return sys.stdin.read(1)
            return None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _clamp(self, motor: str, value: int) -> int:
        min_limit = self.min_limits[motor]
        max_limit = self.max_limits[motor]
        return max(min(value, max_limit), min_limit)

    def _send_command(self, motor: str, direction: int) -> None:
        step = self.gripper_step if motor == GRIPPER_MOTOR else self.arm_step
        target = self.positions[motor] + direction * step
        target = self._clamp(motor, target)

        self.bus.write("Goal_Position", motor, target, normalize=False)
        self.positions[motor] = target
        print(f"\r{motor:<14} -> {target:<6}  ", end="", flush=True)

    def run(self) -> None:
        print("Ready. Keyboard teleop started.")
        print("")
        print("Joint Control")
        print("1 / q - Joint 1")
        print("2 / w - Joint 2")
        print("3 / e - Joint 3")
        print("4 / r - Joint 4")
        print("5 / t - Joint 5")
        print("Gripper Control")
        print("o - Open gripper")
        print("p - Close gripper")
        print("ESC - Exit")

        while True:
            key = self._get_key()
            if key is None:
                continue

            if key == "\x1b":
                print("\nExit requested.")
                break

            if key not in KEY_BINDINGS:
                continue

            now = time.time()
            if now - self.last_command_time < self.command_interval:
                continue

            motor, direction = KEY_BINDINGS[key]
            self._send_command(motor, direction)
            self.last_command_time = now


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OMX-Follower keyboard teleop (python3).",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 omx_f_keyboard_teleop.py --port /dev/ttyACM0\n"
        ),
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port for OMX-F (default: {DEFAULT_PORT}).")
    parser.add_argument(
        "--arm-step",
        type=int,
        default=13,
        help="Raw encoder step for joints 1~5 per key press (default: 13).",
    )
    parser.add_argument(
        "--gripper-step",
        type=int,
        default=25,
        help="Raw encoder step for gripper per key press (default: 25).",
    )
    parser.add_argument(
        "--command-interval",
        type=float,
        default=0.02,
        help="Minimum interval between commands in seconds (default: 0.02).",
    )
    parser.add_argument(
        "--keep-torque-on-exit",
        action="store_true",
        help="Keep torque enabled when exiting (default: disable torque).",
    )
    return parser.parse_args()


def print_port_troubleshooting(selected_port: str) -> None:
    print("\nPort hint:")
    print(f"- Current port: {selected_port}")
    print("- Check follower port: ls -l /dev/omx_follower")


def main() -> int:
    args = parse_args()
    print(f"Using port: {args.port}")
    teleop = KeyboardTeleop(
        port=args.port,
        arm_step=args.arm_step,
        gripper_step=args.gripper_step,
        command_interval=args.command_interval,
    )

    try:
        teleop.connect_and_configure()
        teleop.run()
        return 0
    except KeyboardInterrupt:
        print("\nCtrl+C detected. Exiting.")
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"\nError: {exc}", file=sys.stderr)
        error_text = str(exc)
        if (
            "motor check failed" in error_text
            or "Could not connect on port" in error_text
            or "Failed to open port" in error_text
        ):
            if "motor check failed" in error_text:
                print(
                    f"\nDetected no follower response (expected IDs: {sorted(EXPECTED_MOTOR_IDS)}).",
                    file=sys.stderr,
                )
            print_port_troubleshooting(args.port)
        return 1
    finally:
        teleop.disconnect(disable_torque=not args.keep_torque_on_exit)


if __name__ == "__main__":
    raise SystemExit(main())
