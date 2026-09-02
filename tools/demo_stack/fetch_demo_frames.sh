#!/usr/bin/env bash
#
# 데모 카메라가 흘릴 JPEG 을 만든다. 설치할 때 한 번만 돌린다.
#
# ## 왜 프레임으로 굽나
#
# mock_camera.py 는 stdlib 만 쓴다. 유튜브를 받고 영상을 디코딩하는 무거운
# 일을 상시 프로세스가 하면 그 의존성(yt-dlp·ffmpeg)이 데모 가동의 전제가
# 된다. 여기서 한 번 구워 두면 그 다음부터는 JPEG 을 읽는 일만 남는다 —
# 유튜브에서 영상이 내려가도 데모는 계속 돈다.
#
# ## 산출물
#
#   frames/pinky-01/front   시연 영상 앞 구간
#   frames/pinky-01/rear    같은 구간을 좌우 반전 (후방 시점)
#   frames/pinky-02/front   뒤 구간 — 두 대가 같은 장면이면 바로 티가 난다
#   frames/pinky-02/rear    뒤 구간 좌우 반전
#
# ## 유튜브를 못 받으면
#
# ffmpeg 합성 화면으로 떨어진다. 데모가 '카메라 죽음' 으로 보이는 것보다는
# 낫고, 합성인 게 눈에 보이는 편이 조용히 가짜를 흘리는 것보다 정직하다.
#
# **클라우드 서버에서는 이 경로로 떨어질 가능성이 높다.** 유튜브가 데이터센터
# IP 를 'Sign in to confirm you are not a bot' 으로 막는다. player_client 를
# 바꿔도 안 된다. 그때는 사람이 쓰는 회선에서 받아 소스를 직접 넣어 준다.
#
#     yt-dlp -S res:720 -o demo.mp4 <URL>          # 노트북에서
#     scp demo.mp4 서버:/opt/mingky-demo/.work/    # 확장자는 아무거나 된다
#     sudo -u mingky-demo MINGKY_DEMO_FRAMES_DIR=... ./fetch_demo_frames.sh
#
# 아래 소스 탐색이 `demo.*` 를 통째로 보므로 webm·mkv 를 그대로 넣어도 된다 —
# ffmpeg 은 확장자가 아니라 내용으로 형식을 판별한다.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Pinky 자율주행 시연 (README 의 데모 링크와 같은 영상)
VIDEO_URL="${MINGKY_DEMO_VIDEO_URL:-https://www.youtube.com/watch?v=plwKbx3PGU8}"
OUT_DIR="${MINGKY_DEMO_FRAMES_DIR:-$HERE/frames}"
WORK_DIR="${MINGKY_DEMO_WORK_DIR:-$HERE/.work}"

FPS="${MINGKY_DEMO_FPS:-12}"
SECONDS_PER_CAM="${MINGKY_DEMO_SECONDS:-25}"
WIDTH="${MINGKY_DEMO_WIDTH:-640}"
# JPEG 품질. 2 가 최상, 31 이 최악. 6 이면 눈에 안 띄면서 용량이 절반이다.
QUALITY="${MINGKY_DEMO_QUALITY:-6}"

log() { printf '[프레임] %s\n' "$*"; }
die() { printf '[실패] %s\n' "$*" >&2; exit 1; }

command -v ffmpeg >/dev/null 2>&1 \
  || die "ffmpeg 이 없다. sudo apt install -y ffmpeg"

mkdir -p "$WORK_DIR"

# 이미 있는 소스를 먼저 찾는다. 손으로 넣어 준 파일은 확장자가 무엇일지
# 모르므로(webm·mkv·mp4) 이름만 보고 고른다.
SOURCE="$(find "$WORK_DIR" -maxdepth 1 -type f -name 'demo.*' 2>/dev/null | sort | head -1)"
[ -n "$SOURCE" ] || SOURCE="$WORK_DIR/demo.mp4"

# --- 1. 영상 확보 ---------------------------------------------------------

SYNTHETIC=0
if [ -s "$SOURCE" ]; then
  log "이미 받아 둔 영상을 쓴다: $SOURCE"
elif command -v yt-dlp >/dev/null 2>&1; then
  log "시연 영상 내려받는 중: $VIDEO_URL"
  if ! yt-dlp -S "res:720" -o "$SOURCE" --no-playlist --quiet --no-warnings \
        "$VIDEO_URL"; then
    log "내려받기 실패 — 합성 화면으로 간다"
    SYNTHETIC=1
  fi
else
  log "yt-dlp 이 없다 (pipx install yt-dlp) — 합성 화면으로 간다"
  SYNTHETIC=1
fi

if [ "$SYNTHETIC" = "0" ] && [ ! -s "$SOURCE" ]; then
  SYNTHETIC=1
fi

# --- 2. 카메라별로 굽는다 -------------------------------------------------

# 카메라 : 시작초 : 추가 필터
CAMERAS=(
  "pinky-01/front:4:null"
  "pinky-01/rear:4:hflip"
  "pinky-02/front:40:null"
  "pinky-02/rear:40:hflip"
)

for entry in "${CAMERAS[@]}"; do
  cam="${entry%%:*}"; rest="${entry#*:}"
  start="${rest%%:*}"; extra="${rest#*:}"
  dest="$OUT_DIR/$cam"

  rm -rf "$dest"; mkdir -p "$dest"

  if [ "$SYNTHETIC" = "1" ]; then
    # testsrc2 는 움직이는 패턴과 시계를 같이 그린다. 정지 화면이 아니라는
    # 것이 한눈에 보여야 '스트림은 살아 있다' 를 확인할 수 있다.
    ffmpeg -hide_banner -loglevel error \
      -f lavfi -i "testsrc2=size=${WIDTH}x360:rate=$FPS" \
      -t "$SECONDS_PER_CAM" \
      -vf "drawtext=text='SIMULATED ${cam}':x=16:y=16:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6" \
      -q:v "$QUALITY" "$dest/%04d.jpg"
  else
    # -ss 를 -i 앞에 둬야 키프레임 단위로 건너뛴다. 뒤에 두면 앞부분을 전부
    # 디코딩하고 버려서 느려진다.
    ffmpeg -hide_banner -loglevel error \
      -ss "$start" -t "$SECONDS_PER_CAM" -i "$SOURCE" \
      -vf "fps=$FPS,scale=$WIDTH:-2,$extra" \
      -q:v "$QUALITY" "$dest/%04d.jpg"
  fi

  count=$(find "$dest" -name '*.jpg' | wc -l)
  [ "$count" -gt 0 ] || die "$cam 프레임이 하나도 안 나왔다"
  log "$cam — ${count}장"
done

total=$(du -sh "$OUT_DIR" | cut -f1)
log "완료: $OUT_DIR ($total)"
if [ "$SYNTHETIC" = "1" ]; then
  log "주의 — 합성 화면이다. 실제 시연 영상을 쓰려면 yt-dlp 을 설치하고 다시 돌려라"
fi
