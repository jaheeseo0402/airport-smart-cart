# -*- coding: utf-8 -*-
"""
qr_signature.py

목적:
게이트 QR코드에 게이트번호만 넣으면 누구나 위조 QR을 만들 수 있으므로,
(게이트번호 + 비밀키)로 만든 HMAC-SHA256 서명값을 함께 넣고,
카트(라즈베리파이)가 QR을 읽을 때 서명이 맞는지 검증한다.

QR 안에 들어가는 문자열 형식:
    GATE=B12;SIG=<서명값 16진수 앞 16자리>

실행: QR_SECRET_KEY=임의의긴문자열 python3 qr_signature.py
"""

import hmac
import hashlib
import os


def _load_key() -> bytes:
    """서명 비밀키는 코드에 넣지 않고 환경변수 QR_SECRET_KEY 에서 읽는다. (카트와 QR 발급 쪽만 알고 있음)"""
    key = os.environ.get("QR_SECRET_KEY")
    if not key:
        raise RuntimeError("환경변수 QR_SECRET_KEY 가 설정되지 않았습니다. (README의 '비밀값 설정' 참고)")
    return key.encode("utf-8")


SECRET_KEY = _load_key()

SIG_LEN = 16  # 서명값 앞 16자리만 사용 (QR 크기를 줄이기 위함)


def make_signature(gate: str, key: bytes = SECRET_KEY) -> str:
    """게이트번호와 비밀키로 서명값 생성"""
    digest = hmac.new(key, gate.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:SIG_LEN]


def make_qr_payload(gate: str, key: bytes = SECRET_KEY) -> str:
    """QR에 넣을 문자열 생성"""
    return f"GATE={gate};SIG={make_signature(gate, key)}"


def verify_qr_payload(payload: str, key: bytes = SECRET_KEY):
    """
    QR 문자열 검증.
    반환: (통과여부, 게이트번호 또는 None, 사유)
    """
    try:
        parts = dict(item.split("=", 1) for item in payload.split(";"))
        gate = parts["GATE"]
        sig = parts["SIG"]
    except (ValueError, KeyError):
        return False, None, "형식 오류"

    expected = make_signature(gate, key)
    # 타이밍 공격을 막기 위해 일반 == 대신 compare_digest 사용
    if hmac.compare_digest(sig, expected):
        return True, gate, "서명 일치"
    return False, None, "서명 불일치(위조 의심)"


def run_tests():
    real = make_qr_payload("B12")
    cases = [
        ("정상 QR (B12)", real),
        ("정상 QR (A03)", make_qr_payload("A03")),
        ("게이트번호만 바꿔치기", real.replace("GATE=B12", "GATE=C30")),
        ("서명값 임의 조작", "GATE=B12;SIG=0000000000000000"),
        ("서명값 없이 게이트번호만", "GATE=B12"),
        ("엉뚱한 문자열", "hello world"),
        ("다른 비밀키로 만든 위조 QR", make_qr_payload("B12", key=b"attacker-key")),
    ]

    print(f"{'테스트 케이스':<28}{'결과':<8}{'사유'}")
    print("-" * 60)
    all_ok = True
    for name, payload in cases:
        ok, gate, reason = verify_qr_payload(payload)
        should_pass = name.startswith("정상")
        correct = (ok == should_pass)
        all_ok &= correct
        print(f"{name:<28}{'통과' if ok else '거부':<8}{reason}"
              f"  {'[기대대로]' if correct else '[기대와 다름!]'}")

    print("-" * 60)
    print("전체 결과:", "모든 케이스가 기대대로 동작" if all_ok else "실패 케이스 있음")
    return all_ok


if __name__ == "__main__":
    print("예시 QR 문자열:", make_qr_payload("B12"))
    print()
    run_tests()
