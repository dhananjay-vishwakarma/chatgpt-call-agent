import os
import json
import asyncio
import aiohttp
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview-2024-12")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "verse")
AI_INSTRUCTIONS = os.getenv(
    "AI_INSTRUCTIONS",
    "You are a friendly HRMS payroll assistant. Greet warmly, "
    "give a short pitch about payroll automation, compliance, "
    "and cost savings. Ask follow-up questions to keep the conversation going."
)

app = FastAPI()


# ---------------------------
# Twilio webhook: /voice
# ---------------------------
@app.post("/voice")
async def voice(request: Request):
    form = await request.form()
    from_number = form.get("From")
    print(f"✅ Incoming call from {from_number}")

    twiml = VoiceResponse()
    twiml.say("Hello! Connecting you to our AI assistant now.", voice="alice")

    connect = Connect()
    stream = Stream(
        url="wss://YOUR_DOMAIN/ws",   # <-- replace with your relay URL
        track="both",
        status_callback="/stream-events",
        status_callback_method="POST"
    )
    connect.append(stream)
    twiml.append(connect)

    # keep the call open (AI keeps the loop alive)
    twiml.pause(length=600)  # 10 minutes
    return Response(content=str(twiml), media_type="application/xml")


# ---------------------------
# Twilio debug logger: /stream-events
# ---------------------------
@app.post("/stream-events")
async def stream_events(request: Request):
    print("=== Twilio Stream Event ===")
    try:
        form = await request.form()
        print("Form:", dict(form))
    except Exception:
        pass
    body = await request.body()
    print("Raw body:", body.decode("utf-8"))
    return PlainTextResponse("ok")


@app.get("/")
def home():
    return PlainTextResponse("ChatGPT Voice Relay is running!")


# ---------------------------
# WebSocket relay: /ws
# ---------------------------
@app.websocket("/ws")
async def ws_twilio(websocket: WebSocket):
    await websocket.accept()
    print("✅ Twilio connected to /ws")

    # 1. Create OpenAI Realtime session
    async with aiohttp.ClientSession() as session:
        payload = {
            "model": OPENAI_REALTIME_MODEL,
            "voice": OPENAI_VOICE,
            "instructions": AI_INSTRUCTIONS,
        }
        async with session.post(
            "https://api.openai.com/v1/realtime/sessions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                print("❌ Failed to create OpenAI session:", resp.status, body)
                await websocket.close(code=1011)
                return
            data = await resp.json()

    ws_url = None
    if "client_secret" in data and "value" in data["client_secret"]:
        ws_url = data["client_secret"]["value"]
    elif "url" in data:
        ws_url = data["url"]
    if not ws_url:
        print("❌ Unexpected OpenAI response:", data)
        await websocket.close(code=1011)
        return

    # 2. Connect to OpenAI Realtime WS
    try:
        openai_ws = await websockets.connect(
            ws_url,
            extra_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            subprotocols=["openai-realtime-v1"],
            max_size=8 * 1024 * 1024,
        )
        print("✅ Connected to OpenAI Realtime")
    except Exception as e:
        print("❌ Could not connect OpenAI WS:", e)
        await websocket.close(code=1011)
        return

    async def twilio_to_openai():
        try:
            async for raw in websocket.iter_text():
                msg = json.loads(raw)
                etype = msg.get("event")
                if etype == "start":
                    print("🔔 Twilio stream started")
                    # send initial greeting from AI
                    await openai_ws.send(json.dumps({
                        "type": "response.create",
                        "response": {
                            "instructions": AI_INSTRUCTIONS,
                            "modalities": ["audio"],
                            "conversation": "default",
                        }
                    }))
                elif etype == "media":
                    audio_b64 = msg.get("media", {}).get("payload")
                    if audio_b64:
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": audio_b64,
                        }))
                elif etype == "stop":
                    print("🛑 Twilio stream stopped")
                    await openai_ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    await openai_ws.send(json.dumps({"type": "response.create"}))
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print("❌ Error twilio_to_openai:", e)
        try:
            await openai_ws.close()
        except:
            pass

    async def openai_to_twilio():
        try:
            async for raw in openai_ws:
                try:
                    data = json.loads(raw)
                except:
                    continue
                dtype = data.get("type")
                if dtype == "output_audio_buffer.append":
                    audio_chunk_b64 = data.get("audio")
                    if audio_chunk_b64:
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "media": {"payload": audio_chunk_b64}
                        }))
                elif dtype == "response.completed":
                    print("✅ OpenAI finished a reply")
        except Exception as e:
            print("❌ Error openai_to_twilio:", e)
        try:
            await websocket.close()
        except:
            pass

    await asyncio.gather(twilio_to_openai(), openai_to_twilio())
    print("🔚 Relay session ended")
