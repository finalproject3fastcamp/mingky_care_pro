#!/usr/bin/env bash
set -euo pipefail

readonly BEGIN_MARKER='# BEGIN MINGKY SAFE BATTERY COMMAND'
readonly END_MARKER='# END MINGKY SAFE BATTERY COMMAND'
readonly SHELL_CONFIG="${HOME}/.bashrc"

temp_file="$(mktemp)"
trap 'rm -f "${temp_file}"' EXIT

if [[ -f "${SHELL_CONFIG}" ]]; then
  awk -v begin="${BEGIN_MARKER}" -v end="${END_MARKER}" '
    $0 == begin { managed = 1; next }
    $0 == end { managed = 0; next }
    !managed { print }
  ' "${SHELL_CONFIG}" > "${temp_file}"
fi

cat >> "${temp_file}" <<'EOF'
# BEGIN MINGKY SAFE BATTERY COMMAND
# ROS 토픽만 읽으므로 ADC와 LCD 하드웨어를 직접 점유하지 않는다.
alias battery='ros2 run mingky_bringup battery_status.py'
# 기존 LCD 표시 명령. mingky_lcd_status 실행 중에는 사용하지 않는다.
alias battery-lcd='/home/pinky/ap/check_battery.py'
# END MINGKY SAFE BATTERY COMMAND
EOF

install -m 0644 "${temp_file}" "${SHELL_CONFIG}"

echo "안전한 battery 명령을 ${SHELL_CONFIG} 마지막에 등록했습니다."
echo '현재 셸에 적용: source ~/.bashrc'
echo '기존 LCD 표시 명령: battery-lcd (LCD 상태 노드 실행 중 사용 금지)'
