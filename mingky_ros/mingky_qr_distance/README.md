# mingky_qr_distance

후방 카메라에서 환자 QR을 검출하고, Pinky별 카메라 보정값과 QR 실측 크기로
카메라에서 QR 중심까지의 직선거리(m)를 계산한다.

- 입력: `/rear_camera/image_raw`, `/rear_camera/camera_info`
- 출력: `/rear_qr/observation` (`visible`, `data`, `distance`, `center_x`,
  `center_y`를 한 메시지로 전달)
- `qr_size`: QR 심볼 한 변의 실측 길이. 기본값 `0.028`m

흰색 여백(quiet zone)을 제외하고 검출기가 잡는 정사각 QR 심볼 영역의 한 변을
측정해야 한다. 실제 카드가 바뀌면 반드시 이 값을 다시 실측한다.

관제 게이트웨이는 안내 상태가 `guiding`이고 디코딩한 내용이 현재 환자의
`patient_id`와 같을 때만 이 값을 관제 서버로 전달한다. 주변의 다른 QR은
환자 거리로 표시하지 않는다.

통합 실행에서는 `process_only_while_guiding=true`로 실행한다. 노드는 계속
살아 있지만 안내 세션이 아닐 때는 영상 변환과 QR 검출을 생략하며,
`guiding` 전환 뒤 다음 프레임부터 즉시 검출한다. 단독 카메라 보정 시험에서는
기본값 `false`를 사용하므로 안내 세션 없이도 계속 검출할 수 있다.

`center_x`, `center_y`는 검출된 QR 네 꼭짓점의 영상 픽셀 중심이다.
`mingky_person_follow`가 QR이 짧게 가려질 때 같은 위치의 인형 YOLO 박스를
선택하는 기준으로 사용한다. QR이 보이지 않으면 두 값은 `NaN`이다.
