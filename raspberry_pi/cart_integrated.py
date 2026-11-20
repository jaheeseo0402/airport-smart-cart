# -*- coding: utf-8 -*-
"""
cart_integrated.py : 웹 서버(Flask) + 정지/재출발 판단(main.py)을 한 프로그램으로 합친 파일

왜 합치나: 시리얼 포트는 프로그램 하나만 열 수 있어서,
          웹 서버와 정지 판단 코드를 따로 실행하면 둘 중 하나가 아두이노를 못 쓴다.

구조 (프로세스 1개)
  - 쓰레드 1(백그라운드): 시리얼을 열고 main.py 의 Brain 으로 정지/재출발 판단 -> 아두이노에 S/G
  - 쓰레드 2(메인): Flask 웹서버. 로그인 + 서명 QR 검증을 통과하면 목적지를 저장
  - 연결 규칙: 목적지가 선택돼야 카트가 출발할 수 있다. 목적지가 없으면 항상 정지('S').
               안전 정지(초음파/라인/사람)는 목적지와 상관없이 항상 우선한다.
  - 추가 주소: /status (로그인 필요) 현재 목적지/정지 여부/정지 이유 JSON, /cancel (POST) 목적지 취소 + 정지

실행 (환경변수 3개 필요: QR_SECRET_KEY, CART_SESSION_SECRET, CART_PASSWORD)
  cd raspberry_pi
  python3 cart_integrated.py              (포트 자동 탐색)
  python3 cart_integrated.py /dev/ttyACM0
  브라우저: http://<파이 IP>:5000
종료: Ctrl + C (정지 명령을 보내고 종료)

아직 없는 것: 목적지 "도착" 판단 (게이트 인식은 이 프로토타입에서 구현하지 않음).
              지금은 목적지가 선택되면 라인을 따라 달리고, 정지시키려면 /cancel 또는 웹의 취소 버튼.
"""

import os
import sys
import time
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "web"))   # cart_web.py, qr_signature.py

import serial
from flask import session, jsonify, redirect, url_for

import main as ctrl            # 같은 폴더의 main.py (Brain, 파싱 함수, 설정값)
import cart_web                # 웹 (로그인 + QR 검증). 환경변수가 없으면 여기서 오류가 난다

app = cart_web.app
web_state = cart_web.state     # {"destination": ..., "user": ...}

# 웹 쪽에서 읽는 현재 카트 상태 (컨트롤러 쓰레드가 갱신)
cart_status = {"stopped": True, "reasons": ["시작"], "serial": "연결 전", "updated": 0.0}
status_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 웹에 추가한 주소
# ---------------------------------------------------------------------------
@app.route("/status")
def status():
    if "user" not in session:
        return jsonify(ok=False, reason="로그인 필요"), 401
    with status_lock:
        snap = dict(cart_status)
    snap["destination"] = web_state["destination"]
    return jsonify(ok=True, **snap)


@app.route("/cancel", methods=["POST"])
def cancel():
    if "user" not in session:
        return jsonify(ok=False, reason="로그인 필요"), 401
    web_state["destination"] = None
    web_state["user"] = None
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# 컨트롤러 (main.py 의 루프와 같은 판단. 차이: 목적지가 없으면 출발하지 않음)
# ---------------------------------------------------------------------------
def controller_loop(port, stop_event, serial_factory=serial.Serial):
    ser = serial_factory(port, ctrl.BAUD, timeout=0.05)
    with status_lock:
        cart_status["serial"] = f"{port} 연결됨"
    time.sleep(2)                       # 아두이노 재시작 대기
    ser.reset_input_buffer()

    def send(cmd):
        ser.write((cmd + "\n").encode())

    brain = ctrl.Brain()
    send("S")
    last_stop_sent = time.time()
    last_poll = 0.0

    try:
        while not stop_event.is_set():
            now = time.time()

            if ctrl.USE_LINE_LOST and now - last_poll >= ctrl.LINE_POLL_S:
                last_poll = now
                send("I")

            # 목적지가 없으면 항상 정지 상태로 둔다 (판단기가 G를 내지 못하게 막는다)
            no_dest = web_state["destination"] is None
            if no_dest:
                if not brain.stopped:
                    send("S")           # 도중에 취소된 경우 즉시 정지
                    last_stop_sent = now
                brain.stopped = True
                brain.safe_since = None
                brain.reasons = ["목적지 없음"]

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
                        # 목적지가 없을 때는 decide()를 부르지 않는다 (G 방지). 위험 판단만 갱신
                        cmd = None if no_dest else brain.decide(now, None)
                        if cmd:
                            send(cmd)
                            if cmd == "S":
                                last_stop_sent = now
                            print(f">>> {cmd} 전송  {ctrl.status_text(brain, None, now)}")

            with status_lock:
                cart_status["stopped"] = brain.stopped
                cart_status["reasons"] = list(brain.reasons)
                cart_status["updated"] = now
    finally:
        try:
            send("S")
        finally:
            ser.close()
            print("컨트롤러 종료 (정지 명령 전송)")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else ctrl.find_port()
    if port is None:
        print("시리얼 포트를 찾지 못했습니다. 아두이노 USB 연결을 확인하세요.")
        return

    stop_event = threading.Event()
    t = threading.Thread(target=controller_loop, args=(port, stop_event), daemon=True)
    t.start()
    try:
        app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
    finally:
        stop_event.set()
        t.join(timeout=3)


if __name__ == "__main__":
    main()
