import os
import hmac
import hashlib
import asyncio
import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from dotenv import load_dotenv
from formatters import format_event
from discord_bot import start_bot

load_dotenv()

app = FastAPI()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
JIRA_WEBHOOK_SECRET = os.getenv("JIRA_WEBHOOK_SECRET", "")


def verify_signature(payload: bytes, signature: str) -> bool:
    if not JIRA_WEBHOOK_SECRET:
        return True
    expected = hmac.new(
        JIRA_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(start_bot())


@app.get("/")
async def health():
    return {"status": "ok", "service": "jira-discord-bot"}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
):
    body = await request.body()

    if JIRA_WEBHOOK_SECRET and x_hub_signature_256:
        if not verify_signature(body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("webhookEvent", "unknown")
    embed = format_event(event_type, payload)

    if embed is None:
        return {"status": "ignored", "event": event_type}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            raise HTTPException(status_code=502, detail=f"Discord error: {resp.status_code}")

    return {"status": "sent", "event": event_type}
