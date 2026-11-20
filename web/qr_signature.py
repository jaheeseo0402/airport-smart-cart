# -*- coding: utf-8 -*-
"""
qr_signature.py

카트 뒤에 붙는 QR에 '카트 번호 + 서명'을 넣는다.
누가 가짜 QR을 붙여서 다른 사이트로 보내거나, 카트 번호만 바꾼 QR을 만들면
서명이 안 맞아서 웹앱이 거부한다.

QR 안에 들어가는 내용(URL):
    http://<라즈베리파이 IP>:5000/c/07?sig=<서명 16자리>

※ 서명은 '변조'를 막는다. QR을 사진 찍어 '복사'하는 건 못 막으므로
  cart_web.py의 세션 잠금(먼저 찍은 사람만 조종)이 함께 있어야 한다.
"""
import hashlib
import hmac
import os

# 비밀키는 코드에 넣지 않고 환경변수로만 받는다: export CART_SECRET="아무도 모르는 긴 문자열"
_key = os.environ.get("CART_SECRET")
if not _key:
    raise RuntimeError("환경변수 CART_SECRET 이 설정되지 않았습니다. (export CART_SECRET=긴문자열)")
SECRET_KEY = _key.encode()
SIG_LEN = 16  # 앞 16자리(64비트)만 써서 QR을 작게 유지


def make_signature(cart_id: str, key: bytes = SECRET_KEY) -> str:
    msg = f"CART={cart_id}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:SIG_LEN]


def verify_cart_sig(cart_id: str, sig: str, key: bytes = SECRET_KEY) -> bool:
    if not cart_id or not sig:
        return False
    # 타이밍 공격 방지를 위해 == 대신 compare_digest
    return hmac.compare_digest(sig, make_signature(cart_id, key))


def make_qr_url(cart_id: str, base_url: str, key: bytes = SECRET_KEY) -> str:
    return f"{base_url.rstrip('/')}/c/{cart_id}?sig={make_signature(cart_id, key)}"
