import asyncio
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import edge_tts
import requests
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / config.OUTPUT_DIR
HISTORY = ROOT / config.HISTORY_FILE
FPS = 25

SYSTEM_PROMPT = """You are a professional YouTube scriptwriter for a faceless US-focused channel.
Your narration will be read aloud by a text-to-speech voice.
Rules:
- Write only spoken words. No stage directions, no [music] markers, no headings, no emojis.
- Use short sentences and a friendly, confident tone.
- Give practical, specific, accurate advice. Never invent statistics, prices or tool features.
- Always return valid JSON only, exactly matching the schema requested."""

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",                                  # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",             # Mac
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",          # Linux
]

PALETTES = [
    {"bg1": (12, 24, 72), "bg2": (95, 30, 150), "accent": (255, 200, 0)},
    {"bg1": (0, 58, 66), "bg2": (0, 150, 132), "accent": (255, 110, 64)},
    {"bg1": (72, 0, 28), "bg2": (175, 26, 56), "accent": (255, 226, 120)},
    {"bg1": (4, 40, 26), "bg2": (22, 128, 78), "accent": (255, 214, 0)},
    {"bg1": (10, 10, 16), "bg2": (34, 40, 70), "accent": (0, 222, 255)},
]


# ---------- Helpers ----------
def word_count(text: str) -> int:
    return len(text.split())


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "video"


def fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def check_keys(data: dict, keys: list):
    missing = [k for k in keys if not data.get(k)]
    if missing:
        raise ValueError(f"LLM ne ye keys nahi diye: {missing}")


def load_font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def palette_index(seed: str) -> int:
    return int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % len(PALETTES)


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
        "temperature": 0.7,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }

    for attempt in range(1, 8):
        time.sleep(config.API_PAUSE_SECONDS)
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=240,
        )
        if r.status_code == 429:
            m = re.search(r"try again in ([\d.]+)s", r.text)
            wait = min((float(m.group(1)) if m else 15.0) + 3, 90)
            print(f"   Groq rate limit: {wait:.0f} sec ruk raha hoon... (try {attempt})")
            time.sleep(wait)
            continue
        if r.status_code == 400 and "json_validate_failed" in r.text:
            print(f"   Groq ka JSON kharab tha, dobara try kar raha hoon... (try {attempt})")
            continue
        if r.status_code != 200:
            raise SystemExit(f"Groq error {r.status_code}: {r.text}")
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, KeyError, IndexError):
            print(f"   JSON parse nahi hua, dobara try kar raha hoon... (try {attempt})")

    raise SystemExit("Groq 7 tries mein bhi valid JSON nahi de paya. Thodi der baad dobara run karo.")


def build_long(history: dict) -> dict:
    count = config.LONG_CHAPTERS
    per = config.LONG_WORDS_PER_CHAPTER
    past = history["titles"][-200:]
    avoid = "\n".join(f"- {t}" for t in past) or "- (none yet)"

    outline_prompt = f"""Niche: {config.NICHE}
Language: {config.LANGUAGE}

Plan ONE long YouTube video with exactly {count} chapters.
Pick a specific, high-interest topic that viewers would search for and want to save. It must be clearly different from these past titles:
{avoid}

Return JSON with exactly these keys:
{{
  "title": "clickable YouTube title, under 70 characters, promises a specific result",
  "thumbnail_text": "3 to 5 words for the thumbnail, all caps, a bold promise",
  "thumbnail_text_alt": "a second thumbnail text, 3 to 5 words, all caps, using COMPLETELY DIFFERENT words and a different angle from thumbnail_text",
  "hook": "the first 2 sentences of the video, promising one specific, valuable result",
  "description": "150 to 250 word YouTube description with a short summary and a subscribe call to action",
  "tags": ["15 to 20 relevant tags"],
  "chapters": [{{"name": "short chapter title", "point": "one sentence: what this chapter teaches"}}]
}}
The chapters list must contain exactly {count} items."""

    outline = groq_json(outline_prompt)
    check_keys(outline, ["title", "thumbnail_text", "description", "tags", "chapters"])
    chapters = [c for c in outline["chapters"] if isinstance(c, dict) and c.get("name")]
    if not chapters:
        raise ValueError("LLM ne chapters nahi diye")

    hook = outline.get("hook") or ""
    names = [c["name"] for c in chapters]
    names_str = " | ".join(names)
    total = len(chapters)

    sections = []
    for i, ch in enumerate(chapters):
        extra = ""
        if i == 0:
            extra += "This is the OPENING part. Start with the hook below, saying it nearly word for word, then deliver on it right away.\n"
            if hook:
                extra += f'Hook: "{hook}"\n'
        if i == total - 1:
            extra += (
                "This is the FINAL part: give a short summary of the key takeaways, "
                "then ask viewers to comment one specific question about this topic.\n"
            )
        if sections:
            prev_end = sections[-1][-500:]
            extra += (
                f'The previous part ended with: "...{prev_end}"\n'
                "Continue naturally from there. Do not repeat it.\n"
            )

        section_prompt = f"""Video title: {outline['title']}
Chapters in order: {names_str}
Niche: {config.NICHE}
Language: {config.LANGUAGE}

You are writing PART {i + 1} of {total}: "{ch['name']}".
What this part covers: {ch.get('point', '')}
{extra}
Write about {per} words of spoken narration for this part only (never less than {int(per * 0.85)} words).
Include at least one concrete, actionable step the viewer can do today.
Spoken words only. No headings, no stage directions, no emojis, no [music] markers.
Never invent statistics or tool features.

Return JSON with exactly one key: "section" (the spoken text as a string)."""

        text = ""
        for attempt in range(1, 4):
            data = groq_json(section_prompt)
            text = (data.get("section") or "").strip()
            if word_count(text) >= per * 0.7:
                break
            print(f"   Part {i + 1} chhota hai ({word_count(text)} words), dobara likh raha hoon... (try {attempt})")
        sections.append(text)
        print(f"   Part {i + 1}/{total} ready ({word_count(text)} words)")

    narration = "\n\n".join(s for s in sections if s)
    print(f"   Long script total: {word_count(narration)} words")

    alt = (outline.get("thumbnail_text_alt") or "").strip()
    if not alt or alt.upper() == outline["thumbnail_text"].strip().upper():
        alt = "THE SIMPLE WAY TO START"

    return {
        "title": outline["title"],
        "thumbnail_text": outline["thumbnail_text"],
        "thumbnail_text_alt": alt,
        "description": outline["description"],
        "tags": outline["tags"],
        "chapters": [{"time": "", "name": n} for n in names],
        "narration": narration,
    }


def build_short(history: dict) -> dict:
    target = config.SHORT_WORDS
    past = history["titles"][-200:]
    avoid = "\n".join(f"- {t}" for t in past) or "- (none yet)"

    prompt = f"""Niche: {config.NICHE}
Language: {config.LANGUAGE}
Make ONE YouTube Shorts script: a vertical video, under 50 seconds when spoken.
Narration length: about {target} words.
Rules: one single tip or idea only. The first sentence must be a hook that grabs attention in 2 seconds. Include one concrete step the viewer can do today. End with a line that makes people follow or comment.

Pick a topic clearly different from these past titles:
{avoid}

Return JSON with exactly these keys:
{{
  "title": "catchy title under 60 characters, no hashtags",
  "overlay_text": "3 to 6 words shown big on screen, all caps",
  "description": "2 to 3 short sentences, then 3 relevant hashtags",
  "tags": ["8 to 12 relevant tags"],
  "narration": "the spoken script, about {target} words, no stage directions"
}}"""

    for attempt in range(1, 4):
        data = groq_json(prompt)
        check_keys(data, ["title", "overlay_text", "description", "tags", "narration"])
        words = word_count(data["narration"])
        if words >= target * 0.8:
            return data
        print(f"   Short script chhoti hai ({words} words), dobara likh raha hoon... (try {attempt})")
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
        raw = path.with_name("raw_" + path.name)
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


def audio_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# ---------- 3. Design: background, text, thumbnails, slides ----------
def base_canvas(W: int, H: int, pal: dict) -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    c1, c2 = pal["bg1"], pal["bg2"]
    for y in range(H):
        t = y / H
        c = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([int(W * 0.72), int(-H * 0.35), int(W * 1.22), int(H * 0.40)],
               fill=(*pal["accent"], 40))
    od.ellipse([int(-W * 0.18), int(H * 0.62), int(W * 0.34), int(H * 1.25)],
               fill=(255, 255, 255, 22))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def draw_outlined(draw, x: int, y: int, text: str, font, fill, stroke: int = 4):
    draw.text((x, y), text, font=font, fill=fill,
              stroke_width=stroke, stroke_fill=(0, 0, 0))


def wrap_text(draw, text: str, font, max_width: int):
    lines, current = [], ""
    for word in text.split():
        test = f"{current}
