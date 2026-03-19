"""
server.py
=========
Raspberry Pi Zero 2W — FastAPI MJPEG 스트리밍 서버

V4L2Camera(pi_cam_v4l2.py)를 이용해 카메라 프레임을 획득하고
HTTP를 통해 실시간 MJPEG 스트림으로 제공합니다.

엔드포인트:
  GET /           → 브라우저 뷰어 HTML 페이지
  GET /stream     → MJPEG 무한 스트림  (Content-Type: multipart/x-mixed-replace)
  GET /snapshot   → JPEG 단일 프레임

환경변수:
  CAM_DEVICE      디바이스 경로          (기본: /dev/video0)
  CAM_WIDTH       캡처 가로 해상도        (기본: 1280)
  CAM_HEIGHT      캡처 세로 해상도        (기본: 720)
  CAM_BUFFERS     V4L2 mmap 버퍼 개수    (기본: 4)
  CAM_QUALITY     JPEG 인코딩 품질 0~100 (기본: 80)
  CAM_TARGET_FPS  목표 프레임 레이트      (기본: 30)
  SERVER_HOST     바인딩 호스트           (기본: 0.0.0.0)
  SERVER_PORT     바인딩 포트             (기본: 8000)

실행:
  pip install fastapi uvicorn numpy opencv-python-headless
  python server.py
"""

import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from pi_cam_v4l2 import V4L2Camera

# ---------------------------------------------------------------------------
# 로깅 설정
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 환경변수 기반 설정
# ---------------------------------------------------------------------------
CAM_DEVICE  = os.getenv("CAM_DEVICE",  "/dev/video0")
CAM_WIDTH   = int(os.getenv("CAM_WIDTH",   "1280"))
CAM_HEIGHT  = int(os.getenv("CAM_HEIGHT",  "720"))
CAM_BUFFERS = int(os.getenv("CAM_BUFFERS", "4"))
CAM_QUALITY = int(os.getenv("CAM_QUALITY", "80"))
CAM_TARGET_FPS = int(os.getenv("CAM_TARGET_FPS", "30"))
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# ---------------------------------------------------------------------------
# 공유 카메라 인스턴스
# ---------------------------------------------------------------------------
camera: V4L2Camera | None = None

# ---------------------------------------------------------------------------
# FastAPI Lifespan (앱 시작/종료 시 카메라 오픈/클로즈)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global camera
    logger.info(
        "카메라 초기화: device=%s, %dx%d, buffers=%d",
        CAM_DEVICE, CAM_WIDTH, CAM_HEIGHT, CAM_BUFFERS,
    )
    camera = V4L2Camera(
        device=CAM_DEVICE,
        width=CAM_WIDTH,
        height=CAM_HEIGHT,
        num_buffers=CAM_BUFFERS,
    )
    camera.open()
    logger.info("카메라 준비 완료")
    try:
        yield
    finally:
        logger.info("카메라 종료 중...")
        camera.close()
        logger.info("카메라 종료 완료")


app = FastAPI(
    title="Pi Camera V4L2 Stream",
    description="Raspberry Pi Zero 2W V4L2 기반 카메라 스트리밍 서버",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# MJPEG 스트림 제너레이터
# ---------------------------------------------------------------------------

MJPEG_BOUNDARY = b"--frame"

async def _mjpeg_generator() -> AsyncGenerator[bytes, None]:
    """
    비동기 MJPEG 프레임 제너레이터 (Target FPS 기반 레이트 리미팅 적용).

    각 프레임을 multipart/x-mixed-replace 포맷으로 yield합니다.
    카메라 캡처는 블로킹 작업이므로 run_in_executor로 스레드풀에서 실행합니다.

    레이트 리미팅 전략:
      - 프레임 시작 시각을 기록하고, 캡처+인코딩에 소요된 시간을 측정합니다.
      - 1프레임 목표 시간(= 1 / TARGET_FPS)에서 실제 소요 시간을 뺀 잔여 시간만큼
        asyncio.sleep()으로 대기합니다.
      - 캡처가 목표 시간보다 오래 걸린 경우(슬로우 프레임)에는 sleep을 건너뜁니다.
    """
    loop = asyncio.get_running_loop()
    frame_interval = 1.0 / CAM_TARGET_FPS  # 예: 30fps → 0.03333...초

    while True:
        frame_start = time.monotonic()

        try:
            # 블로킹 캡처를 스레드풀에서 실행 → 이벤트 루프 블로킹 방지
            jpeg_bytes: bytes = await loop.run_in_executor(
                None,
                lambda: camera.capture_jpeg(CAM_QUALITY),
            )
        except Exception as exc:
            logger.warning("프레임 캡처 실패 (계속): %s", exc)
            await asyncio.sleep(frame_interval)
            continue

        yield (
            MJPEG_BOUNDARY
            + b"\r\nContent-Type: image/jpeg\r\n"
            + f"Content-Length: {len(jpeg_bytes)}\r\n\r\n".encode()
            + jpeg_bytes
            + b"\r\n"
        )

        # 실제 소요 시간을 측정하고 남은 시간만 sleep
        elapsed = time.monotonic() - frame_start
        sleep_time = frame_interval - elapsed
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)


# ---------------------------------------------------------------------------
# 라우트 정의
# ---------------------------------------------------------------------------

@app.get(
    "/",
    response_class=HTMLResponse,
    summary="브라우저 뷰어 페이지",
    description="MJPEG 스트림을 보여주는 간단한 HTML 뷰어를 반환합니다.",
)
async def index() -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Pi Camera — Live Stream</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #111;
      color: #eee;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      font-family: monospace;
      gap: 16px;
    }}
    h1 {{ font-size: 1.4rem; letter-spacing: 2px; opacity: .7; }}
    img {{
      max-width: 100%;
      border: 2px solid #333;
      border-radius: 4px;
    }}
    .info {{
      font-size: .75rem;
      opacity: .4;
    }}
    a {{ color: #7af; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>📷 Pi Camera Live</h1>
  <img src="/stream" alt="MJPEG Stream" />
  <p class="info">
    해상도: {CAM_WIDTH}×{CAM_HEIGHT} &nbsp;|&nbsp;
    JPEG 품질: {CAM_QUALITY} &nbsp;|&nbsp;
    디바이스: {CAM_DEVICE}
  </p>
  <p class="info">
    <a href="/snapshot" target="_blank">📸 스냅샷 저장</a>
    &nbsp;|&nbsp;
    <a href="/docs" target="_blank">📄 API 문서</a>
  </p>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get(
    "/stream",
    summary="MJPEG 실시간 스트림",
    description=(
        "multipart/x-mixed-replace 형식의 MJPEG 무한 스트림을 반환합니다. "
        "브라우저의 <img src='/stream'>으로 바로 표시 가능합니다."
    ),
    responses={
        200: {"content": {"multipart/x-mixed-replace": {}}},
        503: {"description": "카메라가 준비되지 않음"},
    },
)
async def stream() -> StreamingResponse:
    if camera is None:
        raise HTTPException(status_code=503, detail="카메라가 준비되지 않았습니다.")
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get(
    "/snapshot",
    summary="단일 프레임 스냅샷",
    description="현재 카메라 프레임을 JPEG 이미지로 반환합니다.",
    responses={
        200: {"content": {"image/jpeg": {}}},
        503: {"description": "카메라가 준비되지 않음"},
    },
)
async def snapshot() -> Response:
    if camera is None:
        raise HTTPException(status_code=503, detail="카메라가 준비되지 않았습니다.")

    loop = asyncio.get_running_loop()
    try:
        jpeg_bytes: bytes = await loop.run_in_executor(
            None,
            lambda: camera.capture_jpeg(CAM_QUALITY),
        )
    except Exception as exc:
        logger.error("스냅샷 캡처 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"캡처 실패: {exc}") from exc

    return Response(content=jpeg_bytes, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="info",
        # reload=True 는 개발 시에만 사용 (Pi 에서는 False 권장)
        reload=False,
    )
