import os
import json
import asyncio
import aiohttp
import websockets
import time
import base64
import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

# ---------------------------
# Config
# ---------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview-2024-12")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "verse")
AI_INSTRUCTIONS = os.getenv(
    "AI_INSTRUCTIONS",
    "You are a friendly HRMS payroll assistant. Greet warmly, "
    "give a short pitch about payroll automation, compliance, "
    "and cost savings. Ask polite follow-up questions to keep the conversation going."
)

COMMIT_INTERVAL = float(os.getenv("COMMIT_INTERVAL", "1.0"))  # seconds between commits
OPENAI_AUDIO_SAMPLE_RATE = int(os.getenv("OPENAI_AUDIO_SAMPLE_RATE", "24000"))
TELEPHONY_SAMPLE_RATE = 8000

app = FastAPI()


# ---------------------------
# μ-law → PCM16 converter (no audioop)
# ---------------------------
MU_LAW_EXPAND_TABLE = np.array([
    int(((i ^ 0xFF) - 128) * 256) for i in range(256)
], dtype=np.int16)

def mulaw_to_pcm16(audio_b64: str) -> str:
    """Convert base64 μ-law audio from Twilio into base64 PCM16 for OpenAI."""
    mulaw_bytes = base64.b64decode(audio_b64)
    pcm16 = MU_LAW_EXPAND_TABLE[np.frombuffer(mulaw_bytes, dtype=np.uint8)]
    return base64.b64encode(pcm16.tobytes()).decode("utf-8")


# ---------------------------
# PCM16 -> μ-law (G.711-like) encoder + resampler
# ---------------------------
def resample_pcm16(pcm: np.ndarray, src_rate: int, tgt_rate: int) -> np.ndarray:
    """Simple linear resampler from src_rate to tgt_rate for 1-D int16 array."""
    if src_rate == tgt_rate:
        return pcm
    if len(pcm) == 0:
        return pcm
    # duration in seconds
    duration = len(pcm) / float(src_rate)
    tgt_len = int(np.round(duration * tgt_rate))
    if tgt_len <= 0:
        return np.array([], dtype=np.int16)
    # linear interpolation on sample indices
    src_idx = np.linspace(0, len(pcm) - 1, num=len(pcm))
    tgt_idx = np.linspace(0, len(pcm) - 1, num=tgt_len)
    resampled = np.interp(tgt_idx, src_idx, pcm).astype(np.int16)
    return resampled


def pcm16_to_mulaw_bytes(pcm16_bytes: bytes) -> bytes:
    """Convert raw PCM16 little-endian bytes to 8-bit μ-law bytes (base G.711-like companding).

    This uses a float-based μ-law companding which produces acceptable telephony audio
    without adding heavy native dependencies.
    """
    if not pcm16_bytes:
        return b""
    pcm = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32)
    # normalize to [-1, 1]
    pcm_norm = pcm / 32768.0
    mu = 255.0
    # μ-law companding
    companded = np.sign(pcm_norm) * np.log1p(mu * np.abs(pcm_norm)) / np.log1p(mu)
    # quantize to 8-bit unsigned (0..255)
    ulaw = ((companded + 1.0) / 2.0 * mu + 0.5).clip(0, 255).astype(np.uint8)
    return ulaw.tobytes()


def pcm16_base64_to_mulaw_base64(pcm16_b64: str, src_rate: int, tgt_rate: int = TELEPHONY_SAMPLE_RATE) -> str:
    """Convert base64 PCM16 (little-endian) at src_rate to base64 μ-law @ tgt_rate."""
    try:
        pcm_bytes = base64.b64decode(pcm16_b64)
        if not pcm_bytes:
            return ""
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        if src_rate != tgt_rate:
            pcm = resample_pcm16(pcm, src_rate, tgt_rate)
        mulaw_bytes = pcm16_to_mulaw_bytes(pcm.tobytes())
        return base64.b64encode(mulaw_bytes).decode("utf-8")
    except Exception as e:
        print("⚠️ pcm16->mulaw conversion failed:", e)
        return ""


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
        url="wss://YOUR_DOMAIN/ws",   # <-- replace with your deployed relay URL
        track="both",
        status_callback="/stream-events",
        status_callback_method="POST"
    )
    connect.append(stream)
    twiml.append(connect)

    # keep call open (AI will keep conversation alive)
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

    # ---------------------------
    # Duplex bridges
    # ---------------------------

    async def twilio_to_openai():
        last_commit = time.time()
        try:
            async for raw in websocket.iter_text():
                msg = json.loads(raw)
                etype = msg.get("event")

                if etype == "start":
                    print("🔔 Twilio stream started")
                    # Kick off AI greeting immediately
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
                        try:
                            pcm16_b64 = mulaw_to_pcm16(audio_b64)
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": pcm16_b64,
                            }))
                        except Exception as e:
                            print("⚠️ Audio decode error:", e)

                        now = time.time()
                        if now - last_commit > COMMIT_INTERVAL:
                            await openai_ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                            await openai_ws.send(json.dumps({
                                "type": "response.create",
                                "response": {"conversation": "default", "modalities": ["audio"]}
                            }))
                            last_commit = now

                elif etype == "stop":
                    print("🛑 Twilio stream stopped")
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
                        # OpenAI sends PCM16 at OPENAI_AUDIO_SAMPLE_RATE (commonly 24000).
                        # Twilio expects μ-law at 8kHz for telephony. Convert before sending.
                        mulaw_b64 = pcm16_base64_to_mulaw_base64(audio_chunk_b64, src_rate=OPENAI_AUDIO_SAMPLE_RATE, tgt_rate=TELEPHONY_SAMPLE_RATE)
                        if mulaw_b64:
                            await websocket.send_text(json.dumps({
                                "event": "media",
                                "media": {"payload": mulaw_b64}
                            }))
                        else:
                            # Fallback: send the original audio if conversion fails
                            await websocket.send_text(json.dumps({
                                "event": "media",
                                "media": {"payload": audio_chunk_b64}
                            }))
                elif dtype == "response.completed":
                    print("✅ AI finished a reply (conversation continues)")
        except Exception as e:
            print("❌ Error openai_to_twilio:", e)

        try:
            await websocket.close()
        except:
            pass

    await asyncio.gather(twilio_to_openai(), openai_to_twilio())
    print("🔚 Relay session ended")
