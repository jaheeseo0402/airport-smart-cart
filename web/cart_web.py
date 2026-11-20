# -*- coding: utf-8 -*-
"""
cart_web.py

목적:
라즈베리파이에서 돌릴 카트 웹페이지(Flask).
- 로그인한 사용자만 목적지(게이트) 선택 가능
- 비로그인 상태로 접근하면 로그인 페이지로 이동 (API는 401 거부)
- 목적지는 서명된 QR(qr_signature.py) 검증을 통과한 게이트만 허용

실행: 환경변수 3개(QR_SECRET_KEY, CART_SESSION_SECRET, CART_PASSWORD)를 설정한 뒤
      python3 cart_web.py  -> 브라우저에서 http://localhost:5000
"""

import os

from flask import Flask, request, session, redirect, url_for, render_template_string, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from qr_signature import make_qr_payload, verify_qr_payload

app = Flask(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"환경변수 {name} 가 설정되지 않았습니다. (README의 '비밀값 설정' 참고)")
    return value


# 비밀값은 코드에 넣지 않고 환경변수로만 받는다
app.secret_key = _require_env("CART_SESSION_SECRET")

# 계정 (비밀번호는 해시로만 보관, 평문은 메모리에도 오래 두지 않음)
USERS = {os.environ.get("CART_USER", "passenger1"): generate_password_hash(_require_env("CART_PASSWORD"))}

# 선택 가능한 게이트와 각 게이트에 붙어 있는 서명 QR
GATES = ["A03", "B12", "C30"]
GATE_QR = {g: make_qr_payload(g) for g in GATES}

# 현재 카트가 향하는 목적지 (데모용 전역 상태)
state = {"destination": None, "user": None}

LOGIN_HTML = """
<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>카트 로그인</title>
<body style="font-family:sans-serif;max-width:360px;margin:60px auto">
<h2>스마트 카트 로그인</h2>
{% if error %}<p style="color:#c00">{{ error }}</p>{% endif %}
<form method="post">
  <p><input name="username" placeholder="아이디" style="width:100%;padding:8px"></p>
  <p><input name="password" type="password" placeholder="비밀번호" style="width:100%;padding:8px"></p>
  <button style="width:100%;padding:10px">로그인</button>
</form></body>
"""

MAIN_HTML = """
<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>목적지 선택</title>
<body style="font-family:sans-serif;max-width:420px;margin:40px auto">
<h2>목적지(게이트) 선택</h2>
<p>{{ user }} 님 로그인됨 · <a href="/logout">로그아웃</a></p>
<p>현재 목적지: <b>{{ dest or "미선택" }}</b></p>
{% for g, qr in gates.items() %}
<form method="post" action="/select" style="margin:8px 0">
  <input type="hidden" name="qr" value="{{ qr }}">
  <button style="width:100%;padding:12px;font-size:16px">{{ g }} 게이트로 이동</button>
</form>
{% endfor %}
</body>
"""


def logged_in():
    return "user" in session


@app.route("/", methods=["GET"])
def index():
    if not logged_in():
        return redirect(url_for("login"))
    return render_template_string(MAIN_HTML, user=session["user"],
                                  dest=state["destination"], gates=GATE_QR)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u in USERS and check_password_hash(USERS[u], p):
            session["user"] = u
            return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML, error="아이디 또는 비밀번호가 틀렸습니다."), 401
    return render_template_string(LOGIN_HTML, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/select", methods=["POST"])
def select():
    # 로그인 안 한 사용자는 카트 조작 자체를 거부
    if not logged_in():
        return jsonify(ok=False, reason="로그인 필요"), 401

    ok, gate, reason = verify_qr_payload(request.form.get("qr", ""))
    if not ok:
        return jsonify(ok=False, reason=reason), 403

    state["destination"] = gate
    state["user"] = session["user"]
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
