from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import os
import requests
from typing import Dict, Any, List

app = FastAPI()

# ====== ENV VARS ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "acspro-verify")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# One global Telegram bot for all users (Option A)
# Which restaurant bot to use for menu?
# For now: default single restaurant bot.
DEFAULT_RESTAURANT_BOT_ID = os.getenv(
    "DEFAULT_RESTAURANT_BOT_ID",
    "c078648e-d564-48c0-b48d-4cc280a953a7"  # your current restaurant bot id
)
RESTAURANT_BOT_ID = DEFAULT_RESTAURANT_BOT_ID

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ====== SIMPLE IN-MEMORY SESSION (PER CHAT) ======
SESSIONS: Dict[int, Dict[str, Any]] = {}


def get_session(chat_id: int) -> Dict[str, Any]:
    if chat_id not in SESSIONS:
        SESSIONS[chat_id] = {
            "state": "IDLE",
            "cart": [],
            "pending_field": None,
            "customer_info": {
                "name": "",
                "phone": "",
                "address": ""
            }
        }
    return SESSIONS[chat_id]


# ====== BOT CONFIG / MENU CACHE ======
BOT_CONFIG: Dict[str, Any] | None = None
MENU: List[Dict[str, Any]] = []


def fetch_bot_config_from_supabase(bot_id: str) -> Dict[str, Any]:
    """
    Fetch config_json for a single bot from Supabase using service role key.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("⚠ SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set.")
        return {}

    url = SUPABASE_URL.rstrip("/") + "/rest/v1/bots"
    params = {
        "id": f"eq.{bot_id}",
        "select": "config_json"
    }
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
    }

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            print("⚠ Supabase error:", resp.status_code, resp.text)
            return {}
        rows = resp.json()
        if not rows:
            print("⚠ No bot found with id", bot_id)
            return {}
        config = rows[0].get("config_json") or {}
        if not isinstance(config, dict):
            print("⚠ config_json is not a dict:", config)
            return {}
        return config
    except Exception as e:
        print("⚠ Exception fetching bot config:", e)
        return {}


def build_menu_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build MENU structure from:
      1) config['menu'] if present (structured with imageUrl, price, etc.)
      2) Otherwise, from config['menuItems'] (multiline text)

    MENU structure:
    [
      {
        "id": "category_id",
        "name": "اسم القسم",
        "items": [
          {
            "id": "item_id",
            "name": "...",
            "description": "...",
            "price": 9.99,
            "imageUrl": "https://..."
          }
        ]
      }
    ]
    """

    # --- Case 1: structured menu in config['menu'] ---
    structured_menu = config.get("menu")
    if isinstance(structured_menu, list) and structured_menu:
        # If it's already in category form (has 'items' on elements), use as is
        if all(isinstance(c, dict) and "items" in c for c in structured_menu):
            # Normalize categories & items
            normalized_categories: List[Dict[str, Any]] = []
            for cat_idx, cat in enumerate(structured_menu, start=1):
                cat_id = cat.get("id") or f"cat_{cat_idx}"
                cat_name = cat.get("name") or "قسم"
                raw_items = cat.get("items") or []
                norm_items = []
                for idx, it in enumerate(raw_items, start=1):
                    item_id = it.get("id") or f"{cat_id}_item_{idx}"
                    norm_items.append({
                        "id": item_id,
                        "name": it.get("name", f"صنف {idx}"),
                        "description": it.get("description", "لا يوجد وصف بعد."),
                        "price": float(it.get("price", 0.0) or 0.0),
                        "imageUrl": it.get("imageUrl", "")
                    })
                normalized_categories.append({
                    "id": cat_id,
                    "name": cat_name,
                    "items": norm_items
                })
            return normalized_categories

        # Otherwise, assume it's a flat list of items
        items: List[Dict[str, Any]] = []
        for idx, it in enumerate(structured_menu, start=1):
            if not isinstance(it, dict):
                continue
            item_id = it.get("id") or f"item_{idx}"
            items.append({
                "id": item_id,
                "name": it.get("name", f"صنف {idx}"),
                "description": it.get("description", "لا يوجد وصف بعد."),
                "price": float(it.get("price", 0.0) or 0.0),
                "imageUrl": it.get("imageUrl", "")
            })

        if items:
            return [
                {
                    "id": "main_menu",
                    "name": "قائمة الطعام",
                    "items": items
                }
            ]

    # --- Case 2: fallback to multiline text menuItems ---
    menu_items_raw = config.get("menuItems", "") or ""
    lines = [line.strip() for line in menu_items_raw.splitlines() if line.strip()]

    items: List[Dict[str, Any]] = []

    for idx, line in enumerate(lines):
        name = line
        description = ""
        price = 0.0

        # Try to split using Arabic dash or normal dash
        if "–" in line:
            name_part, rest = line.split("–", 1)
            name = name_part.strip()
            rest = rest.strip()
        elif "-" in line:
            name_part, rest = line.split("-", 1)
            name = name_part.strip()
            rest = rest.strip()
        else:
            rest = ""

        # Try to parse price if present
        if rest:
            if "حسب" in rest:
                price = 0.0
                description = "السعر حسب الطلب."
            else:
                amount_part = rest.split("$")[0].strip()
                try:
                    price = float(amount_part)
                except ValueError:
                    price = 0.0
                description = rest

        item_id = f"item_{idx+1}"
        items.append({
            "id": item_id,
            "name": name,
            "description": description or "لا يوجد وصف بعد.",
            "price": price,
            "imageUrl": ""
        })

    if not items:
        items = [
            {
                "id": "demo_item",
                "name": "عنصر تجريبي",
                "description": "هذا عنصر تجريبي لأن المنيو غير مهيّأ بعد.",
                "price": 0.0,
                "imageUrl": ""
            }
        ]

    return [
        {
            "id": "main_menu",
            "name": "قائمة الطعام",
            "items": items
        }
    ]


def ensure_bot_menu_loaded():
    """
    Ensure BOT_CONFIG and MENU are loaded from Supabase once.
    For now uses one global RESTAURANT_BOT_ID (Option A).
    """
    global BOT_CONFIG, MENU
    if BOT_CONFIG is not None and MENU:
        return

    print(f"ℹ Loading restaurant bot config from Supabase (bot_id={RESTAURANT_BOT_ID})...")
    BOT_CONFIG = fetch_bot_config_from_supabase(RESTAURANT_BOT_ID)
    MENU = build_menu_from_config(BOT_CONFIG)


def find_category(cat_id: str):
    for c in MENU:
        if c["id"] == cat_id:
            return c
    return None


def find_item(item_id: str):
    for c in MENU:
        for it in c["items"]:
            if it["id"] == item_id:
                return it
    return None


# ====== TELEGRAM HELPERS ======
def tg_send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    if not TELEGRAM_BOT_TOKEN:
        print("⚠ TELEGRAM_BOT_TOKEN is missing.")
        return

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        r = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
        if r.status_code != 200:
            print("Telegram sendMessage error:", r.text)
    except Exception as e:
        print("Telegram sendMessage exception:", e)


def tg_send_photo(chat_id: int, photo_url: str, caption: str = "", reply_markup: dict | None = None):
    """
    Send a photo with optional caption and inline keyboard.
    """
    if not TELEGRAM_BOT_TOKEN:
        print("⚠ TELEGRAM_BOT_TOKEN is missing.")
        return

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo_url,
    }
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        r = requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload)
        if r.status_code != 200:
            print("Telegram sendPhoto error:", r.text)
    except Exception as e:
        print("Telegram sendPhoto exception:", e)


def main_menu_keyboard():
    return {
        "keyboard": [
            [
                {"text": "🧾 عرض المنيو"},
                {"text": "🛒 عرض السلة"}
            ],
            [
                {"text": "❌ إفراغ السلة"}
            ]
        ],
        "resize_keyboard": True
    }


def categories_keyboard():
    buttons = []
    for cat in MENU:
        buttons.append([{"text": f"{cat['name']}", "callback_data": f"CAT:{cat['id']}"}])

    return {
        "inline_keyboard": buttons + [
            [{"text": "🔙 رجوع للقائمة الرئيسية", "callback_data": "BACK:MAIN"}]
        ]
    }


def checkout_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "✅ تأكيد الطلب", "callback_data": "CHECKOUT:CONFIRM"}],
            [{"text": "🔙 متابعة التصفح", "callback_data": "BACK:MAIN"}],
            [{"text": "❌ إفراغ السلة", "callback_data": "CART:CLEAR"}]
        ]
    }


# ====== CART / ORDER HELPERS ======
def format_cart(cart: List[Dict[str, Any]]) -> str:
    if not cart:
        return "السلة فارغة حالياً."

    lines = []
    total = 0.0
    for item in cart:
        item_total = item["price"] * item["qty"]
        total += item_total
        if item["price"] > 0:
            price_part = f"{item['price']:.2f}$"
            item_total_part = f"{item_total:.2f}$"
        else:
            price_part = "حسب الطلب"
            item_total_part = ""
        lines.append(f"• {item['name']} × {item['qty']} – {price_part} {item_total_part}")

    lines.append("\nالإجمالي التقريبي: {:.2f}$".format(total))
    return "\n".join(lines)


# ====== ROOT ======
@app.get("/")
async def root():
    return PlainTextResponse("ACS PRO Backend is running.")


# ====== WHATSAPP WEBHOOK VERIFY (kept for later use) ======
@app.get("/webhook")
async def whatsapp_verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    return JSONResponse({"error": "Verification failed"}, status_code=403)


@app.post("/webhook")
async def whatsapp_webhook_handler(request: Request):
    body = await request.json()
    print("Incoming WhatsApp Message:", body)
    return JSONResponse({"status": "received"})


# ====== TELEGRAM WEBHOOK ======
# Support both /telegram-webhook and /api/telegram-webhook
@app.post("/telegram-webhook")
@app.post("/api/telegram-webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    print("Incoming Telegram update:", update)

    # Load restaurant bot config + menu once (from Supabase)
    ensure_bot_menu_loaded()

    if "message" in update:
        await handle_telegram_message(update["message"])
    if "callback_query" in update:
        await handle_telegram_callback(update["callback_query"])

    return JSONResponse({"ok": True})


async def handle_telegram_message(message: Dict[str, Any]):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return

    text = message.get("text", "").strip()
    session = get_session(chat_id)

    # Get restaurant info from config
    restaurant_name = BOT_CONFIG.get("restaurantName", "مطعمك") if BOT_CONFIG else "مطعمك"
    tagline = BOT_CONFIG.get("restaurantTagline", "") if BOT_CONFIG else ""
    opening_hours = BOT_CONFIG.get("openingHours", "") if BOT_CONFIG else ""

    if text == "/start":
        session["state"] = "IDLE"
        session["cart"] = []
        session["pending_field"] = None
        session["customer_info"] = {"name": "", "phone": "", "address": ""}

        welcome_lines = [
            f"👋 أهلاً بك في <b>{restaurant_name}</b>!"
        ]
        if tagline:
            welcome_lines.append(f"✨ {tagline}")
        welcome_lines.append("")
        welcome_lines.append("يمكنك الإطلاع على المنيو، إضافة الطلبات إلى السلة، ثم تأكيد الطلب مباشرة من هنا.")
        if opening_hours:
            welcome_lines.append("")
            welcome_lines.append(f"⏰ أوقات العمل: {opening_hours}")
        welcome_lines.append("")
        welcome_lines.append("اختر ما تريد من الأزرار بالأسفل 👇")

        welcome = "\n".join(welcome_lines)
        tg_send_message(chat_id, welcome, reply_markup=main_menu_keyboard())
        return

    if session["state"] == "ASK_NAME":
        session["customer_info"]["name"] = text
        session["state"] = "ASK_PHONE"
        tg_send_message(chat_id, "📞 ممتاز، الآن اكتب رقم الجوال للتواصل معك:")
        return

    if session["state"] == "ASK_PHONE":
        session["customer_info"]["phone"] = text
        session["state"] = "ASK_ADDRESS"
        tg_send_message(chat_id, "📍 اكتب العنوان أو أقرب نقطة دلالة (والتعليمات الخاصة إن وجدت):")
        return

    if session["state"] == "ASK_ADDRESS":
        session["customer_info"]["address"] = text
        session["state"] = "IDLE"

        cart_text = format_cart(session["cart"])
        info = session["customer_info"]
        summary = (
            "✅ تم استلام بيانات الطلب:\n\n"
            f"{cart_text}\n\n"
            "👤 الاسم: {name}\n"
            "📞 الجوال: {phone}\n"
            "📍 العنوان: {address}\n\n"
            "سيتم التواصل معك قريباً لتأكيد الطلب، شكراً لاختيارك 🤍"
        ).format(
            name=info["name"],
            phone=info["phone"],
            address=info["address"]
        )
        tg_send_message(chat_id, summary, reply_markup=main_menu_keyboard())
        return

    if text == "🧾 عرض المنيو":
        tg_send_message(
            chat_id,
            "اختر القسم الذي تريد استعراضه من المنيو:",
            reply_markup=categories_keyboard()
        )
        return

    if text == "🛒 عرض السلة":
        cart_text = format_cart(session["cart"])
        tg_send_message(
            chat_id,
            "🛒 <b>سلتك الحالية:</b>\n\n" + cart_text,
            reply_markup=checkout_keyboard() if session["cart"] else main_menu_keyboard()
        )
        return

    if text == "❌ إفراغ السلة":
        session["cart"] = []
        tg_send_message(chat_id, "✅ تم إفراغ السلة.", reply_markup=main_menu_keyboard())
        return

    tg_send_message(
        chat_id,
        "يمكنك استخدام الأزرار بالأسفل للتعامل مع المنيو والسلة 👇",
        reply_markup=main_menu_keyboard()
    )


async def handle_telegram_callback(callback: Dict[str, Any]):
    message = callback.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return

    data = callback.get("data", "")
    session = get_session(chat_id)

    if data.startswith("CAT:"):
        cat_id = data.split(":", 1)[1]
        cat = find_category(cat_id)
        if not cat:
            tg_send_message(chat_id, "⚠ لم يتم العثور على هذا القسم.")
            return

        # Header text
        tg_send_message(
            chat_id,
            f"📂 <b>{cat['name']}</b>\n\nتصفح الأطباق بالأسفل ثم أضف ما تريد إلى السلة:"
        )

        # For each item in this category, send image (if present) or text-only
        for it in cat["items"]:
            price_part = f"{it['price']:.2f}$" if it["price"] > 0 else "حسب الطلب"
            caption = (
                f"<b>{it['name']}</b>\n"
                f"{it['description']}\n"
                f"💰 السعر: {price_part}"
            )

            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": f"➕ إضافة {it['name']}",
                            "callback_data": f"ADD:{it['id']}"
                        }
                    ]
                ]
            }

            if it.get("imageUrl"):
                tg_send_photo(chat_id, it["imageUrl"], caption=caption, reply_markup=keyboard)
            else:
                # Fallback to text-only message with inline button
                tg_send_message(chat_id, caption, reply_markup=keyboard)

        # After listing items, show a shortcut back
        tg_send_message(
            chat_id,
            "يمكنك الرجوع للأقسام أو عرض السلة في أي وقت.",
            reply_markup=categories_keyboard()
        )
        return

    if data.startswith("ADD:"):
        item_id = data.split(":", 1)[1]
        item = find_item(item_id)
        if not item:
            tg_send_message(chat_id, "⚠ لم يتم العثور على هذا الصنف.")
            return

        found = False
        for c_item in session["cart"]:
            if c_item["id"] == item_id:
                c_item["qty"] += 1
                found = True
                break

        if not found:
            session["cart"].append({
                "id": item_id,
                "name": item["name"],
                "price": item["price"],
                "qty": 1
            })

        tg_send_message(
            chat_id,
            f"✅ تمت إضافة \"{item['name']}\" إلى السلة.",
            reply_markup=main_menu_keyboard()
        )
        return

    if data == "BACK:MAIN":
        tg_send_message(
            chat_id,
            "رجعناك للقائمة الرئيسية 👇",
            reply_markup=main_menu_keyboard()
        )
        return

    if data == "BACK:CATS":
        tg_send_message(
            chat_id,
            "اختر القسم الذي تريد استعراضه:",
            reply_markup=categories_keyboard()
        )
        return

    if data == "CART:CLEAR":
        session["cart"] = []
        tg_send_message(chat_id, "✅ تم إفراغ السلة.", reply_markup=main_menu_keyboard())
        return

    if data == "CHECKOUT:CONFIRM":
        if not session["cart"]:
            tg_send_message(chat_id, "السلة فارغة، أضف بعض الطلبات أولاً.", reply_markup=main_menu_keyboard())
            return

        session["state"] = "ASK_NAME"
        tg_send_message(chat_id, "🧾 رائع! قبل تأكيد الطلب، اكتب اسمك الكامل:")
        return
