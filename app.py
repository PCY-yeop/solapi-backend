# app.py
# 정책:
# - from == 대표번호(ENV_SENDER) (고정)
# - to   == 프론트에서 전달된 관리번호(adminPhone)
# - 고객 전화번호는 문자 본문 안에만 포함됨

import os, re, json, hmac, hashlib, uuid
from datetime import datetime
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# ========= ENV =========
SOLAPI_API_KEY    = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
ENV_SENDER        = os.getenv("SOLAPI_SENDER", "")  # = 대표번호(발신번호)

if not (SOLAPI_API_KEY and SOLAPI_API_SECRET and ENV_SENDER):
    raise RuntimeError("ENV 누락: SOLAPI_API_KEY / SOLAPI_API_SECRET / SOLAPI_SENDER")

# ========= APP / CORS =========
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========= UTILS =========
DIGITS = re.compile(r"[^\d]")

def only_digits(s: str) -> str:
    return DIGITS.sub("", s or "")

def normalize_kor(num: str) -> str:
    """8210... -> 010... 보정"""
    n = only_digits(num)
    if n.startswith("82"):
        rest = n[2:]
        if rest.startswith(("10","11","16","17","18","19")):
            return "0" + rest
    return n

def solapi_headers():
    date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    salt = uuid.uuid4().hex
    signature = hmac.new(
        SOLAPI_API_SECRET.encode(),
        (date + salt).encode(),
        hashlib.sha256
    ).hexdigest()
    return {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"HMAC-SHA256 apiKey={SOLAPI_API_KEY}, date={date}, salt={salt}, signature={signature}",
    }

def build_admin_text(site, vd, vt_label, name, phone, memo):
    site_disp = site or ""
    if site_disp and not(site_disp.startswith("[") and site_disp.endswith("]")):
        site_disp = f"[{site_disp}]"
    time_disp = (vt_label or "").strip() or "-"
    return "\n".join([
        f"현장 : {site_disp}",
        f"날짜 : {vd}",
        f"시간 : {time_disp}",
        f"이름 : {name}",
        f"연락처 : {phone}",
        # memo가 필요하면 아래 주석 해제
        # f"메모 : {memo}" if memo else "",
    ]).strip()

# ===== 단건 전송 엔드포인트 =====
SOLAPI_SEND_URL = "https://api.solapi.com/messages/v4/send"

VERSION = "2025-11-07-send-to-client-admin-v2"

# ========= ROUTES =========
@app.get("/health")
async def health():
    return {"ok": True, "time": datetime.now().isoformat()}

@app.get("/version")
async def version():
    return {"version": VERSION, "from_admin": normalize_kor(ENV_SENDER)}

@app.post("/sms")
async def sms(req: Request):
    """
    요청 JSON 예:
    {
      "site": "보라매",
      "vd": "2025-11-06",
      "vtLabel": "10:00 ~ 11:00",
      "name": "홍길동",
      "phone": "01011112222",     ← 고객 전화번호
      "adminPhone": "01022223333" ← 문자 받을 관리자 번호(프론트에서 지정)
    }
    """
    body = await req.json()

    site     = (body.get("site") or "").strip()
    vd       = (body.get("vd") or "").strip()
    vt_label = (body.get("vtLabel") or "").strip()
    name     = (body.get("name") or "").strip()
    phone    = only_digits(body.get("phone"))
    memo     = (body.get("memo") or "").strip()
    admin_to = only_digits(body.get("adminPhone"))   # ✅ 수신자(to)

    admin_from = normalize_kor(ENV_SENDER)           # ✅ 발신자(from) = 대표번호

    # ---- 검증 ----
    if not site:     return {"ok": False, "error": "site 누락"}
    if not vd:       return {"ok": False, "error": "vd 누락"}
    if not name:     return {"ok": False, "error": "name 누락"}
    if not phone:    return {"ok": False, "error": "phone 누락"}
    if not admin_to: return {"ok": False, "error": "adminPhone 누락"}

    # ---- 본문 ----
    text = build_admin_text(site, vd, vt_label, name, phone, memo)

    # ---- 단건 전송 payload (send-many 아님) ----
    payload = {
        "message": {
            "to": admin_to,        # 프론트에서 지정한 관리자 번호
            "from": admin_from,    # 대표번호(ENV)
            "text": text           # type 생략 시 기본 SMS
        }
    }

    try:
        r = requests.post(SOLAPI_SEND_URL, headers=solapi_headers(), json=payload, timeout=12)

        # 원문 보존 + JSON 시도
        raw = r.text
        try:
            res_json = r.json()
        except Exception:
            res_json = {"raw": raw}

        if r.status_code // 100 != 2:
            return {"ok": False, "status": r.status_code, "detail": res_json}

        return {
            "ok": True,
            "result": res_json,
            "from_used": admin_from,
            "to_used": admin_to
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}
