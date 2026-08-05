#!/usr/bin/env bash

# Nav2 실험 세션을 시작·종료하고 기록 노드와 rosbag을 관리하는 개발 도구다.
# 구현 전에는 --help로 설계와 예정 옵션만 확인할 수 있다.
set -euo pipefail

usage() {
  cat <<'EOF'
사용법:
  run_experiment.sh [옵션]

예정 옵션:
  --profile <파일>  적용할 Nav2 실험 프로파일
  --map <파일>      사용할 지도
  --record-bag      rosbag 기록 활성화
  --label <이름>    실험 목적 메모

이 도구는 아직 골격 단계입니다. recorder.py와 프로파일 적용 기능을
구현한 뒤 실주행에 사용하세요.
EOF
}

case "${1:-}" in
  ""|-h|--help)
    usage
    ;;
  *)
    echo "[실패] 아직 구현되지 않은 실험 실행 도구입니다. --help를 확인하세요." >&2
    exit 2
    ;;
esac
