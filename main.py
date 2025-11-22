from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

# ===========================================
#   ENVIRONMENT VARIABLES
# ===========================================
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "acspro-verify")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# This is the bot created from your Restaurant template
DEFAULT_RESTAURANT_BOT_ID = os.getenv("DEFAULT_RESTAURANT_BOT_ID")


# ===========================================
#   HELPERS: SUPABASE BOT LOADER
# ===========================================
def fetch_bot_from_supabase(bot_id: str):
    """
    Load bot row from Supabase using REST API.
    Returns a dict with bot fields or None.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        return None

    try:
        url = f"{SUPABASE_URL}/rest/v1/bots"
        headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        }
        params = {
            "id": f"eq.{bot_id}",
            "select": "id,bot_name,template,config_json",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        if resp.status_code != 200:
            print("❌ Supabase error:", resp.status_code, resp.text)
            return None

        data = resp.json()
        if not data:
            print("⚠️ No bot found in Supabase for id:", bot_id)
            return None

        return data[0]
    except Exception as e:
        print("❌ Exception while fetching bot from Supabase:", e)
        return None


# ===========================================
#   RESTAURANT BOT ENGINE (USING config_json)
# ===========================================
def generate_restaurant_reply(config: dict, user_text: str) -> str:
    """
    Very simple rule-based restaurant bot.
    Uses fields from config_json:
      - restaurantName
      - restaurantTagline
      - welcomeMessage
      - menuItems
      - phoneNumber
      - address
      - openingHours
      - deliveryOptions
      - quickReplies
    """

    if not config:
        return "المساعد غير مهيّأ حالياً. الرجاء المحاولة لاحقاً."

    text = (user_text or "").strip().lower()

    restaurant_name = config.get("restaurantName", "المطعم")
    tagline = config.get("restaurantTagline", "")
    welcome = config.get("welcomeMessage") or f"مرحباً 👋، معك المساعد الذكي لـ {restaurant_name}."
    menu_items = config.get("menuItems", "").strip()
    phone = config.get("phoneNumber", "")
    address = config.get("address", "")
    opening_hours = config.get("openingHours", "")
    delivery_options = config.get("deliveryOptions", "")
    quick_replies = config.get("quickReplies", "")

    # --------- Helpers ----------
    def has_any(words):
        return any(w in text for w in words)

    # --------- Intents ----------

    # 1) Greeting / start
    if has_any(["hi", "hello", "مرحبا", "هلا", "السلام عليكم", "سلام"]):
        parts = [welcome]
        if tagline:
            parts.append(f"\n\n{tagline}")
        if opening_hours:
            parts.append(f"\n\n⏰ أوقات العمل:\n{opening_hours}")
        return "\n".join(parts)

    # 2) Menu / منيو / food
    if has_any(["menu", "منيو", "قائمة", "قائمة الطعام", "الأكل", "اكل", "طعام"]):
        if menu_items:
            msg = f"📋 منيو {restaurant_name}:\n\n{menu_items}"
        else:
            msg = f"حالياً لا توجد منيو مضافة في النظام لـ {restaurant_name}."
        if phone:
            msg += f"\n\n📞 للتواصل: {phone}"
        return msg

    # 3) Delivery / توصيل
    if has_any(["توصيل", "delivery", "دليفري", "ديليفري"]):
        if delivery_options:
            return f"🚚 خيارات التوصيل:\n{delivery_options}"
        else:
            return "🚚 حالياً لا توجد معلومات عن التوصيل في إعدادات المطعم."

    # 4) Opening hours / أوقات العمل
    if has_any(["العمل", "الدوام", "hours", "متى تفتح", "مواعيد", "فتح", "تغلق"]):
        if opening_hours:
            return f"⏰ أوقات العمل:\n{opening_hours}"
        else:
            return "⏰ لم يتم ضبط أوقات العمل بعد."

    # 5) Address / location / موقع
    if has_any(["عنوان", "location", "لوكيشن", "الموقع", "وينكم", "فينكم"]):
        msg = "📍 موقع المطعم:\n"
        if address:
            msg += address
        else:
            msg += "لم يتم إضافة عنوان للمطعم بعد."
        return msg

    # 6) Phone / contact
    if has_any(["اتصال", "رقم", "phone", "اتواصل", "التواصل"]):
        if phone:
            return f"📞 للتواصل:\n{phone}"
        else:
            return "📞 لم يتم إدخال رقم هاتف للمطعم بعد."

    # 7) Fallback with quick replies
    fallback = [f"تم استلام رسالتك 🤝 من {restaurant_name}."]
    if quick_replies:
        fallback.append("\nبعض الأسئلة الشائعة:\n" + quick_replies)
    else:
        fallback.append("\nيمكنك أن تكتب: منيو، توصيل، أوقات العمل، العنوان، رقم التواصل…")
    return "".join(fallback)


def process_bot_message(bot_id: str, user_text: str) -> str:
    """
    Load bot from Supabase and generate a reply
    using the restaurant engine. Later you can
    route by bot.template for other bot types.
    """
    if not bot_id:
        return "لم يتم ربط هذه القناة بأي بوت بعد."

    bot_row = fetch_bot_from_supabase(bot_id)
    if not bot_row:
        return "تعذّر تحميل إعدادات البوت. الرجاء المحاولة لاحقاً."

    config = bot_row.get("config_json") or {}
    # في المستقبل يمكننا استخدام bot_row["template"] للتفرقة بين أنواع القوالب
    return generate_restaurant_reply(config, user_text)


# ===========================================
#   WHATSApp WEBHOOK (STILL BASIC)
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

    # TODO: when Meta is ready, map phone_number_id or business to bot_id
    # For now we just acknowledge
    return {"status": "received"}


# ===========================================
#   TELEGRAM BOT WEBHOOK ENDPOINT
# ===========================================
@app.post("/api/telegram-webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    print("Telegram Incoming:", update)

    chat_id = None
    message_text = None

    if "message" in update:
        chat = update["message"].get("chat", {})
        chat_id = chat.get("id")
        message_text = update["message"].get("text", "")

    if not chat_id:
        return {"status": "no_chat"}

    # Single-tenant for now: one restaurant bot id
    bot_id = DEFAULT_RESTAURANT_BOT_ID
    if not bot_id:
        reply = "لم يتم إعداد البوت لهذه القناة بعد. الرجاء إبلاغ صاحب البوت بضبط DEFAULT_RESTAURANT_BOT_ID."
    else:
        reply = process_bot_message(bot_id, message_text or "")

    send_telegram_message(chat_id, reply)
    return {"status": "sent"}


# ===========================================
#   SEND MESSAGE TO TELEGRAM
# ===========================================
def send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("❌ Missing TELEGRAM_BOT_TOKEN in Render")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    try:
        r = requests.post(url, json=payload, timeout=5)
        print("Telegram send response:", r.text)
    except Exception as e:
        print("❌ Error sending Telegram message:", e)


# ===========================================
#   ROOT PATH
# ===========================================
@app.get("/")
async def home():
    return {"status": "ACS PRO backend running", "telegram": bool(TELEGRAM_TOKEN)}
