# -*- coding: utf-8 -*-
"""
test_cart_web.py
cart_web.py의 로그인/인증/QR 검증 동작을 자동으로 확인하는 테스트.
실행: python3 test_cart_web.py
"""
import os

# 테스트 전용 값 (실제 비밀값이 아님). 이미 환경변수가 있으면 그것을 쓴다.
os.environ.setdefault("QR_SECRET_KEY", "test-only-qr-key")
os.environ.setdefault("CART_SESSION_SECRET", "test-only-session-secret")
os.environ.setdefault("CART_PASSWORD", "test-only-password")

from cart_web import app, state, GATE_QR

PASSWORD = os.environ["CART_PASSWORD"]
USER = os.environ.get("CART_USER", "passenger1")

results = []


def check(name, cond):
    results.append((name, cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


c = app.test_client()

# 1. 비로그인 접근 -> 로그인 페이지로 이동
r = c.get("/")
check("비로그인으로 메인 접근 시 로그인 페이지로 이동(302)", r.status_code == 302 and "/login" in r.headers["Location"])

# 2. 비로그인 상태에서 목적지 선택 시도 -> 거부
r = c.post("/select", data={"qr": GATE_QR["B12"]})
check("비로그인 상태 목적지 선택 요청 거부(401)", r.status_code == 401)
check("거부된 요청은 목적지가 바뀌지 않음", state["destination"] is None)

# 3. 틀린 비밀번호
r = c.post("/login", data={"username": USER, "password": "wrong"})
check("틀린 비밀번호 로그인 거부(401)", r.status_code == 401)

# 4. 정상 로그인
r = c.post("/login", data={"username": USER, "password": PASSWORD})
check("정상 로그인 성공(302 이동)", r.status_code == 302)
r = c.get("/")
check("로그인 후 메인 화면 접근 가능(200)", r.status_code == 200)

# 5. 정상 서명 QR로 목적지 선택
r = c.post("/select", data={"qr": GATE_QR["B12"]})
check("정상 서명 QR로 목적지 선택 성공", r.status_code == 302 and state["destination"] == "B12")

# 6. 위조 QR (게이트번호만 바꿔치기)
forged = GATE_QR["B12"].replace("GATE=B12", "GATE=C30")
r = c.post("/select", data={"qr": forged})
check("위조 QR(게이트번호 변조) 거부(403)", r.status_code == 403)
check("위조 QR 시도 후에도 목적지는 B12 유지", state["destination"] == "B12")

# 7. 서명 없는 QR
r = c.post("/select", data={"qr": "GATE=A03"})
check("서명 없는 QR 거부(403)", r.status_code == 403)

# 8. 로그아웃 후 다시 접근 불가
c.get("/logout")
r = c.get("/")
check("로그아웃 후 메인 접근 시 다시 로그인 요구", r.status_code == 302)

print("-" * 50)
passed = sum(1 for _, ok in results if ok)
print(f"결과: {passed}/{len(results)} 통과")
