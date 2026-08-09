# 로봇 사용 가이드 (팀원용)

처음 로봇을 쓰는 사람이 **위에서부터 그대로 따라 하면 되는** 순서입니다.
한 번만 하면 되는 것과 매번 하는 것을 나눠 두었습니다.

전제: Ubuntu 24.04 + ROS 2 Jazzy 가 설치된 노트북.

---

# 처음 한 번만

## 1. Wi-Fi 를 `mingky` 로 바꾼다

로봇은 `192.168.0.0/24` 안에 있습니다. **`FASTCAMPUS_10F` 에서는 로봇에
접속할 수 없습니다.** 공유기가 NAT 를 하기 때문입니다.

`mingky` 도 인터넷이 됩니다. 공유기가 `FASTCAMPUS` 에 무선으로 물려 있습니다.

```bash
nmcli dev wifi connect mingky password '<비밀번호>'
iwgetid -r        # mingky 가 나와야 한다
```

## 2. 저장소를 받는다

```bash
git clone https://github.com/finalproject3fastcamp/mingky_care_pro.git ~/mingky_care_pro
cd ~/mingky_care_pro
```

## 3. 패키지를 설치한다

```bash
sudo apt update
sudo apt install -y chrony ros-jazzy-foxglove-bridge
```

### chrony 는 반드시 필요합니다

**로봇과 시계가 맞지 않으면 위치추정이 아예 동작하지 않습니다.**
AMCL 이 로봇에서 온 스캔의 시각과 자기 좌표 이력을 대조하는데, 시계가
어긋나면 스캔을 전부 버립니다.

```bash
sudo systemctl disable --now systemd-timesyncd
printf '\nserver 192.168.0.10 iburst minpoll 4 maxpoll 6\nmakestep 1.0 3\n' \
  | sudo tee -a /etc/chrony/chrony.conf
sudo systemctl restart chrony && sudo systemctl enable chrony

sleep 10
chronyc tracking | grep "Reference ID"
```

`192.168.0.10` 이 나와야 합니다.

## 4. 빌드한다

```bash
cd ~/mingky_care_pro
source /opt/ros/jazzy/setup.bash

# PC 에서 필요한 pinky 패키지는 이 둘뿐입니다
colcon build --base-paths pinky mingky_ros \
  --packages-select pinky_navigation pinky_description

# 프로젝트 패키지 전체
colcon build --base-paths mingky_ros

source install/setup.bash
```

> **`--base-paths pinky` 를 통째로 빌드하지 마세요.**
> `pinky_sensor_adc` 처럼 로봇 하드웨어(I2C)용 패키지가 섞여 있어 PC 에서는
> 빌드가 깨집니다.

확인:

```bash
ros2 pkg prefix pinky_navigation      # 경로가 나와야 한다
ros2 pkg list | grep mingky           # 5개
```

## 5. 접속 편의 설정

```bash
sudo tee -a /etc/hosts > /dev/null <<'EOF'

# mingky_care
192.168.0.10  control
192.168.0.21  pinky1
192.168.0.22  pinky2
EOF

mkdir -p ~/.ssh && tee -a ~/.ssh/config > /dev/null <<'EOF'

Host pinky1
  HostName 192.168.0.21
  User pinky

Host pinky2
  HostName 192.168.0.22
  User pinky
EOF
chmod 600 ~/.ssh/config
```

이제 `ssh pinky1` 로 붙습니다.

비밀번호 입력이 귀찮으면 키를 등록하세요.

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519    # 이미 있으면 생략
ssh-copy-id pinky1
ssh-copy-id pinky2
```

## 6. Foxglove Studio (뷰어)

브라우저로 쓰면 설치가 없습니다.

- <https://app.foxglove.dev>
- 데스크톱 앱: <https://foxglove.dev/download>

---

# 매번 하는 것

## 7. 터미널 준비

**새 터미널을 열 때마다** 세 줄을 칩니다.

```bash
cd ~/mingky_care_pro
source /opt/ros/jazzy/setup.bash && source install/setup.bash
export ROS_DOMAIN_ID=21          # pinky1. pinky2 는 20
```

**도메인이 다르면 서로 안 보입니다.** 같은 네트워크여도 그렇습니다.

| 기기 | 도메인 |
| --- | --- |
| pinky1 | **21** |
| pinky2 | **20** |
| 관제컴퓨터 | 25 |

여러 터미널을 쓸 때 **전부 같은 값**이어야 합니다. 가장 흔한 실수입니다.

## 8. 로봇에 접속해서 bringup 실행

```bash
ssh pinky1
```

**띄우기 전에 이미 돌고 있는지 확인하세요.**

```bash
ps aux | grep -E "sllidar|pinky_bringup" | grep -v grep
```

뭔가 나오면 정리합니다. SSH 세션이 끊겨도 프로세스는 남기 때문에 자주
겪습니다. 중복 실행하면 라이다가 포트 충돌로 죽습니다.

```bash
pkill -f bringup_robot.launch ; pkill -f sllidar ; sleep 3
```

실행:

```bash
ros2 launch pinky_bringup bringup_robot.launch.xml
```

> 파일 이름은 **`bringup_robot.launch.xml`** 입니다. `bringup.launch.py` 가
> 아닙니다.

**이 터미널은 그대로 두세요.** 닫으면 로봇이 멈춥니다.

## 9. PC 에서 확인

새 터미널에서 7번을 하고:

```bash
ros2 topic list | grep -E "odom|scan|batt"
```

```
/battery/percent
/battery/voltage
/odom
/scan
```

**네 개가 다 보여야 합니다.**

- 아무것도 없음 → 도메인 확인
- `/scan` 만 없음 → 라이다가 죽음. 로봇 로그에서 `sllidar_node` 확인

---

# 무엇을 하려는가

## A. 주행 디버깅 — Foxglove

로봇에서:

```bash
ros2 launch mingky_bringup foxglove.launch.py
```

```
[foxglove_bridge]: Server listening on port 8765
```

Studio 에서 접속합니다.

| 로봇 | 주소 |
| --- | --- |
| pinky1 | `ws://192.168.0.21:8765` |
| pinky2 | `ws://192.168.0.22:8765` |

**로봇마다 도메인이 달라 한 화면에서 두 대를 동시에 볼 수 없습니다.**
접속을 갈아타야 합니다.

띄울 패널과 읽는 법은
[`nav2-debugging.md`](nav2-debugging.md) 를 보세요.

## B. waypoint 측정

```bash
ros2 run mingky_bringup run_waypoint_teleop.sh
```

RViz 와 teleop 이 뜹니다.

1. RViz 에서 **`2D Pose Estimate`** 로 로봇의 실제 위치·방향을 찍습니다
   - 라이다 점이 맵의 벽 선과 겹쳐야 합니다. 어긋나면 다시 찍으세요
2. teleop 으로 1m 쯤 움직여 위치추정을 수렴시킵니다
3. 목표 지점으로 이동하고 **완전히 정지**시킵니다
4. **찍기 전에 확인합니다** (별도 터미널, 7번 후)

   ```bash
   ros2 run mingky_bringup check_waypoints.py --probe
   ```

   `여기서 찍어도 됩니다` 가 나올 때까지 위치를 옮기세요.

5. 저장합니다

   ```bash
   ros2 run mingky_bringup capture_waypoint.sh reception
   ```

전부 찍은 뒤 검증합니다.

```bash
ros2 run mingky_bringup check_waypoints.py
```

자세한 기준은
[`../mingky_ros/mingky_bringup/README.md`](../mingky_ros/mingky_bringup/README.md)
를 보세요.

## C. pinky2 로 작업할 때

도메인과 IP 를 바꿔서 넘깁니다.

```bash
export ROS_DOMAIN_ID=20
PINKY_IP=192.168.0.22 PINKY_DOMAIN_ID=20 \
  ros2 run mingky_bringup run_waypoint_teleop.sh
```

---

# 안 될 때

| 증상 | 원인 | 확인 |
| --- | --- | --- |
| `ros2 topic list` 가 비어 있음 | 도메인 불일치 | 모든 터미널에서 `echo $ROS_DOMAIN_ID` |
| 로봇에 `ping` 안 됨 | Wi-Fi 가 `mingky` 가 아님 | `iwgetid -r` |
| `/scan` 없음 | 라이다 죽음 | 로봇에서 `ps aux \| grep sllidar` |
| `SL_RESULT_OPERATION_TIMEOUT` | **bringup 중복 실행** | `pkill -f sllidar` 후 재시작 |
| RViz 에 스캔이 안 보이고 로그에 `dropping message` | **PC 시계 미동기화** | `chronyc tracking` |
| `Package 'pinky_navigation' not found` | PC 에 빌드 안 됨 | 4번 |
| RViz mesh 에러 다발 | `pinky_description` 빌드 안 됨 | 동작에는 무관 |
| `현재 Wi-Fi는 ...` 로 스크립트가 거부 | 오래된 버전 | `git pull` 후 재빌드 |
| Wi-Fi·BLE 가 계속 끊김 | 배터리 저전압 | [`infra-setup.md`](infra-setup.md) |

## 로봇이 사라졌을 때

Wi-Fi 목록에 `pinky_XXXX` 가 보이면 로봇이 **AP 모드로 폴백**한 상태입니다.

```
1. 노트북 Wi-Fi 를 pinky_XXXX 로 전환
2. ssh pinky@10.42.0.1   (또는 192.168.4.1)
3. ~/wifi_setup.sh 로 mingky 재설정
4. 노트북을 mingky 로 되돌리고 ping 확인
```

---

# 더 볼 것

| 문서 | 내용 |
| --- | --- |
| [`infra-setup.md`](infra-setup.md) | 네트워크 구성, 시간 동기화, 배터리, 복구 절차 |
| [`nav2-debugging.md`](nav2-debugging.md) | 주행 문제 진단, 파라미터 튜닝 |
| [`../mingky_ros/mingky_bringup/README.md`](../mingky_ros/mingky_bringup/README.md) | waypoint 측정과 검증 |
