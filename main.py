from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

# ========== ENVIRONMENT VARIABLES ==========
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "acspro-verify")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


# ===========================================
#   WHATSAPP CLOUD API WEBHOOK
# ===========================================
@app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)
    return {"error": "Verification failed"}


@app.post("/webhook")
async def whatsapp_handler(request: Request):
    body = await request.json()
    print("WhatsApp Incoming:", body)
    return {"status": "received"}



# ===========================================
#   TELEGRAM BOT WEBHOOK ENDPOINT
# ===========================================
@app.post("/api/telegram-webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    print("Telegram Incoming:", update)

    # 1) Extract chat_id
    chat_id = None
    message_text = None

    if "message" in update:
        chat_id = update["message"]["chat"]["id"]
        message_text = update["message"].get("text", "")

    if not chat_id:
        return {"status": "no_chat"}

    # 2) Process input (REPLACE HERE with real ACS PRO logic later)
    reply = process_message(message_text)

    # 3) Send reply back to Telegram
    send_telegram_message(chat_id, reply)

    return {"status": "sent"}



# ===========================================
#   SIMPLE BOT LOGIC FOR NOW
#   (We will hook ACS PRO Restaurant Bot here)
# ===========================================
def process_message(text):
    text = text.strip().lower()

    # Temporary demo logic (replace with ACS PRO template logic)
    if "menu" in text or "منيو" in text:
        return "قائمة الطعام:\n🍔 برغر\n🍟 بطاطا\n🥤 مشروبات"
    if text in ["hi", "hello", "مرحبا", "هلا"]:
        return "أهلاً! كيف يمكنني مساعدتك اليوم؟ 😊"

    return "تم استلام رسالتك! ✨"



# ===========================================
#   SEND MESSAGE TO TELEGRAM
# ===========================================
def send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("❌ Missing TELEGRAM_BOT_TOKEN in Render")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    r = requests.post(url, json=payload)
    print("Telegram send response:", r.text)



# ===========================================
#   ROOT PATH (OPTIONAL)
# ===========================================
@app.get("/")
async def home():
    return {"status": "ACS PRO backend running"}
