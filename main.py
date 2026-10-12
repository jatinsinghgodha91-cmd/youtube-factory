import asyncio
import datetime
import json
import os
import re
import shutil
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

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",                                  # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",             # Mac
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",          # Linux
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


def build_long(history: dict) -> dict:
    target = config.LONG_MINUTES * 150
    past = history["titles"][-200:]
    avoid = "\n".join(f"- {t}" for t in past) or "- (none yet)"

    prompt = f"""Niche: {config.NICHE}
Language: {config.LANGUAGE}
Narration length: about {target} words (about {config.LONG_MINUTES} minutes of speech).

Pick ONE specific, high-interest video topic in this niche. It must be clearly different from these past titles:
{avoid}

Return JSON with exactly these keys:
{{
  "title": "clickable YouTube title, under 70 characters",
  "thumbnail_text": "3 to 5 words for the thumbnail, all caps",
  "description": "150 to 250 word YouTube description with a short summary and a subscribe call to action",
  "tags": ["15 to 20 relevant tags"],
  "chapters": [{{"time": "0:00", "name": "chapter name"}}],
  "narration": "the full spoken script, about {target} words, no stage directions"
}}
Use 6 to 8 chapters that split the narration into clear parts."""

    for attempt in range(1, 4):
        data = groq_json(prompt)
        check_keys(data, ["title", "thumbnail_text", "description", "tags", "chapters", "narration"])
        words = word_count(data["narration"])
        if words >= target * 0.8:
            return data
        print(f"   Long script chhoti hai ({words} words), dobara likh raha hoon... (try {attempt})")
    return data


def build_short(history: dict) -> dict:
    target = config.SHORT_WORDS
    past = history["titles"][-200:]
    avoid = "\n".join(f"- {t}" for t in past) or "- (none yet)"

    prompt = f"""Niche: {config.NICHE}
Language: {config.LANGUAGE}
Make ONE YouTube Shorts script: a vertical video, under 50 seconds when spoken.
Narration length: about {target} words.
Rules: one single tip or idea only. The first sentence must be a hook that grabs attention in 2 seconds. End with a line that makes people follow or watch again.

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


# ---------- 3. Images (thumbnail, slides, shorts frame) ----------
def draw_gradient(W: int, H: int):
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        shade = int(20 + 60 * y / H)
        draw.line([(0, y), (W, y)], fill=(shade, 24 + shade // 3, 60 + shade))
    return img, draw


def wrap_text(draw, text: str, font, max_width: int):
    lines, current = [], ""
    for word in text.split():
        test = f"{current} {word}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_outlined(draw, x: int, y: int, text: str, font, fill):
    for dx in (-4, 0, 4):
        for dy in (-4, 0, 4):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill)


def draw_lines_centered(draw, lines, font, W: int, y: int, line_h: int, fill=(255, 221, 0)):
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (W - (bbox[2] - bbox[0])) // 2
        draw_outlined(draw, x, y, line, font, fill)
        y += line_h


def make_thumbnail(text: str, path: Path):
    W, H = 1280, 720
    img, draw = draw_gradient(W, H)
    font = load_font(110)
    lines = wrap_text(draw, text.upper(), font, W - 120)
    line_h = 130
    y = (H - line_h * len(lines)) // 2
    draw_lines_centered(draw, lines, font, W, y, line_h)
    img.save(path, "JPEG", quality=92)


def make_slide(path: Path, video_title: str, chapter_name: str):
    W, H = 1920, 1080
    img, draw = draw_gradient(W, H)
    small = load_font(44)
    big = load_font(96)
    top_lines = wrap_text(draw, video_title, small, W - 160)
    draw_lines_centered(draw, top_lines, small, W, 50, 56, fill=(255, 255, 255))
    lines = wrap_text(draw, chapter_name, big, W - 200)
    line_h = 120
    y = (H - line_h * len(lines)) // 2 + 40
    draw_lines_centered(draw, lines, big, W, y, line_h)
    img.save(path, "JPEG", quality=92)


def make_short_frame(path: Path, overlay_text: str):
    W, H = 1080, 1920
    img, draw = draw_gradient(W, H)
    font = load_font(120)
    lines = wrap_text(draw, overlay_text.upper(), font, W - 140)
    line_h = 150
    y = (H - line_h * len(lines)) // 2
    draw_lines_centered(draw, lines, font, W, y, line_h)
    img.save(path, "JPEG", quality=92)


# ---------- 4. Video render (ffmpeg) ----------
def render_video(parts, audio: Path, out_path: Path, max_seconds=None):
    """parts = [(image_path, seconds), ...]; audio ke saath MP4 banata hai."""
    list_file = out_path.parent / "_frames.txt"
    lines = []
    for img, secs in parts:
        lines.append(f"file '{img.resolve().as_posix()}'")
        lines.append(f"duration {secs:.2f}")
    lines.append(f"file '{parts[-1][0].resolve().as_posix()}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-i", str(audio),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "10",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
    ]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd.append(str(out_path))

    subprocess.run(cmd, check=True)
    list_file.unlink()


# ---------- 5. Files save karna ----------
def make_folder(kind: str, title: str) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = OUTPUT / f"{kind}_{stamp}_{slugify(title)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_long(data: dict) -> Path:
    folder = make_folder("LONG", data["title"])
    (folder / "script.txt").write_text(data["narration"], encoding="utf-8")

    print("   Voiceover bana raha hoon...")
    audio = folder / "voice.mp3"
    make_voice(data["narration"], audio)

    print("   Thumbnail bana raha hoon...")
    make_thumbnail(data["thumbnail_text"], folder / "thumbnail.jpg")

    print("   Long video render kar raha hoon...")
    names = []
    for ch in data["chapters"]:
        names.append(ch.get("name", "") if isinstance(ch, dict) else str(ch))
    names = [n for n in names if n] or [data["title"]]

    total = audio_seconds(audio)
    per = total / len(names)

    slides_dir = folder / "_slides"
    slides_dir.mkdir()
    parts = []
    chapters_out = []
    for i, name in enumerate(names):
        img = slides_dir / f"slide_{i + 1:02d}.jpg"
        make_slide(img, data["title"], name)
        parts.append((img, per))
        chapters_out.append({"time": fmt_time(i * per), "name": name})

    render_video(parts, audio, folder / "video.mp4")
    shutil.rmtree(slides_dir)

    meta = {
        "title": data["title"],
        "thumbnail_text": data["thumbnail_text"],
        "description": data["description"],
        "tags": data["tags"],
        "chapters": chapters_out,
    }
    (folder / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return folder


def save_short(data: dict, n: int) -> Path:
    folder = make_folder(f"SHORT{n}", data["title"])
    (folder / "script.txt").write_text(data["narration"], encoding="utf-8")

    print("   Short ki voice bana raha hoon...")
    audio = folder / "voice.mp3"
    make_voice(data["narration"], audio)

    print("   Short video render kar raha hoon...")
    frame = folder / "frame.jpg"
    make_short_frame(frame, data["overlay_text"])
    render_video([(frame, audio_seconds(audio))], audio, folder / "short.mp4", max_seconds=58)
    frame.unlink()

    desc = data["description"]
    if "#Shorts" not in desc:
        desc = desc + "\n\n#Shorts"
    meta = {"title": data["title"], "description": desc, "tags": data["tags"]}
    (folder / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return folder


# ---------- 6. History (duplicate topics se bachne ke liye) ----------
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

    for i in range(config.LONG_VIDEOS_PER_RUN):
        print(f"[LONG {i + 1}/{config.LONG_VIDEOS_PER_RUN}] Script likh raha hoon...")
        data = build_long(history)
        folder = save_long(data)
        history["titles"].append(data["title"])
        save_history(history)
        print(f"   ✅ Long ready: {folder}\n")

    for i in range(config.SHORTS_PER_RUN):
        print(f"[SHORT {i + 1}/{config.SHORTS_PER_RUN}] Script likh raha hoon...")
        data = build_short(history)
        folder = save_short(data, i + 1)
        history["titles"].append(data["title"])
        save_history(history)
        print(f"   ✅ Short ready: {folder}\n")

    print("🎉 Sab ready hai. output folder check karo.")


if __name__ == "__main__":
    main()
