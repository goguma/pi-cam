"""
pi_cam_v4l2.py
==============
Raspberry Pi Zero 2W — V4L2 카메라 제어 모듈

libcamera 없이 리눅스 커널 V4L2(Video4Linux2) 인터페이스를
직접 제어(ioctl + mmap)하여 카메라 프레임을 획득합니다.

의존성: numpy, opencv-python-headless
"""

import os
import mmap
import ctypes
import fcntl
import select
import logging
from typing import Generator

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# V4L2 상수 정의
# ---------------------------------------------------------------------------

# ioctl 요청 번호 (linux/videodev2.h)
_IOC_NONE  = 0
_IOC_WRITE = 1
_IOC_READ  = 2

def _IOC(direction, _type, nr, size):
    return (
        (direction << 30)
        | (ord(_type) << 8)
        | nr
        | (size << 16)
    )

def _IOWR(_type, nr, size):
    return _IOC(_IOC_READ | _IOC_WRITE, _type, nr, size)

def _IOW(_type, nr, size):
    return _IOC(_IOC_WRITE, _type, nr, size)

def _IOR(_type, nr, size):
    return _IOC(_IOC_READ, _type, nr, size)

def _IO(_type, nr):
    return _IOC(_IOC_NONE, _type, nr, 0)


# 픽셀 포맷
V4L2_PIX_FMT_YUYV = 0x56595559  # 'YUYV'
V4L2_PIX_FMT_MJPEG = 0x47504A4D  # 'MJPG'

# 버퍼 타입
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1

# 메모리 타입
V4L2_MEMORY_MMAP = 1

# 필드 설정
V4L2_FIELD_NONE = 1

# ---------------------------------------------------------------------------
# V4L2 구조체 정의 (ctypes)
# ---------------------------------------------------------------------------

class v4l2_pix_format(ctypes.Structure):
    _fields_ = [
        ("width",        ctypes.c_uint32),
        ("height",       ctypes.c_uint32),
        ("pixelformat",  ctypes.c_uint32),
        ("field",        ctypes.c_uint32),
        ("bytesperline", ctypes.c_uint32),
        ("sizeimage",    ctypes.c_uint32),
        ("colorspace",   ctypes.c_uint32),
        ("priv",         ctypes.c_uint32),
        ("flags",        ctypes.c_uint32),
        ("ycbcr_enc",    ctypes.c_uint32),
        ("quantization", ctypes.c_uint32),
        ("xfer_func",    ctypes.c_uint32),
    ]


class _u_fmt(ctypes.Union):
    _fields_ = [
        ("pix",  v4l2_pix_format),
        ("raw",  ctypes.c_uint8 * 200),
    ]


class v4l2_format(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("fmt",  _u_fmt),
    ]


class v4l2_requestbuffers(ctypes.Structure):
    _fields_ = [
        ("count",  ctypes.c_uint32),
        ("type",   ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 2),
    ]


class v4l2_timecode(ctypes.Structure):
    _fields_ = [
        ("type",    ctypes.c_uint32),
        ("flags",   ctypes.c_uint32),
        ("frames",  ctypes.c_uint8),
        ("seconds", ctypes.c_uint8),
        ("minutes", ctypes.c_uint8),
        ("hours",   ctypes.c_uint8),
        ("userbits", ctypes.c_uint8 * 4),
    ]


class v4l2_timeval(ctypes.Structure):
    _fields_ = [
        ("tv_sec",  ctypes.c_long),
        ("tv_usec", ctypes.c_long),
    ]


class _u_m(ctypes.Union):
    _fields_ = [
        ("offset",   ctypes.c_uint32),
        ("userptr",  ctypes.c_ulong),
        ("fd",       ctypes.c_int32),
    ]


class v4l2_buffer(ctypes.Structure):
    _fields_ = [
        ("index",     ctypes.c_uint32),
        ("type",      ctypes.c_uint32),
        ("bytesused", ctypes.c_uint32),
        ("flags",     ctypes.c_uint32),
        ("field",     ctypes.c_uint32),
        ("timestamp", v4l2_timeval),
        ("timecode",  v4l2_timecode),
        ("sequence",  ctypes.c_uint32),
        ("memory",    ctypes.c_uint32),
        ("m",         _u_m),
        ("length",    ctypes.c_uint32),
        ("input",     ctypes.c_uint32),
        ("reserved",  ctypes.c_uint32),
    ]


# ioctl 요청 번호
VIDIOC_QUERYCAP  = _IOR('V', 0,  ctypes.sizeof(ctypes.c_uint8) * 104)
VIDIOC_S_FMT     = _IOWR('V', 5,  ctypes.sizeof(v4l2_format))
VIDIOC_G_FMT     = _IOWR('V', 4,  ctypes.sizeof(v4l2_format))
VIDIOC_REQBUFS   = _IOWR('V', 8,  ctypes.sizeof(v4l2_requestbuffers))
VIDIOC_QUERYBUF  = _IOWR('V', 9,  ctypes.sizeof(v4l2_buffer))
VIDIOC_QBUF      = _IOWR('V', 15, ctypes.sizeof(v4l2_buffer))
VIDIOC_DQBUF     = _IOWR('V', 17, ctypes.sizeof(v4l2_buffer))
VIDIOC_STREAMON  = _IOW('V', 18, ctypes.sizeof(ctypes.c_int))
VIDIOC_STREAMOFF = _IOW('V', 19, ctypes.sizeof(ctypes.c_int))


# ---------------------------------------------------------------------------
# V4L2Camera 클래스
# ---------------------------------------------------------------------------

class V4L2Camera:
    """
    V4L2 ioctl + mmap 기반 카메라 제어 클래스.

    사용 예:
        with V4L2Camera(width=1280, height=720) as cam:
            for frame in cam.iter_frames():
                # frame: (H, W, 3) numpy ndarray (BGR)
                ...
    """

    def __init__(
        self,
        device: str = "/dev/video0",
        width: int = 1280,
        height: int = 720,
        pixel_format: int = V4L2_PIX_FMT_YUYV,
        num_buffers: int = 4,
    ):
        self.device = device
        self.width = width
        self.height = height
        self.pixel_format = pixel_format
        self.num_buffers = num_buffers

        self._fd: int = -1
        self._buffers: list[mmap.mmap] = []
        self._buf_lengths: list[int] = []
        self._streaming: bool = False

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def open(self) -> None:
        """디바이스를 열고 스트리밍을 시작합니다."""
        logger.info("디바이스 오픈: %s", self.device)
        self._fd = os.open(self.device, os.O_RDWR | os.O_NONBLOCK)
        self._set_format()
        self._request_buffers()
        self._mmap_buffers()
        self._queue_all_buffers()
        self._stream_on()
        logger.info(
            "스트리밍 시작: %dx%d, 버퍼 %d개",
            self.width, self.height, self.num_buffers,
        )

    def close(self) -> None:
        """스트리밍을 종료하고 리소스를 해제합니다."""
        if self._streaming:
            self._stream_off()
        for buf in self._buffers:
            buf.close()
        self._buffers.clear()
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
        logger.info("디바이스 닫힘")

    def capture_frame(self) -> np.ndarray:
        """
        단일 프레임을 캡처하여 BGR ndarray로 반환합니다.

        Returns:
            np.ndarray: shape (height, width, 3), dtype=uint8, BGR
        """
        buf = self._dequeue_buffer()
        try:
            raw = self._read_buffer(buf)
            bgr = self._yuyv_to_bgr(raw)
        finally:
            self._queue_buffer(buf.index)
        return bgr

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
        무한정 JPEG 프레임을 yield하는 제너레이터.

        Args:
            quality: JPEG 품질 (0~100)

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

    def __enter__(self) -> "V4L2Camera":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 내부 구현 — V4L2 ioctl 헬퍼
    # ------------------------------------------------------------------

    def _ioctl(self, request: int, arg) -> None:
        """fcntl.ioctl 래퍼 (오류 시 OSError 발생)."""
        fcntl.ioctl(self._fd, request, arg)

    def _set_format(self) -> None:
        """VIDIOC_S_FMT: 해상도와 픽셀 포맷을 설정합니다."""
        fmt = v4l2_format()
        fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        fmt.fmt.pix.width = self.width
        fmt.fmt.pix.height = self.height
        fmt.fmt.pix.pixelformat = self.pixel_format
        fmt.fmt.pix.field = V4L2_FIELD_NONE
        self._ioctl(VIDIOC_S_FMT, fmt)

        # 드라이버가 실제로 적용한 값을 반영
        self.width = fmt.fmt.pix.width
        self.height = fmt.fmt.pix.height
        logger.debug(
            "포맷 설정 완료: %dx%d, sizeimage=%d",
            self.width, self.height, fmt.fmt.pix.sizeimage,
        )

    def _request_buffers(self) -> None:
        """VIDIOC_REQBUFS: 커널 버퍼를 요청합니다."""
        req = v4l2_requestbuffers()
        req.count = self.num_buffers
        req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        req.memory = V4L2_MEMORY_MMAP
        self._ioctl(VIDIOC_REQBUFS, req)
        self.num_buffers = req.count
        logger.debug("버퍼 %d개 할당됨", self.num_buffers)

    def _mmap_buffers(self) -> None:
        """VIDIOC_QUERYBUF + mmap: 버퍼를 유저 공간에 매핑합니다."""
        self._buffers = []
        self._buf_lengths = []
        for i in range(self.num_buffers):
            buf = v4l2_buffer()
            buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
            buf.memory = V4L2_MEMORY_MMAP
            buf.index = i
            self._ioctl(VIDIOC_QUERYBUF, buf)

            mm = mmap.mmap(
                self._fd,
                buf.length,
                mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE,
                offset=buf.m.offset,
            )
            self._buffers.append(mm)
            self._buf_lengths.append(buf.length)
            logger.debug("버퍼[%d] mmap: offset=%d length=%d", i, buf.m.offset, buf.length)

    def _queue_all_buffers(self) -> None:
        """모든 버퍼를 초기 큐에 넣습니다."""
        for i in range(self.num_buffers):
            self._queue_buffer(i)

    def _queue_buffer(self, index: int) -> None:
        """VIDIOC_QBUF: 버퍼를 드라이버에 반환합니다."""
        buf = v4l2_buffer()
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        buf.memory = V4L2_MEMORY_MMAP
        buf.index = index
        self._ioctl(VIDIOC_QBUF, buf)

    def _dequeue_buffer(self) -> v4l2_buffer:
        """VIDIOC_DQBUF: 채워진 버퍼를 드라이버에서 가져옵니다."""
        # 논블로킹 fd이므로 select로 준비 대기
        select.select([self._fd], [], [], 2.0)

        buf = v4l2_buffer()
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        buf.memory = V4L2_MEMORY_MMAP
        self._ioctl(VIDIOC_DQBUF, buf)
        return buf

    def _stream_on(self) -> None:
        """VIDIOC_STREAMON: 스트리밍을 시작합니다."""
        buf_type = ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE)
        self._ioctl(VIDIOC_STREAMON, buf_type)
        self._streaming = True

    def _stream_off(self) -> None:
        """VIDIOC_STREAMOFF: 스트리밍을 중지합니다."""
        buf_type = ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE)
        self._ioctl(VIDIOC_STREAMOFF, buf_type)
        self._streaming = False

    def _read_buffer(self, buf: v4l2_buffer) -> np.ndarray:
        """
        mmap에서 데이터를 Zero-copy로 읽어 numpy ndarray로 반환합니다.

        Returns:
            np.ndarray: shape (bytesused,), dtype=uint8
        """
        mm = self._buffers[buf.index]
        mm.seek(0)
        # numpy.frombuffer는 복사 없이 메모리를 공유합니다.
        raw = np.frombuffer(mm.read(buf.bytesused), dtype=np.uint8)
        return raw

    def _yuyv_to_bgr(self, raw: np.ndarray) -> np.ndarray:
        """
        YUYV (YUV422 packed) 데이터를 BGR ndarray로 변환합니다.

        Args:
            raw: 1-D uint8 ndarray (YUYV packed)

        Returns:
            np.ndarray: shape (height, width, 3), BGR
        """
        # YUYV → (H, W, 2) → OpenCV cvtColor
        yuyv = raw.reshape((self.height, self.width, 2))
        bgr = cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUYV)
        return bgr
