# -*- coding: utf-8 -*-
"""웹(web/cart_web.py)과 cart_bridge.py 통합 시험: 가짜 시리얼 + 실제 웹. 출발, 장애물 정지/재출발, 도착, 반납(우회 통로 복귀), 다음 승객까지 확인"""
import os, sys, time, threading
os.environ.update(CART_SIM="0", CART_ID="07")
os.environ.setdefault("CART_SECRET", "test-only-secret"); os.environ.setdefault("FLASK_SECRET", "test-only-session-secret")  # 테스트 전용 임시 값
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "web")); sys.path.insert(0, HERE)
import cart_web
from qr_signature import make_signature
from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, cart_web.app, threaded=True); threading.Thread(target=srv.serve_forever, daemon=True).start()
import cart_bridge as cb
class FS:
    def __init__(s,*a,**k): s.sent=[]; s.front=100.0; s.rear=100.0; s.closed=False
    def reset_input_buffer(s): pass
    def write(s,b): s.sent.append(b.decode().strip())
    def readline(s): time.sleep(0.02); return f"D:{s.front},100,100,{s.rear}\n".encode()
    def close(s): s.closed=True
fake=FS(); _rs=time.sleep; time.sleep=lambda x: None if x==2 else _rs(x)
cb.ctrl.RESUME_HOLD_S=0.2; cb.ctrl.USE_LINE_LOST=False; cb.TRIP_SECONDS=1.2; cb.RETURN_LAP_SECONDS=1.0
stop=threading.Event(); link=cb.WebLink(stop, base=f"http://127.0.0.1:{srv.server_port}"); link.start()
th=threading.Thread(target=cb.controller_loop,args=("fake",stop,link,lambda *a,**k: fake),daemon=True); th.start()
res=[]
def check(n,ok): res.append(ok); print("통과 " if ok else "실패 ",n)
def wait(c,t=6):
    e=time.time()+t
    while time.time()<e:
        if c(): return True
        time.sleep(0.02)
    return False
phone=cart_web.app.test_client(); sig=make_signature("07")
time.sleep(0.4); phone.get(f"/c/07?sig={sig}"); phone.post("/api/select",json={"gate":"A"})
check("출발(G)",wait(lambda:"G" in fake.sent))
check("도착",wait(lambda:cart_web.cart["phase"]=="arrived"))
n=len(fake.sent); time.sleep(0.5); check("도착 후 반납 전에는 G 안 보냄","G" not in fake.sent[n:])
phone.post("/api/return",json={}); m=len(fake.sent)
check("반납 -> 복귀 주행 시작(G)",wait(lambda:"G" in fake.sent[m:]))
k=len(fake.sent); fake.front=10.0
check("복귀 중 앞 장애물 -> S",wait(lambda:"S" in fake.sent[k:]))
k=len(fake.sent); fake.front=100.0
check("치우면 복귀 재개(G)",wait(lambda:"G" in fake.sent[k:]))
check("복귀 시간 후 정지 + 복귀 완료 -> 웹 idle",wait(lambda:cart_web.cart["phase"]=="idle",8))
last=fake.sent[-6:]
check("마지막은 S (대기소에서 정지)", "S" in last)
j=len(fake.sent); time.sleep(0.5)
check("복귀 완료 후 G 안 보냄","G" not in fake.sent[j:])
phone2=cart_web.app.test_client()
check("다음 승객 점유 가능",phone2.get(f"/c/07?sig={sig}").status_code==302)
phone2.post("/api/select",json={"gate":"B"}); k=len(fake.sent)
check("다음 승객 탑승구 선택 -> G",wait(lambda:"G" in fake.sent[k:]))
stop.set(); th.join(2)
print("전체","통과" if all(res) else "실패"); os._exit(0 if all(res) else 1)
