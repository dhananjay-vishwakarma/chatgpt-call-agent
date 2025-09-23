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

    try:
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
        print(f"📤 Sending to OpenAI: {payload}")
        r = requests.post(
            "https://api.openai.com/v1/realtime/sessions",
            headers={
                "Authorization": f"Bearer {openai_api_key}",
                "Content-Type": "application/json"
            },
            json=payload
        )
    except Exception as e:
        print(f"❌ Exception while calling OpenAI API: {e}")
        return Response("<Response><Say>Sorry, we had an error creating the session.</Say></Response>",
                        mimetype="application/xml")

    print(f"🔎 OpenAI response status: {r.status_code}")
    print(f"🔎 OpenAI raw response: {r.text}")

    if r.status_code != 200:
        print("❌ OpenAI returned a non-200 response. Failing gracefully.")
        return Response("<Response><Say>Sorry, could not connect to the AI agent.</Say></Response>",
                        mimetype="application/xml")

    try:
        data = r.json()
    except Exception as e:
        print(f"❌ Could not parse JSON: {e}")
        return Response("<Response><Say>Sorry, could not parse AI response.</Say></Response>",
                        mimetype="application/xml")

    if "client_secret" not in data:
        print(f"❌ client_secret missing. Response was: {data}")
        return Response("<Response><Say>Sorry, there was a problem creating the call agent session.</Say></Response>",
                        mimetype="application/xml")

    ws_url = data["client_secret"]["value"]
    print(f"✅ WebSocket URL acquired: {ws_url}")

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
