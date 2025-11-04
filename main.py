
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
import os, requests, re

# Optional: load .env for local runs
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = FastAPI(title="Solapi SMS Backend", version="1.0.0")

# ===== ENV =====
SOLAPI_API_KEY   = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET= os.getenv("SOLAPI_API_SECRET", "")   # 일부 환경에서는 X-Secret-Key만 사용할 수도 있음
SOLAPI_SECRET_ONLY= os.getenv("SOLAPI_SECRET_ONLY", "false").lower() in ("1","true","yes")
SOLAPI_SENDER    = os.getenv("SOLAPI_SENDER", "")       # 발신번호
CORS_ORIGINS     = os.getenv("CORS_ORIGINS", "*")       # 쉼표로 여러 도메인
ALLOW_DEV_ECHO   = os.getenv("ALLOW_DEV_ECHO", "false").lower() in ("1","true","yes")

origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if "*" not in origins else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

PHONE_RE = re.compile(r"[^\d]")

@app.get("/health")
def health():
    return {"ok": True}

@app.options("/send")
async def options_send():
    return PlainTextResponse("ok", headers={
        "Access-Control-Allow-Origin": origins[0] if len(origins)==1 else "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })

def normalize_phone(s: str) -> str:
    return PHONE_RE.sub("", s or "")

def build_message(site: str, vd: str, vt_label: str, name: str, phone: str) -> str:
    # 사용자가 원하는 형식으로 메시지 구성
    parts = [
        f"현장: {site}".strip(),
        f"날짜: {vd}".strip()
    ]
    if vt_label:
        parts.append(f"시간: {vt_label}".strip())
    parts += [
        f"성함: {name}".strip(),
        f"연락처: {phone}".strip(),
    ]
    return "\n".join(parts)

def send_via_solapi(text: str, to: str):
    """
    SOLAPI 전송 (메시지 v4)

    두 가지 모드를 지원합니다.
    1) SECRET_ONLY 모드 (일부 환경에서 X-Secret-Key 만으로 사용 가능할 때)
    2) 표준 API KEY/SECRET 모드 (권장: 공식 문서의 HMAC 인증 사용)

    운영 환경에 맞게 헤더를 조정해야 합니다.
    """
    url = "https://api.solapi.com/messages/v4/send"

    headers = {
        "Content-Type": "application/json; charset=utf-8",
    }

    if SOLAPI_SECRET_ONLY and SOLAPI_API_SECRET:
        # 일부 호스팅에서 제공하는 간편 인증 (환경에 따라 불가할 수 있음)
        headers["X-Secret-Key"] = SOLAPI_API_SECRET
    else:
        # 표준 HMAC 인증 — 실제 운영에서는 공식 문서대로 정확히 구현하세요.
        # 여기서는 간이 방식으로 제공하며, 계정 정책에 따라 거부될 수 있습니다.
        # 반드시 실제 배포 전 솔라피 문서를 확인해 올바른 Authorization 헤더를 구성하세요.
        api_key = SOLAPI_API_KEY
        secret  = SOLAPI_API_SECRET
        if not api_key or not secret:
            return {"ok": False, "error": "SOLAPI_API_KEY / SOLAPI_API_SECRET 환경변수 필요"}
        # 안전하게는 공식 SDK 사용 권장
        headers["X-User-Api-Key"] = api_key
        headers["X-Secret-Key"]   = secret

    payload = {
        "message": {
            "to": to,
            "from": SOLAPI_SENDER,
            "text": text
        }
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    return {"ok": resp.ok, "status": resp.status_code, "data": data}

@app.post("/send")
async def send(request: Request):
    body = await request.json()
    site     = (body.get("site") or "").strip() or "현장명"
    vd       = (body.get("vd") or "").strip()
    vt_label = (body.get("vtLabel") or "").strip()
    name     = (body.get("name") or "").strip()
    phone    = normalize_phone(body.get("phone") or "")

    if not vd or not name or not phone:
        return JSONResponse({"ok": False, "error": "필수값 누락(vd/name/phone)"}, status_code=400)

    # 개발 테스트: 실제 발송 대신 echo
    if ALLOW_DEV_ECHO and phone == "00000000000":
        msg = build_message(site, vd, vt_label, name, phone)
        return {"ok": True, "dev_echo": True, "message_preview": msg}

    if not SOLAPI_SENDER:
        return JSONResponse({"ok": False, "error": "SOLAPI_SENDER(발신번호) 미설정"}, status_code=500)

    msg = build_message(site, vd, vt_label, name, phone)
    result = send_via_solapi(msg, phone)

    status = 200 if result.get("ok") else 502
    return JSONResponse({
        "ok": result.get("ok"),
        "message": msg,
        "solapi_result": result.get("data"),
        "status": result.get("status"),
    }, status_code=status)
