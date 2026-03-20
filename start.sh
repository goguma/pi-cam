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
# Python 인터프리터 선택
#
# 우선순위:
#   1. 현재 활성화된 venv의 python (picamera2 import 가능한 경우)
#   2. 시스템 python3 (venv에서 picamera2 안 보일 때 fallback)
#
# picamera2는 apt 시스템 패키지이므로 venv에서 numpy 버전 충돌이
# 발생할 경우 시스템 python3를 직접 사용하는 것이 안전합니다.
# ---------------------------------------------------------------------------
PYTHON_BIN=""

# 현재 venv 활성화되어 있으면 해당 python 우선 시도
if [ -n "$VIRTUAL_ENV" ] && [ -f "$VIRTUAL_ENV/bin/python" ]; then
    if "$VIRTUAL_ENV/bin/python" -c "import picamera2" 2>/dev/null; then
        PYTHON_BIN="$VIRTUAL_ENV/bin/python"
        echo "[INFO] 가상환경 python 사용: $PYTHON_BIN"
    else
        echo "[WARNING] 가상환경에서 picamera2 import 실패 → 시스템 python3 사용"
    fi
fi

# venv에서 안 되면 시스템 python3로 fallback
if [ -z "$PYTHON_BIN" ]; then
    if python3 -c "import picamera2" 2>/dev/null; then
        PYTHON_BIN="$(which python3)"
        echo "[INFO] 시스템 python3 사용: $PYTHON_BIN"
    else
        echo "========================================================="
        echo "[오류] picamera2 를 import할 수 없습니다."
        echo ""
        echo "  설치:"
        echo "    sudo apt update && sudo apt install -y python3-picamera2"
        echo "========================================================="
        exit 1
    fi
fi

# pip 패키지 확인 (fastapi, uvicorn, cv2)
# ※ numpy는 자동 설치하지 않습니다.
#   picamera2는 apt의 시스템 numpy(1.24.x)와 바이너리 호환이 필요하므로
#   pip로 numpy를 업그레이드하면 충돌이 발생합니다.
#   opencv-python-headless도 numpy를 당겨오므로 직접 apt로 설치합니다.
#     sudo apt install -y python3-opencv
if ! "$PYTHON_BIN" -c "import fastapi, uvicorn" 2>/dev/null; then
    echo "[WARNING] fastapi/uvicorn 미설치 → 설치 중..."
    "$PYTHON_BIN" -m pip install --break-system-packages \
        "fastapi>=0.111.0" "uvicorn[standard]>=0.29.0" 2>/dev/null || true
fi
if ! "$PYTHON_BIN" -c "import cv2" 2>/dev/null; then
    echo "[WARNING] opencv 미설치 → apt 설치를 권장합니다:"
    echo "  sudo apt install -y python3-opencv"
    echo "  (pip 설치는 numpy 충돌을 유발할 수 있음)"
fi

# ---------------------------------------------------------------------------
# 서버 실행
# ---------------------------------------------------------------------------
echo "[INFO] picamera2 사용 모드"
echo "[INFO] 서버 시작: http://0.0.0.0:${SERVER_PORT:-8000}"
echo "       설정: ${CAM_WIDTH:-1280}x${CAM_HEIGHT:-720}, ${CAM_TARGET_FPS:-30}fps, JPEG품질=${CAM_QUALITY:-80}"
echo ""

cd "$SCRIPT_DIR"
exec "$PYTHON_BIN" server.py "$@"
