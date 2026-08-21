# Mingky LCD Status

`/guide_manager/state`를 구독해 Pinky의 320x240 LCD에 환자 안내 상태를
표시합니다. 환자 확인, 목적지 주행, 검사실 도착, 대기 장소 이동·도착,
안내 완료와 안전 정지를 의료진 화면 없이 확인할 수 있습니다.

자동 모드에서 세션이 없는 대기·충전 상태는 `idle_brightness`(기본 10%)로
낮춥니다. 안내, 수동 조작, 경고와 화재 대피 중에는
`active_brightness`(기본 100%)로 자동 복원됩니다.

LCD SPI/GPIO는 한 프로세스만 소유해야 합니다. 이 노드를 실행할 때 기존
`pinky_emotion emotion_server`를 동시에 실행하지 마세요.

한글이 네모로 보이면 로봇에 글꼴을 설치합니다.

```bash
sudo apt install fonts-noto-cjk
```

통합 launch에서 기본 실행됩니다. 필요할 때만 끌 수 있습니다.

```bash
ros2 launch mingky_bringup mingky_system.launch.xml start_lcd_status:=false
```
