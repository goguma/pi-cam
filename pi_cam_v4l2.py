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
import ctypes.util
import select
import logging
from typing import Generator

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# V4L2 ioctl 번호 계산 헬퍼 (linux/ioctl.h 기준)
# ---------------------------------------------------------------------------
# ARM64(aarch64) / ARM32 모두 동일한 매크로 레이아웃 사용
#   bits[ 7: 0] = NR   (함수 번호)
#   bits[15: 8] = TYPE (매직 문자)
#   bits[29:16] = SIZE (구조체 크기, 최대 14비트 = 16383)
#   bits[31:30] = DIR  (방향: 0=None, 1=Write, 2=Read, 3=R/W)

_IOC_NRBITS   = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS  = 2

_IOC_NRSHIFT   = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT   + _IOC_NRBITS    # 8
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS   # 16
_IOC_DIRSHIFT  = _IOC_SIZESHIFT + _IOC_SIZEBITS   # 30

_IOC_NONE  = 0
_IOC_WRITE = 1
_IOC_READ  = 2


def _IOC(direction: int, _type: str, nr: int, size: int) -> int:
    return (
        (direction      << _IOC_DIRSHIFT)
        | (ord(_type)   << _IOC_TYPESHIFT)
        | (nr           << _IOC_NRSHIFT)
        | (size         << _IOC_SIZESHIFT)
    )


def _IO(_type: str, nr: int) -> int:
    return _IOC(_IOC_NONE, _type, nr, 0)


def _IOR(_type: str, nr: int, size: int) -> int:
    return _IOC(_IOC_READ, _type, nr, size)


def _IOW(_type: str, nr: int, size: int) -> int:
    return _IOC(_IOC_WRITE, _type, nr, size)


def _IOWR(_type: str, nr: int, size: int) -> int:
    return _IOC(_IOC_READ | _IOC_WRITE, _type, nr, size)


# ---------------------------------------------------------------------------
# V4L2 상수 (linux/videodev2.h)
# ---------------------------------------------------------------------------

# 픽셀 포맷
V4L2_PIX_FMT_YUYV  = 0x56595559  # 'YUYV'
V4L2_PIX_FMT_MJPEG = 0x47504A4D  # 'MJPG'

# 버퍼 타입
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1

# 메모리 타입
V4L2_MEMORY_MMAP = 1

# 필드 설정
V4L2_FIELD_NONE = 1

# ---------------------------------------------------------------------------
# V4L2 구조체 정의 (ctypes)
#
# 주의: 구조체 크기가 커널이 기대하는 값과 정확히 일치해야 합니다.
#   v4l2_format union 크기 = 200 bytes (raw_data[200])
#   v4l2_format 전체 = 4(type) + 200(fmt) = 204 bytes
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
    """v4l2_format.fmt 유니온 — raw_data[200]과 동일한 크기를 유지합니다."""
    _fields_ = [
        ("pix", v4l2_pix_format),
        ("raw", ctypes.c_uint8 * 200),
    ]


class v4l2_format(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("fmt",  _u_fmt),
    ]


class v4l2_requestbuffers(ctypes.Structure):
    _fields_ = [
        ("count",    ctypes.c_uint32),
        ("type",     ctypes.c_uint32),
        ("memory",   ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class v4l2_timecode(ctypes.Structure):
    _fields_ = [
        ("type",     ctypes.c_uint32),
        ("flags",    ctypes.c_uint32),
        ("frames",   ctypes.c_uint8),
        ("seconds",  ctypes.c_uint8),
        ("minutes",  ctypes.c_uint8),
        ("hours",    ctypes.c_uint8),
        ("userbits", ctypes.c_uint8 * 4),
    ]


class v4l2_timeval(ctypes.Structure):
    """
    struct timeval — tv_sec/tv_usec 크기는 플랫폼에 따라 다릅니다.
    64-bit Linux: __kernel_long_t = 64비트 → c_int64 사용
    """
    _fields_ = [
        ("tv_sec",  ctypes.c_int64),
        ("tv_usec", ctypes.c_int64),
    ]


class _u_m(ctypes.Union):
    _fields_ = [
        ("offset",  ctypes.c_uint32),
        ("userptr", ctypes.c_ulong),
        ("fd",      ctypes.c_int32),
    ]


class v4l2_buffer(ctypes.Structure):
    _fields_ = [
        ("index",     ctypes.c_uint32),
        ("type",      ctypes.c_uint32),
        ("bytesused", ctypes.c_uint32),
        ("flags",     ctypes.c_uint32),
        ("field",     ctypes.c_uint32),
        # 64비트 정렬을 위한 패딩 (커널 구조체와 일치)
        ("_pad1",     ctypes.c_uint32),
        ("timestamp", v4l2_timeval),
        ("timecode",  v4l2_timecode),
        ("sequence",  ctypes.c_uint32),
        ("memory",    ctypes.c_uint32),
        ("m",         _u_m),
        ("length",    ctypes.c_uint32),
        ("reserved2", ctypes.c_uint32),
        ("request_fd", ctypes.c_int32),
    ]


# ---------------------------------------------------------------------------
# ioctl 요청 번호 (linux/videodev2.h 기준 실제값)
# ---------------------------------------------------------------------------
VIDIOC_QUERYCAP  = _IOR ('V',  0, ctypes.sizeof(ctypes.c_uint8) * 104)
VIDIOC_G_FMT     = _IOWR('V',  4, ctypes.sizeof(v4l2_format))
VIDIOC_S_FMT     = _IOWR('V',  5, ctypes.sizeof(v4l2_format))
VIDIOC_REQBUFS   = _IOWR('V',  8, ctypes.sizeof(v4l2_requestbuffers))
VIDIOC_QUERYBUF  = _IOWR('V',  9, ctypes.sizeof(v4l2_buffer))
VIDIOC_QBUF      = _IOWR('V', 15, ctypes.sizeof(v4l2_buffer))
VIDIOC_DQBUF     = _IOWR('V', 17, ctypes.sizeof(v4l2_buffer))
VIDIOC_STREAMON  = _IOW ('V', 18, ctypes.sizeof(ctypes.c_int))
VIDIOC_STREAMOFF = _IOW ('V', 19, ctypes.sizeof(ctypes.c_int))

logger.debug(
    "ioctl 번호 확인: VIDIOC_S_FMT=0x%08X (구조체 크기=%d bytes)",
    VIDIOC_S_FMT,
    ctypes.sizeof(v4l2_format),
)

# ---------------------------------------------------------------------------
# libc ioctl 직접 바인딩
#
# Python의 fcntl.ioctl은 내부적으로 libc의 ioctl()을 호출하지만,
# 일부 환경에서 LD_PRELOAD 인터셉터(libcamera v4l2-compat.so 등)와
# 호환되지 않을 수 있습니다.
# ctypes.CDLL을 통해 직접 libc.ioctl을 호출하면 동적 링커의
# 심볼 해석을 거쳐 LD_PRELOAD 인터셉터가 확실히 적용됩니다.
# ---------------------------------------------------------------------------
# ctypes.CDLL(None) = 프로세스 전역 심볼 테이블 (dlopen(NULL, ...))
# 이 방식은 LD_PRELOAD로 주입된 심볼(libcamera v4l2-compat의 ioctl 등)을
# 확실히 포함하며, 명시적으로 libc를 다시 로드하면 LD_PRELOAD를 우회할 수 있는
# 문제를 방지합니다.
_libc = ctypes.CDLL(None, use_errno=True)
_libc_ioctl = _libc.ioctl
_libc_ioctl.restype  = ctypes.c_int
_libc_ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]


# ---------------------------------------------------------------------------
# V4L2Camera 클래스
# ---------------------------------------------------------------------------

class V4L2Camera:
    """
    V4L2 ioctl + mmap 기반 카메라 제어 클래스.

    사용 예:
        with V4L2Camera(width=1280, height=720) as cam:
            for frame in cam.iter_frames():
                # frame: JPEG bytes
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
        self._check_device()
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

    def _ioctl(self, request: int, arg: ctypes.Structure) -> None:
        """
        libc ioctl 직접 호출 래퍼.

        ctypes.CDLL을 통해 libc의 ioctl()을 직접 호출합니다.
        동적 링커를 통해 심볼이 해석되므로 LD_PRELOAD 인터셉터
        (libcamera v4l2-compat.so 등)가 확실히 적용됩니다.
        ctypes.byref(arg)로 구조체의 주소를 직접 전달하여
        커널이 수정한 결과가 원본 구조체에 바로 반영됩니다.
        """
        ret = _libc_ioctl(self._fd, request, ctypes.byref(arg))
        if ret < 0:
            errno = ctypes.get_errno()
            raise OSError(
                errno,
                f"ioctl 실패 (request=0x{request:08X}, errno={errno}): {os.strerror(errno)}",
            )

    def _check_device(self) -> None:
        """
        VIDIOC_QUERYCAP: 디바이스가 V4L2 캡처를 지원하는지 확인합니다.

        지원하지 않으면 명확한 에러 메시지를 출력합니다.
        """
        # QUERYCAP 구조체 (104 bytes raw)
        cap = (ctypes.c_uint8 * 104)()
        ret = _libc_ioctl(self._fd, VIDIOC_QUERYCAP, ctypes.cast(cap, ctypes.c_void_p))
        if ret < 0:
            errno = ctypes.get_errno()
            raise RuntimeError(
                f"{self.device} 는 V4L2 디바이스가 아닙니다 "
                f"(errno={errno}: {os.strerror(errno)})"
            )

        # v4l2_capability 레이아웃:
        #   driver[16]   offset  0
        #   card[32]     offset 16
        #   bus_info[32] offset 48
        #   version u32  offset 80
        #   capabilities offset 84  ← 주의: 96 아님
        #   device_caps  offset 88
        #   reserved[3]  offset 92
        capabilities = int.from_bytes(bytes(cap[84:88]), "little")
        device_caps  = int.from_bytes(bytes(cap[88:92]), "little")

        V4L2_CAP_VIDEO_CAPTURE = 0x00000001
        V4L2_CAP_STREAMING     = 0x04000000
        # device_caps가 유효한 경우(V4L2_CAP_DEVICE_CAPS=0x80000000) device_caps를 우선 사용
        V4L2_CAP_DEVICE_CAPS   = 0x80000000
        effective_caps = device_caps if (capabilities & V4L2_CAP_DEVICE_CAPS) else capabilities

        driver   = bytes(cap[0:16]).rstrip(b"\x00").decode("utf-8", errors="replace")
        card     = bytes(cap[16:48]).rstrip(b"\x00").decode("utf-8", errors="replace")
        bus_info = bytes(cap[48:80]).rstrip(b"\x00").decode("utf-8", errors="replace")
        logger.info(
            "QUERYCAP: driver='%s', card='%s', bus='%s', "
            "capabilities=0x%08X, device_caps=0x%08X",
            driver, card, bus_info, capabilities, device_caps,
        )

        if not (effective_caps & V4L2_CAP_VIDEO_CAPTURE):
            raise RuntimeError(
                f"{self.device} 는 VIDEO_CAPTURE를 지원하지 않습니다 "
                f"(capabilities=0x{capabilities:08X}, device_caps=0x{device_caps:08X}). "
                "다른 /dev/videoN 디바이스를 시도하세요.\n"
                "힌트: ls /dev/video* 로 사용 가능한 디바이스를 확인하세요."
            )
        if not (effective_caps & V4L2_CAP_STREAMING):
            raise RuntimeError(
                f"{self.device} 는 STREAMING을 지원하지 않습니다."
            )

    def _set_format(self) -> None:
        """
        VIDIOC_S_FMT: 해상도와 픽셀 포맷을 설정합니다.

        libcamera v4l2-compat 일부 버전은 VIDIOC_S_FMT를 지원하지 않습니다.
        S_FMT가 실패(ENOTTY)하면 VIDIOC_G_FMT로 현재 포맷을 읽어 사용합니다.
        """
        fmt = v4l2_format()
        fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        fmt.fmt.pix.width       = self.width
        fmt.fmt.pix.height      = self.height
        fmt.fmt.pix.pixelformat = self.pixel_format
        fmt.fmt.pix.field       = V4L2_FIELD_NONE

        try:
            self._ioctl(VIDIOC_S_FMT, fmt)
            logger.info(
                "S_FMT 완료: %dx%d, pixfmt=0x%08X, sizeimage=%d",
                fmt.fmt.pix.width, fmt.fmt.pix.height,
                fmt.fmt.pix.pixelformat, fmt.fmt.pix.sizeimage,
            )
        except OSError as exc:
            if exc.errno != 25:  # ENOTTY가 아니면 재발생
                raise
            logger.warning(
                "VIDIOC_S_FMT 미지원 (errno=25) — G_FMT로 현재 포맷을 읽습니다."
            )
            # G_FMT로 드라이버 기본 포맷 조회
            fmt_g = v4l2_format()
            fmt_g.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
            self._ioctl(VIDIOC_G_FMT, fmt_g)
            fmt = fmt_g
            logger.info(
                "G_FMT 조회: %dx%d, pixfmt=0x%08X, sizeimage=%d",
                fmt.fmt.pix.width, fmt.fmt.pix.height,
                fmt.fmt.pix.pixelformat, fmt.fmt.pix.sizeimage,
            )

        # 드라이버가 실제로 적용한 값을 반영
        self.width        = fmt.fmt.pix.width
        self.height       = fmt.fmt.pix.height
        self.pixel_format = fmt.fmt.pix.pixelformat
        self._sizeimage   = fmt.fmt.pix.sizeimage

    def _request_buffers(self) -> None:
        """VIDIOC_REQBUFS: 커널 버퍼를 요청합니다."""
        req = v4l2_requestbuffers()
        req.count  = self.num_buffers
        req.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE
        req.memory = V4L2_MEMORY_MMAP
        self._ioctl(VIDIOC_REQBUFS, req)
        self.num_buffers = req.count
        logger.info("버퍼 %d개 할당됨", self.num_buffers)

    def _mmap_buffers(self) -> None:
        """VIDIOC_QUERYBUF + mmap: 버퍼를 유저 공간에 매핑합니다."""
        self._buffers     = []
        self._buf_lengths = []
        for i in range(self.num_buffers):
            buf        = v4l2_buffer()
            buf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE
            buf.memory = V4L2_MEMORY_MMAP
            buf.index  = i
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
            logger.debug(
                "버퍼[%d] mmap: offset=%d, length=%d",
                i, buf.m.offset, buf.length,
            )

    def _queue_all_buffers(self) -> None:
        """모든 버퍼를 초기 큐에 넣습니다."""
        for i in range(self.num_buffers):
            self._queue_buffer(i)

    def _queue_buffer(self, index: int) -> None:
        """VIDIOC_QBUF: 버퍼를 드라이버에 반환합니다."""
        buf        = v4l2_buffer()
        buf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE
        buf.memory = V4L2_MEMORY_MMAP
        buf.index  = index
        self._ioctl(VIDIOC_QBUF, buf)

    def _dequeue_buffer(self) -> v4l2_buffer:
        """VIDIOC_DQBUF: 채워진 버퍼를 드라이버에서 가져옵니다."""
        # O_NONBLOCK fd이므로 select로 준비 대기 (최대 2초)
        ready, _, _ = select.select([self._fd], [], [], 2.0)
        if not ready:
            raise TimeoutError("카메라 프레임 대기 시간 초과 (2초)")

        buf        = v4l2_buffer()
        buf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE
        buf.memory = V4L2_MEMORY_MMAP
        self._ioctl(VIDIOC_DQBUF, buf)
        return buf

    def _stream_on(self) -> None:
        """VIDIOC_STREAMON: 스트리밍을 시작합니다."""
        buf_type = ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE)
        ret = _libc_ioctl(self._fd, VIDIOC_STREAMON, ctypes.byref(buf_type))
        if ret < 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"STREAMON 실패: {os.strerror(errno)}")
        self._streaming = True

    def _stream_off(self) -> None:
        """VIDIOC_STREAMOFF: 스트리밍을 중지합니다."""
        buf_type = ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE)
        ret = _libc_ioctl(self._fd, VIDIOC_STREAMOFF, ctypes.byref(buf_type))
        if ret < 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"STREAMOFF 실패: {os.strerror(errno)}")
        self._streaming = False

    def _read_buffer(self, buf: v4l2_buffer) -> np.ndarray:
        """
        mmap에서 데이터를 읽어 numpy ndarray로 반환합니다.

        Returns:
            np.ndarray: shape (bytesused,), dtype=uint8
        """
        mm = self._buffers[buf.index]
        mm.seek(0)
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
        yuyv = raw.reshape((self.height, self.width, 2))
        bgr  = cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUYV)
        return bgr
