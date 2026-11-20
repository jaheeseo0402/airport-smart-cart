# -*- coding: utf-8 -*-
"""
make_cart_qr.py  -  카트 뒤에 붙일 QR 만들기

사용:
  python3 make_cart_qr.py 07 http://192.168.0.23:5000
  -> 서명된 URL 출력 + cart_07_qr.png 저장 (qrcode 패키지가 있으면)

라파 IP 확인: 라파 터미널에서  hostname -I
폰과 라파가 같은 와이파이(또는 폰 핫스팟)에 있어야 열린다.
"""
import sys

from qr_signature import make_qr_url

cart_id = sys.argv[1] if len(sys.argv) > 1 else "07"
base = sys.argv[2] if len(sys.argv) > 2 else "http://192.168.0.23:5000"

url = make_qr_url(cart_id, base)
print("QR에 넣을 주소:", url)

try:
    import qrcode  # pip install qrcode[pil]
    qrcode.make(url).save(f"cart_{cart_id}_qr.png")
    print(f"저장됨: cart_{cart_id}_qr.png  (5cm 이상으로 출력해서 카트 뒤에 붙이기)")
except ImportError:
    print("qrcode 패키지가 없어서 이미지는 안 만들었어요. pip install qrcode[pil] 하거나 위 주소로 QR 생성 사이트를 쓰세요.")
