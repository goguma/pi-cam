"""
pi_cam_v4l2.py
==============
Raspberry Pi Zero 2W — libcamera(picamera2) 기반 카메라 제어 모듈

Raspberry Pi OS Bookworm (64-bit)에서 picamera2 라이브러리를 사용하여
카메라 프레임을 획득하고 JPEG로 인코딩합니다.

의존성: picamera2, numpy, opencv-python-headless
설치:
  sudo apt install -y python3-picamera2
  pip install opencv-python-headless numpy
"""

import logging
import threading
from typing import Generator

import cv2
import numpy as np

logger = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2
    _HAS_PICAMERA2 = True
except ImportError:
    _HAS_PICAMERA2 = False
    logger.warning(
        "picamera2를 import할 수 없습니다. "
        "sudo apt install python3-picamera2 로 설치하세요."
    )


class PiCamera:
    """
    picamera2 기반 카메라 제어 클래스.

    V4L2Camera와 동일한 인터페이스를 제공하므로 server.py를 수정 없이
    사용할 수 있습니다.

    사용 예:
        with PiCamera(width=1280, height=720) as cam:
            jpeg = cam.capture_jpeg(85)

        # 연속 스트리밍
        with PiCamera() as cam:
            for jpeg in cam.iter_frames():
                ...
    """

    def __init__(
        self,
        device: str = "/dev/video0",   # 호환성 유지용 (picamera2에선 무시)
        width: int = 1280,
        height: int = 720,
        num_buffers: int = 4,
        **kwargs,                       # 추가 인자 무시 (호환성)
    ):
        if not _HAS_PICAMERA2:
            raise RuntimeError(
                "picamera2가 설치되어 있지 않습니다.\n"
                "  sudo apt install python3-picamera2"
            )
        self.width       = width
        self.height      = height
        self.num_buffers = num_buffers

        self._cam: Picamera2 | None = None
        self._lock = threading.Lock()   # 멀티 클라이언트 동시접근 보호

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def open(self) -> None:
        """카메라를 초기화하고 스트리밍을 시작합니다."""
        logger.info(
            "카메라 초기화 (picamera2): %dx%d, 버퍼 %d개",
            self.width, self.height, self.num_buffers,
        )
        self._cam = Picamera2()

        # 비디오 스트리밍 설정 — BGR888은 OpenCV가 바로 쓸 수 있는 포맷
        config = self._cam.create_video_configuration(
            main={
                "format": "BGR888",
                "size": (self.width, self.height),
            },
            buffer_count=self.num_buffers,
        )
        self._cam.configure(config)

        # 자동 노출/화이트밸런스 활성화
        self._cam.set_controls({
            "AeEnable": True,
            "AwbEnable": True,
        })

        self._cam.start()
        logger.info("카메라 스트리밍 시작")

    def close(self) -> None:
        """스트리밍을 종료하고 리소스를 해제합니다."""
        if self._cam is not None:
            self._cam.stop()
            self._cam.close()
            self._cam = None
        logger.info("카메라 닫힘")

    def capture_frame(self) -> np.ndarray:
        """
        단일 프레임을 캡처하여 BGR ndarray로 반환합니다.

        Returns:
            np.ndarray: shape (height, width, 3), dtype=uint8, BGR
        """
        if self._cam is None:
            raise RuntimeError("카메라가 열려 있지 않습니다.")
        with self._lock:
            frame = self._cam.capture_array("main")
        return frame

    def capture_jpeg(self, quality: int = 85) -> bytes:
        """
        단일 프레임을 JPEG bytes로 반환합니다.

        Args:
            quality: JPEG 품질 (0~100)

        Returns:
            bytes: JPEG 인코딩된 이미지
        """
        bgr = self.capture_frame()
        ok, encoded = cv2.imencode(
            ".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality]
        )
        if not ok:
            raise RuntimeError("JPEG 인코딩 실패")
        return encoded.tobytes()

    def iter_frames(self, quality: int = 85) -> Generator[bytes, None, None]:
        """
        JPEG 프레임을 무한정 yield하는 제너레이터.

        Yields:
            bytes: JPEG 인코딩된 프레임
        """
        while True:
            try:
                yield self.capture_jpeg(quality)
            except KeyboardInterrupt:
                break
            except Exception as exc:
                logger.warning("프레임 캡처 오류 (계속): %s", exc)

    # ------------------------------------------------------------------
    # 컨텍스트 매니저
    # ------------------------------------------------------------------

    def __enter__(self) -> "PiCamera":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()


# 하위 호환 alias
V4L2Camera = PiCamera
