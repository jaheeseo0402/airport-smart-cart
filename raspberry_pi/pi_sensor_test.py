# -*- coding: utf-8 -*-
"""
pi_sensor_test.py : 초음파 4개 + 라인센서 2개 값을 라즈베리파이 화면에서 확인하는 시험 코드

- 아두이노가 보내는 "D:앞,왼,오,뒤" (cm) 를 받아 보여 준다. (-1 = 측정 없음)
- 0.5초마다 아두이노에 'I' 를 보내 라인센서 값("IR 왼쪽/오른쪽 = a b")을 받아 같이 보여 준다.
- 시험 중에 바퀴가 돌지 않도록, 시작할 때 'S'(정지)를 ACK 가 올 때까지 계속 보낸다.
- 정지/재출발 판단은 하지 않는다. (판단은 pi_obstacle_stop4.py)

필요: pip install pyserial
실행: python3 pi_sensor_test.py                 (포트 자동 탐색)
      python3 pi_sensor_test.py /dev/ttyACM0
종료: Ctrl + C
"""

import re
import sys
import time
import glob
import serial

BLACK_L = 1     # 아두이노 코드의 BLACK_L 과 같게 (검정에서 찍히는 값)
BLACK_R = 1
NAMES = ["앞", "왼", "오", "뒤"]
IR_PATTERN = re.compile(r"IR.*=\s*(\d)\s+(\d)")


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
    return values if len(values) == 4 else None


def parse_line_sensors(line):
    """'IR 왼쪽/오른쪽 = 1 0' -> (1, 0). 아니면 None"""
    m = IR_PATTERN.search(line)
    return (int(m.group(1)), int(m.group(2))) if m else None


def fmt_distance(cm):
    return " 없음 " if cm < 0 else f"{cm:5.1f}cm"


def fmt_line(value, black_value):
    return f"{value}({'검정' if value == black_value else '흰색'})"


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    if port is None:
        print("시리얼 포트를 찾지 못했습니다. 아두이노 USB 연결을 확인하세요.")
        return

    ser = serial.Serial(port, 9600, timeout=0.2)
    print(f"{port} 연결됨. 아두이노 재시작 대기(약 6초)... Ctrl+C 로 종료")
    time.sleep(2)
    ser.reset_input_buffer()

    ir = None               # 마지막으로 받은 라인센서 값 (왼, 오)
    stopped_ack = False     # 'S' 에 대한 ACK 를 받았는지
    last_cmd = 0.0

    try:
        while True:
            now = time.time()
            if now - last_cmd >= 0.5:
                last_cmd = now
                if not stopped_ack:
                    ser.write(b"S\n")      # 시험 중 바퀴가 돌지 않게 정지
                ser.write(b"I\n")          # 라인센서 값 요청

            line = ser.readline().decode(errors="ignore").strip()
            if not line:
                continue

            if line.startswith("ACK:S"):
                stopped_ack = True
                print("(정지 상태로 유지 - 바퀴가 돌지 않습니다)")
                continue

            sensors = parse_line_sensors(line)
            if sensors:
                ir = sensors
                continue

            distances = parse_distances(line)
            if distances:
                dist_text = "  ".join(f"{n} {fmt_distance(d)}" for n, d in zip(NAMES, distances))
                if ir is None:
                    line_text = "라인 (대기중)"
                else:
                    line_text = f"라인 왼 {fmt_line(ir[0], BLACK_L)} 오 {fmt_line(ir[1], BLACK_R)}"
                print(f"{dist_text}  | {line_text}")
                continue

            print("아두이노:", line)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ser.write(b"S\n")
        finally:
            ser.close()
        print("종료")


if __name__ == "__main__":
    main()
