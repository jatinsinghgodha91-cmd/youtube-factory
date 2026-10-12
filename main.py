import asyncio
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

import edge_tts
import requests
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ROOT = Path(__file__).parent
OUTPUT = ROOT / config.OUTPUT_DIR
HISTORY = ROOT / config.HISTORY_FILE

SYSTEM_PROMPT = """You are a professional YouTube scriptwriter for a faceless US-focused channel.
Your narration will be read aloud by a text-to-speech voice.
Rules:
- Write only spoken words. No stage directions, no [music] markers, no headings, no emojis.
- Open with a strong hook in the first 15 seconds.
- Use short sentences and a friendly, confident tone.
- Give practical, specific, accurate advice. Never invent statistics, prices or tool features.
- Always return valid JSON only, exactly matching the schema requested."""


# ---------- 1. LLM se content lena ----------
def groq_json(user_prompt: str) -> dict:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise SystemExit("GROQ_API_KEY environment variable set nahi hai. Setup steps dekho.")

    payload = {
        "model": config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 6000,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=240,
    )
    if r.status_code != 200:
        raise SystemExit(f"Groq error {r.status_code}: {r.text}")
    return json.loads(r.json()["choices"][0]["message"]["content"])


def build_package(history: dict) -> dict:
    past = history["titles"][-200:]
    avoid = "\n".join(f"- {t}" for t in past) or "- (none yet)"
    words = config.TARGET_MINUTES * 150

    prompt = f"""Niche: {config.NICHE}
Language: {config.LANGUAGE}
Narration length: about {words} words (about {config.TARGET_MINUTES} minutes of speech).

Pick ONE specific, high-interest video topic in this niche. It must be clearly different from these past titles:
{avoid}

Return JSON with exactly these keys:
{{
  "title": "clickable YouTube title, under 70 characters",
  "thumbnail_text": "3 to 5 words for the thumbnail, all caps",
  "description": "150 to 250 word YouTube description with a short summary and a subscribe call to action",
  "tags": ["15 to 20 relevant tags"],
  "chapters": [{{"time": "0:00", "name": "chapter name"}}],
  "narration": "the full spoken script, about {words} words, no stage directions"
}}"""

    data = groq_json(prompt)
    required = ["title", "thumbnail_text", "description", "tags", "chapters", "narration"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"LLM ne ye keys nahi diye: {missing}")
    return data


# ---------- 2. Voiceover (MP3) ----------
async def _tts(text: str, path: Path):
    communicate = edge_tts.Communicate(text, config.VOICE)
    await communicate.save(str(path))


def make_voice(text: str, path: Path):
    try:
        asyncio.run(_tts(text, path))
    except Exception as e:
        print(f"   Edge TTS fail hua ({type(e).__name__}), gTTS use kar raha hoon...")
        raw = path.with_name("narration_raw.mp3")
        gTTS(text=text, lang="en", tld="com").save(str(raw))
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(raw),
                "-filter:a", f"atempo={config.VOICE_SPEED}",
                str(path),
            ],
            check=True,
        )
        raw.unlink()


# ---------- 3. Thumbnail (1280x720 JPG) ----------
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",                                  # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",             # Mac
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",          # Linux
]


def load_font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def make_thumbnail(text: str, path: Path):
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (20, 24, 60))
    draw = ImageDraw.Draw(img)

    # Background gradient
    for y in range(H):
        shade = int(20 + 60 * y / H)
        draw.line([(0, y), (W, y)], fill=(shade, 24 + shade // 3, 60 + shade))

    font = load_font(110)

    # Text ko lines me todna
    lines, current = [], ""
    for word in text.upper().split():
        test = f"{current} {word}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= W - 120:
            current = test
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    line_h = 130
    y = (H - line_h * len(lines)) // 2
    outline = [(-4, -4), (4, -4), (-4, 4), (4, 4), (0, -4), (0, 4), (-4, 0), (4, 0)]

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (W - (bbox[2] - bbox[0])) // 2
        for dx, dy in outline:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(255, 221, 0))
        y += line_h

    img.save(path, "JPEG", quality=92)


# ---------- 4. Files save karna ----------
def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "video"


def save_package(data: dict) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = OUTPUT / f"{stamp}_{slugify(data['title'])}"
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "narration.txt").write_text(data["narration"], encoding="utf-8")

    meta = {k: data[k] for k in ["title", "thumbnail_text", "description", "tags", "chapters"]}
    (folder / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("   Voiceover bana raha hoon...")
    make_voice(data["narration"], folder / "narration.mp3")

    print("   Thumbnail bana raha hoon...")
    make_thumbnail(data["thumbnail_text"], folder / "thumbnail.jpg")
    return folder


# ---------- 5. History (duplicate topics se bachne ke liye) ----------
def load_history() -> dict:
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return {"titles": []}


def save_history(history: dict):
    HISTORY.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- MAIN ----------
def main():
    OUTPUT.mkdir(exist_ok=True)
    history = load_history()

    for i in range(config.VIDEOS_PER_RUN):
        print(f"[{i + 1}/{config.VIDEOS_PER_RUN}] Script likh raha hoon...")
        data = build_package(history)
        folder = save_package(data)

        history["titles"].append(data["title"])
        save_history(history)
        print(f"   ✅ Ready: {folder}\n")

    print("🎉 Sab ready hai. output folder check karo.")


if __name__ == "__main__":
    main()
