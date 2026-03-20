#!/usr/bin/env bash
# =============================================================================
# start.sh — Pi Camera V4L2 스트리밍 서버 런처
#
# 64비트 Raspberry Pi OS에서 libcamera v4l2-compat.so를 프리로드하여
# 기존 V4L2 ioctl 코드를 그대로 사용할 수 있도록 합니다.
#
# 사용법:
#   ./start.sh                     # 기본 실행 (720p, 30fps)
#   CAM_WIDTH=640 CAM_HEIGHT=480 ./start.sh
#   CAM_DEVICE=/dev/video1 ./start.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# libcamera v4l2-compat.so 경로 탐색
# ---------------------------------------------------------------------------
V4L2_COMPAT_PATHS=(
    "/usr/lib/aarch64-linux-gnu/libcamera/v4l2-compat.so"   # 64-bit Pi OS
    "/usr/lib/arm-linux-gnueabihf/libcamera/v4l2-compat.so" # 32-bit Pi OS
    "/usr/lib/libcamera/v4l2-compat.so"                      # 일부 배포판
)

V4L2_COMPAT=""
for path in "${V4L2_COMPAT_PATHS[@]}"; do
    if [ -f "$path" ]; then
        V4L2_COMPAT="$path"
        break
    fi
done

if [ -z "$V4L2_COMPAT" ]; then
    echo "========================================================="
    echo "[경고] v4l2-compat.so 를 찾을 수 없습니다."
    echo "  libcamera 패키지가 설치되어 있는지 확인하세요:"
    echo "  sudo apt install libcamera-dev"
    echo ""
    echo "  v4l2-compat 없이 직접 실행을 시도합니다..."
    echo "========================================================="
else
    echo "[INFO] v4l2-compat 프리로드: $V4L2_COMPAT"
    export LD_PRELOAD="$V4L2_COMPAT"
fi

# ---------------------------------------------------------------------------
# 가상환경 활성화 (있을 경우)
# ---------------------------------------------------------------------------
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
    echo "[INFO] 가상환경 활성화: $SCRIPT_DIR/.venv"
fi

# ---------------------------------------------------------------------------
# 서버 실행
# ---------------------------------------------------------------------------
echo "[INFO] 서버 시작: http://0.0.0.0:${SERVER_PORT:-8000}"
echo "       설정: ${CAM_WIDTH:-1280}x${CAM_HEIGHT:-720}, \
${CAM_TARGET_FPS:-30}fps, JPEG품질=${CAM_QUALITY:-80}"
echo ""

cd "$SCRIPT_DIR"
exec python server.py "$@"
