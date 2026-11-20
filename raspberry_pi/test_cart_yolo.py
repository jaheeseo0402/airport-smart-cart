# -*- coding: utf-8 -*-
"""카메라(사람 감지)를 합친 cart_bridge 시험: 가짜 카메라 + 가짜 시리얼 + 실제 웹. 사람이 가까우면 정지, 멀어지면 재출발"""
import os, sys, time, threading
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "web")); sys.path.insert(0, HERE)
os.environ.update(CART_SIM="0", CART_ID="07")
os.environ.setdefault("CART_SECRET", "test-only-secret"); os.environ.setdefault("FLASK_SECRET", "test-only-session-secret")
import cart_web
from qr_signature import make_signature
from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, cart_web.app, threaded=True); threading.Thread(target=srv.serve_forever, daemon=True).start()
import cart_bridge as cb

class FakeSerial:
    def __init__(s, *a, **k): s.sent = []; s.closed = False
    def reset_input_buffer(s): pass
    def write(s, b): s.sent.append(b.decode().strip())
    def readline(s): time.sleep(0.02); return b"D:100,100,100,100\n"
    def close(s): s.closed = True
class FakeCam:
    def __init__(s): s.r = {"person": False, "count": 0, "max_area": 0.0, "fps": 5.0, "t": time.time()}; s.auto = True
    def get(s):
        d = dict(s.r)
        if s.auto: d["t"] = time.time()
        return d

fake, cam = FakeSerial(), FakeCam()
_rs = time.sleep; time.sleep = lambda x: None if x == 2 else _rs(x)
cb.ctrl.RESUME_HOLD_S = 0.2; cb.ctrl.USE_LINE_LOST = False
stop = threading.Event(); link = cb.WebLink(stop, base=f"http://127.0.0.1:{srv.server_port}"); link.start()
th = threading.Thread(target=cb.controller_loop, args=("fake", stop, link, lambda *a, **k: fake), kwargs={"detector": cam}, daemon=True); th.start()
res = []
def check(n, ok): res.append(ok); print("통과 " if ok else "실패 ", n)
def wait(c, t=5):
    e = time.time() + t
    while time.time() < e:
        if c(): return True
        time.sleep(0.02)
    return False

phone = cart_web.app.test_client(); sig = make_signature("07")
time.sleep(0.4); phone.get(f"/c/07?sig={sig}"); phone.post("/api/select", json={"gate": "A"})
check("사람 없으면 출발(G)", wait(lambda: "G" in fake.sent))
n = len(fake.sent); cam.r.update(person=True, count=1, max_area=0.5)
check("사람이 화면의 50% (가까움) -> S", wait(lambda: "S" in fake.sent[n:]))
check("웹 화면에 정지 이유(사람 가까움) 표시", wait(lambda: "사람 가까움" in cart_web.cart["message"]))
k = len(fake.sent); time.sleep(0.6); check("사람이 계속 가까우면 재출발 안 함", "G" not in fake.sent[k:])
cam.r.update(max_area=0.1)
check("사람이 멀어지면(10%) 재출발(G)", wait(lambda: "G" in fake.sent[k:]))
n = len(fake.sent); cam.r.update(max_area=0.6); cam.auto = False; cam.r["t"] = time.time() - 5
time.sleep(0.6); check("카메라 결과가 오래되면(1초 초과) 무시 -> 멈추지 않음", "S" not in fake.sent[n:])
stop.set(); th.join(2)
print("전체", "통과" if all(res) else "실패"); os._exit(0 if all(res) else 1)
