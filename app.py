import os
import requests
from flask import Flask, Response, request
from twilio.twiml.voice_response import VoiceResponse, Connect

app = Flask(__name__)

openai_api_key = os.getenv("OPENAI_API_KEY")

@app.route("/voice", methods=["POST"])
def voice():
    print("✅ Twilio POST /voice received")
    print(f"🔔 Caller: {request.form.get('From')} -> Callee: {request.form.get('To')}")

    # Step 1: Create a Realtime Session with OpenAI
    try:
        r = requests.post(
            "https://api.openai.com/v1/realtime/sessions",
            headers={
                "Authorization": f"Bearer {openai_api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-realtime-preview-2024-12",
                "voice": "verse",
                "instructions": (
                    "You are a friendly HRMS payroll software agent. "
                    "After the call starts, greet warmly, then deliver a concise 60-second pitch "
                    "about payroll automation, compliance benefits, and cost savings. "
                    "Keep it conversational and not pushy. "
                    "Ask one polite follow-up question to engage, then wrap up nicely."
                ),
                # 👇 Force ChatGPT to greet immediately (no waiting for caller speech)
                "conversation": {
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Begin speaking immediately once the call starts. "
                                "Greet warmly (e.g., 'Hello! This is your HRMS payroll assistant'), "
                                "then go into your pitch naturally."
                            )
                        }
                    ]
                }
            }
        )
    except Exception as e:
        print(f"❌ Failed to call OpenAI API: {e}")
        return Response("<Response><Say>Sorry, we had an error creating the session.</Say></Response>",
                        mimetype="application/xml")

    print(f"🔎 OpenAI response status: {r.status_code}")
    print(f"🔎 OpenAI response: {r.text}")

    if r.status_code != 200:
        return Response("<Response><Say>Sorry, could not connect to the AI agent.</Say></Response>",
                        mimetype="application/xml")

    data = r.json()

    if "client_secret" not in data:
        print("❌ Missing client_secret in OpenAI response!")
        return Response("<Response><Say>Sorry, there was a problem creating the call agent session.</Say></Response>",
                        mimetype="application/xml")

    ws_url = data["client_secret"]["value"]
    print(f"✅ WebSocket URL acquired: {ws_url}")

    # Step 2: Build TwiML to connect Twilio voice to OpenAI Realtime
    twiml = VoiceResponse()
    connect = Connect()
    connect.stream(url=ws_url)
    twiml.append(connect)

    print("✅ Returning TwiML to Twilio.")
    return Response(str(twiml), mimetype="application/xml")


@app.route("/", methods=["GET"])
def home():
    return "ChatGPT Call Agent is running!", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
