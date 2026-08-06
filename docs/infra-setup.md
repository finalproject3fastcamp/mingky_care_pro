# 인프라 구성

로봇·관제·네트워크의 실제 구성과 복구 절차입니다.
**이 값들이 기기 안에만 있으면 누가 건드렸을 때 복구할 방법이 없습니다.**

## 구성 요약

```
FASTCAMPUS_10F (기관 Wi-Fi, WPA2-PSK)
      │ 무선 브리지 (iptime 무선 멀티브리지)
[ipTIME N604SR "mingky"]  192.168.0.0/24  게이트웨이 .1
      │                        │
   유선 100Mbps           2.4GHz STA
      │                        │
 [관제컴퓨터]            [pinky1] [pinky2]
```

| | IP | ROS 도메인 | 비고 |
| --- | --- | --- | --- |
| 관제컴퓨터 | `192.168.0.10` | **25** | 유선 `enp131s0` |
| pinky1 | `192.168.0.21` | **21** | 무선 2.4GHz · Raspberry Pi 5 |
| pinky2 | `192.168.0.22` | **20** | 무선 2.4GHz · Raspberry Pi 5 |
| 공유기 | `192.168.0.1` | — | ipTIME N604SR (2.4GHz 싱글밴드, 유선 100Mbps) |

ROS 도메인은 위 표의 장비별 고정값을 사용합니다. IP 끝자리에서 유도하지
않으며, 특히 pinky2는 IP가 `.22`여도 Domain ID는 `20`입니다.

도메인이 다르면 **같은 서브넷이어도 서로 보이지 않습니다.** DDS 디스커버리
포트가 도메인마다 다르기 때문입니다. 관제에서 로봇 토픽을 직접 보려면
`domain_bridge` 를 쓰거나 도메인을 맞춰야 합니다.

## 로봇 접속

**로봇 작업 시 노트북 Wi-Fi 를 `mingky` 로 전환합니다.**
`mingky` 는 iptime 무선 브리지를 통해 인터넷도 됩니다.

```bash
ssh pinky@192.168.0.21    # pinky1
ssh pinky@192.168.0.22    # pinky2
```

`/etc/hosts` 에 등록해두면 편합니다.

```
192.168.0.21  pinky1
192.168.0.22  pinky2
```

### FASTCAMPUS 에서는 로봇에 직접 접속할 수 없습니다

iptime 이 NAT 를 하므로 바깥에서 안으로 들어올 수 없습니다.
관제컴퓨터가 양쪽에 다 있으므로 경유할 수는 있습니다.

```
노트북(FASTCAMPUS) → 관제컴퓨터 → 로봇
```

## ROS 도메인 설정

**`.bashrc` 에 넣지 마세요.** Ubuntu `.bashrc` 는 상단에서 비대화형 셸을
조기 반환하므로, **SSH 명령 실행·systemd·cron 에서는 적용되지 않습니다.**
그러면 노드가 도메인 0 으로 뜹니다.

```bash
# 각 로봇에서
sudo sed -i '/^ROS_DOMAIN_ID=/d' /etc/environment
echo "ROS_DOMAIN_ID=21" | sudo tee -a /etc/environment    # pinky2 는 20
```

확인은 비대화형으로 해야 의미가 있습니다.

```bash
ssh pinky@192.168.0.21 'echo $ROS_DOMAIN_ID'    # 21 이 나와야 한다
```

## 시간 동기화 (chrony)

이벤트 타임라인이 여러 기기의 시각을 섞어 정렬하므로, **시계가 어긋나면
타임라인 자체가 거짓이 됩니다.**

목적은 절대 정확도가 아니라 **기기 간 상대 일치** 입니다. 그래서 관제컴퓨터
하나를 기준으로 삼고 나머지가 따릅니다. 인터넷이 끊겨도 서로는 맞습니다.

### 관제컴퓨터 (서버)

```bash
sudo apt install -y chrony
sudo systemctl disable --now systemd-timesyncd
```

`/etc/chrony/chrony.conf` 끝에 추가합니다.

```
local stratum 10
allow 192.168.0.0/24
```

> `chrony.conf` 는 **줄 끝 주석을 허용하지 않습니다.**
> `local stratum 10   # 설명` 처럼 쓰면 기동에 실패합니다.

`local stratum 10` 이 없으면 상류가 끊겼을 때 로봇에게 시각 제공을
거부합니다. Ubuntu 기본 설정에 이미 `pool` 이 있으므로 상류는 따로 넣지
않습니다.

### 로봇 (클라이언트)

```bash
sudo apt install -y chrony
printf '\nserver 192.168.0.10 iburst minpoll 4 maxpoll 6\nmakestep 1.0 3\n' \
  | sudo tee -a /etc/chrony/chrony.conf
sudo systemctl restart chrony && sudo systemctl enable chrony
sudo timedatectl set-timezone Asia/Seoul
```

`makestep 1.0 3` 이 중요합니다. chrony 는 기본적으로 시계를 점프시키지 않고
서서히 맞추는데, 부팅 직후 오차가 크면 **수렴에 수십 분이 걸립니다.**

### 확인

```bash
# 관제
chronyc clients          # 로봇 2대가 보이면 성공

# 로봇
chronyc tracking | grep -E "Reference ID|Stratum|Leap"
# Reference ID 가 192.168.0.10 을 가리켜야 한다
```

### 부팅 순서 주의

**시계 점프는 이미 기록된 타임스탬프를 소급 수정하지 않습니다.**
동기화 전에 ROS 노드가 뜨면 그 사이 이벤트의 `occurred_at` 이 영구히
틀립니다.

bringup 앞에 넣으세요.

```bash
chronyc waitsync 60 0.1
```

## 개발 PC 준비 (팀원용) — 처음 한 번

로봇을 쓰려는 노트북에서 아래 네 가지를 먼저 하세요.
**하나라도 빠지면 조용히 안 됩니다.**

### 1. Wi-Fi 를 `mingky` 로

로봇은 `192.168.0.0/24` 안에 있습니다. FASTCAMPUS 에서는 NAT 때문에
로봇에 직접 접속할 수 없습니다. `mingky` 는 인터넷도 됩니다.

### 2. chrony — 빠뜨리기 쉽지만 필수입니다

**ROS 노드를 돌리는 모든 기계가 같은 시각 기준을 봐야 합니다.**
로봇 2대만 맞춰두면 부족합니다.

AMCL 은 로봇이 보낸 스캔의 타임스탬프와 자기 TF 캐시를 비교합니다.
PC 시계가 로봇과 어긋나면 스캔이 전부 버려지고 이런 로그가 쏟아집니다.

```
[amcl] Message Filter dropping message: frame 'rplidar_link'
       'the timestamp on the message is earlier than all the data in the transform cache'
       → 'discarding message because the queue is full'
```

위치추정이 아예 동작하지 않습니다.

```bash
sudo apt install -y chrony
sudo systemctl disable --now systemd-timesyncd
printf '\nserver 192.168.0.10 iburst minpoll 4 maxpoll 6\nmakestep 1.0 3\n' \
  | sudo tee -a /etc/chrony/chrony.conf
sudo systemctl restart chrony && sudo systemctl enable chrony

chronyc tracking | grep -E "Reference ID|System time|Leap"
```

`Reference ID` 가 `192.168.0.10` 을 가리켜야 합니다.

### 3. 빌드

PC 에서는 두 패키지만 빌드합니다.

```bash
cd ~/mingky_care_pro
source /opt/ros/jazzy/setup.bash
colcon build --base-paths pinky mingky_ros \
  --packages-select pinky_navigation pinky_description
colcon build --base-paths mingky_ros
source install/setup.bash
```

`pinky_sensor_adc` · `pinky_imu_bno055` 같은 것은 I2C 하드웨어용이라
PC 에서 빌드하면 깨집니다. **`--base-paths pinky` 를 통째로 빌드하지 마세요.**

`pinky_description` 이 없으면 RViz 에 로봇 모양이 안 나오고 mesh 에러가
쏟아집니다. 동작에는 지장이 없지만 로그가 시끄러워 진짜 오류를 놓칩니다.

`pinky_navigation` 이 없으면 localization 실행이 실패합니다.

### 4. 도메인

**터미널마다** 설정해야 합니다. 새 터미널을 열면 초기화됩니다.

```bash
export ROS_DOMAIN_ID=21     # pinky1 · pinky2 는 20
```

## 로봇 bringup

```bash
ssh pinky@192.168.0.21
```

**띄우기 전에 중복부터 확인하세요.**

```bash
ps aux | grep -E "sllidar|pinky_bringup" | grep -v grep
```

이미 떠 있으면 라이다 USB 포트를 잡고 있어서, 새로 띄운 쪽이
`SL_RESULT_OPERATION_TIMEOUT` 으로 죽습니다. SSH 세션이 끊겨도 프로세스는
남으므로 흔하게 겪습니다.

```bash
pkill -f bringup_robot.launch ; pkill -f sllidar ; sleep 3
ros2 launch pinky_bringup bringup_robot.launch.xml
```

**launch 파일 이름은 `bringup_robot.launch.xml` 입니다.**
`bringup.launch.py` 가 아닙니다.

PC 에서 확인합니다.

```bash
export ROS_DOMAIN_ID=21
ros2 topic list | grep -E "odom|scan|batt"
```

```
/battery/percent
/battery/voltage
/odom
/scan
```

**`/scan` 이 없으면 라이다가 죽은 것입니다.** 로봇 쪽 로그에서
`sllidar_node` 를 확인하세요. 중복 실행이거나 전원 부족입니다.

## 잘 안 될 때 확인 순서

| 증상 | 원인 | 확인 |
| --- | --- | --- |
| `ros2 topic list` 가 비어 있음 | 도메인 불일치 | `echo $ROS_DOMAIN_ID` 를 **모든 터미널에서** |
| `/scan` 없음 | 라이다 죽음 | 로봇에서 `ps aux \| grep sllidar` |
| `SL_RESULT_OPERATION_TIMEOUT` | **bringup 중복 실행** | `pkill -f sllidar` 후 재시작 |
| AMCL 이 스캔을 전부 버림 | **PC 시계 미동기화** | `chronyc tracking` |
| `Package 'pinky_navigation' not found` | PC 에 빌드 안 됨 | 위 3번 빌드 |
| RViz mesh 에러 다발 | `pinky_description` 빌드 안 됨 | 동작에는 무관 |
| 로봇에 ping 안 됨 | Wi-Fi 가 `mingky` 가 아님 | `iwgetid -r` |
| Wi-Fi·BLE 가 계속 끊김 | **저전압** | 배터리 전압 |

**맨 아래 두 줄을 먼저 의심하세요.** 원인 불명 증상의 대부분입니다.

## 배터리 — 원인 불명 증상의 공통 분모

```
2S Li-ion    경고 6.9V   위험 6.8V   해제 6.95V
pinkylib     percent = (V - 6.8) / (7.6 - 6.8) * 100    (선형 근사)
```

`battery-buzzer.service` 가 감시하며 `Restart=always` 라 **`pkill` 로는 못
멈춥니다.** 부저가 계속 울리면 서비스를 멈추세요.

```bash
sudo systemctl stop battery-buzzer.service
```

**끄기 전에 실제 전압을 확인하세요.** 부저가 맞을 수 있습니다.

```bash
python3 -c "
from pinkylib import Battery
b = Battery(); p = b.battery_percentage()
print(f'{p:.1f}%  →  약 {6.8 + p/100*0.8:.2f}V'); b.close()"
```

`disable` 은 하지 마세요. 주행 중 방전을 알 방법이 없어집니다.

### 저전압이 만드는 증상

Pi 5 는 전압이 처지면 **무선 칩부터 죽습니다.**

- Wi-Fi 가 끊기고 AP 모드로 폴백
- BLE 광고는 나오는데 연결이 안 됨 (`No device connected`)
- 무작위 재부팅
- Nav2 가 이유 없이 불안정

**원인을 못 찾겠으면 배터리부터 재세요.**

## 로봇 복구 — AP 모드로 떨어졌을 때

Pinky 는 Wi-Fi 에 못 붙으면 자기 AP 를 띄웁니다.
`pinky_XXXX` SSID 가 보이면 그 상태입니다.

```
1. 노트북 Wi-Fi 를 pinky_XXXX 로 전환
2. ssh pinky@10.42.0.1  (또는 192.168.4.1)
3. ~/wifi_setup.sh 로 mingky 재설정
4. 재부팅 후 다시 붙는지 확인   ← 설정이 영구 저장됐는지 검증
```

Pinky Studio 의 BLE provisioning 은 실패 지점이 많습니다.
**AP 직접 접속이 더 확실합니다.**

4번을 꼭 하세요. 재부팅 후 안 붙으면 설정이 저장되지 않은 것이고,
그건 다음에 또 터집니다.

## Wi-Fi 채널

2.4GHz 는 FASTCAMPUS 와 채널을 나눠 써야 합니다.

```
FASTCAMPUS  ch1 · ch6 · ch11 모두 사용. ch6 이 가장 강함
mingky      ch11 권장 (가장 한산)
```

**무선 멀티브리지(WISP) 모드에서는 채널을 고를 수 없습니다.**
싱글 라디오가 상위 AP 채널에 강제로 맞춰집니다.

로봇이 자기 AP 를 계속 띄우고 있으면(`pinky_XXXX`) 간섭원이므로 끄세요.

## Foxglove

```bash
sudo apt install -y ros-jazzy-foxglove-bridge
ros2 launch mingky_bringup foxglove.launch.py
```

**로봇마다 도메인이 달라 관제 한 곳에서 두 대를 동시에 볼 수 없습니다.**
로봇마다 띄우고 Studio 에서 접속을 갈아탑니다.

| 로봇 | 주소 |
| --- | --- |
| pinky1 | `ws://192.168.0.21:8765` |
| pinky2 | `ws://192.168.0.22:8765` |

## 미해결

- **`domain_bridge` 설정이 저장소 밖에 있습니다.** 관제컴퓨터의
  `/root/bridge_configs/pinky1_bridge.yaml` 하나뿐이고 `battery/percent` 만
  브리지합니다. pinky2 용은 없습니다.
- **DDS 가 FASTCAMPUS 망으로도 나갑니다.** 관제컴퓨터가 인터페이스 둘을
  갖고 `ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET` 이라, 다른 팀이 같은 도메인을
  쓰면 토픽이 섞입니다. 인터페이스 제한이 필요합니다.
- **`iptables-persistent`** 가 설치돼 있는지 확인이 필요합니다. 없으면
  재부팅 시 규칙이 날아갑니다.
- **`config/hosts/*.env`** 로 도메인·IP 를 저장소에 넣는 작업이 남았습니다.
