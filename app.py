import os
import requests
from flask import Flask, Response, request
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

app = Flask(__name__)

# Make sure this is set in Render environment variables
openai_api_key = os.getenv("OPENAI_API_KEY")

@app.route("/voice", methods=["POST"])
def voice():
    print("✅ Twilio POST /voice received")
    print(f"🔔 Caller: {request.form.get('From')} -> Callee: {request.form.get('To')}")

    # Prepare OpenAI session payload
    payload = {
        "model": "gpt-4o-realtime-preview-2024-12",
        "voice": "verse",
        "instructions": (
            "You are a friendly HRMS payroll software agent. "
            "As soon as the call connects, start speaking immediately. "
            "Greet warmly (for example: 'Hello! This is your HRMS payroll assistant'), "
            "then deliver a short, engaging 60-second pitch about payroll automation, "
            "compliance benefits, and cost savings. "
            "Keep it conversational and not pushy. "
            "End by asking one polite follow-up question, then wrap up nicely."
        )
    }

    try:
        print(f"📤 Sending to OpenAI: {payload}")
        r = requests.post(
            "https://api.openai.com/v1/realtime/sessions",
            headers={
                "Authorization": f"Bearer {openai_api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=10
        )
    except Exception as e:
        print(f"❌ Exception while calling OpenAI API: {e}")
        return Response("<Response><Say>Sorry, we had an error contacting AI.</Say></Response>",
                        mimetype="application/xml")

    print(f"🔎 OpenAI response status: {r.status_code}")
    print(f"🔎 OpenAI raw response: {r.text}")

    if r.status_code != 200:
        print("❌ OpenAI returned a non-200 response.")
        return Response("<Response><Say>Sorry, could not connect to the AI agent.</Say></Response>",
                        mimetype="application/xml")

    try:
        data = r.json()
        ws_url = data["client_secret"]["value"]
    except Exception as e:
        print(f"❌ Could not parse OpenAI response: {e}")
        return Response("<Response><Say>Sorry, AI session setup failed.</Say></Response>",
                        mimetype="application/xml")

    print(f"✅ WebSocket URL acquired: {ws_url}")

    # Build TwiML with <Connect><Stream>
    twiml = VoiceResponse()
    connect = Connect()
    stream = Stream(
        url=ws_url,
        track="both",  # stream both caller + AI audio
        status_callback="/stream-events",  # log start/stop events
        status_callback_method="POST"
    )
    connect.append(stream)
    twiml.append(connect)

    print("✅ Returning TwiML to Twilio.")
    return Response(str(twiml), mimetype="application/xml")


@app.route("/stream-events", methods=["POST"])
def stream_events():
    """Logs Twilio stream lifecycle events (start, media, stop)."""
    event = request.form.get("Event")
    print(f"🎧 Twilio Stream Event: {event}")
    if event == "start":
        print(f"   ➜ Stream started: {request.form.to_dict()}")
    elif event == "stop":
        print(f"   ➜ Stream stopped.")
    return ("", 204)


@app.route("/", methods=["GET"])
def home():
    return "ChatGPT Call Agent is running!", 200


if __name__ == "__main__":
    # Run locally for testing
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
