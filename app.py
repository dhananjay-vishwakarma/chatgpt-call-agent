import os
import threading
import time
import requests
from flask import Flask, Response, request
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

# Optional Twilio REST client (used for outbound recall attempts)
try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None

app = Flask(__name__)
openai_api_key = os.getenv("OPENAI_API_KEY")
twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_caller_id = os.getenv("TWILIO_CALLER_ID")  # the From number for outbound recalls
# recall settings
RECALL_DELAY = int(os.getenv("RECALL_DELAY_SECONDS", "5"))
RECALL_MAX = int(os.getenv("RECALL_MAX_ATTEMPTS", "12"))

@app.route("/voice", methods=["POST"])
def voice():
    print("✅ Twilio POST /voice received")
    print(f"🔔 Caller: {request.form.get('From')} -> Callee: {request.form.get('To')}")
    call_sid = request.form.get("CallSid")
    from_number = request.form.get("From")

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

    # Ensure the call remains live for at least 60 seconds before TwiML ends.
    # This prevents early disconnects while the AI is speaking. We append a Pause
    # to keep the call open on Twilio's side.
    twiml.pause(length=60)

    # capture values needed by background thread (avoid using request inside thread)
    to_number = request.form.get("To")
    base_url = request.url_root.rstrip('/')

    # Start background monitor thread to check call status after 60s and attempt
    # recalls every RECALL_DELAY seconds if the call ended without a real response.
    def recall_monitor(original_call_sid, user_number, callee_number, base_url_inner):
        # wait at least the minimum hold duration
        print(f"⏱ recall_monitor: sleeping 60s for call {original_call_sid}")
        time.sleep(60)

        if not (twilio_account_sid and twilio_auth_token and TwilioClient):
            print("⚠️ Twilio credentials or client missing; skipping recall attempts.")
            return

        client = TwilioClient(twilio_account_sid, twilio_auth_token)

        try:
            call = client.calls(original_call_sid).fetch()
            status = getattr(call, "status", None)
            duration = getattr(call, "duration", None)
            print(f"🔁 recall_monitor: original call {original_call_sid} status={status} duration={duration}")
        except Exception as e:
            print(f"⚠️ recall_monitor: could not fetch original call: {e}")
            status = None
            duration = None

        # If the call is still in-progress or had a decent duration, assume user heard/responded.
        try:
            if status in ("in-progress", "ringing", "queued"):
                print(f"✅ recall_monitor: call {original_call_sid} still active or ringing; no recall needed.")
                return
            if duration and int(duration) >= 10:
                print(f"✅ recall_monitor: call {original_call_sid} had duration {duration}s; assuming handled.")
                return
        except Exception:
            pass

        # Otherwise attempt to recall the user up to RECALL_MAX times, every RECALL_DELAY seconds.
        attempts = 0
        while attempts < RECALL_MAX:
            attempts += 1
            print(f"📞 recall_monitor: attempt {attempts} to recall {user_number}")
            try:
                # Use the same webhook (this /voice endpoint) for the outbound call to replay the message.
                outbound = client.calls.create(
                    to=user_number,
                    from_=twilio_caller_id or callee_number,
                    url=os.getenv("RECALL_TWIML_URL") or (base_url_inner + "/voice")
                )
                print(f"📤 recall_monitor: created outbound call SID {outbound.sid}")

                # Poll a few seconds for status to become 'in-progress' (answered)
                poll_start = time.time()
                answered = False
                while time.time() - poll_start < RECALL_DELAY:
                    try:
                        c = client.calls(outbound.sid).fetch()
                        if getattr(c, "status", None) == "in-progress":
                            answered = True
                            break
                    except Exception:
                        pass
                    time.sleep(1)

                if answered:
                    print(f"✅ recall_monitor: user answered on attempt {attempts} (call {outbound.sid})")
                    return
            except Exception as e:
                print(f"❌ recall_monitor: error creating outbound call: {e}")

            # wait before next attempt
            time.sleep(RECALL_DELAY)

        print(f"⚠️ recall_monitor: exhausted {RECALL_MAX} recall attempts for {user_number}")

    # launch recall monitor in background
    monitor_thread = threading.Thread(
        target=recall_monitor,
        args=(call_sid, from_number, to_number, base_url),
        daemon=True,
    )
    monitor_thread.start()

    print("✅ Returning TwiML with greeting + stream.")
    return Response(str(twiml), mimetype="application/xml")


@app.route("/stream-events", methods=["POST"])
def stream_events():
    # Verbose debug logging for Twilio Stream events
    try:
        print("=== /stream-events received ===")
        print("Headers:", dict(request.headers))
        try:
            print("Form:", request.form.to_dict())
        except Exception:
            print("Form: <unavailable>")
        try:
            print("Args:", request.args.to_dict())
        except Exception:
            print("Args: <unavailable>")

        raw = request.get_data(as_text=True)
        print("Raw body:", raw)

        # Try to parse JSON body if present
        json_body = None
        try:
            json_body = request.get_json(force=True, silent=True)
            print("JSON body:", json_body)
        except Exception as e:
            print("JSON parse error:", e)

        # Event may be in form or in JSON
        event = request.form.get("Event") or (json_body.get("event") if isinstance(json_body, dict) else None)
        print(f"🎧 Twilio Stream Event: {event}")

        # Try to detect any speech/transcription text in the payload
        if isinstance(json_body, dict):
            # Common transcription/text-like keys to look for
            for key in ("SpeechResult", "transcription", "text", "speech_text", "transcripts", "speech_to_text"):
                if key in json_body:
                    print(f"🗣 Detected speech ({key}): {json_body.get(key)}")

            # Sometimes nested under 'Media' or other keys
            if "Media" in json_body:
                try:
                    print("Media event keys:", list(json_body["Media"].keys()))
                except Exception:
                    print("Media: (non-dict)")

        print("=== /stream-events end ===")
    except Exception as e:
        print("Error in /stream-events logging:", e)

    return ("", 204)


@app.route("/", methods=["GET"])
def home():
    return "ChatGPT Call Agent is running!", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
