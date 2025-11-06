import os, re, json, hmac, hashlib, uuid
from datetime import datetime
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# ========= ENV =========
SOLAPI_API_KEY    = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
ENV_SENDER        = os.getenv("SOLAPI_SENDER", "")  # 솔라피 등록/승인된 기본 발신번호(숫자만)

# (선택) 허용 발신번호 목록 – 보안 강화를 원하면 넣고, 제한 없이 쓰고 싶으면 빈 값 유지
# 예: SOLAPI_ALLOWED_SENDERS="01011112222,01033334444"
def _only_digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s or "")

ALLOWED_SENDERS = [
    _only_digits(x) for x in os.getenv("SOLAPI_ALLOWED_SENDERS", "").split(",") if x.strip()
]

if not (SOLAPI_API_KEY and SOLAPI_API_SECRET and ENV_SENDER):
    raise RuntimeError("ENV 누락: SOLAPI_API_KEY / SOLAPI_API_SECRET / SOLAPI_SENDER")

# ========= APP / CORS =========
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # 운영 시 도메인으로 좁히는 걸 권장
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========= UTILS =========
def only_digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s or "")

def build_admin_text(site: str, vd: str, vt_label: str, name: str, phone: str, memo: str) -> str:
    lines = [
        f"[알림] {site or '현장'} 방문예약 도착",
        f"날짜: {vd or '-'}",
        f"시간: {vt_label or '-'}",
        f"성함: {name or '-'}",
        f"연락처(고객): {phone or '-'}",
    ]
    if memo:
        lines.append(f"메모: {memo}")
    return "\n".join(lines)

def solapi_headers() -> dict:
    """Solapi v4 HMAC 인증 헤더"""
    date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    salt = uuid.uuid4().hex
    to_sign = (date + salt).encode("utf-8")
    signature = hmac.new(SOLAPI_API_SECRET.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()
    auth = f'HMAC-SHA256 apiKey={SOLAPI_API_KEY}, date={date}, salt={salt}, signature={signature}'
    return {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": auth
    }

SOLAPI_URL = "https://api.solapi.com/messages/v4/send-many"

# ========= ROUTES =========
@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/mode")
async def mode():
    return {"mode": "rest-with-dynamic-sender"}

@app.post("/sms")
async def sms(req: Request):
    """
    요청(JSON)
    {
      "site": "보라매",
      "vd": "2025-11-06",
      "vtLabel": "10:00 ~ 11:00",
      "name": "[보라매] 홍길동",
      "phone": "01012345678",       # 고객 연락처(본문 표기용)
      "sp": "01022844859"           # 또는 "0101...,0103..." / ["0101...","0103..."] (관리자 여러 명)
      "sender": "01011112222",      # ★ 발신번호(사이트별로 다르게) – 미전송 시 ENV 기본값 사용
      "memo": ""
    }
    """
    body = await req.json()

    site     = (body.get("site") or "").strip()
    vd       = (body.get("vd") or "").strip()
    vt_label = (body.get("vtLabel") or "").strip()
    name     = (body.get("name") or "").strip()
    phone    = only_digits(body.get("phone"))
    memo     = (body.get("memo") or "").strip()

    # 관리자 수신자: 문자열(쉼표) 또는 배열 모두 허용
    admin_raw = body.get("sp")
    admin_list = []
    if isinstance(admin_raw, list):
        admin_list = [only_digits(x) for x in admin_raw]
    elif isinstance(admin_raw, str):
        admin_list = [only_digits(x) for x in admin_raw.split(",") if x.strip()]

    # ✅ 발신번호 결정: 클라이언트(sender/from) > ENV 기본
    client_sender = only_digits(body.get("sender") or body.get("from") or "")
    sender = client_sender if client_sender else only_digits(ENV_SENDER)

    # ---- validation ----
    if not name:    return {"ok": False, "error": "name 누락"}
    if not vd:      return {"ok": False, "error": "vd(방문일) 누락"}
    if not phone:   return {"ok": False, "error": "phone(고객) 누락"}
    if not re.fullmatch(r"\d{9,12}", phone):
        return {"ok": False, "error": "수신번호(고객) 형식 오류(숫자만 9~12자리)"}
    if not admin_list:
        return {"ok": False, "error": "관리자번호(sp) 누락"}
    if any(not re.fullmatch(r"\d{9,12}", n or "") for n in admin_list):
        return {"ok": False, "error": "관리자번호(sp) 형식 오류(숫자만 9~12자리)"}
    if not re.fullmatch(r"\d{9,12}", sender):
        return {"ok": False, "error": "발신번호 형식 오류 또는 미등록"}

    # (선택) 허용 발신번호 보안 체크 – 사용하지 않으려면 ENV에서 목록을 비워두면 됨
    if ALLOWED_SENDERS and sender not in ALLOWED_SENDERS:
        return {"ok": False, "error": "요청된 발신번호는 허용되지 않음(관리자에 문의)"}

    # ---- Solapi REST 호출 (관리자 N명 발송) ----
    text = build_admin_text(site, vd, vt_label, name, phone, memo)
    messages = [{"to": n, "from": sender, "text": text} for n in admin_list]

    payload = {"messages": messages}
    try:
        r = requests.post(SOLAPI_URL, headers=solapi_headers(), json=payload, timeout=10)
        try:
            res_json = r.json()
        except Exception:
            res_json = {"raw": r.text}

        if r.status_code // 100 != 2:
            return {"ok": False, "status": r.status_code, "detail": res_json, "used_sender": sender}
        return {"ok": True, "result": res_json, "sent": len(messages), "used_sender": sender}
    except Exception as e:
        return {"ok": False, "error": str(e)}
