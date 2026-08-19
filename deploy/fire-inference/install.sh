#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="${1:-}"
[ -n "${MODEL_PATH}" ] || {
    echo "사용법: sudo ./install.sh <fire-model.pt>" >&2
    exit 1
}
[ -f "${MODEL_PATH}" ] || {
    echo "모델 파일을 찾을 수 없습니다: ${MODEL_PATH}" >&2
    exit 1
}
[ "$(id -u)" -eq 0 ] || { echo "sudo 로 실행하세요." >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SOURCE_ROOT="$(cd "${HERE}/../.." && pwd)"

id -u mingky-fire >/dev/null 2>&1 \
    || useradd --system --home /opt/mingky-fire --shell /usr/sbin/nologin mingky-fire
install -d -o mingky-fire -g mingky-fire -m 755 /opt/mingky-fire /etc/mingky
install -o mingky-fire -g mingky-fire -m 644 \
    "${SOURCE_ROOT}/mingky_ros/mingky_fire_evac/infer_server.py" \
    /opt/mingky-fire/infer_server.py
install -o mingky-fire -g mingky-fire -m 640 "${MODEL_PATH}" /opt/mingky-fire/model.pt

if [ ! -x /opt/mingky-fire/venv/bin/python ]; then
    python3 -m venv /opt/mingky-fire/venv
fi
/opt/mingky-fire/venv/bin/pip install --upgrade pip
/opt/mingky-fire/venv/bin/pip install flask pillow ultralytics waitress
chown -R mingky-fire:mingky-fire /opt/mingky-fire

if [ ! -f /etc/mingky/fire-inference.env ]; then
    install -m 640 "${HERE}/fire-inference.env.example" \
        /etc/mingky/fire-inference.env
fi
install -m 644 "${HERE}/mingky-fire-inference.service" \
    /etc/systemd/system/mingky-fire-inference.service
systemctl daemon-reload
systemctl enable --now mingky-fire-inference.service

echo "설치 완료: curl http://127.0.0.1:5000/health"
