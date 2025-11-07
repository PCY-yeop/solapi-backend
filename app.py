# app.py
# 정책:
# - 등록/승인된 단 하나의 관리자 번호만 사용
# - from == to == ADMIN_PHONE(= ENV_SENDER)
# - 요청의 sp는 무시 (보안/일관성)
# - 실시간으로 발신번호 등록 상태 검증 (+82 정규화 대응)
# - 디버그 라우트 제공(/version, /debug/senders)
# - 필요 시 ALLOW_UNREGISTERED=1 로 사전검증 우회 가능

import os, re, json, hmac, hashlib, uuid
from datetime import datetime
import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# ========= ENV =========
SOLAPI_API_KEY    = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
ENV_SENDER        = os.getenv("SOLAPI_SENDER", "")  # 등록/승인된 관리자 번호
ALLOW_UNREGISTERED = os.getenv("ALLOW_UNREGISTERED", "") == "1"  # 디버그/긴급 테스트용

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

def normalize_kor(num: str) -> str:
    """
    Solapi가 8210... 형태로 돌려줄 때도 국내형 010... 과 매칭되도록 보정
    """
    n = only_digits(num)
    if n.startswith("82"):
        rest = n[2:]
        # 10/11/16/17/18/19 등 휴대 국번이면 0 붙여 국내형으로
        if rest.startswith(("10","11","16","17","18","19")):
            return "0" + rest
    return n

def solapi_headers() -> dict:
    """Solapi HMAC 인증 헤더 생성"""
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
    """관리자에게 보낼 본문(5줄 포맷)"""
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
    # if memo: lines.append(f"메모 : {memo}")
    return "\n".join(lines)

# 상태 허용(계정/환경별 용어 차이 대비)
APPROVED_STATES = {
    "APPROVED", "ACTIVE", "REGISTERED",
    "CONFIRMED", "VERIFIED", "ALLOW", "ENABLED", "OK", "AVAILABLE"
}

def fetch_registered_senders() -> set:
    """솔라피 SenderID 목록 조회 → 승인된 발신번호 집합 반환 (+82/010 모두 매칭)"""
    try:
        url = "https://api.solapi.com/senderid/v1/senders"
        r = requests.get(url, headers=solapi_headers(), timeout=10)

        if "application/json" not in (r.headers.get("content-type") or "").lower():
            return set()

        data = r.json()
        items = data.get("data") or data.get("items") or []
        numbers = set()
        for it in items:
            raw = it.get("phoneNumber") or it.get("number") or ""
            status = (it.get("status") or "").upper()
            if status in APPROVED_STATES:
                n1 = only_digits(raw)
                n2 = normalize_kor(raw)
                if n1: numbers.add(n1)
                if n2: numbers.add(n2)
        return numbers
    except Exception:
        return set()

SOLAPI_SEND_URL = "https://api.solapi.com/messages/v4/send-many"

# ========= ROUTES =========
@app.get("/health")
async def health():
    return {"ok": True, "time": datetime.now().isoformat()}

@app.get("/config")
async def config():
    """프런트에서 ADMIN_PHONE 표시/검증용(민감 아님)"""
    return {"ADMIN_PHONE": normalize_kor(ENV_SENDER), "allow_unregistered": ALLOW_UNREGISTERED}

VERSION = "2025-11-07-kor-normalize+debug"

@app.get("/version")
async def version():
    return {"version": VERSION, "admin": normalize_kor(ENV_SENDER)}

@app.get("/debug/senders")
async def debug_senders():
    """솔라피가 인식한 승인번호/상태를 그대로 노출(디버깅용)"""
    try:
        url = "https://api.solapi.com/senderid/v1/senders"
        r = requests.get(url, headers=solapi_headers(), timeout=10)
        ct = (r.headers.get("content-type") or "").lower()
        data = r.json() if "application/json" in ct else {"raw": r.text}
        items = data.get("data") or data.get("items") or []
        view = []
        for it in items:
            raw = it.get("phoneNumber") or it.get("number") or ""
            status = (it.get("status") or "").upper()
            view.append({"raw": raw, "digits": only_digits(raw), "kor": normalize_kor(raw), "status": status})
        return {"ok": True, "senders": view, "admin": normalize_kor(ENV_SENDER)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/sms")
async def sms(req: Request):
    """
    요청 JSON 예:
    {
      "site": "보라매",
      "vd": "2025-11-06",
      "vtLabel": "10:00 ~ 11:00",
      "name": "홍길동",
      "phone": "01012341234",
      "memo": ""
      // sp는 무시됨 (고정 정책)
    }
    """
    body = await req.json()

    site     = (body.get("site") or "").strip()
    vd       = (body.get("vd") or "").strip()
    vt_label = (body.get("vtLabel") or "").strip()
    name     = (body.get("name") or "").strip()
    phone    = only_digits(body.get("phone"))
    memo     = (body.get("memo") or "").strip()

    # 관리자 고정 번호(국내형으로 보정)
    admin_phone = normalize_kor(ENV_SENDER)

    # ---- 기본 검증 ----
    if not site:        return {"ok": False, "error": "site 누락"}
    if not vd:          return {"ok": False, "error": "vd(날짜) 누락"}
    if not name:        return {"ok": False, "error": "name 누락"}
    if not phone:       return {"ok": False, "error": "phone(고객 연락처) 누락"}

    if not re.fullmatch(r"\d{9,12}", admin_phone):
        return {"ok": False, "error": "ENV_SENDER 형식 오류(숫자 9~12자리) 또는 미등록"}

    # ---- 실시간 등록 상태 확인: admin_phone이 등록/승인되어 있어야 함 ----
    registered = fetch_registered_senders()
    if (admin_phone not in registered) and (not ALLOW_UNREGISTERED):
        return {"ok": False, "error": "ENV_SENDER가 솔라피에 등록/승인되지 않았습니다."}

    # ---- 본문 구성 ----
    text = build_admin_text(site, vd, vt_label, name, phone, memo)

    # ---- 전송: from == to == admin_phone ----
    payload = {
        "messages": [
            {
                "to": admin_phone,
                "from": admin_phone,
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
            # 솔라피 원문 에러를 그대로 노출(디버깅 용이)
            return {"ok": False, "status": r.status_code, "detail": res_json}

        return {
            "ok": True,
            "result": res_json,
            "from_used": admin_phone,
            "to_used": admin_phone,
            "allow_unregistered": ALLOW_UNREGISTERED
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
