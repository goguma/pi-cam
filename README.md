# Pi Camera Streaming Server

Raspberry Pi Zero 2W + Camera Module 2 (IMX219)를 위한  
**picamera2(libcamera)** 기반 MJPEG 스트리밍 서버입니다.

---

## 파일 구성

```
pi_cam_v4l2.py   카메라 제어 모듈 (picamera2 기반 PiCamera 클래스)
server.py        FastAPI MJPEG 스트리밍 서버
start.sh         런처 스크립트
requirements.txt Python 패키지 목록
```

---

## 설치

### 1. 시스템 패키지 (apt)

```bash
sudo apt update
sudo apt install -y python3-picamera2
```

### 2. Python 가상환경 생성

picamera2는 시스템 패키지이므로 venv에서 접근하려면  
`--system-site-packages` 옵션이 필요합니다.

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
```

### 3. pip 패키지 설치

```bash
pip install -r requirements.txt
```

### 4. 실행 권한 부여

```bash
chmod +x start.sh
```

---

## 실행

```bash
./start.sh
```

또는 직접:

```bash
python server.py
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
| `CAM_WIDTH` | `1280` | 캡처 가로 해상도 |
| `CAM_HEIGHT` | `720` | 캡처 세로 해상도 |
| `CAM_BUFFERS` | `4` | picamera2 버퍼 개수 |
| `CAM_QUALITY` | `80` | JPEG 인코딩 품질 (0~100) |
| `CAM_TARGET_FPS` | `30` | 목표 프레임 레이트 |
| `SERVER_HOST` | `0.0.0.0` | 서버 바인딩 호스트 |
| `SERVER_PORT` | `8000` | 서버 포트 |

예시:

```bash
# VGA 해상도, 15fps
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
picamera2.create_video_configuration(format="BGR888")
         ↓
picamera2.start()
         ↓
loop:
  capture_array("main")      BGR numpy array (H×W×3)
  cv2.imencode(".jpg")       JPEG 인코딩
  FastAPI StreamingResponse  MJPEG multipart 스트림
```

---

## 동작 환경

- **OS**: Raspberry Pi OS Bookworm (64-bit)
- **카메라**: Camera Module 2 (IMX219)
- **드라이버**: libcamera v0.7.0+ (picamera2)
- **Python**: 3.11+
