# Pi Camera V4L2 Stream

Raspberry Pi Zero 2W + Camera Module 2 (IMX219)를 위한  
**libcamera Python API 없이** V4L2 ioctl/mmap 직접 제어 기반 MJPEG 스트리밍 서버입니다.

---

## 파일 구성

```
pi_cam_v4l2.py   V4L2 카메라 제어 모듈 (ioctl + mmap)
server.py        FastAPI MJPEG 스트리밍 서버
start.sh         런처 스크립트 (v4l2-compat 자동 적용)
requirements.txt Python 패키지 목록
```

---

## 설치

```bash
# 1. 패키지 설치
pip install -r requirements.txt

# 2. 실행 권한 부여
chmod +x start.sh
```

---

## 실행

### 64비트 Pi OS (권장 방법)

64비트 Pi OS에서는 레거시 `bcm2835-v4l2` 드라이버가 동작하지 않습니다.  
libcamera가 제공하는 **v4l2-compat.so** 를 `LD_PRELOAD`로 주입하면  
기존 V4L2 ioctl 코드가 그대로 동작합니다.

```bash
./start.sh
```

`start.sh`가 자동으로 `v4l2-compat.so` 경로를 탐색하여 `LD_PRELOAD`를 설정합니다.

수동으로 실행하려면:

```bash
LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libcamera/v4l2-compat.so python server.py
```

> v4l2-compat.so가 없을 경우: `sudo apt install libcamera-dev`

### 32비트 Pi OS (레거시 드라이버 사용)

```bash
# 레거시 카메라 드라이버 로드
sudo modprobe bcm2835-v4l2

# 서버 실행
python server.py
```

부팅 시 자동 로드하려면:
```bash
echo "bcm2835-v4l2" | sudo tee -a /etc/modules
```

---

## 엔드포인트

| URL | 내용 |
|---|---|
| `http://<Pi IP>:8000/` | 브라우저 뷰어 페이지 |
| `http://<Pi IP>:8000/stream` | MJPEG 무한 스트림 |
| `http://<Pi IP>:8000/snapshot` | JPEG 단일 프레임 캡처 |
| `http://<Pi IP>:8000/docs` | FastAPI 자동 API 문서 |

---

## 환경변수 설정

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CAM_DEVICE` | `/dev/video0` | V4L2 디바이스 경로 |
| `CAM_WIDTH` | `1280` | 캡처 가로 해상도 |
| `CAM_HEIGHT` | `720` | 캡처 세로 해상도 |
| `CAM_BUFFERS` | `4` | mmap 버퍼 개수 |
| `CAM_QUALITY` | `80` | JPEG 인코딩 품질 (0~100) |
| `CAM_TARGET_FPS` | `30` | 목표 프레임 레이트 |
| `SERVER_HOST` | `0.0.0.0` | 서버 바인딩 호스트 |
| `SERVER_PORT` | `8000` | 서버 포트 |

예시:

```bash
# VGA 해상도, 15fps, 낮은 대역폭 설정
CAM_WIDTH=640 CAM_HEIGHT=480 CAM_TARGET_FPS=15 ./start.sh

# 1080p 고화질
CAM_WIDTH=1920 CAM_HEIGHT=1080 CAM_QUALITY=90 ./start.sh
```

---

## 예상 성능 (Pi Zero 2W 기준)

| 해상도 | 예상 FPS | 비고 |
|---|---|---|
| 1080p (1920×1080) | ~30 FPS | 메모리 대역폭 한계치 |
| 720p (1280×720) | 60+ FPS | 안정적 (기본값) |
| VGA (640×480) | 90+ FPS | 고속 촬영 |

---

## 구현 원리

```
Device Open   os.open('/dev/video0', O_RDWR | O_NONBLOCK)
     ↓
VIDIOC_S_FMT  해상도 + YUYV 포맷 설정
     ↓
VIDIOC_REQBUFS  mmap 버퍼 4개 요청
     ↓
mmap()          커널 버퍼 → 유저 공간 Zero-copy 매핑
     ↓
VIDIOC_STREAMON 스트리밍 시작
     ↓
loop:
  select()           프레임 준비 대기
  VIDIOC_DQBUF       채워진 버퍼 가져오기
  numpy.frombuffer() Zero-copy ndarray 변환
  cv2.cvtColor()     YUYV → BGR 변환
  cv2.imencode()     JPEG 인코딩
  VIDIOC_QBUF        버퍼 반환
```
