#!/usr/bin/env python3
"""
I2C ADC(0x08) 를 단독으로 읽어 배터리·초음파·IR 을 발행한다.

이 장치를 만지는 프로세스는 하나여야 한다. 그래서 이 노드가 채널을 전부
읽고, 다른 노드는 I2C 대신 토픽을 구독한다.

왜 하나여야 하나
    0x08 은 "커맨드 바이트로 채널 선택 -> 2바이트 읽기" 구조이고 선택된
    채널을 하나만 기억한다. write 와 read 사이에 다른 프로세스가 채널을
    바꾸면 엉뚱한 채널 값을 읽는다. 통신은 성공하고 예외도 안 나므로
    호출한 쪽에서 알아챌 방법이 없다.

    실측(2026-08-12, pinky2, 각 40표본): 리더가 둘일 때 배터리 전압
    중앙값이 0.397V 낮아지고 산포가 37mV -> 3.22V 로 벌어졌다. 리더를
    하나로 되돌리자 2mV 오차로 복귀했다.

측정으로 확인한 것
    - write 와 read 사이 지연은 필요 없다. 0us 와 6000us 의 결과가 같았다.
      (pinkylib 은 1ms, pinky_sensor_adc 는 6ms 를 넣는데 근거가 없다)
    - 결합 트랜잭션(i2c_rdwr)은 쓸 수 없다. 이 장치는 write 와 read 사이에
      STOP 을 요구한다. repeated-start 로 읽으면 값이 나오지 않는다.
      따라서 원자성은 잠금으로만 얻을 수 있다.
    - 커널 선점 때문에 write 호출이 드물게 3ms 까지 걸린다(p99.9). 언어를
      바꿔도 이건 남는다. 그래서 flock 을 건다.

flock 은 같은 파일을 잠그는 프로세스끼리만 유효하다. pinkylib 은 잠금을
쓰지 않으므로, pinkylib 을 쓰는 노드가 함께 뜨면 여전히 깨진다. 그런 노드를
띄우지 않는 것이 전제이고, 그래서 기동 시 중복을 확인한다.
"""

import fcntl
import statistics
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import Range
from smbus2 import SMBus
from std_msgs.msg import Float32, UInt16MultiArray

I2C_BUS = 1
I2C_ADDR = 0x08

# TI ADS7828 의 단극성 채널 선택 커맨드와 같은 값이다.
#   SD=1(단극성) | 채널 | PD=10(내부 기준전압 off, ADC on) | 00
REG_IR = (0x98, 0xC8, 0x88)     # main_node.cpp 의 발행 순서와 같다
REG_ULTRASONIC = 0xD8
REG_BATTERY = 0xF8

# pinkylib/battery.py 와 같은 변환. 2셀 리튬이온.
EMPTY_V = 6.8   # 0%
FULL_V = 7.6    # 100%
VREF = 4.096
DIVIDER = 13.0 / 28.0


def voltage_from_count(count):
    """ADC 값 -> 전압. pinkylib/battery.py 의 get_voltage() 와 같은 식.

    상수를 미리 접지 않고 같은 연산 순서를 유지한다. 부동소수점은
    결합법칙이 성립하지 않아서, 순서를 바꾸면 마지막 비트가 달라진다.
    """
    return (count / 4096.0) * VREF / DIVIDER


def percent_from_voltage(volt):
    """전압 -> 퍼센트. pinkylib 과 같은 식, 같은 반올림."""
    percent = (volt - EMPTY_V) / (FULL_V - EMPTY_V) * 100
    return round(max(0.0, min(100.0, percent)), 2)


# 초음파 유효 범위. min 은 센서 사양, max 는 변환식이 실제로 낼 수 있는 상한.
#
# 상한을 어림값(0.97 이나 1.0)으로 두면 안 된다. Nav2 RangeSensorLayer 의
# clear_on_max_reading 이 "읽은 값 == max_range" 일 때만 코스트맵을 지우는데,
# max_range 가 도달 불가능한 값이면 그 조건이 영원히 성립하지 않는다.
# 장애물이 사라져도 코스트맵에 남는다.
US_MIN_RANGE = 0.02
US_MAX_RANGE = (4095 / 4096.0) - 0.03


def distance_from_count(count):
    """ADC 값 -> 거리(m). pinkylib/ultrasonic.py 및 main_node.cpp 와 같은 식.

    이 식이 실제 거리와 어떤 관계인지는 확인된 바 없다. 그대로 옮겨 두되,
    초음파를 실제로 쓰기 전에 특성 측정이 필요하다.
    """
    return (count / 4096.0) - 0.03


def clamped_range(count):
    """유효 범위로 자른 거리.

    ADC 가 낮게 읽히면 식이 음수(-0.03m 까지)를 낸다. Range 메시지에서
    min_range 미만은 "측정 불가" 로 해석되는데, 그 처리는 구독자마다 다르다.
    Nav2 RangeSensorLayer 는 범위 밖 값을 clear_on_max_reading 등의 규칙으로
    다루므로, 발행 측에서 정의된 구간으로 잘라 보낸다.
    """
    return min(max(distance_from_count(count), US_MIN_RANGE), US_MAX_RANGE)


class AdcReader(Node):
    """0x08 을 단독으로 읽어 배터리·초음파·IR 을 발행한다."""

    def __init__(self, **kwargs):
        super().__init__('adc_reader', **kwargs)

        # 배터리는 battery_guard 의 창 크기가 5초 주기에 맞춰 잡혀 있다.
        # 주기를 바꾸면 confirm_window(6) 와 trend_samples(5) 의 의미가 바뀐다.
        self.declare_parameter('battery_period_sec', 5.0)
        # 초음파·IR 은 아직 구독자가 없다. 쓸 사람이 켜라. 0 이면 읽지 않는다.
        self.declare_parameter('sensor_rate_hz', 0.0)
        self.declare_parameter('duplicate_check_sec', 3.0)
        self.declare_parameter('exit_on_duplicate', True)
        # 채널 선택 후 대기. 측정상 0 으로 충분하지만 하드웨어 개체차를
        # 대비해 파라미터로 남긴다.
        self.declare_parameter('settle_sec', 0.0)
        # 배터리는 한 번 발행할 때 여러 번 읽어 중앙값을 쓴다. 평균과 달리
        # 오염된 표본 하나에 끌려가지 않는다.
        self.declare_parameter('battery_samples', 5)

        g = self.get_parameter
        self.settle = max(0.0, float(g('settle_sec').value))
        self.battery_samples = max(1, int(g('battery_samples').value))
        self.exit_on_duplicate = bool(g('exit_on_duplicate').value)

        self.bus = SMBus(I2C_BUS)
        # flock 대상. 버스 장치 자체를 잠근다.
        self._lock_fd = open(f'/dev/i2c-{I2C_BUS}', 'rb')

        self.voltage_pub = self.create_publisher(Float32, 'battery/voltage', 10)
        self.percent_pub = self.create_publisher(Float32, 'battery/percent', 10)
        self.range_pub = self.create_publisher(Range, 'us_sensor/range', 10)
        self.ir_pub = self.create_publisher(
            UInt16MultiArray, 'ir_sensor/range', 10)

        battery_period = float(g('battery_period_sec').value)
        self.create_timer(battery_period, self.publish_battery)

        rate = float(g('sensor_rate_hz').value)
        if rate > 0.0:
            self.create_timer(1.0 / rate, self.publish_sensors)

        check_after = float(g('duplicate_check_sec').value)
        self._dup_timer = self.create_timer(check_after, self._check_duplicate)

        self.get_logger().info(
            f'ADC 단독 읽기 시작 | 배터리 {battery_period:.1f}초 주기 '
            f'({self.battery_samples}회 중앙값) '
            f'| 초음파·IR {"꺼짐" if rate <= 0 else f"{rate:.1f}Hz"}')

    # ------------------------------------------------------------------

    def read_channel(self, reg):
        """채널 하나를 읽는다. write~read 를 flock 으로 묶는다.

        결합 트랜잭션을 못 쓰는 장치라 두 번의 전송으로 나뉘고, 그 사이가
        임계 구역이다. 커널 선점으로 창이 밀리세컨드까지 벌어질 수 있어
        잠금이 필요하다.
        """
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
        try:
            self.bus.write_byte(I2C_ADDR, reg)
            if self.settle:
                time.sleep(self.settle)
            data = self.bus.read_i2c_block_data(I2C_ADDR, 0, 2)
        finally:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        return (data[0] << 4) | (data[1] >> 4)

    def _check_duplicate(self):
        """같은 토픽에 다른 발행자가 있는지 한 번만 본다."""
        self._dup_timer.cancel()

        others = self.count_publishers('battery/voltage') - 1
        if others <= 0:
            return

        self.get_logger().error(
            f'battery/voltage 에 다른 발행자가 {others}개 있습니다. '
            'I2C 를 동시에 읽으면 측정값이 조용히 낮아집니다.')
        self.get_logger().error(
            'pinky_bringup 의 battery_publisher 가 함께 떠 있지 않은지 '
            '확인하세요. 이 노드가 그 자리를 대체합니다.')

        if not self.exit_on_duplicate:
            self.get_logger().warn('exit_on_duplicate=false — 그대로 진행합니다.')
            return

        self.get_logger().error('중복을 피해 종료합니다.')
        raise SystemExit(1)

    def publish_battery(self):
        """여러 번 읽어 중앙값으로 전압과 퍼센트를 발행한다."""
        counts = []
        for _ in range(self.battery_samples):
            try:
                counts.append(self.read_channel(REG_BATTERY))
            except OSError as e:
                self.get_logger().warn(f'I2C 읽기 실패: {e}')
        if not counts:
            return

        volt = voltage_from_count(statistics.median(counts))
        self.voltage_pub.publish(Float32(data=float(volt)))
        self.percent_pub.publish(
            Float32(data=float(percent_from_voltage(volt))))

    def publish_sensors(self):
        """초음파와 IR 을 한 바퀴 읽어 발행한다."""
        try:
            us = self.read_channel(REG_ULTRASONIC)
            ir = [self.read_channel(reg) for reg in REG_IR]
        except OSError as e:
            self.get_logger().warn(f'I2C 읽기 실패: {e}')
            return

        msg = Range()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'ultrasonic_link'
        msg.radiation_type = Range.ULTRASOUND
        msg.field_of_view = 0.26
        msg.min_range = US_MIN_RANGE
        # 변환식이 낼 수 있는 최댓값. 상류 main_node.cpp 는 3.0 으로 선언하는데
        # (4095/4096 - 0.03) = 0.97 이라 그 값은 나올 수 없다.
        msg.max_range = US_MAX_RANGE
        msg.range = float(clamped_range(us))
        self.range_pub.publish(msg)

        self.ir_pub.publish(UInt16MultiArray(data=[int(v) for v in ir]))

    def destroy_node(self):
        """I2C 버스와 잠금 파일을 닫는다."""
        try:
            self.bus.close()
            self._lock_fd.close()
        except OSError:
            pass
        return super().destroy_node()


def main(args=None):
    """노드를 띄운다. 중복이 감지되면 종료코드 1 을 낸다."""
    rclpy.init(args=args)
    node = None
    code = 0
    try:
        node = AdcReader()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # systemctl stop 은 SIGTERM 을 보낸다. 잡지 않으면 종료할 때마다
        # traceback 이 서비스 로그에 남는다.
        pass
    except SystemExit as e:
        code = getattr(e, 'code', 0) or 0
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
