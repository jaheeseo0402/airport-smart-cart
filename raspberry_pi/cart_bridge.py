# -*- coding: utf-8 -*-
"""
cart_bridge.py : 카트 프로그램 (시리얼 + 정지 판단) + 웹(cart_web.py)과 HTTP로 연결

구조: 프로세스 2개
  [웹]  CART_SIM=0 python3 cart_web.py        <- 시리얼을 열지 않음 (웹 담당 팀원 코드)
  [카트] python3 cart_bridge.py /dev/ttyACM0    <- 시리얼을 이 프로그램만 엶 (포트 충돌 없음)
  연결: 카트가 웹의 주소를 0.1초마다 읽고, 상태를 보내 준다 (파이 안에서만: 127.0.0.1:5000)
    GET  /robot/command  -> {"phase":"driving","gate":"A","stopped":false}
    POST /robot/status   -> {"progress":0.4,"message":"...","tier":"위험","ttc":1.6}   도착: {"arrived": true}

규칙
  - 출발 허가: phase == "driving" 그리고 stopped == false. 아니면 항상 정지('S').
  - 웹에 연결이 안 되면 안전하게 정지한다.
  - 안전 정지(초음파/라인)는 허가와 상관없이 항상 우선한다. (main.py 의 Brain 판단을 그대로 씀)
  - 이 버전은 YOLO(카메라)를 쓰지 않는다.

자동 반납(우회 통로 방식): 게이트 뒤로 라인이 이어져 출발점으로 돌아오는 우회 통로(순환 경로)를 만든다.
  승객이 반납을 누르면(phase == "returning") 카트가 앞으로 다시 출발해 라인을 따라 달리고,
  RETURN_LAP_SECONDS(게이트에서 출발점까지 달리는 시간)가 되면 정지한 뒤 {"returned": true} 를 보고한다.
  복귀 중에도 장애물 정지/재출발은 그대로 동작한다. 아두이노 코드는 바꿀 필요가 없다. 커브가 있어도 된다.
  RETURN_LAP_SECONDS 가 None 이면 움직이지 않고 3초 뒤 복귀 완료만 보고한다.

주의(데모 단순화): 라인 센서만으로는 "도착"을 알 수 없어서,
  TRIP_SECONDS 를 정하면 '주행한 시간'이 그 값에 이르렀을 때 도착으로 보고하고 progress 도 시간 비율로 보낸다.
  None 이면 도착을 보고하지 않는다. (보고서에는 시간 기반 단순화라고 적을 것)

종료: Ctrl + C (정지 명령 전송)
"""

import sys
import json
import time
import threading
import urllib.request

import serial

import main as ctrl            # 같은 폴더의 main.py (Brain, 파싱 함수, 설정값)
from week1_obstacle_logic import ThreatLevel

WEB = "http://127.0.0.1:5000"
COMMAND_POLL_S = 0.1
STATUS_POST_S = 0.3
HTTP_TIMEOUT_S = 0.5
TRIP_SECONDS = None             # 예: 25 (주행 시간이 이만큼이면 도착 보고). None = 도착 보고 안 함

RETURN_SECONDS = 3.0            # RETURN_LAP_SECONDS 가 None 일 때만: 반납 후 이 시간이 지나면 "복귀 완료"만 보고 (카트는 안 움직임)
RETURN_LAP_SECONDS = None       # 반납 후 우회 통로를 달려 출발점에 닿는 시간(초). 예: 15. None = 움직이지 않고 복귀 완료만 보고

TIER_NAME = {ThreatLevel.NONE: "안전", ThreatLevel.MEDIUM: "주의",
             ThreatLevel.HIGH: "위험", ThreatLevel.CRITICAL: "임박"}   # 웹 화면의 단계: 안전/주의/위험/임박/정지


class WebLink(threading.Thread):
    """웹과 주고받는 쓰레드. 시리얼 루프가 HTTP 때문에 느려지지 않게 따로 돈다."""

    def __init__(self, stop_event, base=WEB):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.base = base
        self.lock = threading.Lock()
        self.command = None            # 마지막으로 받은 command (실패하면 None)
        self.command_t = 0.0
        self.outbox = None             # 보낼 status (가장 최근 것만)

    def get_command(self, max_age=1.0):
        with self.lock:
            if self.command is None or time.time() - self.command_t > max_age:
                return None
            return dict(self.command)

    def post_status(self, payload):
        with self.lock:
            self.outbox = dict(payload)

    def _get(self):
        with urllib.request.urlopen(self.base + "/robot/command", timeout=HTTP_TIMEOUT_S) as r:
            return json.loads(r.read().decode())

    def _post(self, payload):
        req = urllib.request.Request(self.base + "/robot/status", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S).read()

    def run(self):
        last_post = 0.0
        while not self.stop_event.is_set():
            try:
                cmd = self._get()
                with self.lock:
                    self.command, self.command_t = cmd, time.time()
            except Exception:
                with self.lock:
                    self.command = None
            now = time.time()
            if now - last_post >= STATUS_POST_S or self._has_arrived():
                with self.lock:
                    payload, self.outbox = self.outbox, None
                if payload:
                    try:
                        self._post(payload)
                    except Exception:
                        pass
                last_post = now
            time.sleep(COMMAND_POLL_S)

    def _has_arrived(self):
        with self.lock:
            return bool(self.outbox and self.outbox.get("arrived"))


def allowed_to_go(cmd):
    """출발 허가: 주행 중(driving)이거나, 우회 통로로 복귀 중(returning, RETURN_LAP_SECONDS 설정 시)이고 정지 버튼이 눌리지 않았을 때"""
    if not cmd or cmd.get("stopped", False):
        return False
    phase = cmd.get("phase")
    return phase == "driving" or (phase == "returning" and RETURN_LAP_SECONDS is not None)


def build_status(brain, driven_s):
    top = brain.us_top
    tier = TIER_NAME[top["level"]] if top else "안전"
    ttc = top["ttc_s"] if top else None
    if brain.stopped:
        tier = "정지"
        message = "정지 · " + ", ".join(brain.reasons) if brain.reasons else "정지"
    else:
        message = "주행 중"
    progress = None if not TRIP_SECONDS else min(1.0, driven_s / TRIP_SECONDS)
    payload = {"message": message, "tier": tier, "ttc": ttc}
    if progress is not None:
        payload["progress"] = round(progress, 2)
    return payload


def controller_loop(port, stop_event, link, serial_factory=serial.Serial):
    ser = serial_factory(port, ctrl.BAUD, timeout=0.05)
    time.sleep(2)
    ser.reset_input_buffer()

    def send(cmd):
        ser.write((cmd + "\n").encode())

    brain = ctrl.Brain()
    send("S")
    last_stop_sent = time.time()
    last_poll = 0.0
    driven_s = 0.0
    last_t = time.time()
    arrived_sent = False
    returning_since = None
    returned_sent = False
    lap_s = 0.0
    try:
        while not stop_event.is_set():
            now = time.time()
            if ctrl.USE_LINE_LOST and now - last_poll >= ctrl.LINE_POLL_S:
                last_poll = now
                send("I")

            cmd_now = link.get_command()
            phase = cmd_now.get("phase") if cmd_now else None
            allowed = allowed_to_go(cmd_now)
            if (phase == "driving" and arrived_sent) or (phase == "returning" and returned_sent):
                allowed = False                      # 도착/복귀 보고를 한 뒤에는 웹 상태가 바뀔 때까지 다시 출발하지 않음
            dt = now - last_t
            lap_mode = (phase == "returning" and RETURN_LAP_SECONDS is not None and not returned_sent)

            if not allowed:
                if not brain.stopped:
                    send("S")
                    last_stop_sent = now
                brain.stopped = True
                brain.safe_since = None
                brain.reasons = ["웹 허가 없음"]
                if phase != "driving":
                    driven_s, arrived_sent = 0.0, False
                if phase != "returning":
                    lap_s = 0.0
            elif not brain.stopped:
                if phase == "driving":
                    driven_s += dt
                elif lap_mode:
                    lap_s += dt
            last_t = now

            # 우회 통로 복귀: 정해진 시간만큼 달렸으면 정지하고 복귀 완료 보고
            if lap_mode and lap_s >= RETURN_LAP_SECONDS:
                send("S")
                brain.stopped = True
                brain.reasons = ["대기소 도착"]
                link.post_status({"returned": True})
                returned_sent = True
                print(">>> 자동 반납 완료")

            # RETURN_LAP_SECONDS 가 없으면 움직이지 않고 복귀 완료만 보고 (화면용 단순화)
            if phase == "returning" and RETURN_LAP_SECONDS is None:
                if returning_since is None:
                    returning_since = now
                elif not returned_sent and now - returning_since >= RETURN_SECONDS:
                    link.post_status({"returned": True})
                    returned_sent = True
            elif phase != "returning":
                returning_since, returned_sent = None, False

            if brain.stopped and now - last_stop_sent >= ctrl.RESEND_STOP_S:
                last_stop_sent = now
                send("S")

            line = ser.readline().decode(errors="ignore").strip()
            if line:
                ir = ctrl.parse_line_sensors(line)
                if ir:
                    brain.on_line(ir[0], ir[1], now)
                else:
                    distances = ctrl.parse_distances(line)
                    if distances:
                        brain.on_distances(distances, now)
                        cmd = None if not allowed else brain.decide(now, None)
                        if not allowed:
                            brain.ultrasonic_danger()          # 상태 표시용 갱신
                        if cmd:
                            send(cmd)
                            if cmd == "S":
                                last_stop_sent = now
                            print(f">>> {cmd} 전송  {ctrl.status_text(brain, None, now)}")

            if allowed and TRIP_SECONDS and driven_s >= TRIP_SECONDS and not arrived_sent:
                send("S")
                brain.stopped = True
                brain.reasons = ["도착"]
                link.post_status({"progress": 1.0, "message": "도착", "tier": "안전", "ttc": None, "arrived": True})
                arrived_sent = True
                print(">>> 도착 보고")
            elif phase == "driving" and not arrived_sent:
                link.post_status(build_status(brain, driven_s))
    finally:
        try:
            send("S")
        finally:
            ser.close()
            print("카트 프로그램 종료 (정지 명령 전송)")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else ctrl.find_port()
    if port is None:
        print("시리얼 포트를 찾지 못했습니다. 아두이노 USB 연결을 확인하세요.")
        return
    stop_event = threading.Event()
    link = WebLink(stop_event)
    link.start()
    try:
        controller_loop(port, stop_event, link)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
