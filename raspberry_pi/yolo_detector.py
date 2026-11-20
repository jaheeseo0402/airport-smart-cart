# -*- coding: utf-8 -*-
"""
yolo_detector.py : 파이 카메라 모듈로 사람(person)을 감지해 최신 결과를 계속 갱신하는 쓰레드 (팀원이 작성)
get() -> {"person": bool, "count": int, "max_area": 0~1(화면에서 가장 큰 사람 박스가 차지하는 비율, 클수록 가까움), "fps": float, "t": 시각}
필요: pip install ultralytics  (picamera2 는 파이 OS 기본 패키지)  / 단독 테스트: python3 yolo_detector.py
"""
import threading, time
from picamera2 import Picamera2
from ultralytics import YOLO

class YoloDetector:
    """카메라로 사람을 감지해서 최신 결과를 계속 갱신 (스레드)"""

    def __init__(self, model_path="yolo11n.pt", imgsz=320, conf=0.5):
        self.model = YOLO(model_path)
        self.imgsz, self.conf = imgsz, conf
        self.cam = Picamera2()
        self.cam.configure(self.cam.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"}))
        self.lock = threading.Lock()
        self.result = {"person": False, "count": 0, "max_area": 0.0, "fps": 0.0, "t": 0.0}
        self.running = False

    def start(self):
        self.cam.start()
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False
        self.cam.stop()

    def get(self):
        with self.lock:
            return dict(self.result)

    def _loop(self):
        while self.running:
            t0 = time.time()
            frame = self.cam.capture_array()
            r = self.model(frame, imgsz=self.imgsz, conf=self.conf,
                           classes=[0], verbose=False)[0]   # 0 = person
            h, w = frame.shape[:2]
            areas = [float((b[2]-b[0]) * (b[3]-b[1])) / (w*h) for b in r.boxes.xyxy.tolist()]
            with self.lock:
                self.result = {
                    "person": len(areas) > 0,
                    "count": len(areas),
                    "max_area": max(areas, default=0.0),  # 화면에서 차지하는 비율 → 클수록 가까움
                    "fps": 1.0 / (time.time() - t0),
                    "t": time.time(),
                }

if __name__ == "__main__":      # 단독 테스트
    det = YoloDetector()
    det.start()
    try:
        while True:
            r = det.get()
            print(f"사람 {r['count']}명  가장 큰 박스 {r['max_area']*100:4.1f}%  {r['fps']:.1f} FPS")
            time.sleep(0.5)
    except KeyboardInterrupt:
        det.stop()
