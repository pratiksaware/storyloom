"""StoryLoom: personalized, multilingual, interactive storytelling on Google Cloud."""
import os, json, uuid, base64, datetime
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from google import genai
from google.genai import types
from google.cloud import texttospeech, storage, bigquery
from google.cloud import translate_v2 as translate

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
BUCKET = os.environ["MEDIA_BUCKET"]
GEMINI = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image")  # replaces retired Imagen 4
IMAGE_LOCATION = os.getenv("IMAGE_LOCATION", "global")
TABLE = f"{PROJECT}.{os.getenv('BQ_DATASET', 'storyloom')}.events"

ai = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
img_ai = genai.Client(vertexai=True, project=PROJECT, location=IMAGE_LOCATION)
tts = texttospeech.TextToSpeechClient()
tr = translate.Client()
gcs = storage.Client()
bq = bigquery.Client()
pool = ThreadPoolExecutor(4)
app = FastAPI(title="StoryLoom")

SCHEMA = [bigquery.SchemaField(n, t) for n, t in [
    ("ts", "TIMESTAMP"), ("session_id", "STRING"), ("event", "STRING"), ("topic", "STRING"),
    ("age_group", "STRING"), ("language", "STRING"), ("choice", "STRING"), ("step", "INTEGER")]]

@app.on_event("startup")
def init_bq():
    bq.create_dataset(TABLE.rsplit(".", 1)[0], exists_ok=True)
    bq.create_table(bigquery.Table(TABLE, schema=SCHEMA), exists_ok=True)

class Req(BaseModel):
    session_id: str | None = None
    topic: str
    age_group: str = "8-12"
    language: str = "en"
    tone: str = "adventurous"
    history: list[dict] = []   # [{"narration":..., "choice":...}]
    choice: str | None = None

def log(r: Req, session_id: str, event: str):
    row = {"ts": datetime.datetime.utcnow().isoformat(), "session_id": session_id, "event": event,
           "topic": r.topic, "age_group": r.age_group, "language": r.language,
           "choice": r.choice or "", "step": len(r.history)}
    try: bq.insert_rows_json(TABLE, [row])
    except Exception as e: print("bq error", e)

def make_scene(r: Req) -> dict:
    story = "\n".join(f"Scene: {h['narration']}\nReader chose: {h.get('choice','')}" for h in r.history)
    final = len(r.history) >= 3
    prompt = f"""You write interactive educational stories for ages {r.age_group}. Tone: {r.tone}.
Topic to weave in and teach: {r.topic}.
Story so far:\n{story or '(none, begin the story)'}
Write the next scene in English (60-90 words). {'Wrap up the story with a satisfying ending and no choices.' if final else 'End on a decision with exactly 2 choices.'}
Return JSON: {{"title": str, "narration": str, "image_prompt": str (vivid illustration, no text in image),
"learning_nugget": str (one true fact about the topic, 1 sentence), "choices": [str, str], "ending": bool}}"""
    out = ai.models.generate_content(model=GEMINI, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.9))
    return json.loads(out.text)

def make_image(prompt: str, sid: str):
    res = img_ai.models.generate_content(
        model=IMAGE_MODEL,
        contents=prompt + ", children's book illustration, warm colors, no text in the image",
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"],
                                           image_config=types.ImageConfig(aspect_ratio="16:9")))
    for part in res.candidates[0].content.parts:
        if part.inline_data:
            data, mime = part.inline_data.data, part.inline_data.mime_type or "image/png"
            gcs.bucket(BUCKET).blob(f"{sid}/{uuid.uuid4().hex}.png").upload_from_string(data, content_type=mime)
            return f"data:{mime};base64," + base64.b64encode(data).decode()
    raise RuntimeError("The image model returned no image")

def make_audio(text: str, lang: str, sid: str):
    resp = tts.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code=lang),
        audio_config=texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3))
    gcs.bucket(BUCKET).blob(f"{sid}/{uuid.uuid4().hex}.mp3").upload_from_string(resp.audio_content, content_type="audio/mpeg")
    return "data:audio/mpeg;base64," + base64.b64encode(resp.audio_content).decode()

TTS_LANG = {"en": "en-US", "hi": "hi-IN", "mr": "mr-IN", "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "ta": "ta-IN", "bn": "bn-IN"}

@app.post("/api/story")
def story(r: Req):
    sid = r.session_id or uuid.uuid4().hex[:12]
    try:
        scene = make_scene(r)
        img = pool.submit(make_image, scene["image_prompt"], sid)
        def localize(t):  # Cloud Translation for multilingual output
            return t if r.language == "en" else tr.translate(t, target_language=r.language, format_="text")["translatedText"]
        texts = {k: localize(scene[k]) for k in ("title", "narration", "learning_nugget")}
        texts["choices"] = [localize(c) for c in scene.get("choices", [])]
        audio = pool.submit(make_audio, texts["narration"], TTS_LANG.get(r.language, r.language), sid)
        log(r, sid, "scene_shown" if not scene.get("ending") else "story_completed")
        return {"session_id": sid, "ending": scene.get("ending", False), **texts,
                "image": img.result(), "audio": audio.result()}
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {e}")

@app.get("/api/analytics")
def analytics():
    q = f"""SELECT language, topic, COUNTIF(event='scene_shown') scenes,
            COUNTIF(event='story_completed') completions, COUNT(DISTINCT session_id) sessions
            FROM `{TABLE}` GROUP BY language, topic ORDER BY sessions DESC LIMIT 20"""
    return [dict(row) for row in bq.query(q).result()]

app.mount("/static", StaticFiles(directory="static"), name="static")
@app.get("/")
def index(): return FileResponse("static/index.html")
