#!/usr/bin/env bash
#
# 데모 스택을 클라우드 서버에 세운다. 실기가 회수된 뒤 대시보드를 살려 두는
# 세 프로세스를 systemd 에 얹는다.
#
#   mingky-demo-scenario   heartbeat·세션·이벤트 (fake_robot --loop)
#   mingky-demo-teleop     지도 위 좌표·라이다·파티클·주행 모드
#   mingky-demo-camera     전방·후방 MJPEG
#
# ## 배포를 건드리지 않는다
#
# nginx 도, compose 도, 프론트엔드도 고치지 않는다. 카메라는 역터널이 끝나던
# 바로 그 포트(1880x)에 대신 앉고, 나머지는 로봇이 쓰던 것과 같은 HTTP·WS
# 경로로 들어간다. 실기가 돌아오면 이 세 서비스를 끄기만 하면 된다.
#
# ## 저장소를 통째로 안 옮기는 이유
#
# 상시로 도는 데모가 배포 소스와 같은 디렉터리를 물면, 배포하려고 git 을
# 만질 때마다 데모가 흔들린다. /opt/mingky-demo 에 필요한 것만 복사해 둔다.
# 다만 **경로 구조는 저장소와 같게** 둔다 — fake_robot.py 와 fake_teleop.py 가
# `parents[2]` 로 정본(config/event_codes.yaml)과 waypoint 를 찾기 때문이다.
#
# 사용법:  sudo ./tools/demo_stack/install.sh

set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "sudo 로 실행하세요." >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SOURCE_ROOT="$(cd "${HERE}/../.." && pwd)"
DEST=/opt/mingky-demo
USER_NAME=mingky-demo

echo "[1/6] 사용자와 디렉터리"
id -u "${USER_NAME}" >/dev/null 2>&1 \
    || useradd --system --home "${DEST}" --shell /usr/sbin/nologin "${USER_NAME}"
install -d -o "${USER_NAME}" -g "${USER_NAME}" -m 755 "${DEST}" /etc/mingky

echo "[2/6] 소스 복사 (저장소와 같은 경로 구조)"
# fake_robot.py 는 parents[2]/config/event_codes.yaml 을,
# fake_teleop.py 는 parents[2]/mingky_ros/.../waypoints 를 읽는다.
# 아래 네 갈래가 그 최소 집합이다.
for path in \
    tools/demo_stack \
    tools/fake_robot \
    config \
    mingky_ros/mingky_bringup/config/waypoints
do
    install -d -o "${USER_NAME}" -g "${USER_NAME}" -m 755 "${DEST}/$(dirname "${path}")"
    rm -rf "${DEST:?}/${path}"
    cp -r "${SOURCE_ROOT}/${path}" "${DEST}/${path}"
done
# 프레임은 복사 대상이 아니다. 아래에서 굽는다.
rm -rf "${DEST}/tools/demo_stack/frames" "${DEST}/tools/demo_stack/.work"
chown -R "${USER_NAME}:${USER_NAME}" "${DEST}"

echo "[3/6] venv"
if [ ! -x "${DEST}/venv/bin/python" ]; then
    python3 -m venv "${DEST}/venv"
fi
"${DEST}/venv/bin/pip" install --upgrade --quiet pip
"${DEST}/venv/bin/pip" install --quiet -r "${DEST}/tools/demo_stack/requirements.txt"
chown -R "${USER_NAME}:${USER_NAME}" "${DEST}/venv"

echo "[4/6] 설정"
if [ ! -f /etc/mingky/demo.env ]; then
    install -m 640 "${HERE}/demo.env.example" /etc/mingky/demo.env
    echo "     /etc/mingky/demo.env 를 새로 만들었습니다 — 주소를 확인하세요"
else
    echo "     /etc/mingky/demo.env 가 이미 있습니다 — 덮어쓰지 않았습니다"
fi

echo "[5/6] 카메라 프레임"
if [ -d "${DEST}/frames" ] && [ -n "$(ls -A "${DEST}/frames" 2>/dev/null)" ]; then
    echo "     이미 있습니다 — 다시 구우려면 ${DEST}/frames 를 지우고 재실행"
else
    # 한 번만 굽는다. 상시 프로세스가 yt-dlp·ffmpeg 에 기대지 않게 하는 것이
    # 이 단계의 요점이다 (fetch_demo_frames.sh 머리말).
    MINGKY_DEMO_FRAMES_DIR="${DEST}/frames" \
    MINGKY_DEMO_WORK_DIR="${DEST}/.work" \
        bash "${DEST}/tools/demo_stack/fetch_demo_frames.sh"
    chown -R "${USER_NAME}:${USER_NAME}" "${DEST}/frames"
fi

echo "[6/6] systemd"
for unit in mingky-demo-scenario mingky-demo-teleop mingky-demo-camera; do
    install -m 644 "${HERE}/systemd/${unit}.service" \
        "/etc/systemd/system/${unit}.service"
done
systemctl daemon-reload
for unit in mingky-demo-scenario mingky-demo-teleop mingky-demo-camera; do
    systemctl enable --now "${unit}.service"
done

echo
echo "설치 완료. 확인:"
echo "  systemctl status 'mingky-demo-*'"
echo "  journalctl -u mingky-demo-scenario -f"
echo "  curl -s http://127.0.0.1:18801/health"
