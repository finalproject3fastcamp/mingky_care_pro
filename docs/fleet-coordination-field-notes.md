# 군집 조정을 실제 로봇에 올려본 기록

[`fleet-coordination.md`](fleet-coordination.md) 가 **왜 그렇게 만들었는가** 라면,
이 문서는 **그걸 진짜 핑키에 올렸더니 무슨 일이 있었는가** 입니다.

2026-08-23, 핑키 두 대에 `feat/fleet-coordination` 을 올려 개발 PC 의 백엔드에
붙였습니다.

여기 적은 것은 대부분 **코드 문제가 아니라 환경이 문서와 달라서** 생긴 일입니다.
그중 하나(§1 의 DDS)는 하루의 절반을 먹었고, 다섯 가지 다른 증상으로 위장하고
있었습니다. **다른 것을 다 건너뛰더라도 §1 만은 읽어 두시길 권합니다.**

---

## 1. DDS 공유메모리 — 오늘의 진범

### 무엇이 보였나

증상이 하루 종일 다섯 가지로 나타났습니다. 처음에는 전부 다른 문제로 보였습니다.

| 겉으로 보인 증상 | 실제로는 |
|---|---|
| `/mode` 가 `None` 이라 위치추정이 거부됨 | `mode_manager` 는 정상 발행 중. 다른 프로세스가 못 받음 |
| `amcl이 20초 안에 active 상태가 되지 않았습니다` | `amcl` 은 이미 `active`. 물어볼 길이 없었음 |
| `battery_stale` 로 arming 거부 (5024초 경과) | `adc_reader` 는 5초마다 발행 중. 게이트웨이가 못 받음 |
| `map→base_link` TF 소실 | 컨테이너가 발행 중. 브리지가 못 받음 |
| `ros2 node list` 가 10개만 (다른 대는 36개) | 나머지 34개는 살아 있으나 고립 |

로그에는 이것이 계속 흘렀습니다.

```
RTPS_TRANSPORT_SHM Error: Failed init_port fastrtps_port7011:
open_and_lock_file failed -> Function open_port_internal
```

### 왜 어려웠나 — 한 줄로 말하면

> **프로세스 *안* 에서는 멀쩡하고, 프로세스를 *넘을 때만* 끊깁니다.**

그래서 각 노드의 로그만 보면 전부 정상입니다. `nav2_container` 안에서
`lifecycle_manager` 가 `amcl` 을 잘 켜고 `Managed nodes are active` 까지 찍는데,
바깥의 `auto_localize_node` 는 그 `amcl` 에 닿지 못해 "active 가 안 됐다" 고
판단합니다. **두 로그가 모두 사실이고 서로 모순됩니다.**

이 대조가 결정적인 단서였습니다.

```
mode_manager        "모드 관리 시작 (현재 auto)"      ← 발행 중
teleop_limiter      "모드 수신: None → auto"         ← 같은 프로세스라 받음
auto_localize_node  받지 못함                        ← 다른 프로세스
```

`teleop_limiter` 와 `mode_manager` 는 같은 런치(`fg-teleop`)의 형제라서 통하고,
프로세스를 넘는 순간 끊깁니다.

### 원인을 어떻게 좁혔나

가설을 하나씩 배제했습니다. **배제 과정 자체가 다음 사람에게 쓸모 있으므로**
실패한 가설도 남깁니다.

**가설 1 — `/dev/shm` 이 꽉 찼다?**

```bash
df -h /dev/shm
#  tmpfs  3.9G  19M  3.9G  1%
```

아닙니다. 3.9G 중 19M 만 씁니다. fastrtps 세그먼트가 216개였지만 각 549 KB 라
용량으로는 여유가 넘칩니다.

**가설 2 — 파일 디스크립터 한도(1024)를 넘었다?**

세그먼트가 216개니 그럴듯했습니다. 그런데 실제 프로세스를 보면:

```bash
grep "Max open files" /proc/$(pgrep -f component_container_isolated)/limits
#  Max open files  1024  524288
ls /proc/<pid>/fd | wc -l
#  41
```

1024 중 41개입니다. 아닙니다. **셸의 `ulimit -n` 이 아니라 프로세스의
`/proc/<pid>/limits` 를 봐야 합니다** — systemd 서비스는 셸 한도를 물려받지
않습니다.

**가설 3 — 죽은 프로세스가 세그먼트를 잠근 채 남았다?**

FastDDS 의 알려진 실패 방식입니다. 그런데 재부팅하면 `/dev/shm` 이 비워지는데도
재발했습니다. 아닙니다.

**가설 4 — 기동 시 경합.** ← 이것이었습니다.

부팅 때 서비스 다섯 개가 **같은 초에** 뜹니다.

```
Sun 2026-08-23 20:52:48 KST   mingky-system
Sun 2026-08-23 20:52:48 KST   fg-teleop
Sun 2026-08-23 20:52:48 KST   mingky-gateway
Sun 2026-08-23 20:52:48 KST   mingky-teleop-bridge
Sun 2026-08-23 20:52:48 KST   mingky-battery-pub
```

이들이 같은 SHM 포트를 두고 경합하다 일부가 `init_port` 에 실패하고, **실패한
참가자는 그 뒤로 복구하지 않습니다.** 서비스를 하나씩 재시작하면 (그때는 혼자
뜨므로) 잠깐 낫는데, 이 관찰이 가설을 확정해 주었습니다.

```
sudo systemctl restart fg-teleop          → /mode 가 통하기 시작
sudo systemctl restart mingky-system      → 노드 10개 → 26개, amcl 보임
sudo systemctl restart mingky-gateway     → 배터리 값이 올라오기 시작
```

증상마다 다른 서비스를 재시작해야 했던 것이 같은 병의 다른 얼굴이었습니다.

### 해결 — SHM 을 끄고 UDP 만 쓴다

경합의 원인 자체를 없앱니다. 프로파일은 저장소에 있습니다:
[`deploy/robot/fastdds-udp-only.xml`](../deploy/robot/fastdds-udp-only.xml)

```xml
<participant profile_name="udp_only_participant" is_default_profile="true">
  <rtps>
    <userTransports><transport_id>udp_only_transport</transport_id></userTransports>
    <useBuiltinTransports>false</useBuiltinTransports>
  </rtps>
</participant>
```

`useBuiltinTransports` 를 `false` 로 두는 것이 요점입니다. `true` 면 SHM 이 다시
끼어듭니다.

로봇마다 이렇게 넣습니다.

```bash
sudo install -m 644 -o root -g root \
  ~/mingky_care_pro/deploy/robot/fastdds-udp-only.xml /etc/mingky/fastdds-udp-only.xml
echo 'FASTRTPS_DEFAULT_PROFILES_FILE=/etc/mingky/fastdds-udp-only.xml' \
  | sudo tee -a /etc/mingky/robot.env
sudo reboot
```

**재부팅이 필요합니다.** 지금 도는 모든 프로세스가 새 프로파일로 다시 떠야 하고,
재부팅하면 `/dev/shm` 의 묵은 세그먼트도 같이 정리됩니다.

> 환경변수 이름이 헷갈립니다. ROS 2 Jazzy 는 Fast DDS **2.14** 라
> `FASTRTPS_DEFAULT_PROFILES_FILE` 입니다. Fast DDS 3.x 부터 쓰는
> `FASTDDS_DEFAULT_PROFILES_FILE` 로 적으면 **조용히 무시됩니다.**

### 효과

```
                이전            이후
pinky1 노드     10개            44개
pinky2 노드     19개            44개
모드            전달 안 됨       auto 전달됨
SHM 오류        계속 발생        0건
위치 보고       한 대만          두 대 다
위치추정        간헐 실패        두 대 다 한 번에 성공
```

하루 종일 안 되던 것들이 **한 번에** 됐습니다. 적용 여부는 이렇게 확인합니다.

```bash
# 프로파일이 프로세스에 물렸나 (1 이면 OK)
p=$(pgrep -f mingky_system.launch | head -1)
tr '\0' '\n' < /proc/$p/environ | grep -c FASTRTPS_DEFAULT_PROFILES_FILE

# SHM 을 안 쓰나 (0 이면 OK)
ls /dev/shm | grep -c fastrtps

# 부팅 이후 SHM 오류 (0 이면 OK)
journalctl -u mingky-system --no-pager -b | grep -c open_and_lock_file
```

### 대가와 되돌리기

같은 기계 안의 통신이 UDP 루프백을 탑니다. 이 구성에서 DDS 로 오가는 것은
스캔(10 Hz · 수 KB)과 상태 토픽이라 부담이 되지 않습니다. **카메라는 DDS 가
아니라 별도 스트리머와 역터널로 나가므로 영향이 없습니다.**

되돌리려면 `robot.env` 의 그 한 줄을 지우고 재부팅하면 기본(SHM + UDP)으로
돌아옵니다.

### 더 나은 처방이 있다면

SHM 은 큰 메시지에서 UDP 보다 빠릅니다. 카메라를 DDS 로 옮기거나 포인트클라우드를
다루게 되면 이 선택을 다시 봐야 합니다. 그때는 SHM 을 살리되 **서비스 기동을
흩뜨리는 쪽**(`ExecStartPre=/bin/sleep`, 또는 `After=` 사슬)을 먼저 시도해
보시길 권합니다. 경합이 원인이므로 그것만 없애도 됩니다.

---

## 2. 어디까지 됐나

### 실물에서 확인된 것

**조정층 배선이 실제 로봇에서 성립합니다.** 이 브랜치의 핵심입니다.

```
guide_manager  구독 /fleet/decision              ← 서버 판정을 실제로 받음
               발행 /guide_manager/fleet_intent  ← 목표를 서버에 올림
서버           두 로봇에 방을 갈라 배정
               pinky-01 → 임상병리실,  pinky-02 → X-ray
```

**주행도 한 번 완주했습니다.**

```
[1.121, 0.058] → [1.040, 0.196] → [1.018, 0.181] → "충전소 도착"
```

Nav2 목표 수신 → 실제 이동 → 도착까지 갔습니다. **주행 스택 자체에는 문제가
없습니다.**

그 밖에 확인된 것:

- 두 대에 브랜치 배포 + 28개 패키지 빌드
- 두 대 다 자동 위치추정 성공 (`auto_localize` 5 cm 프로브)
- 실제 로봇 위치가 대시보드 지도까지 도달 (`x=1.249 y=0.187`)
- 적응형 경로 복구 실기 동작 — 후보 4개를 평가해 하나를 거부하고 `left_075` 선택
- 세션 생명주기 정상 — 취소 → 충전소 복귀 → `idle`

### 아직 못 본 것

**주행 중의 실제 양보.** 두 대가 움직이면서 서로 비켜주고 화면에 배지가 뜨는
장면입니다. §7 에 지금 막힌 지점을 적었습니다.

---

## 3. 로봇에 접속하기

### 문서가 두 벌입니다

[`robot-onboarding.md`](robot-onboarding.md) 의 `192.168.0.21/22` 는 **옛
`mingky` 공유기 시절 주소**입니다. 로봇이 기관 Wi-Fi 로 옮기면서 무효가 됐는데
그 문서가 그대로 남아 있습니다.

최신은 [`team-robot-access.md`](team-robot-access.md) 이고, 핵심은 이겁니다.

> 로봇이 **밖으로 거는** 연결이라, 로봇이 어느 Wi-Fi 에 있든 같은 방법으로
> 붙습니다. **로봇 IP 를 알 필요가 없습니다.**

저는 이걸 모르고 망을 훑었습니다. 시간 낭비였습니다.

### 같은 망에 있다면 직접 붙는 편이 빠릅니다

클라우드를 거치면 해외를 돌아오느라 새 연결 하나에 3~4초가 걸립니다.

```
Host pinky1-lan
    HostName 192.168.129.24
    User pinky
    IdentityFile ~/.ssh/id_ed25519_pinky_local
    IdentitiesOnly yes
```

IP 는 DHCP 라 바뀝니다. 안 붙으면 MAC 으로 찾으세요. 라즈베리파이 5 는
`2c:cf:67` 대역이라 한 줄이면 걸립니다.

```bash
ip neigh | grep 2c:cf:67
```

**포트를 훑어서 찾으려 하지 마세요.** 교육장에는 다른 팀 로봇이 여러 대 있고,
SSH 배너로는 남의 우분투 노트북과 구분이 안 됩니다. MAC 대역이 유일하게
확실합니다.

### 두 로봇을 눈으로 구분하기

**본체에 라벨이 없습니다.** 화면의 `pinky-01` 과 눈앞의 실물을 잇는 유일한
표식은 각 로봇이 띄우는 AP 이름입니다.

```
pinky1   wlan0 2c:cf:67:aa:62:95   →  pinky_6294
pinky2   wlan0 2c:cf:67:e8:15:e4   →  pinky_15e2
```

SSID 는 무선 칩 MAC 의 끝 두 옥텟에서 나오는데, **AP 인터페이스 MAC 이 station
MAC 과 한두 값 차이가 나서 정확히 일치하지는 않습니다**(`62:95→6294`,
`15:e4→15e2`). 그래서 계산하지 말고 실측해서 외우는 편이 낫습니다.

> 이 AP 가 보인다고 로봇이 AP 모드로 떨어진 것은 아닙니다. 기관 Wi-Fi 에 정상
> 접속한 상태에서도 자기 AP 를 같이 띄웁니다. 저는 처음에 이걸 장애로 오진해
> 한참 헤맸습니다.

### `ssh-copy-id` 가 "이미 있다" 고 해도 믿지 마세요

`~/.ssh/config` 에 `ControlMaster auto` 가 있으면 열린 연결을 재사용합니다.
그 상태로 `ssh-copy-id` 를 돌리면 **키가 없는데도** "already exist" 로 끝납니다.

```bash
ssh -o ControlPath=none -o BatchMode=yes pinky1-lan hostname
```

그리고 키 이름이 기본값이 아니면 ssh 가 자동으로 시도하지 않습니다.
`IdentityFile` 로 박아두세요.

---

## 4. 로봇에서 빌드하기

### 워크스페이스는 저장소 루트입니다

개발 PC 에서는 `mingky_ros/` 가 colcon 워크스페이스지만 **로봇은 다릅니다.**

```
/home/pinky/mingky_care_pro/install/             ← 서비스가 보는 곳
/home/pinky/mingky_care_pro/mingky_ros/install/  ← 여기 지으면 안 보입니다
```

엉뚱한 곳에 지으면 `Package 'mingky_fleet_agent' not found` 로 10초마다
재시작합니다. **유닛의 `ExecStart` 를 먼저 읽으면 5초에 알 수 있는 일입니다.**

### 필요한 것만 골라 짓지 마세요

4개만 지었더니 `mingky_bringup` 이 옛 빌드로 남아 `camera_power_manager.py` 가
없었고, 런치가 그걸 찾다가 **288번 재시작**했습니다.

```bash
cd ~/mingky_care_pro
unset VIRTUAL_ENV PYTHONHOME       # ← 이게 빠지면 empy 로 죽습니다
source /opt/ros/jazzy/setup.bash
source ~/pinky_pro/install/local_setup.bash
colcon build                        # Pi 5 에서 28개가 1분 30초
```

`unset VIRTUAL_ENV` 는 꼭 넣으세요. PATH 에서 venv 를 빼는 것만으로는 부족합니다.
`VIRTUAL_ENV` 가 남아 있으면 cmake 가 그쪽 python 을 잡는데 거기엔 `empy` 가
없어서 `No module named 'em'` 로 죽습니다.

---

## 5. 서비스가 남의 코드로 뜬다면

`install.sh` 를 돌리고 재시작까지 했는데 계속 옛 코드로 뜨는 일이 있었습니다.
원인은 systemd drop-in 이었습니다.

```
/etc/systemd/system/mingky-system.service.d/99-yunseo-private.conf
```

이 파일이 `ExecStart=` 로 원본을 지우고 다른 워크스페이스를 쓰게 재정의합니다.
**drop-in 이 항상 이깁니다.**

```bash
# 지금 이 서비스가 실제로 무엇을 실행하는가
p=$(pgrep -f "mingky_system.launch" | head -1)
tr '\0' '\n' < /proc/$p/environ | grep AMENT_PREFIX_PATH | tr ':' '\n' \
  | grep -oE '/home/pinky/[a-z]*/?mingky_care_pro' | sort -u

ls /etc/systemd/system/mingky-*.service.d/
```

저장소의 [`mingky-private-runtime`](../deploy/robot/bin/mingky-private-runtime)
이 이 전환을 위한 도구입니다. 원래는 `/run` 에 만들어 **재부팅하면 사라지는** 게
설계인데, 실제로는 `/etc` 에 영구로 박혀 있었습니다.

**남의 설정이니 지우지 말고 옮기고, 주인에게 먼저 물어보세요.**

---

## 6. 로봇이 이상하게 굴 때 먼저 볼 것

### "충전소로 복귀중" 이 떴다면 배터리를 의심하기 전에

이틀 전 시험 때 만든 세션이 `ended_at IS NULL` 로 남아 있었습니다. 진짜 로봇이
붙자마자 그걸 물었고, 환자가 실제로는 없으니 정책대로 안내를 접고 복귀했습니다.
LCD 에는 "충전소로 복귀중" 만 뜹니다.

```sql
SELECT session_id, robot_id, started_at FROM guidance_sessions WHERE ended_at IS NULL;
```

취소할 때 `reason` 은 **`aborted` · `robot_offline` · `system_failure`** 중
하나여야 합니다. 다른 문자열은 `알 수 없는 세션 취소 사유를 무시합니다` 로 조용히
버려집니다.

```bash
ros2 topic pub --once /guide_manager/cancel_session std_msgs/msg/String \
  '{data: "{\"session_id\": 13, \"reason\": \"aborted\"}"}'
```

### `paused` 에서 안 풀린다면 — 버그가 아닙니다

```python
if failed_reason == 'guidance_canceled':
    # 복귀 최종 실패 뒤 재배정되면 현재 위치를 보장할 수 없다.
    self.robot_state = GuideState.ROBOT_PAUSED
```

**안전장치입니다.** 백엔드도 `paused` 로봇에는 환자를 배정하지 않습니다. 푸는
방법은 복귀를 성공시키는 것입니다.

```bash
curl -X POST localhost:8000/robots/pinky-02/orders \
  -H 'Content-Type: application/json' \
  -d '{"command":"goto","argument":"charging_station_2"}'
```

### 로봇이 세션 번호를 모른다면

`start_session` 명령은 `patient_id` 만 설정하고 **`session_id` 는 0 으로
남깁니다.** 코드 주석이 "임시 입구. 나중에 QR 노드가 대체한다" 라고 적어 둔
그대로입니다. 그래서 `start_guidance` 를 보내면 이렇게 거부됩니다.

```
출발 명령 세션 불일치: 요청=14, 현재=0
```

세션 번호는 **QR 경로로만** 들어옵니다. QR 없이 시험하려면 QR 노드가 만드는
메시지를 직접 넣습니다.

```bash
ros2 topic pub --once /qr_reader_node/session_start \
  mingky_interfaces/msg/SessionStart \
  "{session_id: 14, patient_id: p001, current_step_order: 1,
    visit_names: [X-ray, 임상병리실, 물리치료실]}"
```

### 환자 없이 주행시키려면

환자가 없으면 20초 뒤에 안내를 접고 복귀합니다(#136). 고장이 아닙니다.

```
환자를 20.4초 동안 확인하지 못해 안내 세션 14를 종료하고 충전소로 복귀합니다
```

재부팅 없이 런타임 파라미터로 끌 수 있습니다.

```bash
ros2 param set /guide_manager patient_follow_enabled false
```

`robot.env` 의 `MINGKY_PATIENT_FOLLOW_ENABLED` 를 고치는 방법도 있지만 재시작이
필요하고, 재시작하면 위치추정이 날아갑니다. **시험 중에는 파라미터 쪽이
낫습니다.**

### 환자 배정이 거부된다면 — 관문이 넷입니다

```
POST /qr/scan  →  robot not armed          → POST /robots/{id}/arm
               →  battery_low (40% 미만)    → 충전
               →  battery_stale (300초)     → 게이트웨이가 배터리를 못 받는 것
               →  robot unavailable ...     → paused/returning 해제
```

`arm` 은 "의료진이 이 핑키를 지금 쓰겠다" 는 안전 인터록입니다. armed 가 아니면
로봇은 QR 조차 디코드하지 않습니다.

### 주행이 아예 안 된다면 — 이것부터

```bash
ros2 run tf2_ros tf2_echo map base_link
```

이게 안 나오면 위치추정이 안 된 것이고, 그러면 Nav2 는 무슨 목표를 줘도
거부합니다. 로그에는 `충전소 복귀 시도 1/3 → Nav2 가 목표를 거부했습니다` 가
반복될 뿐이라 원인이 잘 안 보입니다.

위치추정은 모드가 `auto` 여야 시작합니다. 프로브는 **5 cm 씩 최대 3회**, 앞
20 cm 에 장애물이 있으면 멈춥니다.

### 명령 큐를 들여다볼 때

`GET /robots/{id}/orders/next` 는 **꺼내 보기만 하고 지우지 않습니다.** 주석이
이유를 적어 두었습니다 — 응답이 무선에서 유실되면 로봇은 못 받았는데 서버는
보냈다고 믿게 되므로, 지우는 것은 로봇이 `ack` 를 보냈을 때뿐입니다.

> 저는 이걸 모르고 "진단하다 큐를 먹었다" 고 한동안 오해했습니다. 코드를 읽고서야
> `peek` 인 것을 알았습니다. **엔드포인트 이름만 보고 부작용을 넘겨짚지 마세요.**

대기 중인 명령 전체를 보려면 `GET /robots/orders/pending` 입니다.

---

## 7. 배터리 — 표시를 그대로 믿지 마세요

두 로봇 모두에서 봤습니다.

핑키1은 실측 **99.0% (7.59V)** 인데 DB 에는 **6.83V** 로 기록됐고, 그 값 때문에
세션이 `end_reason=battery` 로 종료됐습니다. **만충인 로봇이 배터리 사유로 안내를
접은 겁니다.**

주행 중에는 모터 부하로 전압이 순간 처지는데, 그 한 표본이 위험선(6.8V)을 스치면
그대로 판정됩니다. `adc_reader` 는 5회 중앙값을 쓰지만(`배터리 5.0초 주기
(5회 중앙값)`), 판정 쪽에는 그런 완화가 없습니다.

**충전은 손으로 꽂는 것보다 도킹이 확실합니다.** 핑키2 는 충전기를 물리고 10분을
지켜봐도 전압이 오르지 않다가(33% → 23%), 로봇이 스스로 충전소로 주행해 도킹하니
51% 까지 올랐습니다. 접점을 사람 손으로 맞추기 어려운 것으로 보입니다.

측정에 `0` 이 섞이는 것은 별개 신호입니다 —
[`team-robot-access.md`](team-robot-access.md) 는 그걸 접점 문제로 봅니다.

---

## 8. 머지 전에 로컬 백엔드로 시험하기

```bash
# 개발 PC — 127.0.0.1 이 아니라 전체 인터페이스에 열어야 합니다
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 로봇 — 도달하는지부터
curl -m5 http://<개발PC>:8000/health

# 로봇 — 바라볼 곳을 바꿉니다
sudo sed -i 's|^MINGKY_BACKEND_URL=.*|MINGKY_BACKEND_URL=http://<개발PC>:8000|' \
  /etc/mingky/robot.env
sudo systemctl restart mingky-gateway mingky-teleop-bridge mingky-fleet-agent
```

`FASTCAMPUS_10F` 는 단말 간 통신을 막지 않았고 `ufw` 도 통과했습니다. 다만 **이
백엔드에는 인증이 없습니다.** 공용 망에서 포트를 열어야 한다면 대역 전체가 아니라
로봇 두 대의 IP 만 허용하세요.

### 설정을 바꿨는데 안 먹는다면

`fleet_agent` 는 연결 실패를 **프로세스 안에서 재시도**합니다. 죽지 않으니
`Restart=always` 가 발동하지 않고, 그래서 `EnvironmentFile` 을 다시 읽을 일이
없습니다. **명시적으로 재시작해야 합니다.**

옛 URL 을 쥐고 있으면 프로덕션 nginx 가 `403 Forbidden` 을 줍니다 — 그 브랜치의
경로가 아직 거기 없기 때문입니다. 로그에 `nginx/1.24.0` 이 보이면 그겁니다.

### 되돌리기

```bash
sudo sed -i 's|^MINGKY_BACKEND_URL=.*|MINGKY_BACKEND_URL=https://mingkycarepro.site/api|' \
  /etc/mingky/robot.env
sudo systemctl restart mingky-gateway mingky-teleop-bridge mingky-fleet-agent
cd ~/mingky_care_pro && git checkout main && git stash pop
```

---

## 9. 지금 막혀 있는 것

**출발 명령이 로봇에 닿지 않습니다.**

```
백엔드      POST /robots/pinky-01/orders  start_guidance(14)  → 201
게이트웨이   "명령 실행" 로그 0건
guide_manager 반응 없음
```

전제조건은 다 갖춰져 있습니다 — 두 대 다 44 노드, 위치추정 완료, 링크 연결,
환자추종 꺼짐, 핑키1 배터리 70%.

**원인은 아직 못 밝혔습니다.** 처음에는 제가 `orders/next` 로 큐를 먹은 줄
알았는데, 코드를 읽어 보니 그건 `peek` 이라 무해했습니다(§6). 그러니 다른
곳입니다.

단서가 둘 있습니다. `pending` 이 비어 있었다는 것은 **명령이 어딘가에서
소비됐다**는 뜻이고, 게이트웨이는 `명령 실행` 을 안 찍었습니다. 그리고 게이트웨이
로그에 이것이 간헐적으로 흐릅니다.

```
heartbeat 실패: ('Connection aborted.', RemoteDisconnected(...))
heartbeat 복구
```

다음에 볼 순서:

1. `start_guidance` 를 보내고 **즉시** `GET /robots/orders/pending` 으로 큐에
   실제로 들어갔는지 확인 (백엔드가 받았는가)
2. 게이트웨이 로그에서 `명령 실행: start_guidance(14)` 를 기다린다 (로봇이
   가져갔는가)
3. 가져갔는데 반응이 없으면 `guide_manager` 쪽 거부 사유를 본다 —
   `_reject_start_guidance` 가 이유를 로그로 남긴다
4. 애초에 안 가져가면 롱폴링(`MINGKY_ORDER_WAIT=25.0`)과 위 `heartbeat 실패` 를
   함께 조사한다. 같은 HTTP 연결이 끊기는 것이라면 두 증상의 뿌리가 같다

---

## 10. 돌아보며

시간을 많이 썼는데, 거의 다 **로봇 환경을 확인하지 않고 추측한 탓**이었습니다.

| 걸린 것 | 확인했으면 |
|---|---|
| IP 를 못 찾아 망을 훑음 | `team-robot-access.md` 를 먼저 읽었으면 |
| 빌드가 서비스에 안 잡힘 | 유닛의 `ExecStart` 를 먼저 읽었으면 |
| 288번 크래시 | 처음부터 전체를 지었으면 |
| 재시작해도 옛 코드 | `*.service.d/` 를 먼저 봤으면 |
| "복귀중" 을 배터리로 오진 | `ended_at IS NULL` 을 먼저 조회했으면 |
| 증상마다 서비스 재시작 | **공통 원인(DDS)을 더 일찍 의심했으면** |
| `orders/next` 를 소비형으로 단정 | 코드를 먼저 읽었으면 (실제로는 `peek`) |

끝의 두 줄이 제일 아깝습니다. 다섯 가지 증상이 **한 서비스를 재시작할 때마다
하나씩 나았다**는 패턴을 더 일찍 읽었다면, 개별 증상을 쫓는 대신 전송 계층을
먼저 봤을 겁니다.

그리고 마지막 줄은 종류가 다른 실수입니다 — **틀린 원인을 잠시 사실로 믿었고,
그대로 문서에 적을 뻔했습니다.** 대조하다 잡았습니다. 원인을 적을 때는 근거가
코드에 있는지 확인하는 편이 낫습니다.

---

## 11. 남은 순서

```
1. 출발 명령 경로 규명 (§9)
2. 핑키1 단독 — X-ray 까지 주행
3. 핑키2 충전 (도킹으로)
4. 두 대 동시 — 양보 · 검사실 재정렬 · 충전소 블로킹
```

[`fleet-coordination.md` §11](fleet-coordination.md#11-남은-것) 의 미검증 항목 중
**AMCL 오차 대 구간 판정** 은 여전히 못 봤습니다. 두 대가 실제로 좁은 구간을
지나봐야 나오는 숫자입니다.
