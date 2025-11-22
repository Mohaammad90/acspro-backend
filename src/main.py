from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import os
import requests
from typing import Dict, Any, List, Optional

app = FastAPI()

# ====== ENV VARS ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "acspro-verify")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

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


# ====== BOT CACHE (PER bot_id) ======
# BOT_CACHE[bot_id] = {"config": {...}, "menu": [...]}
BOT_CACHE: Dict[str, Dict[str, Any]] = {}


# ====== SUPABASE HELPERS ======
def supabase_headers() -> Dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json"
    }


def fetch_bot_config_from_supabase(bot_id: str) -> Dict[str, Any]:
    """
    Fetch config_json for a single bot from Supabase 'bots' table.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("⚠ SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set.")
        return {}

    url = SUPABASE_URL.rstrip("/") + "/rest/v1/bots"
    params = {
        "id": f"eq.{bot_id}",
        "select": "config_json"
    }

    try:
        resp = requests.get(url, headers=supabase_headers(), params=params, timeout=10)
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


def fetch_telegram_session(chat_id: int) -> Optional[Dict[str, Any]]:
    """
    Load telegram_sessions row for a given chat_id.
    Table: telegram_sessions (chat_id BIGINT, bot_id UUID/TEXT)
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("⚠ Supabase not configured for telegram_sessions.")
        return None

    url = SUPABASE_URL.rstrip("/") + "/rest/v1/telegram_sessions"
    params = {
        "chat_id": f"eq.{chat_id}",
        "select": "chat_id,bot_id",
        "limit": "1"
    }

    try:
        resp = requests.get(url, headers=supabase_headers(), params=params, timeout=10)
        if resp.status_code != 200:
            print("⚠ Supabase telegram_sessions GET error:", resp.status_code, resp.text)
            return None
        rows = resp.json()
        if not rows:
            return None
        return rows[0]
    except Exception as e:
        print("⚠ Exception fetching telegram_session:", e)
        return None


def upsert_telegram_session(chat_id: int, bot_id: str) -> None:
    """
    Upsert telegram_sessions row: (chat_id, bot_id)
    Requires telegram_sessions table with chat_id as primary key or unique.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("⚠ Supabase not configured for telegram_sessions upsert.")
        return

    url = SUPABASE_URL.rstrip("/") + "/rest/v1/telegram_sessions"
    data = [
        {
            "chat_id": chat_id,
            "bot_id": bot_id
        }
    ]

    headers = supabase_headers()
    # Tell Supabase to merge on conflicts
    headers["Prefer"] = "resolution=merge-duplicates,return=representation"

    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        if resp.status_code not in (200, 201):
            print("⚠ Supabase telegram_sessions UPSERT error:", resp.status_code, resp.text)
    except Exception as e:
        print("⚠ Exception upserting telegram_session:", e)


# ====== MENU BUILDING ======
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


def get_bot_context(bot_id: str) -> Optional[Dict[str, Any]]:
    """
    Return dict: {"bot_id": ..., "config": ..., "menu": [...]}
    Uses in-memory cache; loads from Supabase if needed.
    """
    if not bot_id:
        return None

    if bot_id in BOT_CACHE:
        return BOT_CACHE[bot_id]

    print(f"ℹ Loading restaurant bot config from Supabase (bot_id={bot_id})...")
    config = fetch_bot_config_from_supabase(bot_id)
    if not config:
        return None
    menu = build_menu_from_config(config)
    ctx = {"bot_id": bot_id, "config": config, "menu": menu}
    BOT_CACHE[bot_id] = ctx
    return ctx


# ====== TELEGRAM HELPERS ======
def tg_send_message(chat_id: int, text: str, reply_markup: Optional[dict] = None):
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


def tg_send_photo(chat_id: int, photo_url: str, caption: str = "", reply_markup: Optional[dict] = None):
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


def categories_keyboard(menu: List[Dict[str, Any]]):
    buttons = []
    for cat in menu:
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


def find_category(menu: List[Dict[str, Any]], cat_id: str):
    for c in menu:
        if c["id"] == cat_id:
            return c
    return None


def find_item(menu: List[Dict[str, Any]], item_id: str):
    for c in menu:
        for it in c["items"]:
            if it["id"] == item_id:
                return it
    return None


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


# ====== BOT CONTEXT RESOLUTION (DYNAMIC LOADING) ======
def extract_chat_id(update: Dict[str, Any]) -> Optional[int]:
    if "message" in update:
        return update["message"].get("chat", {}).get("id")
    if "callback_query" in update:
        return update["callback_query"].get("message", {}).get("chat", {}).get("id")
    return None


def extract_text(update: Dict[str, Any]) -> str:
    if "message" in update:
        return update["message"].get("text", "") or ""
    return ""


def parse_start_payload(text: str) -> Optional[str]:
    """
    Parse /start <bot_id> payload.
    We expect Option 1: https://t.me/ACS_PRO_BOT?start=<bot_id>
    Telegram will send: "/start <bot_id>"
    """
    text = text.strip()
    if not text.startswith("/start"):
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip() or None


def resolve_bot_context_for_update(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Decide which bot to use for this Telegram update.

    Logic:
      - If message text is "/start <bot_id>":
          * Validate bot_id exists in Supabase
          * Save (chat_id -> bot_id) in telegram_sessions
          * Load config/menu and return context
      - Else:
          * Look up telegram_sessions by chat_id
          * If none:
              - If "/start" (no payload): show error
              - Else: show "no bot for this chat" error
          * If found:
              * Load context for that bot_id
    """
    chat_id = extract_chat_id(update)
    if chat_id is None:
        return None

    text = extract_text(update)
    start_payload = parse_start_payload(text) if text else None

    # Case 1: /start <bot_id>
    if start_payload:
        bot_id = start_payload
        ctx = get_bot_context(bot_id)
        if not ctx:
            tg_send_message(
                chat_id,
                "❌ هذا الرابط غير صالح.\nالرجاء استخدام الرابط الرسمي الذي حصلت عليه من لوحة التحكم."
            )
            return None

        # Save mapping: chat_id -> bot_id
        upsert_telegram_session(chat_id, bot_id)
        return ctx

    # Case 2: Any other message/callback -> use existing mapping
    sess_row = fetch_telegram_session(chat_id)
    if not sess_row:
        # No mapping exists
        if text.startswith("/start"):
            # /start with no payload (or user typed manually)
            tg_send_message(
                chat_id,
                "❌ هذا الرابط غير صالح.\nالرجاء استخدام رابط المطعم من الموقع الرسمي."
            )
        else:
            tg_send_message(
                chat_id,
                "❌ لم يتم العثور على بوت مرتبط بهذه المحادثة.\n"
                "الرجاء الدخول للبوت من خلال رابط المطعم الخاص بك."
            )
        return None

    bot_id = sess_row.get("bot_id")
    ctx = get_bot_context(str(bot_id))
    if not ctx:
        tg_send_message(
            chat_id,
            "⚠ تعذّر تحميل إعدادات المطعم.\nحاول لاحقاً أو تواصل مع الدعم الفني."
        )
        return None

    return ctx


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
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    print("Incoming Telegram update:", update)

    # Decide which bot to use for this update
    bot_context = resolve_bot_context_for_update(update)

    # If context couldn't be resolved, we already sent an error to the user
    if not bot_context:
        return JSONResponse({"ok": True})

    if "message" in update:
        await handle_telegram_message(update["message"], bot_context)

    if "callback_query" in update:
        await handle_telegram_callback(update["callback_query"], bot_context)

    return JSONResponse({"ok": True})


# ====== TELEGRAM MESSAGE HANDLER ======
async def handle_telegram_message(message: Dict[str, Any], bot_context: Dict[str, Any]):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return

    text = (message.get("text") or "").strip()
    session = get_session(chat_id)

    config = bot_context["config"]
    menu = bot_context["menu"]

    # Get restaurant info from config
    restaurant_name = config.get("restaurantName", "مطعمك")
    tagline = config.get("restaurantTagline", "")
    opening_hours = config.get("openingHours", "")

    # ----- /start (with or without payload) -----
    if text.startswith("/start"):
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

    # ----- State machine for checkout info -----
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

    # ----- Menu / Cart commands -----
    if text == "🧾 عرض المنيو":
        tg_send_message(
            chat_id,
            "اختر القسم الذي تريد استعراضه من المنيو:",
            reply_markup=categories_keyboard(menu)
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

    # ----- Fallback -----
    tg_send_message(
        chat_id,
        "يمكنك استخدام الأزرار بالأسفل للتعامل مع المنيو والسلة 👇",
        reply_markup=main_menu_keyboard()
    )


# ====== TELEGRAM CALLBACK HANDLER ======
async def handle_telegram_callback(callback: Dict[str, Any], bot_context: Dict[str, Any]):
    message = callback.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return

    data = callback.get("data", "")
    session = get_session(chat_id)

    menu = bot_context["menu"]

    # Category selection
    if data.startswith("CAT:"):
        cat_id = data.split(":", 1)[1]
        cat = find_category(menu, cat_id)
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
            reply_markup=categories_keyboard(menu)
        )
        return

    # Add to cart
    if data.startswith("ADD:"):
        item_id = data.split(":", 1)[1]
        item = find_item(menu, item_id)
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
            reply_markup=categories_keyboard(menu)
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
