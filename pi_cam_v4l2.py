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

# ---------------------------------------------------------------------------
# 얼굴 인식 — OpenCV Haar Cascade (추가 패키지 불필요)
# ---------------------------------------------------------------------------

def _load_face_cascade() -> cv2.CascadeClassifier:
    """OpenCV 내장 Haar Cascade 정면 얼굴 분류기를 로드합니다."""
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        raise RuntimeError(
            f"Haar Cascade 파일을 로드할 수 없습니다: {cascade_path}\n"
            "opencv-python-headless 패키지가 올바르게 설치되어 있는지 확인하세요."
        )
    return cascade


# 모듈 레벨에서 한 번만 로드 (스레드 안전, 읽기 전용)
try:
    _face_cascade: cv2.CascadeClassifier | None = _load_face_cascade()
except RuntimeError as _e:
    _face_cascade = None
    logger.warning("얼굴 인식 비활성화: %s", _e)


def detect_faces(
    gray: np.ndarray,
    scale_factor: float = 1.1,
    min_neighbors: int = 5,
    min_size: tuple[int, int] = (30, 30),
) -> list[tuple[int, int, int, int]]:
    """
    그레이스케일 이미지에서 얼굴을 검출합니다.

    Args:
        gray:          그레이스케일 ndarray (단일 채널)
        scale_factor:  이미지 피라미드 스케일 (클수록 빠르고 덜 정확)
        min_neighbors: 최소 이웃 사각형 수 (클수록 오탐 감소)
        min_size:      최소 얼굴 크기 (픽셀)

    Returns:
        [(x, y, w, h), ...] 형태의 얼굴 바운딩박스 목록
    """
    if _face_cascade is None:
        return []
    faces = _face_cascade.detectMultiScale(
        gray,
        scaleFactor=scale_factor,
        minNeighbors=min_neighbors,
        minSize=min_size,
    )
    if len(faces) == 0:
        return []
    return [(int(x), int(y), int(w), int(h)) for x, y, w, h in faces]


def draw_faces(
    img: np.ndarray,
    faces: list[tuple[int, int, int, int]],
    color: tuple[int, int, int] = (0, 255, 0),
    thickness: int = 2,
) -> np.ndarray:
    """
    이미지에 얼굴 바운딩박스와 라벨을 그립니다.

    Args:
        img:       BGR 또는 그레이스케일 ndarray (원본은 수정하지 않음)
        faces:     detect_faces()가 반환한 바운딩박스 목록
        color:     BGR 색상 (기본: 초록)
        thickness: 선 굵기

    Returns:
        바운딩박스가 그려진 새 ndarray
    """
    out = img.copy()
    for i, (x, y, w, h) in enumerate(faces):
        cv2.rectangle(out, (x, y), (x + w, y + h), color, thickness)
        label = f"Face {i + 1}"
        cv2.putText(
            out, label,
            (x, y - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55, color, thickness,
        )
    return out


# ---------------------------------------------------------------------------
# 카메라 클래스
# ---------------------------------------------------------------------------

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
        단일 프레임을 흑백 JPEG bytes로 반환합니다.

        Args:
            quality: JPEG 품질 (0~100)

        Returns:
            bytes: JPEG 인코딩된 이미지
        """
        bgr = self.capture_frame()
        # BGR → Grayscale 변환 (버퍼 레벨에서 흑백 처리)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        ok, encoded = cv2.imencode(
            ".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, quality]
        )
        if not ok:
            raise RuntimeError("JPEG 인코딩 실패")
        return encoded.tobytes()

    def capture_jpeg_with_faces(self, quality: int = 85) -> tuple[bytes, int]:
        """
        얼굴을 검출하고 바운딩박스가 그려진 컬러 JPEG bytes를 반환합니다.

        처리 순서:
          1. BGR 버퍼 캡처
          2. Grayscale 변환 → Haar Cascade 얼굴 검출
          3. 원본 BGR 이미지에 바운딩박스 + 라벨 렌더링
          4. JPEG 인코딩

        Args:
            quality: JPEG 품질 (0~100)

        Returns:
            (jpeg_bytes, face_count) 튜플
        """
        bgr = self.capture_frame()

        # 얼굴 검출은 그레이스케일에서 수행 (속도/정확도 최적)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = detect_faces(gray)

        # 원본 컬러 이미지에 얼굴 박스 그리기
        annotated = draw_faces(bgr, faces)

        ok, encoded = cv2.imencode(
            ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, quality]
        )
        if not ok:
            raise RuntimeError("JPEG 인코딩 실패")
        return encoded.tobytes(), len(faces)

    def iter_frames(self, quality: int = 85) -> Generator[bytes, None, None]:
        """
        흑백 JPEG 프레임을 무한정 yield하는 제너레이터.

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

    def iter_frames_with_faces(self, quality: int = 85) -> Generator[bytes, None, None]:
        """
        얼굴 인식 결과가 표시된 컬러 JPEG 프레임을 무한정 yield하는 제너레이터.

        Yields:
            bytes: 얼굴 바운딩박스가 그려진 JPEG 인코딩 프레임
        """
        while True:
            try:
                jpeg, face_count = self.capture_jpeg_with_faces(quality)
                if face_count > 0:
                    logger.debug("얼굴 검출: %d명", face_count)
                yield jpeg
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
