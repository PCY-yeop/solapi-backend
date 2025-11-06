# app.py
# - sp(수신자) 번호가 솔라피에 '발신번호'로 등록/승인되어 있으면: from=sp, to=sp
# - 등록/승인이 안 되어 있으면: from=ENV_SENDER(고정), to=sp
# - 실시간 확인(캐시 없음)

import os, re, json, hmac, hashlib, uuid
from datetime import datetime
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# ========= ENV =========
SOLAPI_API_KEY    = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
ENV_SENDER        = os.getenv("SOLAPI_SENDER", "")  # 반드시 솔라피에 등록/승인된 발신번호

if not (SOLAPI_API_KEY and SOLAPI_API_SECRET and ENV_SENDER):
    raise RuntimeError("ENV 누락: SOLAPI_API_KEY / SOLAPI_API_SECRET / SOLAPI_SENDER")

# ========= APP / CORS =========
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 운영 시 허용 도메인으로 제한 권장
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========= UTILS =========
DIGITS = re.compile(r"[^\d]")

def only_digits(s: str) -> str:
    return DIGITS.sub("", s or "")

def solapi_headers() -> dict:
    """Solapi HMAC 인증 헤더"""
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

def build_admin_text(site: str, vd: str, vt_label: str, name: str, phone: str, memo: str) -> str:
    """관리자에게 보낼 본문(요청한 5줄 포맷)"""
    site_disp = site or ""
    if site_disp and not (site_disp.startswith("[") and site_disp.endswith("]")):
        site_disp = f"[{site_disp}]"
    time_disp = (vt_label or "").strip() or "-"
    lines = [
        f"현장 : {site_disp or '-'}",
        f"날짜 : {vd or '-'}",
        f"시간 : {time_disp}",
        f"이름 : {name or '-'}",
        f"연락처 : {phone or '-'}",
    ]
    # 필요 시 메모 추가
    # if memo: lines.append(f"메모 : {memo}")
    return "\n".join(lines)

def fetch_registered_senders() -> set:
    """
    ✅ 실시간: 솔라피 SenderID 목록을 즉시 조회하여
    등록/승인된 발신번호 집합을 반환.
    """
    try:
        # SenderID 목록 조회 (솔라피 공식 엔드포인트)
        url = "https://api.solapi.com/senderid/v1/senders"
        r = requests.get(url, headers=solapi_headers(), timeout=10)

        # JSON 파싱
        if "application/json" in (r.headers.get("content-type") or "").lower():
            data = r.json()
        else:
            # 비정상 응답이면 빈 집합
            return set()

        items = data.get("data") or data.get("items") or []
        numbers = set()
        for it in items:
            # 응답 키 이름은 계정/버전에 따라 다를 수 있어 방어적으로 처리
            num = only_digits(it.get("phoneNumber") or it.get("number") or "")
            status = (it.get("status") or "").upper()
            # 승인 상태(케이스에 따라 APPROVED/ACTIVE/REGISTERED 등)만 허용
            if num and status in {"APPROVED", "ACTIVE", "REGISTERED"}:
                numbers.add(num)
        return numbers
    except Exception:
        return set()

SOLAPI_SEND_URL = "https://api.solapi.com/messages/v4/send-many"

# ========= ROUTES =========
@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/sms")
async def sms(req: Request):
    """
    요청 JSON 예:
    {
      "site": "보라매",
      "vd": "2025-11-06",
      "vtLabel": "10:00 ~ 11:00",
      "name": "홍길동",
      "phone": "01012341234",     # 고객 연락처(본문 표기용)
      "sp": "01022844859",        # 관리자(수신자) 번호
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
    admin_sp = only_digits(body.get("sp") or "")   # ← 수신자

    sender_default = only_digits(ENV_SENDER)

    # ---- 기본 검증 ----
    if not site:     return {"ok": False, "error": "site 누락"}
    if not vd:       return {"ok": False, "error": "vd(날짜) 누락"}
    if not name:     return {"ok": False, "error": "name 누락"}
    if not phone:    return {"ok": False, "error": "phone(고객 연락처) 누락"}
    if not admin_sp: return {"ok": False, "error": "관리자번호(sp) 누락"}

    if not re.fullmatch(r"\d{9,12}", admin_sp):
        return {"ok": False, "error": "관리자번호(sp) 형식 오류(숫자만 9~12자리)"}
    if not re.fullmatch(r"\d{9,12}", sender_default):
        return {"ok": False, "error": "기본 발신번호(SOLAPI_SENDER) 형식 오류 또는 미등록"}

    # ---- 실시간: 등록 발신번호 목록 조회 → sp가 있으면 from=sp ----
    registered_senders = fetch_registered_senders()
    sender = admin_sp if admin_sp in registered_senders else sender_default

    # ---- 본문 구성 ----
    text = build_admin_text(site, vd, vt_label, name, phone, memo)

    # ---- 전송 ----
    # (문자 길이가 짧으니 SMS 강제. 길어지면 LMS/MMS로 전환될 수 있음)
    payload = {
        "messages": [
            {
                "to": admin_sp,
                "from": sender,
                "type": "SMS",
                "text": text
            }
        ]
    }

    try:
        r = requests.post(SOLAPI_SEND_URL, headers=solapi_headers(), json=payload, timeout=10)
        try:
            res_json = r.json()
        except Exception:
            res_json = {"raw": r.text}

        if r.status_code // 100 != 2:
            # 솔라피 측 에러 메시지를 그대로 반환해 디버깅 용이
            return {"ok": False, "status": r.status_code, "detail": res_json}
        return {"ok": True, "result": res_json, "from_used": sender, "registered_hit": (sender == admin_sp)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
