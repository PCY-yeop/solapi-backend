import os, re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from solapi import SolapiMessageService

# ========= ENV =========
SOLAPI_API_KEY    = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
ENV_SENDER        = os.getenv("SOLAPI_SENDER", "")  # 솔라피 등록/승인된 발신번호(숫자만)

if not (SOLAPI_API_KEY and SOLAPI_API_SECRET and ENV_SENDER):
    raise RuntimeError("ENV 누락: SOLAPI_API_KEY / SOLAPI_API_SECRET / SOLAPI_SENDER")

svc = SolapiMessageService(SOLAPI_API_KEY, SOLAPI_API_SECRET)

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

# ========= ROUTES =========
@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/sms")
async def sms(req: Request):
    """
    요청(JSON)
    {
      "site": "보라매",
      "vd": "2025-11-06",
      "vtLabel": "10:00 ~ 11:00",
      "name": "[보라매] 홍길동",
      "phone": "01012345678",   # 고객 연락처(본문 표기용)
      "sp": "01022844859",      # ✅ 관리자 번호(여기로만 보냄)
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
    admin_sp = only_digits(body.get("sp") or "")

    sender = only_digits(ENV_SENDER)

    # ---- validation ----
    if not name:    return {"ok": False, "error": "name 누락"}
    if not vd:      return {"ok": False, "error": "vd(방문일) 누락"}
    if not phone:   return {"ok": False, "error": "phone(고객) 누락"}
    if not re.fullmatch(r"\d{9,12}", phone):
        return {"ok": False, "error": "수신번호(고객) 형식 오류(숫자만 9~12자리)"}
    if not admin_sp:
        return {"ok": False, "error": "관리자번호(sp) 누락"}
    if not re.fullmatch(r"\d{9,12}", admin_sp):
        return {"ok": False, "error": "관리자번호(sp) 형식 오류(숫자만 9~12자리)"}
    if not re.fullmatch(r"\d{9,12}", sender):
        return {"ok": False, "error": "발신번호 형식 오류 또는 미등록"}

    admin_text = build_admin_text(site, vd, vt_label, name, phone, memo)

    try:
        def send_msg(msg: dict):
            """
            SDK 버전별 시그니처 차이를 흡수:
            - send_one / sendOne : {"message": {...}}
            - send_many / sendMany: {"messages": [{...}]}
            - send : 보통 {"message": {...}}를 받음 (안되면 messages로 재시도)
            """
            if hasattr(svc, "send_one"):
                return getattr(svc, "send_one")({"message": msg})
            if hasattr(svc, "sendOne"):
                return getattr(svc, "sendOne")({"message": msg})
            if hasattr(svc, "send_many"):
                return getattr(svc, "send_many")({"messages": [msg]})
            if hasattr(svc, "sendMany"):
                return getattr(svc, "sendMany")({"messages": [msg]})
            if hasattr(svc, "send"):
                try:
                    return getattr(svc, "send")({"message": msg})
                except Exception:
                    return getattr(svc, "send")({"messages": [msg]})
            raise RuntimeError("Solapi SDK: send 메서드를 찾을 수 없음")

        # 관리자에게만 전송
        res = send_msg({"to": admin_sp, "from": sender, "text": admin_text})
        return {"ok": True, "result": {"admin": res}}

    except Exception as e:
        return {"ok": False, "error": str(e)}
