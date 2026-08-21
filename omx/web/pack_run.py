#!/usr/bin/env python
"""약통을 봉투에 넣는 정책을 한 번 실행한다 — 관제 백엔드가 subprocess 로 부른다.

## 왜 07_run.sh 를 그대로 쓰지 않는가

`omx/il/07_run.sh` 는 **평가용**이다. 웹 백엔드가 부르기에는 계약이 맞지 않는다.

  - `read -r _` 로 엔터를 기다린다 — 헤드리스로 못 붙인다
  - `lerobot-record` 로 돌아 평가 데이터셋을 남긴다. 조제 실행이 아니라 실험 기록이다
  - 로컬 패치가 첫 에피소드 앞에 리셋 대기 루프를 넣어, → 를 누를 때까지 서 있는다
  - 스페이스바 DAgger 개입·`--display_data=true` 전제 (사람이 앞에 앉아 있어야 한다)
  - 진행 문구를 찍지 않는다 — 화면에 올릴 것이 없다

그래서 같은 정책을 **키 입력 없이 한 번만** 돌리고, 진행 상황을 stdout 으로 찍고
끝나는 경로를 따로 둔다. 데이터셋도 남기지 않는다.

## 계약

`PACK_JSON ` 으로 시작하는 줄만 백엔드가 읽는다. 앞뒤로 lerobot·torch 로그가
아무리 섞여도 이 접두어로 골라낸다 (`count_tray.py` 의 `TRAY_JSON` 과 같은 방식).

    PACK_JSON {"단계": "정책 로드"}
    PACK_JSON {"단계": "로봇 연결"}
    PACK_JSON {"단계": "약 투입", "진행": 0.42}
    PACK_JSON {"완료": true, "초": 18.6, "조기종료": true, "최대이탈": 41.3}
    PACK_JSON {"오류": "top 카메라를 열지 못했습니다"}

## 언제 끝나는가

`--seconds` 는 **상한이지 소요 시간이 아니다.** ACT 는 "끝났다" 를 내놓지 않지만
이 작업은 팔이 학습 시작 자세로 돌아오며 끝나므로, 홈을 충분히 떠났다가 다시
돌아와 머무르면 거기서 끊는다. 그러지 않으면 실제로 40초에 끝난 작업이 60초를
채울 때까지 팔이 서 있고 화면도 그만큼 기다린다.

**성공 판정이 아니다** — 약통을 놓치고 돌아와도 똑같이 끝난다. 없애는 것은 꼬리
시간뿐이고 성공 여부는 사람이 본다 (omx/il/TASK.md 의 성공 기준).

감지가 어긋나면 `--no-early-stop` (백엔드는 `PACK_NO_EARLY_STOP=1`) 으로 끄고
예전처럼 상한을 채운다. 임계값은 `--leave-margin` · `--home-tol` · `--settle` 로
조정한다. 완료 줄의 `최대이탈` 이 `--leave-margin` 을 못 넘기면 감지가 아예
시작되지 않은 것이다.

`--dry-run` 은 로봇에 붙어 관측을 읽고 추론까지 하지만 **행동을 보내지 않는다.**
배선을 확인할 때 쓴다 — 팔이 움직이지 않는다.

## 사용

    ~/venv/il/bin/python pack_run.py --ckpt ~/train/act_pill_bottle_v1/checkpoints/last/pretrained_model
    ~/venv/il/bin/python pack_run.py --dry-run --seconds 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

MARKER = "PACK_JSON"

# 05_record.sh · 07_run.sh 와 글자 하나까지 같아야 한다. 데이터에 그대로 들어간
# 문자열이라 다듬으면 정책이 다른 작업으로 알아듣는다 (omx/il/TASK.md 참고).
DEFAULT_TASK = "put the pill bottle in the envelope"
DEFAULT_CKPT = "~/train/act_pill_bottle_v1/checkpoints/last/pretrained_model"
DEFAULT_PORT = "/dev/omx_follower"
DEFAULT_ROBOT_ID = "omx_follower_arm"
# 05_record.sh 가 60초로 찍었다. 그보다 길게 줘도 정책이 배운 것 이상은 못 한다.
# **이 값은 상한이지 소요 시간이 아니다** — 아래 홈 복귀 감지가 먼저 끊는다.
DEFAULT_SECONDS = 60.0
DEFAULT_FPS = 30

# 학습 시작 자세. `omx/il/10_home.py` 의 `HOME` 과 같은 값이어야 한다 —
# 데이터셋 29회차 첫 프레임의 관절 중앙값이다. import 하지 않고 복사해 두는 이유는
# 10_home.py 가 numpy 와 argparse 를 끌어오는 독립 스크립트라, 이 러너의 기동만
# 무거워지기 때문이다. **한쪽을 고치면 다른 쪽도 고칠 것.**
HOME = {
    "shoulder_pan.pos": 1.98,
    "shoulder_lift.pos": -63.22,
    "elbow_flex.pos": 54.58,
    "wrist_flex.pos": 48.33,
    "wrist_roll.pos": 0.02,
    "gripper.pos": 59.80,
}
# 홈에서 이만큼 벗어난 적이 있어야 "작업을 했다" 로 본다. 시작하자마자 홈 근처라는
# 이유로 끝나 버리는 것을 막는 관문이다.
DEFAULT_LEAVE_MARGIN = 15.0
# 다시 이 안으로 들어오면 "돌아왔다". 실측 복귀 오차가 0.8~4.5 였어서 여유를 뒀다.
DEFAULT_HOME_TOL = 8.0
# 지나가는 길에 잠깐 스친 것과 끝난 것을 가른다. 이만큼 머물러야 끝으로 친다.
DEFAULT_SETTLE = 2.0


def emit(payload: dict) -> None:
    print(f"{MARKER} {json.dumps(payload, ensure_ascii=False)}", flush=True)


def home_gap(obs: dict) -> float | None:
    """지금 자세와 학습 시작 자세의 **최대** 관절 오차.

    합이 아니라 최대를 쓴다 — 다섯 관절이 맞아도 하나가 크게 어긋나 있으면 팔은
    돌아온 것이 아니다. 관절값이 하나라도 빠지면 None 을 돌려 감지를 건너뛴다
    (감지 실패로 에피소드를 일찍 끊는 것보다 상한을 채우는 쪽이 안전하다).
    """
    gaps = [abs(obs[j] - h) for j, h in HOME.items()
            if isinstance(obs.get(j), (int, float))]
    return max(gaps) if len(gaps) == len(HOME) else None


def load_cams(cams_env: Path) -> tuple[str | None, str | None]:
    """`omx/il/cams.env` 를 읽는다.

    by-id 경로라 USB 포트를 옮겨도 그대로다 — 이 파일을 정본으로 쓰고 여기서
    /dev/videoN 을 추측하지 않는다. top/wrist 를 반대로 잡는 사고가 실제로 있었다.
    """
    top = wrist = None
    if not cams_env.is_file():
        return top, wrist
    for line in cams_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        if key.strip() == "TOP_CAM":
            top = val
        elif key.strip() == "WRIST_CAM":
            wrist = val
    return top, wrist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=DEFAULT_CKPT, help="pretrained_model 디렉터리")
    ap.add_argument("--task", default=DEFAULT_TASK)
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--robot-id", default=DEFAULT_ROBOT_ID)
    ap.add_argument("--cams-env", default=str(Path(__file__).resolve().parents[1] / "il" / "cams.env"))
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS)
    ap.add_argument("--dry-run", action="store_true",
                    help="추론까지만 하고 행동을 보내지 않는다 (팔이 움직이지 않음)")
    # 비상정지 (FC-14). 작업 영역에 사람이 보이면 행동 전송을 멈추고 팔의
    # 힘을 뺀다. 기본값이 켜짐인 이유: 끄는 것은 명시적 선택이어야 한다.
    ap.add_argument("--safety-server",
                    default=os.environ.get("OMX_SAFETY_SERVER",
                                           "http://127.0.0.1:5003/infer"),
                    help="사람 감지 추론 서버 URL. 빈 값을 주면 감시를 끈다")
    ap.add_argument("--safety-required", action="store_true",
                    help="감시 서버에 못 붙으면 팔을 세운다 (기본: 경고 후 계속)")
    ap.add_argument("--leave-margin", type=float, default=DEFAULT_LEAVE_MARGIN,
                    help="홈에서 이만큼 벗어나야 작업을 시작한 것으로 본다")
    ap.add_argument("--home-tol", type=float, default=DEFAULT_HOME_TOL,
                    help="홈으로 돌아왔다고 볼 최대 관절 오차")
    ap.add_argument("--settle", type=float, default=DEFAULT_SETTLE,
                    help="홈 근처에 이만큼(초) 머물러야 끝난 것으로 본다")
    ap.add_argument("--no-early-stop", action="store_true",
                    help="홈 복귀 감지를 끄고 --seconds 를 끝까지 채운다")
    args = ap.parse_args()

    # 로컬 디렉터리이거나 HF Hub repo id 다. `policies.json` 이 조제 정책을
    # repo id 로 참조하는 것과 같은 방식이라, 체크포인트를 저장소에 넣지 않고도
    # 다른 자리에서 같은 정책을 쓸 수 있다. `from_pretrained` 가 둘 다 받는다.
    ckpt: str | Path = Path(args.ckpt).expanduser()
    if (Path(ckpt) / "config.json").is_file():
        pass                                  # 로컬 체크포인트
    elif "/" in args.ckpt and not args.ckpt.startswith(("/", "~", ".")):
        ckpt = args.ckpt                      # HF Hub repo id — 그대로 넘긴다
    else:
        emit({"오류": f"체크포인트를 찾지 못했습니다: {ckpt}/config.json "
                      f"(로컬 경로 또는 HF Hub repo id)"})
        return 1
    if not Path(args.port).exists():
        emit({"오류": f"로봇 포트가 없습니다: {args.port} — 전원을 켜고 "
                      f"omx/il/02_find_ports.sh 를 실행하세요"})
        return 1

    top_cam, wrist_cam = load_cams(Path(args.cams_env).expanduser())
    for label, path in (("top", top_cam), ("wrist", wrist_cam)):
        if not path:
            emit({"오류": f"{label} 카메라 설정이 없습니다 — omx/il/03_check_cameras.sh --view"})
            return 1
        if not Path(path).exists():
            emit({"오류": f"{label} 카메라가 없습니다: {path}"})
            return 1

    # import 가 무겁다 (torch·lerobot). 인자 검증을 먼저 끝내고 여기서 끌어온다.
    emit({"단계": "정책 로드"})
    from lerobot.cameras.configs import Cv2Backends
    from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.datasets.utils import build_dataset_frame, hw_to_dataset_features
    from lerobot.policies.factory import get_policy_class, make_pre_post_processors
    from lerobot.policies.utils import make_robot_action
    from lerobot.robots.omx_follower import OmxFollower
    from lerobot.robots.omx_follower.config_omx_follower import OmxFollowerConfig
    from lerobot.utils.control_utils import predict_action
    from lerobot.utils.utils import get_safe_torch_device

    try:
        cfg = PreTrainedConfig.from_pretrained(ckpt)
        cfg.pretrained_path = str(ckpt)
        policy = get_policy_class(cfg.type).from_pretrained(ckpt)
        policy.eval()
        preprocessor, postprocessor = make_pre_post_processors(
            policy_cfg=cfg, pretrained_path=str(ckpt))
    except Exception as e:  # noqa: BLE001
        emit({"오류": f"정책을 불러오지 못했습니다: {type(e).__name__}: {e}"})
        return 1

    # _common.sh 의 build_cams() 와 같은 설정. backend=V4L2 · fourcc=MJPG 를 반드시
    # 준다 — 기본 백엔드로 열면 해상도·포맷 지정이 조용히 무시된다.
    # backend 는 문자열이 아니라 Cv2Backends 열거형이어야 한다 (YAML 경로에서는
    # draccus 가 이름을 변환해 주지만 파이썬에서 직접 만들 때는 아니다).
    cam_spec = dict(width=640, height=480, fps=30, fourcc="MJPG",
                    backend=Cv2Backends.V4L2)
    robot_cfg = OmxFollowerConfig(
        port=args.port,
        id=args.robot_id,
        cameras={
            "top": OpenCVCameraConfig(index_or_path=top_cam, **cam_spec),
            "wrist": OpenCVCameraConfig(index_or_path=wrist_cam, **cam_spec),
        },
    )

    emit({"단계": "로봇 연결"})
    robot = OmxFollower(robot_cfg)
    try:
        robot.connect()
    except Exception as e:  # noqa: BLE001
        emit({"오류": f"로봇에 연결하지 못했습니다: {type(e).__name__}: {e}"})
        return 1

    # 정책 입출력은 로봇의 평평한 dict (`shoulder_pan.pos: float`) 가 아니라
    # 데이터셋 형식 (`observation.state` 벡터 + 이미지) 이다. record_loop 이 쓰는
    # 그 변환을 그대로 쓴다 — 데이터셋은 만들지 않고 features 만 빌린다.
    ds_features = {
        **hw_to_dataset_features(robot.observation_features, "observation", True),
        **hw_to_dataset_features(robot.action_features, "action", True),
    }
    device = get_safe_torch_device(cfg.device)

    policy.reset()
    preprocessor.reset()
    postprocessor.reset()

    watch = None
    if args.safety_server:
        from safety_watch import SafetyWatch
        watch = SafetyWatch(
            args.safety_server, required=args.safety_required,
            warn=lambda msg: emit({"경고": msg}))
        emit({"단계": "안전 감시 시작", "서버": args.safety_server})

    emit({"단계": "약 투입", "진행": 0.0})
    started = time.perf_counter()
    period = 1.0 / args.fps
    last_report = 0.0
    rc = 0
    frame_no = 0
    # 홈 복귀 감지 상태. 떠났나 → 복귀시작 → 조기종료 순으로 한 방향으로만 간다.
    떠났나 = False
    복귀시작: float | None = None
    조기종료 = False
    최대이탈 = 0.0
    마지막이탈: float | None = None
    try:
        while True:
            elapsed = time.perf_counter() - started
            if elapsed >= args.seconds:
                break
            loop_start = time.perf_counter()

            obs = robot.get_observation()

            # 행동을 계산하기 전에 본다. 사람이 보이면 이번 프레임의 행동은
            # 아예 만들지 않는다 — 멈추기로 한 팔에 줄 명령은 없다.
            if watch is not None:
                trip = watch.check(obs, frame_no)
                if trip is not None:
                    emit({"오류": f"비상정지 — {trip.pop('이유')}",
                          "비상정지": True, **trip})
                    rc = 3
                    break
            frame_no += 1

            frame = build_dataset_frame(ds_features, obs, prefix="observation")
            action_values = predict_action(
                observation=frame,
                policy=policy,
                device=device,
                preprocessor=preprocessor,
                postprocessor=postprocessor,
                use_amp=cfg.use_amp,
                task=args.task,
                robot_type=robot.robot_type,
            )
            if not args.dry_run:
                robot.send_action(make_robot_action(action_values, ds_features))

            # 진행률은 시간 기준이다. 정책이 "끝났다" 를 알려주지 않기 때문이다 —
            # ACT 는 성공 판정을 내놓지 않는다. 화면에는 막대가 차오르는 것으로만
            # 쓰고, 성공 여부는 사람이 본다 (omx/il/TASK.md 의 성공 기준).
            if elapsed - last_report >= 1.0:
                last_report = elapsed
                emit({"단계": "약 투입", "진행": round(elapsed / args.seconds, 3)})

            # **끝났는데 상한을 채우느라 서 있는 시간을 없앤다.** ACT 는 "끝났다" 를
            # 내놓지 않지만, 이 작업은 팔이 학습 시작 자세로 돌아오며 끝난다. 홈을
            # 충분히 떠났다가(작업을 했다) 다시 돌아와(끝났다) 머무르면(지나가는
            # 길이 아니다) 그때 끊는다.
            #
            # 성공 판정이 아니다 — 약통을 놓치고 돌아와도 똑같이 끝난다. 없애는
            # 것은 꼬리 시간뿐이고, 성공 여부는 여전히 사람이 본다.
            gap = home_gap(obs)
            if gap is not None and not args.no_early_stop:
                최대이탈 = max(최대이탈, gap)
                마지막이탈 = gap
                if not 떠났나:
                    떠났나 = gap > args.leave_margin
                elif gap <= args.home_tol:
                    if 복귀시작 is None:
                        복귀시작 = time.perf_counter()
                    elif time.perf_counter() - 복귀시작 >= args.settle:
                        조기종료 = True
                else:
                    복귀시작 = None      # 다시 나갔다 — 아직이다
            if 조기종료:
                break

            slack = period - (time.perf_counter() - loop_start)
            if slack > 0:
                time.sleep(slack)
    except KeyboardInterrupt:
        emit({"오류": "중단되었습니다"})
        rc = 130
    except Exception as e:  # noqa: BLE001
        emit({"오류": f"{type(e).__name__}: {e}"})
        rc = 1
    finally:
        try:
            # disable_torque_on_disconnect=True 라 여기서 팔에 힘이 풀린다.
            robot.disconnect()
        except Exception:  # noqa: BLE001, S110
            pass

    if rc == 0:
        # 조기 종료든 상한이든, 이 단계는 끝났다 — 화면의 막대를 채우고 넘어간다.
        # 66% 에서 막대가 사라지면 중단된 것처럼 읽힌다.
        emit({"단계": "약 투입", "진행": 1.0})
        # 임계값 조정에 쓰는 두 숫자다. `최대이탈` 이 `--leave-margin` 을 못 넘기면
        # 감지가 아예 시작되지 않은 것이고, 상한을 채우고 끝났는데 `끝이탈` 이
        # `--home-tol` 보다 크면 팔이 홈으로 안 돌아온 것이다 (감지 잘못이 아니다).
        emit({"완료": True, "초": round(time.perf_counter() - started, 1),
              "dry_run": bool(args.dry_run), "조기종료": 조기종료,
              "최대이탈": round(최대이탈, 1),
              "끝이탈": None if 마지막이탈 is None else round(마지막이탈, 1)})
    return rc


if __name__ == "__main__":
    sys.exit(main())
