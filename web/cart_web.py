# -*- coding: utf-8 -*-
"""
cart_web.py  -  라즈베리파이에서 돌리는 스마트 카트 웹앱 (Flask)

흐름 (시연 영상과 같음)
  1) 승객이 카트 뒤 QR을 폰 카메라로 찍음 -> /c/07?sig=... 열림
  2) 서명이 맞으면 이 폰이 카트를 '점유'함 (다른 폰은 "사용 중"으로 거부)
  3) 탑승구 선택 -> 카트 출발
  4) 주행 중 화면(탑승권 모양): 실시간 상태(장애물 감지·정지·회피 등) / TTC 단계 / 진행률
     ※ 비상정지는 앱에서 뺐음 -> 카트 손잡이의 물리 버튼으로. /api/stop 은 관리자/확장용으로 남겨둠
  5) 도착 -> 반납 버튼 -> 카트 잠금 해제, 카트는 대기소로 복귀

로봇(주행 코드)과의 연결
  - 주행 코드는 같은 라파에서 따로 돌고, localhost로만 이 API를 쓴다.
      GET  /robot/command  -> {"phase", "gate", "stopped"} 어디로 갈지/멈출지 받아감
      POST /robot/status   -> {"progress", "message", "tier", "ttc",
                               "arrived", "returned"} 현재 상태를 올려줌
  - CART_SIM=1 (기본값)이면 로봇 없이 가짜로 움직여서 화면만 시연할 수 있음.

실행
  python3 cart_web.py                    # 시뮬레이션 모드
  CART_SIM=0 python3 cart_web.py         # 실제 로봇 연동 모드
"""
import os
import secrets
import threading
import time

from flask import Flask, jsonify, redirect, render_template_string, request, session

from qr_signature import verify_cart_sig

CART_ID = os.environ.get("CART_ID", "07")
SIM = os.environ.get("CART_SIM", "1") == "1"
LOCK_TIMEOUT = 10 * 60  # 10분 동안 폰에서 아무 요청 없으면 잠금 해제

GATES = {
    "A": {"flight": "SW101", "code": "NRT", "dest": "도쿄", "time": "08:30", "status": "탑승중"},
    "B": {"flight": "SW202", "code": "KIX", "dest": "오사카", "time": "09:10", "status": "탑승준비"},
}

app = Flask(__name__)
_flask_secret = os.environ.get("FLASK_SECRET")
if not _flask_secret:
    raise RuntimeError("환경변수 FLASK_SECRET 이 설정되지 않았습니다. (export FLASK_SECRET=긴문자열)")
app.secret_key = _flask_secret

_lock = threading.Lock()
cart = {}


def reset_cart():
    cart.clear()
    cart.update(
        owner=None,        # 점유한 폰의 세션 토큰
        last_seen=0.0,
        phase="idle",      # idle -> ready -> driving -> arrived -> returning -> idle
        gate=None,
        progress=0.0,      # 0~1
        message="대기 중",
        tier="안전",        # 안전 / 주의 / 위험 / 임박 / 정지
        ttc=None,
        stopped=False,     # 비상정지
    )


reset_cart()


# ---------------- 공통 ----------------
def _expire_if_idle():
    if cart["owner"] and time.time() - cart["last_seen"] > LOCK_TIMEOUT and cart["phase"] in ("ready", "arrived"):
        reset_cart()


def _is_owner():
    tok = session.get("tok")
    return tok is not None and tok == cart["owner"]


def _need_owner():
    """점유한 폰이 아니면 에러 응답, 맞으면 None"""
    _expire_if_idle()
    if not _is_owner():
        return jsonify(ok=False, reason="이 카트를 사용 중인 폰이 아니에요. 카트 뒤 QR을 찍어주세요."), 403
    if not request.is_json and request.method == "POST":
        # 다른 사이트의 form으로 몰래 보내는 요청(CSRF) 차단
        return jsonify(ok=False, reason="잘못된 요청 형식"), 400
    cart["last_seen"] = time.time()
    return None


def _public_state():
    return {k: cart[k] for k in ("phase", "gate", "progress", "message", "tier", "ttc", "stopped")}


def _local_only():
    return request.remote_addr in ("127.0.0.1", "::1")


# ---------------- 승객(폰) 쪽 ----------------
@app.route("/c/<cart_id>")
def scan(cart_id):
    """카트 QR을 찍으면 열리는 주소"""
    sig = request.args.get("sig", "")
    if cart_id != CART_ID or not verify_cart_sig(cart_id, sig):
        return render_template_string(PAGE, mode="bad", cart_id=CART_ID, gates=GATES), 403
    with _lock:
        _expire_if_idle()
        tok = session.get("tok")
        if cart["owner"] and cart["owner"] != tok:
            return render_template_string(PAGE, mode="busy", cart_id=CART_ID, gates=GATES), 409
        if cart["phase"] == "returning":
            return render_template_string(PAGE, mode="busy", cart_id=CART_ID, gates=GATES), 409
        if not tok:
            tok = secrets.token_urlsafe(16)
            session["tok"] = tok
        cart["owner"] = tok
        cart["last_seen"] = time.time()
        if cart["phase"] == "idle":
            cart["phase"] = "ready"
            cart["message"] = "탑승구를 골라주세요"
    return redirect("/")


@app.route("/")
def index():
    with _lock:
        _expire_if_idle()
        mode = "app" if _is_owner() else "noscan"
    return render_template_string(PAGE, mode=mode, cart_id=CART_ID, gates=GATES)


@app.route("/api/status")
def api_status():
    with _lock:
        err = _need_owner()
        if err:
            return err
        return jsonify(ok=True, cart=_public_state())


@app.route("/api/select", methods=["POST"])
def api_select():
    with _lock:
        err = _need_owner()
        if err:
            return err
        gate = (request.get_json(silent=True) or {}).get("gate")
        if gate not in GATES:
            return jsonify(ok=False, reason="없는 탑승구예요"), 400
        if cart["phase"] != "ready":
            return jsonify(ok=False, reason="지금은 목적지를 바꿀 수 없어요"), 409
        cart.update(gate=gate, phase="driving", progress=0.0, stopped=False,
                    message=f"{gate}게이트로 이동 중", tier="안전", ttc=None)
        return jsonify(ok=True, cart=_public_state())


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with _lock:
        err = _need_owner()
        if err:
            return err
        cart["stopped"] = True
        cart["message"] = "비상정지됨"
        return jsonify(ok=True, cart=_public_state())


@app.route("/api/resume", methods=["POST"])
def api_resume():
    with _lock:
        err = _need_owner()
        if err:
            return err
        cart["stopped"] = False
        if cart["phase"] == "driving":
            cart["message"] = f"{cart['gate']}게이트로 이동 중"
        return jsonify(ok=True, cart=_public_state())


@app.route("/api/return", methods=["POST"])
def api_return():
    with _lock:
        err = _need_owner()
        if err:
            return err
        if cart["phase"] not in ("arrived", "ready"):
            return jsonify(ok=False, reason="도착한 뒤에 반납할 수 있어요"), 409
        # 잠금 해제 -> 이 폰은 더 이상 조종 못 함, 카트는 대기소로 복귀
        cart.update(owner=None, phase="returning", stopped=False, message="대기소로 복귀 중")
        session.pop("tok", None)
        return jsonify(ok=True)


# ---------------- 로봇(주행 코드) 쪽: localhost 전용 ----------------
@app.route("/robot/command")
def robot_command():
    if not _local_only():
        return jsonify(ok=False), 403
    with _lock:
        return jsonify(phase=cart["phase"], gate=cart["gate"], stopped=cart["stopped"])


@app.route("/robot/status", methods=["POST"])
def robot_status():
    if not _local_only():
        return jsonify(ok=False), 403
    d = request.get_json(silent=True) or {}
    with _lock:
        for k in ("progress", "message", "tier", "ttc"):
            if k in d:
                cart[k] = d[k]
        if d.get("arrived") and cart["phase"] == "driving":
            cart.update(phase="arrived", progress=1.0, message=f"{cart['gate']}게이트 도착", tier="안전", ttc=None)
        if d.get("returned") and cart["phase"] == "returning":
            reset_cart()
    return jsonify(ok=True)


# ---------------- 시뮬레이션 (로봇 없이 화면 시연) ----------------
def _sim_loop():
    while True:
        time.sleep(0.2)
        with _lock:
            p = cart["phase"]
            if p == "driving" and not cart["stopped"]:
                x = cart["progress"]
                if 0.30 <= x < 0.36:      # 장애물 접근: TTC 단계 올라감
                    cart.update(tier="위험", ttc=1.6, message="캐리어 감지 · 감속")
                    x += 0.004
                elif 0.36 <= x < 0.40:
                    cart.update(tier="정지", ttc=0.6, message="장애물 앞 정지 · 옆 확인")
                    x += 0.003
                elif 0.40 <= x < 0.52:
                    cart.update(tier="주의", ttc=None, message="왼쪽으로 회피 중")
                    x += 0.008
                else:
                    if x >= 0.52 and cart["tier"] != "안전":
                        cart["message"] = "라인 복귀 · 이동 중"
                    cart.update(tier="안전", ttc=None)
                    if 0.60 < x < 0.70:
                        side = "왼쪽" if cart["gate"] == "A" else "오른쪽"
                        cart["message"] = f"갈림길 인식 · {side} 곡선 진입"
                    elif x >= 0.70:
                        cart["message"] = f"{cart['gate']}게이트로 이동 중"
                    x += 0.012
                cart["progress"] = min(x, 1.0)
                if x >= 1.0:
                    cart.update(phase="arrived", message=f"{cart['gate']}게이트 도착")
            elif p == "returning":
                cart["progress"] = max(0.0, cart["progress"] - 0.03)
                if cart["progress"] <= 0:
                    reset_cart()


# ---------------- 화면 (HTML 한 장) ----------------
PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f2f2f7">
<title>스마트 카트 #{{ cart_id }}</title>
<style>
:root{--bg:#f2f2f7;--card:#fff;--ink:#000;--sub:#8e8e93;--line:#e5e5ea;--navy:#2b4fb3;--red:#ff3b30;--green:#34c759}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
[hidden]{display:none!important}
html,body{margin:0;background:var(--bg);color:var(--ink)}
body{font:15px/1.35 -apple-system,BlinkMacSystemFont,"SF Pro Text","Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",sans-serif;
  letter-spacing:-.2px;padding:env(safe-area-inset-top) 0 calc(110px + env(safe-area-inset-bottom));max-width:520px;margin:0 auto}
.bar{padding:14px 16px 4px;text-align:center}
.bar h1{margin:0;font-size:15px;font-weight:600}
.hint{color:var(--sub);font-size:13px;text-align:center;margin:8px 24px 0}
.dotc{width:6px;height:6px;border-radius:50%;background:var(--green);display:inline-block;margin-right:5px;vertical-align:1px}
/* 탑승구 목록 (지갑 앱 목록) */
.group{background:var(--card);border-radius:16px;margin:12px 14px 0;overflow:hidden}
.item{display:flex;align-items:center;width:100%;background:none;border:0;padding:13px 16px;font:inherit;color:inherit;text-align:left;cursor:pointer;position:relative;line-height:1.35}
.item+.item::before{content:"";position:absolute;top:0;left:16px;right:0;height:.5px;background:var(--line)}
.item:active{background:#f6f6f8}
.it{font-size:15px;font-weight:600;display:flex;align-items:center;gap:5px}
.it svg{width:17px;height:17px}
.is{color:var(--sub);font-size:13px}
.chev{margin-left:auto;color:#c7c7cc;font-size:18px;flex:none;padding-left:12px}
/* 탑승권 */
.pass{background:var(--card);border-radius:14px;margin:10px 14px;padding:14px 16px 16px;border:.5px solid var(--line)}
.ph{display:flex;justify-content:space-between;align-items:flex-start}
.brand{color:var(--navy);font-weight:600;font-size:13px;margin-top:8px}
.lab{color:var(--navy);font-size:11px}
.val{color:var(--navy);font-size:15px;margin-top:1px}
.r{text-align:right}
.route{display:flex;align-items:flex-end;gap:10px;margin:16px 0 12px}
.code{color:var(--navy);font-size:30px;line-height:1;letter-spacing:-.5px}
.track{flex:1;position:relative;height:24px}
.track::before{content:"";position:absolute;left:0;right:0;top:50%;border-top:1.5px dashed #c9d3ef}
.track .fill{position:absolute;left:0;top:50%;margin-top:-1px;height:2px;background:var(--navy);width:0;transition:width .5s}
.track svg{position:absolute;top:50%;left:0;width:17px;height:17px;transform:translate(-50%,-55%);color:var(--navy);background:var(--card);transition:left .5s}
.fields{display:flex;justify-content:space-between}
.now{border-top:.5px dashed var(--line);margin-top:12px;padding-top:11px}
.now .msg{font-size:14px;font-weight:600;margin:2px 0 6px;color:var(--ink)}
.chip{display:inline-block;padding:1px 8px;border-radius:99px;color:#fff;font-size:11px;font-weight:600}
.map svg{width:140px;display:block;margin:12px auto 0}
/* 도착 후 반납 */
.dock{position:fixed;left:0;right:0;bottom:0;max-width:520px;margin:0 auto;padding:10px 14px calc(16px + env(safe-area-inset-bottom));background:var(--bg)}
.pill{display:block;width:100%;border:0;border-radius:14px;padding:15px;font-family:inherit;font-size:16px;font-weight:600;cursor:pointer;background:var(--navy);color:#fff}
.dock .hint{margin-top:8px;font-size:12px}
/* 안내 화면 */
.box{background:var(--card);border-radius:16px;margin:12px 14px;padding:24px 20px;text-align:center}
.box h2{font-size:16px;font-weight:600;margin:0 0 6px}.box p{color:var(--sub);margin:0;font-size:13px}
.big{font-size:34px;line-height:1;margin:2px 0 10px}
.err{color:var(--red);font-size:13px;text-align:center;min-height:18px;margin:8px 16px}
</style></head><body>
{% if mode in ("bad", "busy", "noscan") %}
  <div class="bar"><h1>스마트 카트 #{{ cart_id }}</h1></div>
  <div class="box">
  {% if mode == "bad" %}<div class="big" style="color:var(--red)">!</div><h2>확인되지 않은 QR이에요</h2><p>카트에 붙은 공식 QR이 아니에요.<br>다른 카트를 이용하거나 안내 데스크에 문의해 주세요.</p>
  {% elif mode == "busy" %}<h2>이미 사용 중인 카트예요</h2><p>다른 승객이 이용 중이거나<br>대기소로 돌아가는 중이에요.</p>
  {% else %}<h2>카트 뒤 QR을 찍어주세요</h2><p>폰 카메라로 카트 뒷면의 QR을 찍으면<br>이 화면에서 카트를 쓸 수 있어요.</p>{% endif %}
  </div>
{% else %}
  <!-- 1. 탑승구 선택 (지갑 앱 목록) -->
  <section id="s-ready" hidden>
    <div class="bar"><h1>탑승구 선택</h1></div>
    <p class="hint"><span class="dotc"></span>카트 #{{ cart_id }} 연결됨</p>
    <div class="group">
      {% for g, f in gates.items() %}
      <button class="item" data-gate="{{ g }}">
        <span>
          <span class="it">ICN<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.5 12c0-.9-.7-1.6-1.6-1.6h-5.1L10 2.5H7.8l2.6 7.9H5.5L3.6 8H2l1.1 4L2 16h1.6l1.9-2.4h4.9l-2.6 7.9H10l4.8-7.9h5.1c.9 0 1.6-.7 1.6-1.6z"/></svg>{{ f.code }}</span>
          <span class="is" style="display:block;margin-top:2px">{{ g }} 탑승구, {{ f.flight }}, {{ f.status }}</span>
          <span class="is" style="display:block">{{ f.time }} 출발</span>
        </span>
        <span class="chev">›</span>
      </button>
      {% endfor %}
    </div>
    <p class="hint" style="font-size:12px;margin-top:10px">고르면 카트가 앞장서요. 뒤따라 오세요.</p>
  </section>

  <!-- 2. 이동 중 / 3. 도착 (탑승권) -->
  <section id="s-drive" hidden>
    <div class="bar"><h1 id="title">이동 중</h1></div>
    <div class="pass">
      <div class="ph">
        <span class="brand">SMART CART</span>
        <span class="r"><div class="lab">출발 시각</div><div class="val" style="font-size:16px" id="t0">--:--</div></span>
      </div>
      <div class="route">
        <div><div class="lab">카트</div><div class="code">C{{ cart_id }}</div></div>
        <div class="track"><i class="fill" id="fill"></i>
          <svg id="cartIco" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h2l2.4 10.2a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 1.9-1.4L21 8H6.2"/><circle cx="10" cy="20" r="1.2"/><circle cx="17" cy="20" r="1.2"/></svg></div>
        <div class="r"><div class="lab">탑승구</div><div class="code" id="gcode">-</div></div>
      </div>
      <div class="fields">
        <div><div class="lab">항공편</div><div class="val" id="flight">-</div></div>
        <div><div class="lab">TTC</div><div class="val" id="ttc">-</div></div>
        <div class="r"><div class="lab">진행</div><div class="val" id="pct">0%</div></div>
      </div>
      <div class="now">
        <div class="lab">지금 카트는</div>
        <div class="msg" id="msg">-</div>
        <span class="chip" id="tier">안전</span>
      </div>
      <div class="map"><svg viewBox="0 0 300 150" aria-label="미니맵">
        <path d="M150 145 V70 A40 40 0 0 0 110 30 H56" fill="none" stroke="#e5e5ea" stroke-width="10" stroke-linecap="round" id="pA"/>
        <path d="M150 145 V70 A40 40 0 0 1 190 30 H244" fill="none" stroke="#e5e5ea" stroke-width="10" stroke-linecap="round" id="pB"/>
        <text x="24" y="37" font-size="20" font-weight="600" fill="#2b4fb3">A</text><text x="262" y="37" font-size="20" font-weight="600" fill="#2b4fb3">B</text>
        <circle r="10" fill="#2b4fb3" stroke="#fff" stroke-width="4" id="dot" cx="150" cy="145"/></svg></div>
    </div>
    <div class="dock" id="dockDone" hidden>
      <button class="pill" id="ret">카트 반납</button>
      <p class="hint">짐을 꺼낸 뒤 눌러주세요. 카트는 혼자 대기소로 돌아가요.</p>
    </div>
  </section>

  <!-- 4. 반납 완료 -->
  <section id="s-done" hidden>
    <div class="bar"><h1>반납 완료</h1></div>
    <div class="box"><div class="big" style="color:var(--green)">✓</div><h2>이용해 주셔서 감사합니다</h2><p>즐거운 여행 되세요.</p></div>
  </section>
  <p class="err" id="err"></p>
<script>
{
const $=id=>document.getElementById(id);
const GATES={{ gates|tojson }};
const TC={"안전":"#34c759","주의":"#ff9f0a","위험":"#ff6a00","임박":"#ff3b30","정지":"#ff3b30"};
let done=false,t0=null;
function show(id){for(const s of ["s-ready","s-drive","s-done"])$(s).hidden=s!==id}
async function post(url,body){
  try{const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})});
    const j=await r.json();$("err").textContent=j.ok?"":(j.reason||"오류가 났어요");return j}
  catch(e){$("err").textContent="카트와 연결이 끊겼어요";return {ok:false}}}
function render(c){
  if(c.phase==="ready"){show("s-ready");t0=null;return}
  show("s-drive");
  const arrived=c.phase==="arrived";
  if(!t0){const d=new Date();t0=String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0")}
  $("t0").textContent=t0;
  $("title").textContent=arrived?"도착":(c.stopped?"정지됨":"이동 중");
  $("gcode").textContent=c.gate||"-";
  const f=GATES[c.gate];$("flight").textContent=f?f.flight:"-";
  $("msg").textContent=arrived?c.gate+" 탑승구에 도착했어요":c.message;
  const t=arrived?"도착":(c.stopped?"정지":c.tier);
  $("tier").textContent=t;$("tier").style.background=arrived?"#34c759":(c.stopped?"#ff3b30":(TC[c.tier]||"#8e8e93"));
  $("ttc").textContent=c.ttc?c.ttc.toFixed(1)+"초":"-";
  const p=arrived?1:c.progress,pc=Math.round(p*100);
  $("pct").textContent=pc+"%";$("fill").style.width=pc+"%";$("cartIco").style.left=pc+"%";
  const path=$(c.gate==="B"?"pB":"pA");path.setAttribute("stroke","#2b4fb3");
  const pt=path.getPointAtLength(path.getTotalLength()*p);$("dot").setAttribute("cx",pt.x);$("dot").setAttribute("cy",pt.y);
  $("dockDone").hidden=!arrived;
}
async function poll(){
  if(done)return;
  try{const r=await fetch("/api/status");const j=await r.json();
    if(j.ok){render(j.cart);$("err").textContent=""}
    else if(r.status===403){done=true;for(const s of ["s-ready","s-drive","s-done"])$(s).hidden=true;
      $("err").textContent="연결이 끊겼어요. 카트 뒤 QR을 다시 찍어주세요.";return}
    else $("err").textContent=j.reason}
  catch(e){$("err").textContent="카트와 연결이 끊겼어요"}
  setTimeout(poll,500)}
document.querySelectorAll("[data-gate]").forEach(b=>b.onclick=async()=>{const j=await post("/api/select",{gate:b.dataset.gate});if(j.ok)render(j.cart)});
$("ret").onclick=async()=>{const j=await post("/api/return");if(j.ok){done=true;show("s-done")}};
poll();
}
</script>
{% endif %}
</body></html>"""


if __name__ == "__main__":
    if SIM:
        threading.Thread(target=_sim_loop, daemon=True).start()
        print("시뮬레이션 모드: 로봇 없이 화면만 움직여요 (CART_SIM=0 이면 실제 로봇 연동)")
    app.run(host="0.0.0.0", port=5000)
