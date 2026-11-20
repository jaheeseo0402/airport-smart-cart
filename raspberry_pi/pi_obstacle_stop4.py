# -*- coding: utf-8 -*-
"""
pi_obstacle_stop4.py  (초음파 4개: 앞/왼쪽/오른쪽/뒤)

아두이노가 보내는 "D:앞,왼,오,뒤" (cm)를 받아 각 센서를 칼만필터 + TTC + 위험 단계로 판단하고,
가장 위험한 방향이 CRITICAL/HIGH 이면 아두이노에 'S'(정지),
앞이 비어 있는 상태가 잠시 이어지면 'G'(재출발)를 보낸다.

필요: 같은 폴더에 week1_obstacle_logic.py,  pip install pyserial
실행: python3 pi_obstacle_stop4.py            (포트 자동 탐색)
      python3 pi_obstacle_stop4.py /dev/ttyUSB1
종료: Ctrl + C (종료할 때 정지 명령을 보낸다)
"""

import sys
import time
import glob
import serial

from week1_obstacle_logic import SensorChannel, Direction, ThreatLevel, resolve_priority, BASE_THRESHOLDS_CM

# ---- 방향별 기준 (cm) : 정지는 HIGH 이상에서 걸린다 ----
BASE_THRESHOLDS_CM["front"] = {"CRITICAL": 10, "HIGH": 20, "MEDIUM": 40}   # 앞: 20cm 이하 정지
BASE_THRESHOLDS_CM["side"]  = {"CRITICAL": 5,  "HIGH": 8,  "MEDIUM": 15}   # 왼/오: 8cm 이하 정지
# 뒤는 정지 판단에 쓰지 않는다 (앞으로만 가므로). 화면 표시만 한다.

DT = 0.1                  # 아두이노 전송 주기(100ms). 합친 코드의 CYCLE_MS 와 같아야 함
RESUME_DIST_M = 0.30      # 앞 센서가 이 거리(30cm)보다 멀어야 재출발 가능 (옆/뒤는 무시)
RESUME_FRAMES = 5         # 그 상태가 이만큼(약 0.5초) 이어져야 재출발

DIRECTIONS = [Direction.S1_FRONT, Direction.S2_LEFT, Direction.S3_RIGHT, Direction.S6_REAR]   # 앞, 왼, 오
NAMES = ["앞", "왼", "오", "뒤"]


def find_port():
    ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    return ports[0] if ports else None


class ObstacleStopper:
    """거리 4개를 받아 정지/재출발 명령('S'/'G'/None)을 돌려주는 판단 부분"""

    def __init__(self):
        self.channels = [SensorChannel(direction=d, dt=DT) for d in DIRECTIONS]
        self.stopped = False
        self.clear_count = 0

    def update(self, distances_cm, now_t):
        """distances_cm: [앞, 왼, 오, 뒤] (cm). 측정 실패(<=0)는 해당 센서만 이번 프레임에서 건너뜀."""
        results = []
        for ch, cm in zip(self.channels, distances_cm):
            if cm is None or cm <= 0:
                results.append(None)
                continue
            results.append(ch.process(cm / 100.0, now_t))

        # 뒤 센서는 정지 판단에서 제외 (앞으로만 주행)
        valid = [r for r, d in zip(results, DIRECTIONS)
                 if r is not None and d != Direction.S6_REAR]
        top = resolve_priority(valid)
        command = None

        if not self.stopped:
            if top and top["level"] in (ThreatLevel.CRITICAL, ThreatLevel.HIGH):
                command = "S"
                self.stopped = True
                self.clear_count = 0
                for ch, r in zip(self.channels, results):
                    if r is not None and r["direction"] == top["direction"]:
                        ch.trigger_emergency_brake(now_t)
        else:
            # 측정 실패(-1)는 '2m 안에 아무것도 없음'이므로 안전(멀다)으로 센다
            front = results[0]   # 앞 센서만 본다
            all_clear = front is None or front["distance_m"] > RESUME_DIST_M
            self.clear_count = self.clear_count + 1 if all_clear else 0
            if self.clear_count >= RESUME_FRAMES:
                command = "G"
                self.stopped = False
                self.clear_count = 0

        return command, results, top


def parse_distances(line):
    """'D:12.3,45.6,-1.0' -> [12.3, 45.6, -1.0]. 형식이 다르면 None"""
    if not line.startswith("D:"):
        return None
    try:
        values = [float(x) for x in line[2:].split(",")]
    except ValueError:
        return None
    return values if len(values) == len(DIRECTIONS) else None


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    if port is None:
        print("시리얼 포트를 찾지 못했습니다. 아두이노 USB 연결을 확인하세요.")
        return

    ser = serial.Serial(port, 9600, timeout=0.2)
    time.sleep(2)
    ser.reset_input_buffer()
    print(f"{port} 연결됨. Ctrl+C 로 종료")

    stopper = ObstacleStopper()
    try:
        while True:
            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue
            distances = parse_distances(line)
            if distances is None:
                print("아두이노:", line)
                continue

            command, results, top = stopper.update(distances, time.time())

            parts = []
            for name, r in zip(NAMES, results):
                parts.append(f"{name} --" if r is None else f"{name} {r['distance_m']*100:5.1f}cm")
            level = top["level"].name if top else "NONE"
            ttc = top["ttc_s"] if top else None
            print("  ".join(parts) + f"  | 위험 {level:<8} TTC {ttc}  "
                  f"{'정지중' if stopper.stopped else '주행중'}")

            if command:
                ser.write((command + "\n").encode())
                print(f">>> 명령 전송: {command}")
    except KeyboardInterrupt:
        pass
    finally:
        ser.write(b"S\n")
        ser.close()
        print("종료 (정지 명령 전송)")


if __name__ == "__main__":
    main()
