# 핑키 접속 안내

로봇이 공유기(`mingky`)를 떠나 기관 Wi-Fi 로 옮겼습니다. **이제 IP 로 직접
붙을 수 없습니다.** 관제 서버를 거쳐 들어갑니다.

```
내 노트북 ──→ 관제 서버(클라우드) ──→ 핑키
                    ↑
            로봇이 미리 걸어둔 터널
```

로봇이 **밖으로 거는** 연결이라, 로봇이 어느 Wi-Fi 에 있든(기관망, 테더링)
같은 방법으로 붙습니다. 로봇 IP 를 알 필요가 없습니다.

---

## 1. 처음 한 번만 — 설정 파일 만들기

### 받을 것

관리자에게 두 가지를 받으세요.

| 파일 | 용도 |
| --- | --- |
| `id_rsa_oci` | 관제 서버 접속용 |
| `id_ed25519_pinky` | 로봇 접속용 |

받은 파일을 `~/.ssh/` 에 두고 권한을 잠급니다. **권한이 열려 있으면 ssh 가
키를 거부합니다.**

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
mv id_rsa_oci id_ed25519_pinky ~/.ssh/
chmod 600 ~/.ssh/id_rsa_oci ~/.ssh/id_ed25519_pinky
```

### `~/.ssh/config` 에 추가

```
# 연결 재사용. 로봇 접속은 클라우드를 거쳐 SSH 핸드셰이크를 두 번 하고
# 그게 해외 서버를 돌아온다. 매번 새로 맺으면 명령 하나에 3~4초가 걸린다.
# 첫 연결만 느리고 이후 명령은 기존 연결에 얹혀 1초 아래로 떨어진다.
Host *
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 10m
    ServerAliveInterval 30
    ServerAliveCountMax 3

Host mingky-cloud
    HostName 161.118.208.109
    User ubuntu
    IdentityFile ~/.ssh/id_rsa_oci
    IdentitiesOnly yes

Host pinky1
    HostName 127.0.0.1
    Port 22021
    User pinky
    IdentityFile ~/.ssh/id_ed25519_pinky
    IdentitiesOnly yes
    ProxyJump mingky-cloud
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

Host pinky2
    HostName 127.0.0.1
    Port 22022
    User pinky
    IdentityFile ~/.ssh/id_ed25519_pinky
    IdentitiesOnly yes
    ProxyJump mingky-cloud
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
```

```bash
chmod 600 ~/.ssh/config
```

> **`127.0.0.1` 은 로봇이 아니라 관제 서버 자신입니다.** 서버 안쪽 22021 포트가
> 로봇의 SSH 로 이어져 있습니다. 그래서 `ProxyJump` 로 서버를 먼저 거칩니다.
>
> `StrictHostKeyChecking no` 를 둔 이유는, 로봇이 재부팅하거나 망을 옮기면
> 같은 포트에 다른 호스트키가 오기 때문입니다. 없으면 매번 경고로 막힙니다.

---

## 2. 접속

```bash
ssh pinky1
ssh pinky2
ssh mingky-cloud     # 관제 서버
```

끝입니다. 파일 복사도 됩니다.

```bash
scp 파일 pinky1:~/
```

> 긴 형식(`ssh -J ubuntu@161.118.208.109 -p 22021 pinky@127.0.0.1`)도 되지만
> 로봇 키를 `-i` 로 따로 줘야 합니다. **짧은 형식을 쓰세요.**

---

## 3. 관제 서버에서 작업할 때

각자 계정이 있습니다. 비밀번호 로그인은 꺼져 있어 SSH 키로만 들어갑니다.

```bash
ssh <내계정>@mingkycarepro.site
```

**`ROS_DOMAIN_ID` 는 사람마다 다릅니다.** 직접 `export` 하지 마세요 — 접속하면
자동으로 잡힙니다. 겹치면 남이 띄운 노드가 내 `ros2 node list` 에 섞입니다.

```bash
echo $ROS_DOMAIN_ID     # 본인 번호가 나와야 합니다
```

| 번호 | 주인 |
| --- | --- |
| 20 · 21 | pinky2 · pinky1 |
| 25 | 관제컴퓨터 |
| 30~ | 팀원 (자동 배정) |

자세한 것은 저장소의 `docs/cloud-dev-server.md` 를 보세요.

---

## 4. 로봇에서 무엇이 자동으로 도는가

전원만 켜면 다음이 부팅과 함께 뜹니다. **따로 실행할 필요가 없습니다.**

| 서비스 | 역할 |
| --- | --- |
| `mingky-ssh-tunnel` | 이 접속 경로를 여는 터널 |
| `mingky-gateway` | 이벤트·heartbeat 를 관제로, 명령을 받아옴 |
| `mingky-teleop-bridge` | 대시보드 실시간 조작·위치 |
| `mingky-camera-tunnel` | 전·후방 카메라 미리보기 역터널 |
| `fg-bridge` · `fg-tunnel` | Foxglove 원격 관측 |
| `fg-teleop` | 주행 모드·속도 상한 |

```bash
systemctl status mingky-ssh-tunnel mingky-camera-tunnel
journalctl -u mingky-gateway -n 50
```

주행 스택(Nav2, 라이다, 모터)은 자동이 **아닙니다.** 필요할 때 띄웁니다.

```bash
ros2 launch pinky_bringup bringup_robot.launch.xml   # 모터·라이다
ros2 launch mingky_bringup twist_mux.launch.py       # 명령 중재기 (없으면 안 움직임)
```

---

## 5. 안 될 때

### `ssh pinky1` 이 멈추거나 timeout

로봇 전원과 인터넷을 먼저 의심하세요. **관제 대시보드에서 확인하는 게 가장 빠릅니다.**

<https://mingkycarepro.site/medical> 에서 해당 로봇이 `online` 인지 봅니다.
`offline` 이면 로봇이 꺼졌거나 인터넷이 끊긴 것이고, SSH 로도 못 들어갑니다.

```bash
# 서버에서 터널이 살아 있는지
ssh mingky-cloud 'ss -tln | grep 2202'
```

`22021`(pinky1) · `22022`(pinky2) 가 보여야 합니다.

### `Permission denied (publickey)`

키가 `~/.ssh/` 에 없거나 권한이 열려 있습니다. 1번을 다시 확인하세요.
긴 형식으로 접속했다면 `-i ~/.ssh/id_ed25519_pinky` 를 빠뜨린 것입니다.

### 로봇이 AP 모드로 떨어졌을 때

`ssh pinky1` 이 안 되고 대시보드에도 `offline` 이면, 로봇이 Wi-Fi 에 못 붙어
**자기 AP 를 띄운** 상태일 수 있습니다. 주변에 `pinky_XXXX` SSID 가 보이면
그 상태입니다.

```
1. 노트북 Wi-Fi 를 pinky_XXXX 로 전환
2. ssh pinky@10.42.0.1        (안 되면 192.168.4.1)
3. sudo nmcli con up FASTCAMPUS_10F
4. 재부팅 후 자동으로 붙는지 확인
```

**4번을 꼭 하세요.** 재부팅 후 안 붙으면 설정이 저장되지 않은 것이고 다음에
또 터집니다. 실제로 이 문제로 로봇 한 대가 접근 불가가 된 적이 있습니다 —
`FASTCAMPUS_10F` 의 autoconnect 가 꺼져 있고 이미 없어진 `mingky` 가 우선순위
100 이라, 재부팅 후 없는 망만 찾다 실패한 경우였습니다.

```bash
# 현재 설정 확인 — FASTCAMPUS_10F 가 yes / 100 이어야 한다
nmcli -f NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY con show
```

### 접속이 느리게 느껴질 때

로봇이 느린 게 아니라 경로가 깁니다. 클라우드를 거쳐 해외를 돌아오므로 새
연결 하나에 3~4초가 걸립니다. 위 `ControlMaster` 설정이 있으면 두 번째
명령부터 1초 아래로 떨어집니다.

CPU 는 대개 한가합니다. 실제로 무거운지는 로봇에서 확인하세요.

```bash
uptime && free -h
```

### 배터리 때문에 로봇이 죽는 경우

**전압이 6.8V 아래로 내려가면 통신이 끊기다 꺼집니다.** 대시보드에서 배터리가
0% 로 보이거나 `comm_lost` 가 반복되면 충전 상태를 물리적으로 확인하세요.
충전기에 물려 있는데도 전압이 안 오르면 접점 문제입니다.

```bash
battery      # 로봇에서 직접 확인
```

정상 만충은 **8.1V 대 / 100%** 입니다.
