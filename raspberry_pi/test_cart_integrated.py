# -*- coding: utf-8 -*-
"""cart_integrated.py 시험: 가짜 시리얼로 '목적지 없으면 출발 안 함 / 목적지 선택 후 출발 / 위험하면 정지 / 취소하면 정지' 확인"""
import os, sys, time, threading
os.environ.setdefault("QR_SECRET_KEY", "test-qr")
os.environ.setdefault("CART_SESSION_SECRET", "test-session")
os.environ.setdefault("CART_PASSWORD", "test-pass")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cart_integrated as ci

class FakeSerial:
    def __init__(self, *a, **k):
        self.sent, self.front, self.closed = [], 100.0, False
    def reset_input_buffer(self): pass
    def write(self, b): self.sent.append(b.decode().strip())
    def readline(self):
        time.sleep(0.02)
        return f"D:{self.front},100,100,100\n".encode()
    def close(self): self.closed = True

fake = FakeSerial()
ci.ctrl.RESUME_HOLD_S = 0.2
ci.ctrl.USE_LINE_LOST = False
ci.time.sleep = lambda s: None  # 시작 대기 생략 (이 모듈의 time만)
stop = threading.Event()
th = threading.Thread(target=ci.controller_loop, args=("fake", stop, lambda *a, **k: fake), daemon=True)
th.start()

def wait(cond, t=3):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.02)
    return False

results = []
def check(name, ok): results.append(ok); print(("통과 " if ok else "실패 "), name)

time.sleep(0.5)
check("목적지 없으면 G 를 보내지 않음", "G" not in fake.sent and ci.cart_status["stopped"])

c = ci.app.test_client()
check("로그인 없이 /status 거부", c.get("/status").status_code == 401)
c.post("/login", data={"username": "passenger1", "password": "test-pass"})
qr = ci.cart_web.GATE_QR["B12"]
c.post("/select", data={"qr": qr})
check("목적지 선택 후 G 전송", wait(lambda: "G" in fake.sent))
check("/status 에 목적지 표시", c.get("/status").get_json()["destination"] == "B12")

n = len(fake.sent); fake.front = 10.0
check("앞 10cm 장애물 -> S 전송", wait(lambda: "S" in fake.sent[n:]))
fake.front = 100.0
check("치워지면 다시 G", wait(lambda: "G" in fake.sent[n:]))

n = len(fake.sent); c.post("/cancel")
check("취소하면 S 전송", wait(lambda: "S" in fake.sent[n:]))
time.sleep(0.4)
check("취소 후 G 안 보냄", "G" not in fake.sent[n:])
stop.set(); th.join(2)
check("종료 시 시리얼 닫힘", fake.closed)
print("전체", "통과" if all(results) else "실패"); sys.exit(0 if all(results) else 1)
