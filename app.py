import os
import requests
from flask import Flask, Response
from twilio.twiml.voice_response import VoiceResponse, Connect

# Get configuration from environment
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_from_number = os.getenv("TWILIO_FROM_NUMBER")
to_number = os.getenv("TO_NUMBER")
openai_api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)

@app.route("/voice", methods=["POST"])
def voice():
    # 1. Create a realtime session with OpenAI
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
                "Greet the person warmly, then give a 60-second pitch about payroll automation, "
                "compliance, and cost savings. Keep tone conversational and not pushy. "
                "Ask a polite follow-up question about whether they’d like to learn more."
            )
        }
    )

    data = r.json()
    ws_url = data["client_secret"]["value"]

    # 2. Return TwiML instructing Twilio to connect to OpenAI Realtime session
    twiml = VoiceResponse()
    connect = Connect()
    connect.stream(url=ws_url)
    twiml.append(connect)

    return Response(str(twiml), mimetype="application/xml")
