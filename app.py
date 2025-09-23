import os
import requests
from flask import Flask, Response, request
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

app = Flask(__name__)
openai_api_key = os.getenv("OPENAI_API_KEY")

@app.route("/voice", methods=["POST"])
def voice():
    print("✅ Twilio POST /voice received")
    print(f"🔔 Caller: {request.form.get('From')} -> Callee: {request.form.get('To')}")

    # Start TwiML response with greeting
    twiml = VoiceResponse()
    twiml.say(
        "Hello! Thanks for taking this call. Please hold for a quick message about payroll automation.",
        voice="alice"
    )

    # Step 1: Create OpenAI session
    try:
        payload = {
            "model": "gpt-4o-realtime-preview-2024-12",
            "voice": "verse",
            "instructions": (
                "You are a friendly HRMS payroll software agent. "
                "As soon as the call connects, start speaking immediately. "
                "Greet warmly, then deliver a concise 60-second pitch about payroll automation, "
                "compliance benefits, and cost savings. "
                "Keep it conversational and not pushy. "
                "End by asking one polite follow-up question, then wrap up nicely."
            )
        }
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
        twiml.say("Sorry, our AI assistant is unavailable right now.", voice="alice")
        return Response(str(twiml), mimetype="application/xml")

    print(f"🔎 OpenAI response status: {r.status_code}")
    print(f"🔎 OpenAI raw response: {r.text}")

    if r.status_code != 200:
        print("❌ OpenAI returned a non-200 response.")
        twiml.say("Sorry, we could not connect to the AI agent. Please try again later.", voice="alice")
        return Response(str(twiml), mimetype="application/xml")

    try:
        data = r.json()
        ws_url = data["client_secret"]["value"]
    except Exception as e:
        print(f"❌ Could not parse OpenAI response: {e}")
        twiml.say("Sorry, we could not process the AI response.", voice="alice")
        return Response(str(twiml), mimetype="application/xml")

    # Step 2: Only connect stream if OpenAI session was created successfully
    print(f"✅ WebSocket URL acquired: {ws_url}")
    connect = Connect()
    stream = Stream(
        url=ws_url,
        track="both",
        status_callback="/stream-events",
        status_callback_method="POST"
    )
    connect.append(stream)
    twiml.append(connect)

    print("✅ Returning TwiML with greeting + stream.")
    return Response(str(twiml), mimetype="application/xml")


@app.route("/stream-events", methods=["POST"])
def stream_events():
    event = request.form.get("Event")
    print(f"🎧 Twilio Stream Event: {event}")
    return ("", 204)


@app.route("/", methods=["GET"])
def home():
    return "ChatGPT Call Agent is running!", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
