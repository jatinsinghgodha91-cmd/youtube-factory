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
ROOT = Path(__file__).parent
OUTPUT = ROOT / config.OUTPUT_DIR
HISTORY = ROOT / config.HISTORY_FILE
FPS = 25

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

# 5 color themes. Har video ka theme uske title se chunta jaata hai.
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


def pick_palette(seed: str) -> dict:
    idx = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % len(PALETTES)
    return PALETTES[idx]


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
        "max_tokens": 2500,
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
            wait = float(m.group(1)) if m else 15.0
            wait = min(wait + 3, 90)
            print(f"   Groq rate limit: {wait:.0f} sec ruk raha hoon... (try {attempt})")
            time.sleep(wait)
            continue
        if r.status_code != 200:
            raise SystemExit(f"Groq error {r.status_code}: {r.text}")
        return json.loads(r.json()["choices"][0]["message"]["content"])

    raise SystemExit("Groq rate limit baar-baar aa raha hai. Thodi der baad dobara run karo.")


def build_long(history: dict) -> dict:
    count = config.LONG_CHAPTERS
    per = config.LONG_WORDS_PER_CHAPTER
    past = history["titles"][-200:]
    avoid = "\n".join(f"- {t}" for t in past) or "- (none yet)"

    outline_prompt = f"""Niche: {config.NICHE}
Language: {config.LANGUAGE}

Plan ONE long YouTube video with exactly {count} chapters.
Pick a specific, high-interest topic in this niche. It must be clearly different from these past titles:
{avoid}

Return JSON with exactly these keys:
{{
  "title": "clickable YouTube title, under 70 characters",
  "thumbnail_text": "3 to 5 words for the thumbnail, all caps",
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

    names = [c["name"] for c in chapters]
    names_str = " | ".join(names)
    total = len(chapters)

    sections = []
    for i, ch in enumerate(chapters):
        extra = ""
        if i == 0:
            extra += "This is the OPENING part: start with a strong hook in the first 15 seconds.\n"
        if i == total - 1:
            extra += "This is the FINAL part: finish with a short summary and a call to subscribe.\n"
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
Spoken words only. No headings, no stage directions, no emojis, no [music] markers.
Give practical, specific, accurate advice. Never invent statistics or tool features.

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

    return {
        "title": outline["title"],
        "thumbnail_text": outline["thumbnail_text"],
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


# ---------- 3. Design: background, text, thumbnail, slides ----------
def base_canvas(W: int, H: int, pal: dict) -> Image.Image:
    """Gradient background + translucent accent circles, professional look ke liye."""
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


def draw_lines_centered(draw, lines, font, W: int, y: int, line_h: int,
                        fill=(255, 221, 0), last_fill=None, stroke: int = 4):
    for i, line in enumerate(lines):
        color = last_fill if (last_fill and i == len(lines) - 1) else fill
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (W - (bbox[2] - bbox[0])) // 2
        draw_outlined(draw, x, y, line, font, color, stroke)
        y += line_h


def make_thumbnail(text: str, tag: str, pal: dict, path: Path):
    W, H = 1280, 720
    img = base_canvas(W, H, pal)
    d = ImageDraw.Draw(img)

    # Upar wala tag (FULL GUIDE / QUICK TIP)
    tag_font = load_font(42)
    tb = d.textbbox((0, 0), tag, font=tag_font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.rounded_rectangle([60, 56, 60 + tw + 40, 56 + th + 26], radius=14, fill=pal["accent"])
    d.text((80, 56 + 10), tag, font=tag_font, fill=(20, 20, 30))

    # Main text, last line accent color mein
    font = load_font(124)
    lines = wrap_text(d, text.upper(), font, 900)
    line_h = 140
    block = line_h * len(lines)
    y = 150 + max(0, (520 - block) // 2)
    for i, line in enumerate(lines):
        color = pal["accent"] if i == len(lines) - 1 else (255, 255, 255)
        draw_outlined(d, 60, y, line, font, color, stroke=8)
        y += line_h

    # Right side accent stripe
    d.rectangle([W - 16, 0, W, H], fill=pal["accent"])
    img.save(path, "JPEG", quality=92)


def make_slide(path: Path, video_title: str, chapter_name: str, idx: int, total: int, pal: dict):
    W, H = 1920, 1080
    img = base_canvas(W, H, pal)
    d = ImageDraw.Draw(img)

    # Upar video ka title aur accent line
    f_top = load_font(38)
    top = video_title.upper()
    if len(top) > 80:
        top = top[:77] + "..."
    d.text((100, 60), top, font=f_top, fill=(230, 230, 240))
    d.rectangle([100, 118, 260, 124], fill=pal["accent"])

    # Part label
    f_part = load_font(46)
    d.text((100, 360), f"PART {idx:02d} / {total:02d}", font=f_part, fill=pal["accent"])

    # Chapter ka naam bade font mein
    f_big = load_font(104)
    lines = wrap_text(d, chapter_name, f_big, W - 200)
    y = 440
    for line in lines[:3]:
        draw_outlined(d, 100, y, line, f_big, (255, 255, 255), stroke=3)
        y += 130

    # Neeche progress bar
    bar_y = H - 90
    d.rectangle([100, bar_y, W - 100, bar_y + 12], fill=(70, 70, 95))
    filled = int((W - 200) * idx / total)
    d.rectangle([100, bar_y, 100 + filled, bar_y + 12], fill=pal["accent"])

    img.save(path, "JPEG", quality=92)


def make_short_frame(path: Path, tag: str, overlay_text: str, pal: dict):
    W, H = 1080, 1920
    img = base_canvas(W, H, pal)
    d = ImageDraw.Draw(img)

    tag_font = load_font(44)
    tb = d.textbbox((0, 0), tag, font=tag_font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    tx = (W - tw - 40) // 2
    d.rounded_rectangle([tx, 150, tx + tw + 40, 150 + th + 26], radius=14, fill=pal["accent"])
    d.text((tx + 20, 150 + 10), tag, font=tag_font, fill=(20, 20, 30))

    font = load_font(124)
    lines = wrap_text(d, overlay_text.upper(), font, W - 140)
    line_h = 150
    y = (H - line_h * len(lines)) // 2
    draw_lines_centered(d, lines, font, W, y, line_h,
                        fill=(255, 255, 255), last_fill=pal["accent"], stroke=8)

    f_bottom = load_font(46)
    label = "WATCH TILL THE END"
    bb = d.textbbox((0, 0), label, font=f_bottom)
    d.text(((W - (bb[2] - bb[0])) // 2, H - 220), label, font=f_bottom, fill=(255, 255, 255))
    d.rectangle([W // 2 - 120, H - 150, W // 2 + 120, H - 138], fill=pal["accent"])

    img.save(path, "JPEG", quality=92)


# ---------- 4. Video render (ffmpeg, motion + fade) ----------
def make_clip(img_path: Path, seconds: float, out: Path, W: int, H: int):
    """Ek still image se zoom + fade wali clip banata hai."""
    frames = max(1, int(seconds * FPS))
    fade = min(0.6, seconds / 3)
    vf = (
        f"zoompan=z='min(zoom+0.0008,1.12)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H},"
        f"fade=t=in:st=0:d={fade:.2f},"
        f"fade=t=out:st={max(0.0, seconds - fade):.2f}:d={fade:.2f},"
        "format=yuv420p"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(img_path),
            "-vf", vf,
            "-frames:v", str(frames),
            "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            str(out),
        ],
        check=True,
    )


def assemble(clips, audio: Path, out_path: Path, tmp_dir: Path, max_seconds=None):
    """Saari clips jod kar audio ke saath final MP4 banata hai."""
    list_file = tmp_dir / "clips.txt"
    list_file.write_text(
        "\n".join(f"file '{c.resolve().as_posix()}'" for c in clips) + "\n",
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
    ]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True)


# ---------- 5. Files save karna ----------
def make_folder(kind: str, title: str) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = OUTPUT / f"{kind}_{stamp}_{slugify(title)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_long(data: dict) -> Path:
    folder = make_folder("LONG", data["title"])
    pal = pick_palette(data["title"])
    (folder / "script.txt").write_text(data["narration"], encoding="utf-8")

    print("   Voiceover bana raha hoon...")
    audio = folder / "voice.mp3"
    make_voice(data["narration"], audio)

    print("   Thumbnail bana raha hoon...")
    make_thumbnail(data["thumbnail_text"], "FULL GUIDE", pal, folder / "thumbnail.jpg")

    print("   Long video render kar raha hoon...")
    names = [c["name"] for c in data["chapters"] if c.get("name")] or [data["title"]]
    total = audio_seconds(audio)
    per = total / len(names)

    tmp = folder / "_tmp"
    tmp.mkdir()
    clips = []
    for i, name in enumerate(names):
        img = tmp / f"slide_{i + 1:02d}.jpg"
        make_slide(img, data["title"], name, i + 1, len(names), pal)
        clip = tmp / f"clip_{i + 1:02d}.mp4"
        make_clip(img, per, clip, 1920, 1080)
        clips.append(clip)

    assemble(clips, audio, folder / "video.mp4", tmp)
    shutil.rmtree(tmp)

    chapters_out = [{"time": fmt_time(i * per), "name": n} for i, n in enumerate(names)]

    # YouTube chapters ke liye description mein timestamps (pehla hamesha 0:00)
    chapter_lines = "\n".join(f"{c['time']} {c['name']}" for c in chapters_out)
    description = data["description"] + "\n\nChapters:\n" + chapter_lines

    meta = {
        "title": data["title"],
        "thumbnail_text": data["thumbnail_text"],
        "description": description,
        "tags": data["tags"],
        "chapters": chapters_out,
    }
    (folder / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return folder


def save_short(data: dict, n: int) -> Path:
    folder = make_folder(f"SHORT{n}", data["title"])
    pal = pick_palette(data["title"] + str(n))
    (folder / "script.txt").write_text(data["narration"], encoding="utf-8")

    print("   Short ki voice bana raha hoon...")
    audio = folder / "voice.mp3"
    make_voice(data["narration"], audio)

    print("   Short video render kar raha hoon...")
    tmp = folder / "_tmp"
    tmp.mkdir()
    frame = tmp / "frame.jpg"
    make_short_frame(frame, "QUICK TIP", data["overlay_text"], pal)
    seconds = min(audio_seconds(audio), 58)
    clip = tmp / "clip.mp4"
    make_clip(frame, seconds, clip, 1080, 1920)
    assemble([clip], audio, folder / "short.mp4", tmp, max_seconds=58)
    shutil.rmtree(tmp)

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
        print(f"[LONG {i + 1}/{config.LONG_VIDEOS_PER_RUN}] Script likh raha hoon (chapter by chapter)...")
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
