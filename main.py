from fastapi import FastAPI, Request
import os

app = FastAPI()

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "acspro-verify")


@app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)
    return {"error": "Verification failed"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    body = await request.json()
    print("Incoming WhatsApp Message:", body)
    return {"status": "received"}
