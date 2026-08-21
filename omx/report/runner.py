"""OMX 박스 로컬 HTTP 러너 — 조제/포장/트레이를 HTTP 로 노출한다.

## 왜 이 러너가 있나

관제 백엔드는 클라우드로 옮겨 갔고, 조제 파트(run.sh·카메라·로봇팔)는 각 OMX
박스(.41=조제, .97=포장)에만 있다. 백엔드가 박스의 로컬 스크립트를 직접 돌릴 수
없으므로, 박스마다 이 경량 러너를 띄우고 백엔드가 robot_id 별로 여기에 프록시한다
(backend/app/pharmacy.py 의 `_run_remote_*`).

## 표준 라이브러리만 쓴다

리포터(omx/report/reporter.py)와 같은 이유다 — il venv(lerobot v0.4.4)에 추가
의존성을 요구하지 않는다. http.server + subprocess 만 쓴다. run.sh 는 자기 venv 를
스스로 source 하고, count_tray.py·pack_run.py 는 `OMX_PYTHON` 으로 띄운다.

## 상태 모델

한 박스는 한 번에 조제 하나 또는 포장 하나만 돈다(로봇팔이 하나뿐). 자식
프로세스를 배경 스레드에서 돌리고, stdout 을 파싱해 진행 단계를 상태 dict 에
쌓는다. 백엔드는 `GET /dispense/state` 를 폴링해 화면(SSE)으로 옮긴다.

  POST /dispense/start   {sequence: [color...], policy?: str}
  POST /dispense/stop
  GET  /dispense/state   {상태, 완료단계, 총단계, 메모}
  POST /pack/start
  POST /pack/stop
  GET  /pack/state
  GET  /tray             count_tray.py 결과
  GET  /health

`상태` 는 대기 · 진행 · 완료 · 중단 · 오류. 백엔드는 완료/오류/중단을 종료로 본다.

## 실기와 시뮬

`OMX_PILL_ROOT`(run.sh 가 있는 조제 파트)와 `OMX_PYTHON`(il venv)이 있어야 실기가
돈다. `MINGKY_RUNNER_SIM=1` 이면 스크립트 없이 각 단계를 짧게 흉내 내 배관만
확인한다 — 하드웨어 없는 개발/CI 용이다.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ── 경로·설정 (pharmacy.py 와 같은 규칙) ────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _PROJECT_ROOT / "omx" / "web"
_OMX_PROJECT = Path(
    os.environ.get("OMX_PILL_ROOT", str(Path.home() / "omx_pill_project"))
).expanduser()
_OMX_PYTHON = Path(
    os.environ.get("OMX_PYTHON", str(Path.home() / "venv" / "il" / "bin" / "python"))
).expanduser()
_TRAY_SCRIPT = _WEB_DIR / "count_tray.py"
_PACK_SCRIPT = _WEB_DIR / "pack_run.py"

SIM = os.environ.get("MINGKY_RUNNER_SIM", "0") == "1"
PICK_TIMEOUT = int(os.environ.get("MINGKY_RUNNER_PICK_TIMEOUT", "150"))
TRAY_FRAMES = int(os.environ.get("MINGKY_RUNNER_TRAY_FRAMES", "5"))
TRAY_TIMEOUT = int(os.environ.get("MINGKY_RUNNER_TRAY_TIMEOUT", "60"))
PACK_SECONDS = os.environ.get("PACK_SECONDS", "60")
PACK_CKPT = os.environ.get(
    "PACK_CKPT", "~/train/act_pill_bottle_v1/checkpoints/last/pretrained_model")

# run.sh 가 찍는 진행 문자열. pharmacy.py 의 UI 계약과 같은 값이라야 한다.
_LOG_STEP_DONE = "담기 완료"
_LOG_NEXT_TARGET = "다음 목표"
_LOG_ALL_DONE = "처방 조제 완료"
_TRAY_MARKER = "TRAY_JSON"
_PACK_MARKER = "PACK_JSON"


def _policies() -> dict:
    raw = json.loads((_WEB_DIR / "policies.json").read_text(encoding="utf-8"))["정책"]
    return {p["id"]: p for p in raw}


# ── 작업 상태 (한 박스 = 한 작업) ───────────────────────────────────────────
class _Job:
    """배경에서 도는 조제/포장 하나. 상태를 잠금으로 보호한다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.kind = None            # 'dispense' | 'pack'
            self.status = "대기"          # 대기·진행·완료·중단·오류
            self.done_steps = 0
            self.total_steps = 0
            self.memo = ""
            self.stop_requested = False

    def state(self) -> dict:
        with self._lock:
            return {"상태": self.status, "완료단계": self.done_steps,
                    "총단계": self.total_steps, "메모": self.memo,
                    "종류": self.kind}

    def busy(self) -> bool:
        with self._lock:
            return self.status == "진행"

    def request_stop(self) -> None:
        with self._lock:
            self.stop_requested = True
            proc = self._proc
        if proc and proc.poll() is None:
            # SIGINT 로 보내야 run.sh 의 finally 가 돌아 팔의 토크가 풀린다.
            proc.send_signal(signal.SIGINT)

    # ── 조제 ────────────────────────────────────────────────────────────
    def start_dispense(self, sequence: list[str], policy_id: str) -> dict:
        if self.busy():
            return {"오류": "이미 작업이 진행 중입니다"}
        if not sequence:
            return {"오류": "조합이 비어 있습니다"}
        self.reset()
        with self._lock:
            self.kind = "dispense"
            self.status = "진행"
            self.total_steps = len(sequence)
        self._thread = threading.Thread(
            target=self._run_dispense, args=(sequence, policy_id), daemon=True)
        self._thread.start()
        return {"상태": "진행", "총단계": len(sequence)}

    def _run_dispense(self, sequence: list[str], policy_id: str) -> None:
        if SIM:
            self._simulate(len(sequence))
            return
        try:
            pols = _policies()
            pol = pols.get(policy_id) or next(iter(pols.values()))
            run_sh = _OMX_PROJECT / "run.sh"
            if not run_sh.is_file():
                self._fail(f"run.sh 를 찾지 못했습니다: {run_sh} — OMX_PILL_ROOT 확인")
                return
            cmd = ["timeout", "-s", "INT", str(PICK_TIMEOUT * len(sequence)),
                   "bash", str(run_sh), pol["ckpt"],
                   "--repo-id", pol["repo"], "--relax-on-exit",
                   "--no-freeze-on-grasp", "--offset-step", "1",
                   "--sequence", ",".join(sequence), "--trace"]
            if pol.get("앙상블", True):
                cmd.append("--temporal-ensemble")
            env = {**os.environ, "RUN": pol.get("run", ""),
                   "TASK": f"pick {sequence[0]} pill", "HF_HUB_OFFLINE": "1"}
            self._pump(cmd, cwd=str(_OMX_PROJECT), env=env, total=len(sequence))
        except Exception as e:  # noqa: BLE001
            self._fail(f"{type(e).__name__}: {e}")

    def _pump(self, cmd: list[str], cwd: str, env: dict, total: int) -> None:
        """자식 stdout 을 읽어 진행 단계를 상태로 옮긴다."""
        proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        with self._lock:
            self._proc = proc
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            with self._lock:
                stop = self.stop_requested
            if stop:
                break
            if _LOG_STEP_DONE in line:
                with self._lock:
                    self.done_steps = min(self.done_steps + 1, total)
            elif _LOG_ALL_DONE in line:
                pass
        proc.wait()
        with self._lock:
            self._proc = None
            if self.stop_requested:
                self.status, self.memo = "중단", "사용자가 중단했습니다"
            elif self.done_steps >= total:
                self.status, self.memo = "완료", "완료"
            elif proc.returncode not in (0, None):
                self.status = "오류"
                self.memo = f"러너가 종료 코드 {proc.returncode} 로 끝났습니다"
            else:
                self.status = "오류"
                self.memo = f"{self.done_steps}/{total} 만 담았습니다"

    # ── 포장 ────────────────────────────────────────────────────────────
    def start_pack(self) -> dict:
        if self.busy():
            return {"오류": "이미 작업이 진행 중입니다"}
        self.reset()
        with self._lock:
            self.kind = "pack"
            self.status = "진행"
            self.total_steps = 1
        self._thread = threading.Thread(target=self._run_pack, daemon=True)
        self._thread.start()
        return {"상태": "진행"}

    def _run_pack(self) -> None:
        if SIM:
            self._simulate(1)
            return
        try:
            if not _PACK_SCRIPT.is_file():
                self._fail(f"포장 러너를 찾지 못했습니다: {_PACK_SCRIPT}")
                return
            cmd = [str(_OMX_PYTHON), str(_PACK_SCRIPT),
                   "--ckpt", PACK_CKPT, "--seconds", str(PACK_SECONDS)]
            proc = subprocess.Popen(cmd, cwd=str(_WEB_DIR),
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            with self._lock:
                self._proc = proc
            last_err = ""
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip()
                with self._lock:
                    stop = self.stop_requested
                if stop:
                    break
                if _PACK_MARKER in line:
                    try:
                        ev = json.loads(line.split(_PACK_MARKER, 1)[1].strip())
                    except json.JSONDecodeError:
                        continue
                    if ev.get("오류"):
                        last_err = str(ev["오류"])
            proc.wait()
            with self._lock:
                self._proc = None
                if self.stop_requested:
                    self.status, self.memo = "중단", "사용자가 중단했습니다"
                elif last_err:
                    self.status, self.memo = "오류", last_err
                elif proc.returncode not in (0, None):
                    self.status = "오류"
                    self.memo = f"포장 러너가 종료 코드 {proc.returncode} 로 끝났습니다"
                else:
                    self.status, self.memo, self.done_steps = "완료", "완료", 1
        except Exception as e:  # noqa: BLE001
            self._fail(f"{type(e).__name__}: {e}")

    # ── 공통 ────────────────────────────────────────────────────────────
    def _simulate(self, total: int) -> None:
        for _ in range(total):
            for _ in range(8):
                with self._lock:
                    if self.stop_requested:
                        self.status, self.memo = "중단", "사용자가 중단했습니다"
                        return
                time.sleep(0.05)
            with self._lock:
                self.done_steps = min(self.done_steps + 1, total)
        with self._lock:
            self.status, self.memo = "완료", "완료"

    def _fail(self, memo: str) -> None:
        with self._lock:
            self.status, self.memo, self._proc = "오류", memo, None


_DISPENSE_JOB = _Job()
_PACK_JOB = _Job()


def _read_tray() -> dict:
    """count_tray.py 를 il venv 로 띄워 개수를 읽는다."""
    if SIM:
        return {"모드": "시뮬레이션", "개수": {"red": 1, "yellow": 1, "green": 1}}
    if not _TRAY_SCRIPT.is_file():
        return {"오류": f"트레이 계수 스크립트가 없습니다: {_TRAY_SCRIPT}"}
    cmd = [str(_OMX_PYTHON), str(_TRAY_SCRIPT),
           "--root", str(_OMX_PROJECT), "--frames", str(TRAY_FRAMES)]
    try:
        out = subprocess.run(cmd, cwd=str(_OMX_PROJECT), capture_output=True,
                             text=True, timeout=TRAY_TIMEOUT)
    except subprocess.TimeoutError:
        return {"오류": f"트레이를 읽는 데 {TRAY_TIMEOUT}초를 넘겼습니다"}
    for line in reversed(out.stdout.splitlines()):
        if line.startswith(_TRAY_MARKER):
            return json.loads(line[len(_TRAY_MARKER):])
    tail = (out.stderr.strip().splitlines() or ["(출력 없음)"])[-1]
    return {"오류": f"트레이 계수가 실패했습니다 — {tail}"}


# ── HTTP ────────────────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send(200, {"상태": "살아있음", "시각": datetime.now().isoformat(),
                             "sim": SIM})
        elif self.path == "/dispense/state":
            self._send(200, _DISPENSE_JOB.state())
        elif self.path == "/pack/state":
            self._send(200, _PACK_JOB.state())
        elif self.path == "/tray":
            self._send(200, _read_tray())
        else:
            self._send(404, {"오류": f"모르는 경로: {self.path}"})

    def do_POST(self):  # noqa: N802
        body = self._body()
        if self.path == "/dispense/start":
            result = _DISPENSE_JOB.start_dispense(
                [str(c) for c in (body.get("sequence") or [])],
                str(body.get("policy") or ""))
            self._send(400 if result.get("오류") else 200, result)
        elif self.path == "/dispense/stop":
            _DISPENSE_JOB.request_stop()
            self._send(200, {"결과": "중단 요청을 보냈습니다"})
        elif self.path == "/pack/start":
            result = _PACK_JOB.start_pack()
            self._send(400 if result.get("오류") else 200, result)
        elif self.path == "/pack/stop":
            _PACK_JOB.request_stop()
            self._send(200, {"결과": "중단 요청을 보냈습니다"})
        else:
            self._send(404, {"오류": f"모르는 경로: {self.path}"})

    def log_message(self, *args):  # journald 가 이미 타임스탬프를 붙인다
        pass


def main() -> None:
    host = os.environ.get("MINGKY_RUNNER_HOST", "0.0.0.0")
    port = int(os.environ.get("MINGKY_RUNNER_PORT", "8800"))
    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"OMX 러너 시작 — http://{host}:{port} (sim={SIM}, root={_OMX_PROJECT})",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
