import os


@app.post("/sms")
async def sms(request: Request):
"""
프런트에서 JSON 예시:
{
"site": "보라매",
"vd": "2025-11-06",
"vtLabel": "10:00 ~ 11:00",
"name": "[보라매] 홍길동", # 태깅을 프런트에서 붙이는 경우
"phone": "010-1234-5678",
"memo": "선택"
}
"""
body = await request.json()


site = (body.get("site") or "").strip()
vd = (body.get("vd") or "").strip()
vt_label = (body.get("vtLabel") or "").strip()
name = (body.get("name") or "").strip()
phone = normalize_phone(body.get("phone"))
memo = (body.get("memo") or "").strip()


# 필수값 검증
if not name:
return {"ok": False, "error": "name 누락"}
if not vd:
return {"ok": False, "error": "vd(방문일) 누락"}
if not phone:
return {"ok": False, "error": "phone 누락"}
if not re.fullmatch(r"\d{9,12}", phone):
return {"ok": False, "error": "수신번호 형식 오류(숫자만)"}


# 발신번호도 숫자만
sender = normalize_phone(SOLAPI_SENDER)
if not re.fullmatch(r"\d{9,12}", sender):
return {"ok": False, "error": "발신번호 형식 오류 또는 미등록"}


# 문자 본문
lines = [
f"[{site}] 방문예약 접수" if site else "[방문예약] 접수",
f"날짜: {vd}",
f"시간: {vt_label or '-'}",
f"성함: {name}",
f"연락처: {phone}",
]
if memo:
lines.append(f"메모: {memo}")
text = "\n".join(lines)


# 발송
try:
res = message_service.send({
"to": phone,
"from": sender,
"text": text,
})
# res: groupId, messageId 등 포함
return {"ok": True, "result": res}
except Exception as e:
# Render Logs에서 traceback 확인
return {"ok": False, "error": str(e)}