import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import httpx
from supabase import create_client, Client

# ==============================
#  ENVIRONMENT / GLOBAL CLIENTS
# ==============================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # not anon key
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # your Telegram bot token

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set as env vars on Render")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN must be set as env var on Render")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = FastAPI()

# CORS (you can adjust origins as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or your domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================================
#  HELPER: LOAD BOT FROM SUPABASE
# ==================================

def get_bot_from_supabase(bot_id: str) -> Optional[Dict[str, Any]]:
    """
    Load bot row (including config_json) from Supabase 'bots' table.
    bot_id must be a valid UUID string.
    """
    try:
        result = (
            supabase
            .table("bots")
            .select("*")
            .eq("id", bot_id)
            .single()
            .execute()
        )
    except Exception as e:
        print("Supabase error while loading bot:", e)
        return None

    if getattr(result, "error", None):
        print("Supabase returned error:", result.error)
        return None

    # supabase-py v2 returns .data
    return getattr(result, "data", None)


# ==================================
#  HELPER: BUILD MENU TEXT
# ==================================

def build_menu_text(config: Dict[str, Any]) -> str:
    """
    Convert restaurant config_json into a nice Telegram text message.
    Uses:
      - restaurantName, restaurantTagline
      - menu (structured) and cartSettings
    """
    restaurant_name = config.get("restaurantName") or "مطعمك"
    tagline = config.get("restaurantTagline") or ""
    phone = config.get("phoneNumber") or ""
    address = config.get("address") or ""
    opening = config.get("openingHours") or ""
    delivery = config.get("deliveryOptions") or ""

    cart = config.get("cartSettings") or {}
    currency = cart.get("currency") or "USD"

    lines: List[str] = []

    # Header
    lines.append(f"🍽️ *{restaurant_name}*")
    if tagline:
        lines.append(f"_{tagline}_")
    if phone or address:
        contact_line = []
        if phone:
            contact_line.append(f"📞 {phone}")
        if address:
            contact_line.append(f"📍 {address}")
        lines.append(" • ".join(contact_line))
    if opening:
        lines.append(f"⏰ {opening}")
    if delivery:
        lines.append(f"🚚 {delivery}")

    lines.append("")  # empty line

    # Menu
    menu = config.get("menu") or []
    if not menu:
        # fallback to simple menuItems text if present
        menu_items_raw = (config.get("menuItems") or "").strip()
        if menu_items_raw:
            lines.append("📖 *المنيو:*")
            lines.append(menu_items_raw)
        else:
            lines.append("لا يوجد منيو محفوظ بعد في هذا البوت.")
    else:
        lines.append("📖 *المنيو:*")
        for category in menu:
            cat_name = category.get("name") or "قسم بدون اسم"
            lines.append(f"\n📂 *{cat_name}*")
            for item in category.get("items", []):
                item_name = item.get("name") or "طبق بدون اسم"
                desc = item.get("description") or ""
                price_val = item.get("price") or 0
                if price_val and price_val > 0:
                    price = f"{price_val:.2f} {currency}"
                else:
                    price = "حسب الطلب"

                lines.append(f"• {item_name} – {price}")
                if desc:
                    lines.append(f"  _{desc}_")

    # Cart info
    if cart.get("enabled"):
        lines.append("\n🧺 *نظام السلة مفعّل*")
        min_order = cart.get("minOrder") or 0
        max_items = cart.get("maxItems") or 0

        if min_order and min_order > 0:
            lines.append(f"الحد الأدنى للطلب: {min_order:.2f} {currency}")
        else:
            lines.append("لا يوجد حد أدنى للطلب.")

        if max_items and max_items > 0:
            lines.append(f"أقصى عدد للعناصر في السلة: {max_items}")
    else:
        lines.append("\n🧺 السلة غير مفعّلة – سيتم تنفيذ الطلب على شكل طبق واحد في كل مرة.")

    lines.append("")
    lines.append("✉️ أرسل كلمة *طلب* أو اسم الطبق لبدء طلب جديد.")

    return "\n".join(lines)


# ==================================
#  HELPER: SEND MESSAGE TO TELEGRAM
# ==================================

async def telegram_send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> None:
    """
    Send a text message back to Telegram.
    """
    url = f"{TELEGRAM_API_BASE}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                print("Telegram sendMessage error:", resp.status_code, resp.text)
        except Exception as e:
            print("Exception sending Telegram message:", e)


# ==================================
#  TELEGRAM WEBHOOK ENDPOINT
#  URL: /api/telegram-webhook/{bot_id}
#  bot_id = UUID from Supabase bots table
# ==================================

@app.post("/api/telegram-webhook/{bot_id}")
async def telegram_webhook(bot_id: str, request: Request):
    """
    Telegram will POST updates here.
    We use bot_id (from URL) to know which restaurant bot config to use.
    """
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Telegram sends either "message" or "edited_message"
    message = update.get("message") or update.get("edited_message")
    if not message:
        # Could be callback_query, etc. For now just ignore.
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id:
        return {"ok": True}

    # 1) Load bot config from Supabase
    bot_row = get_bot_from_supabase(bot_id)
    if not bot_row:
        await telegram_send_message(chat_id, "❌ لم يتم العثور على هذا البوت في النظام.")
        return {"ok": True}

    config = bot_row.get("config_json") or {}
    restaurant_name = config.get("restaurantName") or "مطعمك"

    # 2) Determine reply based on text
    lowered = text.lower()

    if lowered in ["/start", "start", "menu", "المنيو", "منيو", "قائمة الطعام"]:
        reply = build_menu_text(config)
    elif lowered in ["hi", "hello", "مرحبا", "السلام عليكم"]:
        reply = (
            f"مرحباً 👋 معك المساعد الذكي لمطعم *{restaurant_name}*.\n\n"
            "اكتب كلمة *المنيو* لعرض قائمة الطعام، أو أرسل اسم الطبق مباشرة لبدء الطلب."
        )
    else:
        # simple fallback – later you can plug in full NLP / Dialogflow, etc.
        reply = (
            f"تلقيت رسالتك: _{text}_\n\n"
            "لرؤية قائمة الطعام أرسل كلمة *المنيو*.\n"
            "أو اكتب اسم الطبق الذي تريده."
        )

    # 3) Send reply
    await telegram_send_message(chat_id, reply)
    return {"ok": True}


# ==========================
#  SIMPLE HEALTH CHECK
# ==========================

@app.get("/")
async def root():
    return {"status": "ok", "service": "ACS PRO backend", "telegram": True}
