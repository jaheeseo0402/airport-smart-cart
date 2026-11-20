# -*- coding: utf-8 -*-
"""cart_bridge.py 시험: 가짜 웹(/robot/command, /robot/status) + 가짜 시리얼"""
import json, sys, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import cart_bridge as cb

web = {"command": {"phase": "ready", "gate": None, "stopped": False}, "statuses": []}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps(web["command"]).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers["Content-Length"]); web["statuses"].append(json.loads(self.rfile.read(n)))
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()

class FakeSerial:
    def __init__(self, *a, **k): self.sent, self.front, self.closed = [], 100.0, False
    def reset_input_buffer(self): pass
    def write(self, b): self.sent.append(b.decode().strip())
    def readline(self):
        time.sleep(0.02); return f"D:{self.front},100,100,100\n".encode()
    def close(self): self.closed = True

fake = FakeSerial()
_real_sleep = time.sleep
time.sleep = lambda s: None if s == 2 else _real_sleep(s)
cb.ctrl.RESUME_HOLD_S = 0.2; cb.ctrl.USE_LINE_LOST = False
cb.TRIP_SECONDS = 1.5
stop = threading.Event()
link = cb.WebLink(stop, base=f"http://127.0.0.1:{srv.server_port}"); link.start()
th = threading.Thread(target=cb.controller_loop, args=("fake", stop, link, lambda *a, **k: fake), daemon=True); th.start()

def wait(c, t=4):
    e = time.time() + t
    while time.time() < e:
        if c(): return True
        time.sleep(0.02)
    return False
res = []
def check(n, ok): res.append(ok); print("통과 " if ok else "실패 ", n)

time.sleep(0.6)
check("phase=ready 이면 G 안 보냄", "G" not in fake.sent)
web["command"] = {"phase": "driving", "gate": "A", "stopped": False}
check("phase=driving 이면 G 전송", wait(lambda: "G" in fake.sent))
check("웹에 status 가 올라감", wait(lambda: any(s.get("message") == "주행 중" for s in web["statuses"])))
n = len(fake.sent); fake.front = 10.0
check("앞 10cm 장애물 -> S", wait(lambda: "S" in fake.sent[n:]))
check("tier=정지 이 웹에 전달", wait(lambda: any(s.get("tier") == "정지" for s in web["statuses"])))
fake.front = 100.0
check("치워지면 다시 G", wait(lambda: "G" in fake.sent[n:]))
check("도착 보고 {arrived: true}", wait(lambda: any(s.get("arrived") for s in web["statuses"]), 5))
n = len(fake.sent); web["command"]["stopped"] = True
web["command"] = {"phase": "ready", "gate": None, "stopped": False}
time.sleep(0.5)
check("도착 후 ready 면 G 안 보냄", "G" not in fake.sent[n:])
stop.set(); th.join(2)
check("종료 시 시리얼 닫힘", fake.closed)
print("전체", "통과" if all(res) else "실패"); sys.exit(0 if all(res) else 1)
