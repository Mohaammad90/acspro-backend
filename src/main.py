from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import os
import requests
from typing import Dict, Any, List

app = FastAPI()

# ===== ENV VARS =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ===== SIMPLE CACHE =====
BOT_CACHE: Dict[str, Dict[str, Any]] = {}   # bot_id -> config_json
MENU_CACHE: Dict[str, List[Dict[str, Any]]] = {}  # bot_id -> menu structure


# ============================================================
#   SUPABASE HELPERS
# ============================================================

def supabase_get(path: str, params=None):
    """GET wrapper using service role."""
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{path}"
    resp = requests.get(url, params=params, headers=headers)

    if resp.status_code >= 300:
        print("⚠ Supabase GET error:", resp.status_code, resp.text)
        return None
    return resp.json()


def supabase_upsert(path: str, json_body: dict):
    """UPSERT with service role."""
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{path}"
    resp = requests.post(url, json=json_body, headers=headers)

    if resp.status_code >= 300:
        print("⚠ Supabase UPSERT error:", resp.status_code, resp.text)
        return None
    return resp.json()


# ============================================================
#   BOT CONFIG LOADING
# ============================================================

def load_bot_config(bot_id: str) -> Dict[str, Any]:
    """Loads config_json from Supabase, cached."""
    if bot_id in BOT_CACHE:
        return BOT_CACHE[bot_id]

    rows = supabase_get("bots", {
        "id": f"eq.{bot_id}",
        "select": "config_json"
    })
    if not rows:
        print("⚠ No bot found:", bot_id)
        return {}

    config = rows[0].get("config_json") or {}
    if not isinstance(config, dict):
        config = {}

    BOT_CACHE[bot_id] = config
    return config


def build_menu(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Converts config.menu → internal menu structure."""
    structured = config.get("menu")
    if structured and isinstance(structured, list):
        # Already structured
        return structured

    # Legacy fallback: menuItems
    menu_items_raw = config.get("menuItems", "") or ""
    lines = [x.strip() for x in menu_items_raw.splitlines() if x.strip()]

    items = []
    for i, line in enumerate(lines, start=1):
        name = line
        price = 0.0
        desc = ""

        if "–" in line:
            name, rest = line.split("–", 1)
            rest = rest.strip()
        elif "-" in line:
            name, rest = line.split("-", 1)
            rest = rest.strip()
        else:
            rest = ""

        if rest:
            if "حسب" in rest:
                price = 0.0
                desc = "السعر حسب الطلب."
            else:
                try:
                    price = float(rest.split(" ")[0])
                except:
                    price = 0.0
                desc = rest

        items.append({
            "id": f"item_{i}",
            "name": name.strip(),
            "description": desc,
            "price": price,
            "imageUrl": ""
        })

    return [
        {
            "id": "main_menu",
            "name": "قائمة الطعام",
            "items": items
        }
    ]


def get_menu_for_bot(bot_id: str):
    """Returns menu from cache or Supabase."""
    if bot_id in MENU_CACHE:
        return MENU_CACHE[bot_id]

    config = load_bot_config(bot_id)
    menu = build_menu(config)
    MENU_CACHE[bot_id] = menu
    return menu


# ============================================================
#   TELEGRAM HELPERS
# ============================================================

def tg_send_message(chat_id: int, text: str, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)


def tg_send_photo(chat_id: int, url: str, caption="", reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "photo": url
    }
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    if reply_markup:
        payload["reply_markup"] = reply_markup

    requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload)


# ============================================================
#   TELEGRAM SESSION ASSIGNMENT
# ============================================================

def assign_chat_to_bot(chat_id: int, bot_id: str):
    """Writes mapping to telegram_sessions."""
    supabase_upsert("telegram_sessions", {
        "telegram_chat_id": str(chat_id),
        "bot_id": bot_id
    })


def get_bot_for_chat(chat_id: int) -> str | None:
    rows = supabase_get("telegram_sessions", {
        "telegram_chat_id": f"eq.{chat_id}",
        "select": "bot_id"
    })
    if rows and rows[0].get("bot_id"):
        return rows[0]["bot_id"]
    return None


# ============================================================
#   TELEGRAM WEBHOOK
# ============================================================

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    print("Incoming Telegram update:", update)

    if "message" in update:
        await handle_message(update["message"])

    if "callback_query" in update:
        await handle_callback(update["callback_query"])

    return JSONResponse({"ok": True})


# ============================================================
#   HANDLE MESSAGES
# ============================================================

async def handle_message(msg: Dict[str, Any]):
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()

    # -------- Case 1: /start with bot_id --------
    if text.startswith("/start "):
        bot_id = text.split(" ", 1)[1].strip()
        print("User started bot:", bot_id)

        config = load_bot_config(bot_id)
        if not config:
            tg_send_message(chat_id, "❌ هذا الرابط غير صالح.\nالرجاء طلب الرابط الصحيح من صاحب المطعم.")
            return

        # Save mapping
        assign_chat_to_bot(chat_id, bot_id)

        # Send welcome
        restaurant = config.get("restaurantName", "المطعم")
        tagline = config.get("restaurantTagline", "")
        opening = config.get("openingHours", "")

        msg = f"👋 أهلاً بك في <b>{restaurant}</b>!\n"
        if tagline:
            msg += f"✨ {tagline}\n"
        if opening:
            msg += f"\n⏰ ساعات العمل: {opening}"
        msg += "\n\nاستخدم الأزرار بالأسفل لعرض المنيو 👇"

        tg_send_message(chat_id, msg, reply_markup=main_keyboard())
        return

    # -------- Case 2: /start without parameter --------
    if text == "/start":
        # If we already know the bot, it's OK
        known_bot = get_bot_for_chat(chat_id)
        if known_bot:
            config = load_bot_config(known_bot)
            name = config.get("restaurantName", "مطعمك")
            tg_send_message(chat_id, f"مرحباً من جديد! 👋\nأنت تتحدث مع <b>{name}</b>.") 
            return

        # Otherwise => reject
        tg_send_message(chat_id, "❌ هذا الرابط غير صالح.\nالرجاء استخدام الرابط الرسمي الخاص بالمطعم.")
        return

    # -------- Normal Messages --------
    bot_id = get_bot_for_chat(chat_id)
    if not bot_id:
        tg_send_message(chat_id, "❌ يرجى الدخول عبر رابط المطعم.")
        return

    menu = get_menu_for_bot(bot_id)

    if text == "🧾 عرض المنيو":
        tg_send_message(chat_id, "اختر القسم:", reply_markup=categories_keyboard(menu))
        return

    tg_send_message(chat_id, "استخدم الأزرار 👇", reply_markup=main_keyboard())


# ============================================================
#   HANDLE CALLBACKS
# ============================================================

async def handle_callback(callback: Dict[str, Any]):
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")

    bot_id = get_bot_for_chat(chat_id)
    if not bot_id:
        tg_send_message(chat_id, "❌ يرجى الدخول عبر رابط المطعم.")
        return

    menu = get_menu_for_bot(bot_id)

    if data.startswith("CAT:"):
        cat_id = data.split(":", 1)[1]
        category = next((c for c in menu if c["id"] == cat_id), None)

        if not category:
            tg_send_message(chat_id, "⚠ القسم غير موجود.")
            return

        tg_send_message(chat_id, f"📂 قسم <b>{category['name']}</b>:")

        for item in category["items"]:
            price = f"{item['price']:.2f}$" if item["price"] > 0 else "حسب الطلب"
            caption = f"<b>{item['name']}</b>\n{item['description']}\n💰 {price}"

            kb = {
                "inline_keyboard": [
                    [{"text": "➕ أضف للسلة", "callback_data": f"ADD:{item['id']}"}]
                ]
            }

            if item.get("imageUrl"):
                tg_send_photo(chat_id, item["imageUrl"], caption, kb)
            else:
                tg_send_message(chat_id, caption, kb)

        return


# ============================================================
#   KEYBOARDS
# ============================================================

def main_keyboard():
    return {
        "keyboard": [
            [{"text": "🧾 عرض المنيو"}],
        ],
        "resize_keyboard": True
    }


def categories_keyboard(menu):
    rows = []
    for cat in menu:
        rows.append([{"text": cat["name"], "callback_data": f"CAT:{cat['id']}"}])

    return {
        "inline_keyboard": rows
    }


# ============================================================
#   ROOT
# ============================================================

@app.get("/")
def root():
    return PlainTextResponse("ACS PRO Backend is running with dynamic bot loading.")
