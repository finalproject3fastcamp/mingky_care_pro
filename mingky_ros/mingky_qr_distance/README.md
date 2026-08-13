# mingky_qr_distance

후방 카메라에서 환자 QR을 검출하고, Pinky별 카메라 보정값과 QR 실측 크기로
카메라에서 QR 중심까지의 직선거리(m)를 계산한다.

- 입력: `/rear_camera/image_raw`, `/rear_camera/camera_info`
- 출력: `/rear_qr/observation` (`visible`, `data`, `distance`를 한 메시지로 전달)
- `qr_size`: QR 심볼 한 변의 실측 길이. 기본값 `0.028`m

흰색 여백(quiet zone)을 제외하고 검출기가 잡는 정사각 QR 심볼 영역의 한 변을
측정해야 한다. 실제 카드가 바뀌면 반드시 이 값을 다시 실측한다.

관제 게이트웨이는 안내 상태가 `guiding`이고 디코딩한 내용이 현재 환자의
`patient_id`와 같을 때만 이 값을 관제 서버로 전달한다. 주변의 다른 QR은
환자 거리로 표시하지 않는다.
