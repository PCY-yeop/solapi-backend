from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os, requests

# (Optional) load .env when running on environments that don't inject env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = FastAPI(title="Solapi SMS Backend")

# ===== ENV =====
SOLAPI_API_KEY = os.getenv("SOLAPI_API_KEY", "")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "")
DEFAULT_SENDER = os.getenv("SOLAPI_SENDER", "")
SOLAPI_URL = "https://api.solapi.com/messages/v4/send"

# 허용 도메인 (쉼표로 여러 개 가능). 테스트는 "*" 가능
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
origins = [o.strip() for o in CORS_ORIGINS.split(",")] if CORS_ORIGINS else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Root & Health =====
@app.get("/")
def root():
    return {"ok": True, "service": "solapi-sms", "routes": ["/health", "/reserve"]}

@app.get("/health")
def health():
    has_env = bool(SOLAPI_API_KEY and SOLAPI_API_SECRET and DEFAULT_SENDER)
    return {"ok": True, "service": "solapi-sms", "has_env": has_env}

# ===== OPTIONS (Preflight) =====
@app.options("/reserve")
async def options_reserve():
    return JSONResponse(
        content={"ok": True},
        headers={
            "Access-Control-Allow-Origin": origins[0] if len(origins) == 1 else "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )

# ===== 예약/문자 발송 =====
@app.get("/reserve")
@app.post("/reserve")
async def reserve(request: Request):
    if request.method == "GET":
        data = dict(request.query_params)
    else:
        data = await request.json()

    site   = data.get("site", "현장")
    name   = data.get("name", "")
    phone  = data.get("phone", "")
    vd     = data.get("vd", "")
    vt     = data.get("vt", "")
    sp     = data.get("sp", "")
    sender = data.get("sender", DEFAULT_SENDER)  # 프런트에서 덮어쓰기 허용
    msg    = data.get("msg")

    if not msg:
        msg = (
            f"현장이름 : {site}\n"
            f"날짜 : {vd}\n"
            f"시간 : {vt}\n"
            f"이름 : {name}\n"
            f"연락처 : {phone}"
        )

    to_number = (sp or phone or "").replace("-", "")
    sender = (sender or "").replace("-", "")

    if not to_number or not sender:
        return JSONResponse({"ok": False, "error": "phone/sender required"}, status_code=400)

    headers = {
        "Content-Type": "application/json",
        # Lambda에서 사용하던 키 방식과 동일 (필요 시 Authorization 방식으로 교체)
        "X-SOLAPI-API-KEY": SOLAPI_API_KEY,
        "X-SOLAPI-API-SECRET": SOLAPI_API_SECRET,
    }
    body = {"message": {"to": to_number, "from": sender, "text": msg}}

    try:
        resp = requests.post(SOLAPI_URL, json=body, headers=headers, timeout=15)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    try:
        solapi_data = resp.json()
    except Exception:
        solapi_data = {"raw": resp.text}

    return JSONResponse(
        content={
            "ok": resp.ok,
            "sent_to": to_number,
            "from": sender,
            "message": msg,
            "solapi_response": solapi_data,
        },
        status_code=200 if resp.ok else (resp.status_code or 500),
        headers={
            "Access-Control-Allow-Origin": origins[0] if len(origins) == 1 else "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
