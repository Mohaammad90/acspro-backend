import os
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

app = FastAPI()

# ==========================
#  ENVIRONMENT VARIABLES
# ==========================
# You MUST set these on your server / hosting platform
SUPABASE_URL = os.getenv("SUPABASE_URL")  # e.g. https://splyctvmbihdllbomrpg.supabase.co
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# WhatsApp Cloud API credentials (for now, one global number)
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")       # you choose this string in Meta
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")       # from Meta WhatsApp app
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID") # from Meta WhatsApp app


# ==========================
#  HELPERS
# ==========================

def require_env_var(name: str) -> str:
    """Utility: fail loudly if critical env var is missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def fetch_bot_config(bot_id: str):
    """
    Load bot row from Supabase 'bots' table using the service role key.
    This is UNIVERSAL: any bot_id from any user can be loaded.
    """
    supabase_url = require_env_var("SUPABASE_URL")
    service_key = require_env_var("SUPABASE_SERVICE_ROLE_KEY")

    url = f"{supabase_url}/rest/v1/bots"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json",
    }
    params = {
        "id": f"eq.{bot_id}",
        "select": "*",
    }

    resp = requests.get(url, headers=headers, params=params, timeout=10)
    if not resp.ok:
        raise RuntimeError(f"Supabase error: {resp.status_code} {resp.text}")

    rows = resp.json()
    if not rows:
        return None

    # Return the first matching bot
    return rows[0]


def build_reply_from_bot(bot: dict, incoming_text: str) -> str:
    """
    Very simple restaurant logic based on config_json.
    Later you can make this smarter (NLU, flows, etc.)
    """
    config = bot.get("config_json") or {}

    restaurant_name = config.get("restaurantName") or "المطعم"
    tagline = config.get("restaurantTagline") or ""
    welcome = config.get("welcomeMessage") or f"مرحباً 👋 في {restaurant_name}!"
    menu_items_str = config.get("menuItems") or ""

    # Build a short menu preview
    menu_preview = ""
    if menu_items_str:
        lines = [line.strip() for line in menu_items_str.split("\n") if line.strip()]
        if lines:
            menu_preview = " • ".join(lines[:4])

    # Simple rule-based logic (you can expand later)
    text_lower = (incoming_text or "").strip().lower()

    if text_lower in ["hi", "hello", "مرحبا", "السلام عليكم", "اهلا", "هاي"]:
        # Greeting
        reply = welcome
        if tagline:
            reply += "\n" + tagline
        if menu_preview:
            reply += "\n\nبعض الأصناف في المنيو:\n" + menu_preview
        reply += "\n\nاكتب مثلاً: قائمة الطعام، طلب جديد، أو توصيل."
    elif "قائمة" in text_lower or "منيو" in text_lower:
        reply = "هذه بعض أصناف المنيو:\n"
        reply += menu_preview or "لم يتم إدخال منيو بعد."
        reply += "\n\nاكتب: طلب جديد لبدء الطلب."
    elif "طلب" in text_lower:
        reply = "رائع! 😊\n\nاكتب لي اسم الطبق الذي تريد طلبه وعدد الحصص، مثلاً:\nشاورما دجاج ٢\nكبسة لحم ١"
    else:
        # Default fallback
        reply = welcome
        if menu_preview:
            reply += "\n\nلم أفهم رسالتك بالضبط، لكن هذه بعض الأصناف لدينا:\n" + menu_preview

    return reply


def send_whatsapp_text(to_number: str, body: str):
    """
    Send a text message via WhatsApp Cloud API.
    """
    phone_number_id = require_env_var("WHATSAPP_PHONE_NUMBER_ID")
    access_token = require_env_var("WHATSAPP_ACCESS_TOKEN")

    url = f"https://graph.facebook.com/v20.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": body,
        },
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    if not resp.ok:
        raise RuntimeError(f"WhatsApp API error: {resp.status_code} {resp.text}")


# ==========================
#  WHATSAPP WEBHOOK (GET)
#  Used by Meta to VERIFY URL
# ==========================

@app.get("/api/whatsapp-webhook")
async def verify_whatsapp(request: Request):
    """
    Meta sends a GET request to verify the webhook:
    /api/whatsapp-webhook?hub.mode=subscribe&hub.verify_token=XXX&hub.challenge=YYYY
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    verify_token = require_env_var("WHATSAPP_VERIFY_TOKEN")

    if mode == "subscribe" and token == verify_token:
        # Meta expects the challenge string as plain text
        return PlainTextResponse(challenge or "")
    else:
        raise HTTPException(status_code=403, detail="Verification failed")


# ==========================
#  WHATSAPP WEBHOOK (POST)
#  Receives messages from users
# ==========================

@app.post("/api/whatsapp-webhook")
async def receive_whatsapp(request: Request):
    """
    This handles incoming WhatsApp messages.

    Meta will POST a JSON payload.
    We also expect a query param: ?bot_id=... (from your deploy page link)
    """
    params = request.query_params
    bot_id = params.get("bot_id")

    if not bot_id:
        # You could also log this and return 200 to avoid retries.
        raise HTTPException(status_code=400, detail="Missing bot_id in query string")

    data = await request.json()

    # Safely navigate WhatsApp payload
    try:
        entry = data["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages", [])
        if not messages:
            # e.g. delivery / status updates, ignore
            return JSONResponse({"status": "no-message"}, status_code=200)

        msg = messages[0]
        from_number = msg["from"]  # e.g. "201234567890"
        text_body = msg.get("text", {}).get("body", "")
    except Exception as e:
        # Log and ignore malformed payloads
        print("Error parsing WhatsApp payload:", e, data)
        return JSONResponse({"status": "ignored"}, status_code=200)

    # Load bot config from Supabase
    bot = fetch_bot_config(bot_id)
    if not bot:
        # If bot not found, send a generic message
        try:
            send_whatsapp_text(from_number, "عذراً، هذا البوت غير متوفر حالياً.")
        except Exception as e:
            print("Error sending fallback message:", e)
        return JSONResponse({"status": "bot-not-found"}, status_code=200)

    # Build reply based on bot config + user text
    try:
        reply_text = build_reply_from_bot(bot, text_body)
    except Exception as e:
        print("Error building reply:", e)
        reply_text = "حدث خطأ غير متوقع. يرجى المحاولة لاحقاً."

    # Send reply to user
    try:
        send_whatsapp_text(from_number, reply_text)
    except Exception as e:
        print("Error sending WhatsApp message:", e)
        return JSONResponse({"status": "send-error"}, status_code=500)

    return JSONResponse({"status": "ok"}, status_code=200)


# ==========================
#  OPTIONAL: GENERIC BOT API
#  /api/acspro-bot
#  (matches your deploy page example)
# ==========================

@app.post("/api/acspro-bot")
async def acspro_bot_endpoint(request: Request):
    """
    Example universal bot endpoint (not tied to WhatsApp only).

    Request JSON example:
    {
      "bot_id": "...",
      "channel": "whatsapp",
      "from": "+1234567890",
      "message": "مرحبا",
      "meta": {...}
    }
    """
    body = await request.json()
    bot_id = body.get("bot_id")
    message = body.get("message") or ""
    from_id = body.get("from")

    if not bot_id:
        raise HTTPException(status_code=400, detail="bot_id is required")

    bot = fetch_bot_config(bot_id)
    if not bot:
        return JSONResponse(
            {"reply": "عذراً، هذا البوت غير متوفر حالياً.", "bot_found": False},
            status_code=200,
        )

    reply_text = build_reply_from_bot(bot, message)

    # We just return the reply here; the caller decides what channel to send on
    return JSONResponse(
        {"reply": reply_text, "bot_found": True, "channel": body.get("channel"), "from": from_id},
        status_code=200,
    )
