# -*- coding: utf-8 -*-
"""
test_cart_web.py  -  새 흐름(카트 QR -> 점유 -> 탑승구 선택 -> 주행 -> 도착 -> 반납) 자동 테스트
실행: python3 test_cart_web.py
"""
import os

os.environ["CART_SIM"] = "0"  # 테스트 중엔 가짜 주행 끔
os.environ.setdefault("CART_SECRET", "test-only-secret")      # 테스트 전용 임시 값 (실제 값 아님)
os.environ.setdefault("FLASK_SECRET", "test-only-session-secret")
os.environ["CART_ID"] = "07"

import cart_web
from cart_web import app, cart, reset_cart
from qr_signature import make_signature

results = []


def check(name, cond):
    results.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


SIG = make_signature("07")
reset_cart()
phone1 = app.test_client()
phone2 = app.test_client()

# --- QR 서명 ---
r = phone1.get("/c/07?sig=0000000000000000")
check("서명 틀린 카트 QR 거부(403)", r.status_code == 403 and cart["owner"] is None)
r = phone1.get(f"/c/08?sig={SIG}")
check("카트 번호만 바꾼 QR 거부(403)", r.status_code == 403)
r = phone1.get("/c/07")
check("서명 없는 QR 거부(403)", r.status_code == 403)

# --- QR 안 찍고 조작 시도 ---
r = phone1.post("/api/select", json={"gate": "A"})
check("QR 안 찍은 폰은 탑승구 선택 불가(403)", r.status_code == 403 and cart["gate"] is None)

# --- 정상 QR -> 점유 ---
r = phone1.get(f"/c/07?sig={SIG}")
check("정상 QR 찍으면 카트 점유 + 메인으로 이동(302)", r.status_code == 302 and cart["owner"] and cart["phase"] == "ready")
check("점유한 폰은 상태 조회 가능", phone1.get("/api/status").get_json()["ok"])

# --- 다른 폰이 같은 QR (복사) ---
r = phone2.get(f"/c/07?sig={SIG}")
check("다른 폰이 같은 QR 찍으면 '사용 중' 거부(409)", r.status_code == 409)
r = phone2.post("/api/select", json={"gate": "B"})
check("다른 폰은 목적지 변경 불가(403)", r.status_code == 403 and cart["gate"] is None)
r = phone2.post("/api/stop", json={})
check("다른 폰은 비상정지도 못 누름(403)", r.status_code == 403 and not cart["stopped"])

# --- 탑승구 선택 ---
r = phone1.post("/api/select", json={"gate": "Z"})
check("없는 탑승구 거부(400)", r.status_code == 400)
r = phone1.post("/api/select", data={"gate": "A"})
check("JSON 아닌 form 요청 거부(CSRF 방지, 400)", r.status_code == 400 and cart["gate"] is None)
r = phone1.post("/api/select", json={"gate": "A"})
check("A게이트 선택 -> 주행 시작", r.status_code == 200 and cart["phase"] == "driving" and cart["gate"] == "A")
r = phone1.post("/api/select", json={"gate": "B"})
check("주행 중엔 목적지 변경 불가(409)", r.status_code == 409 and cart["gate"] == "A")

# --- 비상정지 ---
phone1.post("/api/stop", json={})
check("비상정지 -> 로봇 명령에 stopped=True", phone1.get("/robot/command").get_json()["stopped"] is True)
phone1.post("/api/resume", json={})
check("다시 출발 -> stopped=False", cart["stopped"] is False)

# --- 로봇 API ---
outsider = app.test_client()
r = outsider.get("/robot/command", environ_base={"REMOTE_ADDR": "192.168.0.50"})
check("외부 IP에서 로봇 API 접근 거부(403)", r.status_code == 403)
r = outsider.post("/robot/status", json={"arrived": True}, environ_base={"REMOTE_ADDR": "192.168.0.50"})
check("외부 IP가 '도착' 가짜 보고 못 함", r.status_code == 403 and cart["phase"] == "driving")
phone1.post("/robot/status", json={"progress": 0.5, "message": "캐리어 감지 · 감속", "tier": "위험", "ttc": 1.4})
s = phone1.get("/api/status").get_json()["cart"]
check("로봇이 올린 상태가 폰 화면 데이터에 반영", s["tier"] == "위험" and s["ttc"] == 1.4)

# --- 반납은 도착 후에만 ---
r = phone1.post("/api/return", json={})
check("주행 중엔 반납 불가(409)", r.status_code == 409)
phone1.post("/robot/status", json={"arrived": True})
check("로봇 도착 보고 -> arrived", cart["phase"] == "arrived")
r = phone1.post("/api/return", json={})
check("도착 후 반납 -> 잠금 해제 + 복귀 중", r.status_code == 200 and cart["owner"] is None and cart["phase"] == "returning")
check("반납한 폰은 더 이상 조작 불가", phone1.post("/api/stop", json={}).status_code == 403)
r = phone2.get(f"/c/07?sig={SIG}")
check("복귀 중엔 다른 사람도 못 잡음(409)", r.status_code == 409)
phone1.post("/robot/status", json={"returned": True})
r = phone2.get(f"/c/07?sig={SIG}")
check("대기소 복귀 후엔 다음 승객이 점유 가능", r.status_code == 302 and cart["phase"] == "ready")

# --- 방치 타임아웃 ---
cart["last_seen"] -= cart_web.LOCK_TIMEOUT + 1
r = phone1.get(f"/c/07?sig={SIG}")
check("10분 방치된 잠금은 풀려서 다른 폰이 점유 가능", r.status_code == 302)

# --- 화면 ---
html = phone1.get("/").get_data(as_text=True)
check("점유한 폰은 탑승구 선택 화면을 받음", "탑승구 선택" in html)
html = app.test_client().get("/").get_data(as_text=True)
check("QR 안 찍은 폰은 'QR을 찍어주세요' 화면", "카트 뒤 QR을 찍어주세요" in html)

print("-" * 50)
print(f"결과: {sum(results)}/{len(results)} 통과")
