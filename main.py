from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os, requests

app = FastAPI()

SOLAPI_API_KEY = os.getenv("SOLAPI_API_KEY", "NCSRQHAI28ERLLA2")
SOLAPI_API_SECRET = os.getenv("SOLAPI_API_SECRET", "VALWDYZFPOOUMC47MSCGBNKUJQWAVMCS")
DEFAULT_SENDER = os.getenv("SOLAPI_SENDER", "01012345678")

SOLAPI_URL = "https://api.solapi.com/messages/v4/send"


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
    sender = data.get("sender", DEFAULT_SENDER)  # ✅ 프런트에서 동적으로 지정 가능
    msg    = data.get("msg")

    if not msg:
        msg = (
            f"현장이름 : {site}\n"
            f"날짜 : {vd}\n"
            f"시간 : {vt}\n"
            f"이름 : {name}\n"
            f"연락처 : {phone}"
        )

    to_number = sp or phone

    resp = requests.post(
        SOLAPI_URL,
        json={
            "message": {
                "to": to_number,
                "from": sender,
                "text": msg,
            }
        },
        headers={
            "Content-Type": "application/json",
            "X-SOLAPI-API-KEY": SOLAPI_API_KEY,
            "X-SOLAPI-API-SECRET": SOLAPI_API_SECRET,
        },
    )

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
        status_code=200 if resp.ok else 500,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
