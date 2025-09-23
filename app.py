import os
import requests
from flask import Flask, Response, request, url_for

app = Flask(__name__)

# API Keys
openai_api_key = os.getenv("OPENAI_API_KEY")

# Folder to save temporary audio files
AUDIO_FOLDER = "static/audio"
os.makedirs(AUDIO_FOLDER, exist_ok=True)

# ----------------------------
# Step 1: Handle incoming call
# ----------------------------
@app.route("/voice", methods=["POST"])
def voice():
    from_number = request.form.get("From")
    print(f"✅ Incoming call from {from_number}")

    # Ask caller to say something
    twiml = f"""
    <Response>
        <Say voice="alice">Hello! Please tell me your question after the beep. Then press star.</Say>
        <Record 
            action="/process_recording" 
            method="POST" 
            maxLength="20" 
            finishOnKey="*"
            playBeep="true"/>
        <Say>I did not receive any input. Goodbye.</Say>
    </Response>
    """
    return Response(twiml, mimetype="application/xml")

# -----------------------------------
# Step 2: Process recording from user
# -----------------------------------
@app.route("/process_recording", methods=["POST"])
def process_recording():
    recording_url = request.form.get("RecordingUrl")
    print(f"🎙 Received recording: {recording_url}.wav")

    # Download Twilio recording (WAV file)
    audio_file = os.path.join(AUDIO_FOLDER, "caller.wav")
    r = requests.get(recording_url + ".wav")
    with open(audio_file, "wb") as f:
        f.write(r.content)

    # Step 2.1: Transcribe with Whisper
    with open(audio_file, "rb") as f:
        transcription = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {openai_api_key}"},
            files={"file": f},
            data={"model": "gpt-4o-transcribe"}
        )
    user_text = transcription.json().get("text", "")
    print(f"🗣 Transcribed text: {user_text}")

    # Step 2.2: Send to ChatGPT
    chat = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are a helpful HR payroll assistant."},
                {"role": "user", "content": user_text}
            ]
        }
    )
    ai_text = chat.json()["choices"][0]["message"]["content"]
    print(f"🤖 AI reply: {ai_text}")

    # Step 2.3: Convert AI text → speech
    tts_file = os.path.join(AUDIO_FOLDER, "reply.mp3")
    tts = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {openai_api_key}"},
        json={"model": "gpt-4o-mini-tts", "voice": "verse", "input": ai_text},
        stream=True
    )
    with open(tts_file, "wb") as f:
        for chunk in tts.iter_content(chunk_size=1024):
            f.write(chunk)

    # Step 2.4: Twilio <Play> the AI response
    audio_url = url_for("static", filename=f"audio/reply.mp3", _external=True)
    print(f"🔊 Serving AI audio at {audio_url}")

    twiml = f"""
    <Response>
        <Play>{audio_url}</Play>
        <Say voice="alice">Thank you for your time. Goodbye!</Say>
        <Hangup/>
    </Response>
    """
    return Response(twiml, mimetype="application/xml")

# ----------------------------
@app.route("/", methods=["GET"])
def home():
    return "ChatGPT Voice Agent (batch mode) is running!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
