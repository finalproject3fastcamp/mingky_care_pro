# mingky_qr_reader

Pinky 카메라에서 환자 카드 QR을 인식해 백엔드(`POST /qr/scan`)로 `patient_id`를 전달하는 ROS2 노드.

스펙 배경: [`../../docs/monitoring-spec.md`](../../docs/monitoring-spec.md) 1장 · 5장

## 노드

`qr_reader_node`

### 파라미터

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `source` | `image` | `image` / `usb` / `csi`(미구현) |
| `image_path` | `""` | `source=image` 일 때 읽을 정적 이미지 경로 |
| `usb_device_index` | `0` | `source=usb` 일 때 `/dev/videoN` 인덱스 |
| `backend_url` | `http://localhost:8000` | FastAPI 백엔드 주소 |
| `fps` | `10.0` | 프레임 처리 주기 |
| `debounce_seconds` | `5.0` | 같은 값 연속 스캔 무시 구간 |
| `http_timeout_seconds` | `3.0` | 백엔드 요청 타임아웃 |

## 샘플 QR / 인쇄용 카드

개발용 샘플 이미지는 `samples/p001.png`, `p002.png`, `p003.png` (870×870, ECC=H).
seed 데이터 `p001~p003` 과 매칭.

예시 (`p001.png`):

<img src="samples/p001.png" alt="p001 QR" width="200" />

인쇄용 카드 PDF는 `samples/cards.pdf` — A4 한 장에 사원증 사이즈(85×54mm) 카드 3장, 컷 마크 포함. Adobe Reader 등에서 **100% 스케일**로 인쇄한 뒤 컷 마크를 따라 자르면 된다.

카드 재생성:

```bash
cd mingky_ros/mingky_qr_reader
pip install -r scripts/requirements.txt
python3 scripts/generate_cards.py
```

한글 폰트는 macOS(AppleGothic), Ubuntu(NanumGothic / NotoSansCJK) 순으로 자동 탐색한다. Ubuntu에선 `sudo apt install fonts-nanum` 권장.

## 실행 예시

정적 이미지로:

```bash
ros2 run mingky_qr_reader qr_reader_node --ros-args \
  -p source:=image \
  -p image_path:=$(ros2 pkg prefix mingky_qr_reader)/share/mingky_qr_reader/samples/p001.png
```

USB 웹캠으로:

```bash
ros2 run mingky_qr_reader qr_reader_node --ros-args -p source:=usb
```

launch 파일로:

```bash
# 기본 (source=image, samples/p001.png)
ros2 launch mingky_bringup qr_reader.launch.py

# USB 웹캠으로 오버라이드
ros2 launch mingky_bringup qr_reader.launch.py source:=usb

# 다른 이미지로
ros2 launch mingky_bringup qr_reader.launch.py \
  image_path:=/absolute/path/to/foo.png

# 백엔드 주소 변경
ros2 launch mingky_bringup qr_reader.launch.py \
  backend_url:=http://192.168.0.10:8000
```

## 의존성

- `python3-opencv`
- `python3-requests`
- `python3-pyzbar` (내부적으로 `libzbar0` 시스템 패키지 필요)
