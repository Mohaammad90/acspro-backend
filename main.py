from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import os
import requests
from typing import Dict, Any, List

app = FastAPI()

# ====== ENV VARS ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "acspro-verify")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ====== SIMPLE IN-MEMORY SESSION (PER CHAT) ======
# NOTE: This is fine for demo / MVP. Later we move to Supabase.
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

# ====== DEMO MENU FOR RESTAURANT TEMPLATE ======
# Later we read this from Supabase config_json
MENU: List[Dict[str, Any]] = [
    {
        "id": "shawarma",
        "name": "الشاورما",
        "items": [
            {
                "id": "shawarma_chicken",
                "name": "شاورما دجاج",
                "description": "دجاج متبّل على الطريقة السورية مع ثوم وبطاطس.",
                "price": 9.99
            },
            {
                "id": "shawarma_beef",
                "name": "شاورما لحم",
                "description": "لحم بقري متبّل مع خضار طازجة وصوص خاص.",
                "price": 10.99
            }
        ]
    },
    {
        "id": "mezza",
        "name": "مقبلات باردة",
        "items": [
            {
                "id": "fattoush",
                "name": "فتوش",
                "description": "سلطة فتوش مع خبز مقرمش وخضار طازجة.",
                "price": 5.99
            },
            {
                "id": "hummus",
                "name": "حمص",
                "description": "حمص بالطحينة وزيت الزيتون.",
                "price": 4.99
            }
        ]
    },
    {
        "id": "friday",
        "name": "مناسف الجمعة",
        "items": [
            {
                "id": "friday_mansaf",
                "name": "مناسف الجمعة",
                "description": "طلبيات خاصة للمناسبات، السعر حسب الكمية.",
                "price": 0.0
            }
        ]
    }
]


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


def items_keyboard(cat_id: str):
    cat = find_category(cat_id)
    if not cat:
        return {"inline_keyboard": [[{"text": "🔙 رجوع", "callback_data": "BACK:CATS"}]]}

    rows = []
    for it in cat["items"]:
        label = f"{it['name']} – {it['price']:.2f}$" if it["price"] > 0 else it["name"]
        rows.append([
            {"text": f"➕ {label}", "callback_data": f"ADD:{it['id']}"}
        ])

    rows.append([{"text": "🔙 رجوع للأقسام", "callback_data": "BACK:CATS"}])

    return {
        "inline_keyboard": rows
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
        price_part = f"{item['price']:.2f}$" if item["price"] > 0 else "حسب الطلب"
        item_total_part = f"{item_total:.2f}$" if item["price"] > 0 else ""
        lines.append(f"• {item['name']} × {item['qty']} – {price_part} {item_total_part}")

    lines.append("\nالإجمالي التقريبي: {:.2f}$".format(total))
    return "\n".join(lines)


# ====== ROOT (OPTIONAL) ======
@app.get("/")
async def root():
    return PlainTextResponse("ACS PRO Backend is running.")


# ====== WHATSAPP WEBHOOK VERIFY (STAYS FOR LATER) ======
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
    # Later we map WhatsApp messages to same restaurant ordering flow
    return JSONResponse({"status": "received"})


# ====== TELEGRAM WEBHOOK ======
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    print("Incoming Telegram update:", update)

    # Handle messages
    if "message" in update:
        await handle_telegram_message(update["message"])
    # Handle callback queries (button clicks)
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

    # Commands
    if text == "/start":
        session["state"] = "IDLE"
        session["cart"] = []
        session["pending_field"] = None
        session["customer_info"] = {"name": "", "phone": "", "address": ""}

        welcome = (
            "👋 أهلاً بك في <b>مطعم الشام للأكلات الشرقية</b>!\n\n"
            "يمكنك الإطلاع على المنيو، إضافة الطلبات إلى السلة، ثم تأكيد الطلب مباشرة من هنا.\n\n"
            "اختر ما تريد من الأزرار بالأسفل 👇"
        )
        tg_send_message(chat_id, welcome, reply_markup=main_menu_keyboard())
        return

    # Normal text depending on current state (for checkout info)
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
            "سيتم التواصل معك قريباً لتأكيد الطلب، شكراً لاختيارك مطعم الشام 🤍"
        ).format(
            name=info["name"],
            phone=info["phone"],
            address=info["address"]
        )
        tg_send_message(chat_id, summary, reply_markup=main_menu_keyboard())
        return

    # Main menu buttons (text-based)
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

    # Fallback
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

        # Build category text
        lines = [f"📂 <b>{cat['name']}</b>\n"]
        for it in cat["items"]:
            price_part = f"{it['price']:.2f}$" if it["price"] > 0 else "حسب الطلب"
            lines.append(f"• <b>{it['name']}</b> – {price_part}\n  {it['description']}")
        text = "\n".join(lines)

        tg_send_message(
            chat_id,
            text,
            reply_markup=items_keyboard(cat_id)
        )
        return

    if data.startswith("ADD:"):
        item_id = data.split(":", 1)[1]
        item = find_item(item_id)
        if not item:
            tg_send_message(chat_id, "⚠ لم يتم العثور على هذا الصنف.")
            return

        # Add to cart (increase qty if exists)
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
