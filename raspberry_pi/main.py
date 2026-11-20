# -*- coding: utf-8 -*-
"""
main.py : 초음파 4개 + 라인센서 2개 + 카메라(YOLO) 통합 정지/재출발 판단

역할 분담
- 아두이노: 라인 따라가기(조향), 초음파 측정, "D:앞,왼,오,뒤" 전송, S/G 명령대로 정지/출발
- 라즈베리파이(이 파일): 칼만필터 + TTC + 위험 단계, YOLO 사람 감지, 라인 이탈 감시 → S/G 결정

정지 조건 (하나라도 해당되면 'S')
  1. 초음파: 앞/왼/오 중 위험 단계 HIGH 이상 (뒤는 화면 표시만)
  2. 카메라: 사람 박스가 화면의 PERSON_AREA_STOP 이상 (= 가까운 사람)
  3. 라인: 라인센서 둘 다 흰색이 LINE_LOST_S 이상 계속 (= 라인 이탈)
재출발 조건 ('G')
  초음파 앞이 RESUME_DIST_M(30cm) 보다 멀고 + 가까운 사람 없음 + 라인 위
  → 이 상태가 RESUME_HOLD_S 동안 유지
  (라인 이탈로 멈췄다면 카트를 라인 위에 다시 올려야 재출발)

필요: 같은 폴더에 week1_obstacle_logic.py, yolo_detector.py
      pip install pyserial ultralytics
실행: python3 main.py               (포트 자동 탐색)
      python3 main.py /dev/ttyACM0
종료: Ctrl + C (종료할 때 정지 명령을 보낸다)
"""

import re
import sys
import time
import glob
import serial

from week1_obstacle_logic import SensorChannel, Direction, ThreatLevel, resolve_priority, BASE_THRESHOLDS_CM

# ---- 방향별 위험 기준 (cm): 정지는 HIGH 이상에서 걸린다 ----
BASE_THRESHOLDS_CM["front"] = {"CRITICAL": 10, "HIGH": 20, "MEDIUM": 40}   # 앞: 20cm 이하 정지
BASE_THRESHOLDS_CM["side"]  = {"CRITICAL": 5,  "HIGH": 8,  "MEDIUM": 15}   # 왼/오: 8cm 이하 정지

# ---------------------------------------------------------------------------
# 설정값 (테스트하면서 여기만 조정)
# ---------------------------------------------------------------------------
BAUD = 9600
DT = 0.1                    # 아두이노 거리 전송 주기(100ms)

NAMES = ["앞", "왼", "오", "뒤"]
DIRECTIONS = [Direction.S1_FRONT, Direction.S2_LEFT, Direction.S3_RIGHT, Direction.S6_REAR]
USE_FOR_STOP = [True, True, True, False]   # 뒤 센서는 정지 판단에 쓰지 않음
RESUME_DIST_M = 0.30        # 재출발하려면 앞이 이 거리(30cm)보다 멀어야 함 (옆/뒤는 보지 않음)
RESUME_HOLD_S = 0.5         # 안전한 상태가 이만큼 유지돼야 재출발

USE_YOLO = True
PERSON_AREA_STOP = 0.30     # 사람 박스가 화면의 30% 이상이면 정지
YOLO_STALE_S = 1.0          # YOLO 결과가 이보다 오래되면 카메라 판단 무시

USE_LINE_LOST = True
BLACK_L = 1                 # 아두이노 코드의 BLACK_L 과 같게
BLACK_R = 1
LINE_POLL_S = 0.5           # 라인센서 값 요청('I') 주기
LINE_LOST_S = 4.0           # 둘 다 흰색이 이만큼 계속되면 라인 이탈 (아두이노가 급커브에서 라인을 찾아 회전하는 시간 3초보다 길게)
LINE_STALE_S = 2.0          # 라인 값이 이보다 오래되면 라인 판단 무시

RESEND_STOP_S = 1.0         # 정지 중에는 'S'를 이 주기로 다시 보냄 (명령 유실 대비)
PRINT_EVERY_S = 0.3         # 화면 출력 주기

IR_PATTERN = re.compile(r"IR.*=\s*(\d)\s+(\d)")


# ---------------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------------
def find_port():
    ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return ports[0] if ports else None


def parse_distances(line):
    """'D:12.3,45.6,-1.0,30.0' -> [12.3, 45.6, -1.0, 30.0]. 형식이 다르면 None"""
    if not line.startswith("D:"):
        return None
    try:
        values = [float(x) for x in line[2:].split(",")]
    except ValueError:
        return None
    return values if len(values) == len(DIRECTIONS) else None


def parse_line_sensors(line):
    """'IR 왼쪽/오른쪽 = 1 0' -> (1, 0). 아니면 None"""
    m = IR_PATTERN.search(line)
    return (int(m.group(1)), int(m.group(2))) if m else None


# ---------------------------------------------------------------------------
# 판단
# ---------------------------------------------------------------------------
class Brain:
    def __init__(self):
        self.channels = [SensorChannel(direction=d, dt=DT) for d in DIRECTIONS]
        self.us_results = [None] * len(DIRECTIONS)
        self.us_top = None
        self.ir = None
        self.ir_t = 0.0
        self.white_since = None
        self.stopped = True          # 시작은 정지 상태
        self.safe_since = None
        self.reasons = ["시작"]

    # ----- 입력 -----
    def on_distances(self, distances_cm, now):
        results = []
        for ch, cm in zip(self.channels, distances_cm):
            if cm is None or cm <= 0:       # -1 = 측정 없음(멀다)
                results.append(None)
                continue
            results.append(ch.process(cm / 100.0, now))
        self.us_results = results

    def on_line(self, left, right, now):
        self.ir = (left, right)
        self.ir_t = now
        both_white = (left != BLACK_L) and (right != BLACK_R)
        if both_white:
            if self.white_since is None:
                self.white_since = now
        else:
            self.white_since = None

    # ----- 개별 판단 -----
    def ultrasonic_danger(self):
        used = [r for r, use in zip(self.us_results, USE_FOR_STOP) if use and r is not None]
        self.us_top = resolve_priority(used)
        if self.us_top and self.us_top["level"] in (ThreatLevel.CRITICAL, ThreatLevel.HIGH):
            name = NAMES[[d.value for d in DIRECTIONS].index(self.us_top["direction"])]
            return f"초음파 {name} {self.us_top['level'].name}"
        return None

    def ultrasonic_clear(self):
        front = self.us_results[0]          # 앞 센서만 본다 (옆 벽 때문에 못 출발하는 것 방지)
        return front is None or front["distance_m"] > RESUME_DIST_M

    @staticmethod
    def person_near(yolo, now):
        if yolo is None or now - yolo["t"] > YOLO_STALE_S:
            return None                     # 카메라 결과 없음/오래됨 → 판단에서 제외
        if yolo["person"] and yolo["max_area"] >= PERSON_AREA_STOP:
            return f"사람 가까움 {yolo['max_area'] * 100:.0f}%"
        return None

    def line_lost(self, now):
        if not USE_LINE_LOST or self.ir is None or now - self.ir_t > LINE_STALE_S:
            return None
        if self.white_since is not None and now - self.white_since >= LINE_LOST_S:
            return "라인 이탈"
        return None

    # ----- 종합 -----
    def decide(self, now, yolo):
        """'S' / 'G' / None 반환"""
        us = self.ultrasonic_danger()
        person = self.person_near(yolo, now)
        line = self.line_lost(now)

        if not self.stopped:
            reasons = [r for r in (us, person, line) if r]
            if reasons:
                self.stopped = True
                self.safe_since = None
                self.reasons = reasons
                return "S"
            return None

        # 정지 중 → 재출발 가능한지
        blocking = []
        if not self.ultrasonic_clear():
            blocking.append("초음파 가까움")
        if person:
            blocking.append(person)
        if line:
            blocking.append(line)

        if blocking:
            self.safe_since = None
            self.reasons = blocking
            return None

        if self.safe_since is None:
            self.safe_since = now
        if now - self.safe_since >= RESUME_HOLD_S:
            self.stopped = False
            self.safe_since = None
            self.reasons = []
            return "G"
        return None


# ---------------------------------------------------------------------------
# 화면 출력
# ---------------------------------------------------------------------------
def status_text(brain, yolo, now):
    parts = []
    for name, r in zip(NAMES, brain.us_results):
        parts.append(f"{name} --" if r is None else f"{name}{r['distance_m'] * 100:5.1f}")
    us_text = " ".join(parts)

    if brain.ir is None or now - brain.ir_t > LINE_STALE_S:
        line_text = "라인 --"
    else:
        l, r = brain.ir
        line_text = f"라인 {'B' if l == BLACK_L else 'W'}{'B' if r == BLACK_R else 'W'}"

    if yolo is None or now - yolo["t"] > YOLO_STALE_S:
        cam_text = "카메라 --"
    else:
        cam_text = f"사람 {yolo['count']}({yolo['max_area'] * 100:3.0f}%) {yolo['fps']:.1f}fps"

    state = "정지중" if brain.stopped else "주행중"
    why = f" ({', '.join(brain.reasons)})" if brain.stopped and brain.reasons else ""
    return f"{us_text} | {line_text} | {cam_text} | {state}{why}"


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------
def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    if port is None:
        print("시리얼 포트를 찾지 못했습니다. 아두이노 USB 연결을 확인하세요.")
        return

    # YOLO 먼저 켜기 (모델 로딩이 오래 걸림). 실패해도 초음파/라인만으로 동작
    detector = None
    if USE_YOLO:
        try:
            from yolo_detector import YoloDetector
            print("YOLO 로딩 중...")
            detector = YoloDetector()
            detector.start()
            print("YOLO 시작됨")
        except Exception as e:
            print(f"[경고] YOLO를 켜지 못했습니다. 초음파/라인만 사용합니다: {e}")
            detector = None

    ser = serial.Serial(port, BAUD, timeout=0.05)
    print(f"{port} 연결됨. 아두이노 재시작 대기... Ctrl+C 로 종료")
    time.sleep(2)
    ser.reset_input_buffer()

    def send(cmd):
        ser.write((cmd + "\n").encode())

    brain = Brain()
    send("S")
    last_stop_sent = time.time()
    last_poll = 0.0
    last_print = 0.0

    try:
        while True:
            now = time.time()

            if USE_LINE_LOST and now - last_poll >= LINE_POLL_S:
                last_poll = now
                send("I")
            if brain.stopped and now - last_stop_sent >= RESEND_STOP_S:
                last_stop_sent = now
                send("S")

            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue
            if line.startswith("ACK:"):
                continue

            ir = parse_line_sensors(line)
            if ir:
                brain.on_line(ir[0], ir[1], now)
                continue

            distances = parse_distances(line)
            if distances:
                brain.on_distances(distances, now)
                yolo = detector.get() if detector else None
                cmd = brain.decide(now, yolo)
                if cmd:
                    send(cmd)
                    if cmd == "S":
                        last_stop_sent = now
                    print(f">>> {cmd} 전송  {status_text(brain, yolo, now)}")
                    last_print = now
                elif now - last_print >= PRINT_EVERY_S:
                    print(status_text(brain, yolo, now))
                    last_print = now
                continue

            print("아두이노:", line)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            send("S")
        finally:
            ser.close()
            if detector:
                detector.stop()
        print("종료 (정지 명령 전송)")


if __name__ == "__main__":
    main()