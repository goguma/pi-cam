#!/usr/bin/env bash
# =============================================================================
# start.sh — Pi Camera 스트리밍 서버 런처
#
# Raspberry Pi OS Bookworm (64-bit) + picamera2 환경에서 실행합니다.
#
# 사용법:
#   ./start.sh
#   CAM_WIDTH=640 CAM_HEIGHT=480 ./start.sh
#   CAM_TARGET_FPS=15 SERVER_PORT=8080 ./start.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 가상환경 활성화 (있을 경우)
# ---------------------------------------------------------------------------
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
    echo "[INFO] 가상환경 활성화: $SCRIPT_DIR/.venv"
fi

# ---------------------------------------------------------------------------
# picamera2 설치 확인 (venv 활성화 후)
# ---------------------------------------------------------------------------
if ! python -c "import picamera2" 2>/dev/null; then
    echo "========================================================="
    echo "[오류] picamera2 를 import할 수 없습니다."
    echo ""
    echo "  1. 시스템 패키지 설치:"
    echo "     sudo apt update && sudo apt install -y python3-picamera2"
    echo ""
    echo "  2. 가상환경을 사용 중이라면 --system-site-packages 로 재생성:"
    echo "     rm -rf .venv"
    echo "     python3 -m venv .venv --system-site-packages"
    echo "     source .venv/bin/activate"
    echo "     pip install -r requirements.txt"
    echo "========================================================="
    exit 1
fi

# ---------------------------------------------------------------------------
# 서버 실행
# ---------------------------------------------------------------------------
echo "[INFO] picamera2 사용 모드"
echo "[INFO] 서버 시작: http://0.0.0.0:${SERVER_PORT:-8000}"
echo "       설정: ${CAM_WIDTH:-1280}x${CAM_HEIGHT:-720}, ${CAM_TARGET_FPS:-30}fps, JPEG품질=${CAM_QUALITY:-80}"
echo ""

cd "$SCRIPT_DIR"
exec python server.py "$@"
