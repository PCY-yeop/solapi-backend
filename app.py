import os, re, json, hmac, hashlib, uuid
from datetime import datetime
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# ========= ENV =========
SOLAPI_API_KEY    = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
ENV_SENDER        = os.getenv("SOLAPI_SENDER", "")  # ✅ 솔라피 등록된 발신번호

if not (SOLAPI_API_KEY and SOLAPI_API_SECRET and ENV_SENDER):
    raise RuntimeError("ENV 누락: SOLAPI_API_KEY / SOLAPI_API_SECRET / SOLAPI_SENDER")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DIGITS = re.compile(r"[^\d]")
def only_digits(s: str) -> str:
    return DIGITS.sub("", s or "")

def build_admin_text(site: str, vd: str, vt_label: str, name: str, phone: str, memo: str) -> str:
    return "\n".join([
        f"[알림] {site or '현장'} 방문예약 도착",
        f"날짜: {vd or '-'}",
        f"시간: {vt_label or '-'}",
        f"성함: {name or '-'}",
        f"연락처(고객): {phone or '-'}",
        *( [f"메모: {memo}"] if memo else [] )
    ])

def solapi_headers() -> dict:
    date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    salt = uuid.uuid4().hex
    signature = hmac.new(
        SOLAPI_API_SECRET.encode("utf-8"),
        (date + salt).encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"HMAC-SHA256 apiKey={SOLAPI_API_KEY}, date={date}, salt={salt}, signature={signature}"
    }

SOLAPI_URL = "https://api.solapi.com/messages/v4/send-many"

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/sms")
async def sms(req: Request):
    body = await req.json()

    site     = (body.get("site") or "").strip()
    vd       = (body.get("vd") or "").strip()
    vt_label = (body.get("vtLabel") or "").strip()
    name     = (body.get("name") or "").strip()
    phone    = only_digits(body.get("phone"))
    memo     = (body.get("memo") or "").strip()
    admin_sp = only_digits(body.get("sp") or "")      # ✅ 받는 사람

    sender = only_digits(ENV_SENDER)                  # ✅ 보내는 사람(서버 고정)

    # ---- validation ----
    if not admin_sp:
        return {"ok": False, "error": "관리자번호(sp) 누락"}
    if not re.fullmatch(r"\d{9,12}", admin_sp):
        return {"ok": False, "error": "관리자번호 형식 오류(숫자만 9~12자리)"}
    if not re.fullmatch(r"\d{9,12}", sender):
        return {"ok": False, "error": "발신번호 형식 오류 또는 미등록"}

    payload = {
        "messages": [
            {"to": admin_sp, "from": sender, "text": build_admin_text(site, vd, vt_label, name, phone, memo)}
        ]
    }

    try:
        r = requests.post(SOLAPI_URL, headers=solapi_headers(), json=payload, timeout=10)
        try:
            res_json = r.json()
        except:
            res_json = {"raw": r.text}

        if r.status_code // 100 != 2:
            return {"ok": False, "status": r.status_code, "detail": res_json}

        return {"ok": True, "result": res_json}

    except Exception as e:
        return {"ok": False, "error": str(e)}
