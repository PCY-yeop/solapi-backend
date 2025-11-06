import os, re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from solapi import SolapiMessageService

# ========= 환경변수 =========
SOLAPI_API_KEY    = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
ENV_SENDER        = os.getenv("SOLAPI_SENDER", "")  # 발신번호(숫자만, 솔라피 등록/승인)

if not (SOLAPI_API_KEY and SOLAPI_API_SECRET and ENV_SENDER):
    raise RuntimeError("ENV 누락: SOLAPI_API_KEY / SOLAPI_API_SECRET / SOLAPI_SENDER")

svc = SolapiMessageService(SOLAPI_API_KEY, SOLAPI_API_SECRET)

# ========= FastAPI & CORS =========
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 운영 시 도메인으로 좁히세요 (예: https://*.vercel.app)
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS", "GET"],
    allow_headers=["Content-Type"],
)

# ========= 유틸 =========
DIGITS = re.compile(r"[^\d]")
def only_digits(s: str) -> str:
    return DIGITS.sub("", s or "")

def build_customer_text(site: str, vd: str, vt_label: str, name: str, phone: str) -> str:
    # 고객에게 보내는 본문
    lines = [
        f"[{site}] 방문예약 접수" if site else "[방문예약] 접수",
        f"날짜: {vd or '-'}",
        f"시간: {vt_label or '-'}",
        f"성함: {name or '-'}",
    ]
    return "\n".join(lines)

def build_admin_text(site: str, vd: str, vt_label: str, name: str, phone: str, memo: str) -> str:
    # 관리자(sp)에게 보내는 알림(고객 번호 포함)
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

# ========= 라우터 =========
@app.get("/health")
async def health():
    return {"ok": True}

@app.options("/sms")
async def _options_sms():
    return {"ok": True}

@app.post("/sms")
async def send_sms(req: Request):
    """
    요청(JSON):
    {
      "site": "보라매",
      "vd": "2025-11-06",
      "vtLabel": "10:00 ~ 11:00",
      "name": "[보라매] 홍길동",
      "phone": "01012345678",        # 고객 번호 (필수)
      "sp": "01099998888",           # 관리자 번호 (선택) → 있으면 관리자에게도 발송
      "memo": ""
    }
    """
    body = await req.json()

    site     = (body.get("site") or "").strip()
    vd       = (body.get("vd") or "").strip()
    vt_label = (body.get("vtLabel") or "").strip()
    name     = (body.get("name") or "").strip()
    phone    = only_digits(body.get("phone"))  # 고객
    memo     = (body.get("memo") or "").strip()
    admin_sp = only_digits(body.get("sp") or "")  # 관리자 (선택)

    sender = only_digits(ENV_SENDER)

    # 기본 검증
    if not name:
        return {"ok": False, "error": "name 누락"}
    if not vd:
        return {"ok": False, "error": "vd(방문일) 누락"}
    if not phone:
        return {"ok": False, "error": "phone(고객) 누락"}
    if not re.fullmatch(r"\d{9,12}", phone):
        return {"ok": False, "error": "수신번호(고객) 형식 오류(숫자만 9~12자리)"}
    if not re.fullmatch(r"\d{9,12}", sender):
        return {"ok": False, "error": "발신번호 형식 오류 또는 미등록"}

    # 본문 생성
    customer_text = build_customer_text(site, vd, vt_label, name, phone)
    admin_text    = build_admin_text(site, vd, vt_label, name, phone, memo)

        results = {}
    try:
        def send_msg(msg):
            # SDK 버전에 따라 존재하는 메서드가 달라서 안전하게 순차 시도
            if hasattr(svc, "send_many"):
                return getattr(svc, "send_many")([msg])        # messages 배열로 전달
            if hasattr(svc, "sendOne"):
                return getattr(svc, "sendOne")(msg)            # camelCase 버전
            if hasattr(svc, "send_one"):
                return getattr(svc, "send_one")(msg)           # snake_case 버전
            if hasattr(svc, "send"):
                try:
                    # 일부 버전은 dict 하나를 받기도 함
                    return getattr(svc, "send")(msg)
                except Exception:
                    # dict가 안 되면 messages 배열로 재시도
                    return getattr(svc, "send")({"messages": [msg]})
            # 위가 다 없을 경우(거의 없음)
            raise RuntimeError("Solapi SDK: no valid send method found")

        # 1) 고객에게 전송
        results["customer"] = send_msg({"to": phone, "from": sender, "text": customer_text})

        # 2) 관리자(sp)에게도 전송(옵션)
        if admin_sp:
            if not re.fullmatch(r"\d{9,12}", admin_sp):
                return {"ok": False, "error": "관리자번호(sp) 형식 오류(숫자만 9~12자리)"}
            results["admin"] = send_msg({"to": admin_sp, "from": sender, "text": admin_text})

        return {"ok": True, "result": results}
