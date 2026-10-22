import asyncio
import bisect
import datetime
import hashlib
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

import edge_tts
import requests
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

import config

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / config.OUTPUT_DIR
HISTORY = ROOT / "motion_long_history.json"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

W, H = 1920, 1080
FPS = 24
VOICE_SPEED = 1.08
CHAPTER_COUNT = 8

PALETTES = [
    {"bg1": (12, 24, 72), "bg2": (95, 30, 150), "accent": (255, 200, 0)},
    {"bg1": (0, 58, 66), "bg2": (0, 150, 132), "accent": (255, 110, 64)},
    {"bg1": (72, 0, 28), "bg2": (175, 26, 56), "accent": (255, 226, 120)},
    {"bg1": (4, 40, 26), "bg2": (22, 128, 78), "accent": (255, 214, 0)},
    {"bg1": (10, 10, 16), "bg2": (34, 40, 70), "accent": (0, 222, 255)},
]
WHITE = (255, 255, 255)
GREY = (208, 214, 228)
DARK = (10, 12, 24)
TRACK = (40, 46, 80)
CARD = (18, 26, 56)
CARD_EDGE = (60, 74, 118)
RED = (232, 38, 38)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_fonts = {}

SYSTEM_PROMPT = """You write clear, accurate YouTube video scripts for a US audience about AI tools for freelancers and small businesses.
Narration is read aloud by a text-to-speech voice: spoken words only, no stage directions, no emojis.
Never invent prices, features or statistics. Always return valid JSON only."""


# ---------- Basic helpers ----------
def font(size):
    if size not in _fonts:
        path = next((p for p in FONT_CANDIDATES if Path(p).exists()), None)
        _fonts[size] = ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)
    return _fonts[size]


def text_size(d, text, f):
    b = d.textbbox((0, 0), text, font=f)
    return b[2] - b[0], b[3] - b[1]


def wrap(d, text, f, max_w):
    lines, cur = [], ""
    for word in str(text).split():
        test = f"{cur} {word}".strip()
        if text_size(d, test, f)[0] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def word_count(text):
    return len(str(text).split())


def center(d, text, f, y, fill, stroke=0):
    w, _ = text_size(d, text, f)
    d.text(((W - w) // 2, y), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=DARK)


def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def palette_for(seed):
    idx = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % len(PALETTES)
    return PALETTES[idx]


def slugify(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:50] or "video"


def fmt_time(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def make_bg(pal, w=W, h=H):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    c1, c2 = pal["bg1"], pal["bg2"]
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([int(w * 0.72), int(-h * 0.35), int(w * 1.22), int(h * 0.40)], fill=(*pal["accent"], 40))
    od.ellipse([int(-w * 0.18), int(h * 0.62), int(w * 0.34), int(h * 1.25)], fill=(255, 255, 255, 22))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def card(d, x, y, w, h):
    d.rounded_rectangle([x, y, x + w, y + h], radius=26, fill=CARD, outline=CARD_EDGE, width=3)


# ---------- Icons ----------
def icon_check(d, cx, cy, s, c):
    d.line([(cx - s * 0.5, cy), (cx - s * 0.15, cy + s * 0.42), (cx + s * 0.5, cy - s * 0.38)],
           fill=c, width=max(4, int(s * 0.2)), joint="curve")


def icon_bolt(d, cx, cy, s, c):
    pts = [(cx + 0.15 * s, cy - s), (cx - 0.55 * s, cy + 0.12 * s), (cx - 0.02 * s, cy + 0.12 * s),
           (cx - 0.18 * s, cy + s), (cx + 0.55 * s, cy - 0.18 * s), (cx + 0.02 * s, cy - 0.18 * s)]
    d.polygon(pts, fill=c)


def icon_clock(d, cx, cy, s, c):
    w = max(4, int(s * 0.12))
    d.ellipse([cx - s, cy - s, cx + s, cy + s], outline=c, width=w)
    d.line([(cx, cy), (cx, cy - s * 0.6)], fill=c, width=w)
    d.line([(cx, cy), (cx + s * 0.45, cy)], fill=c, width=w)


def icon_dollar(d, cx, cy, s, c):
    d.ellipse([cx - s, cy - s, cx + s, cy + s], fill=c)
    f = font(int(s * 1.5))
    w, h = text_size(d, "$", f)
    d.text((cx - w // 2, cy - h // 2 - int(s * 0.08)), "$", font=f, fill=DARK)


def icon_gear(d, cx, cy, s, c):
    for i in range(8):
        a = i * math.pi / 4
        x, y = cx + s * 0.85 * math.cos(a), cy + s * 0.85 * math.sin(a)
        d.ellipse([x - s * 0.22, y - s * 0.22, x + s * 0.22, y + s * 0.22], fill=c)
    d.ellipse([cx - s * 0.8, cy - s * 0.8, cx + s * 0.8, cy + s * 0.8], fill=c)
    d.ellipse([cx - s * 0.35, cy - s * 0.35, cx + s * 0.35, cy + s * 0.35], fill=DARK)


def icon_star(d, cx, cy, s, c):
    pts = []
    for i in range(10):
        r = s if i % 2 == 0 else s * 0.45
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, fill=c)


def icon_arrow(d, cx, cy, s, c):
    d.polygon([(cx - s, cy - s * 0.35), (cx + s * 0.2, cy - s * 0.35), (cx + s * 0.2, cy - s * 0.8),
               (cx + s, cy), (cx + s * 0.2, cy + s * 0.8), (cx + s * 0.2, cy + s * 0.35),
               (cx - s, cy + s * 0.35)], fill=c)


ICONS = {"check": icon_check, "bolt": icon_bolt, "clock": icon_clock, "dollar": icon_dollar,
         "gear": icon_gear, "star": icon_star, "arrow": icon_arrow}


# ---------- Chrome, caption, scenes ----------
def draw_chrome(d, pal, progress):
    d.text((120, 60), "AI TOOLS", font=font(34), fill=pal["accent"])
    d.rectangle([0, H - 12, W, H], fill=TRACK)
    d.rectangle([0, H - 12, int(W * progress), H], fill=pal["accent"])


def draw_caption(d, narration, t, dur):
    words = str(narration).split()
    if not words:
        return
    idx = min(len(words) - 1, int(t / max(dur, 0.1) * len(words)))
    start = (idx // 6) * 6
    group = " ".join(words[start:start + 6])
    f = font(50)
    lines = wrap(d, group, f, W - 300)[:2]
    line_h = text_size(d, "Ag", f)[1] + 14
    box_w = max(text_size(d, line, f)[0] for line in lines) + 70
    box_h = line_h * len(lines) + 22
    bottom = H - 80
    top = bottom - box_h
    left = (W - box_w) // 2
    d.rounded_rectangle([left, top, left + box_w, top + box_h], radius=18, fill=DARK)
    y = top + 11
    for line in lines:
        tw, _ = text_size(d, line, f)
        d.text(((W - tw) // 2, y), line, font=f, fill=WHITE)
        y += line_h


def scene_chapter(d, sc, t, pal):
    p = ease(t / 0.8)
    off = int((1 - p) * 60)
    d.text((120, 300 + off), "CHAPTER", font=font(64), fill=GREY)
    d.text((120, 360 + off), sc["num"], font=font(240), fill=pal["accent"])
    y = 620 + off
    for line in wrap(d, sc["title"].upper(), font(90), 1700)[:2]:
        d.text((120, y), line, font=font(90), fill=WHITE)
        y += 104
    n, idx = sc["total"], sc["index"]
    for i in range(n):
        x = 1260 + i * 72
        col = pal["accent"] if i <= idx else TRACK
        d.ellipse([x - 16, 140 - 16, x + 16, 140 + 16], fill=col)


def scene_point(d, sc, t, pal):
    p = ease(t / 0.7)
    off = int((1 - p) * 70)
    d.text((120, 130), f"CHAPTER {sc['chapter']:02d}", font=font(40), fill=pal["accent"])
    card(d, 120, 230 + off, 1680, 600)
    ICONS.get(sc["icon"], icon_check)(d, 1560, 530 + off, 130, pal["accent"])
    y = 300 + off
    for line in wrap(d, sc["title"].upper(), font(84), 1100)[:2]:
        d.text((220, y), line, font=font(84), fill=WHITE)
        y += 100
    y += 20
    for line in wrap(d, sc["body"], font(46), 1100)[:3]:
        d.text((220, y), line, font=font(46), fill=GREY)
        y += 62


def scene_outro(d, sc, t, pal):
    p = ease(t / 0.8)
    center(d, "SAVE THIS", font(120), 260 + int((1 - p) * 40), pal["accent"], stroke=8)
    center(d, "Follow for more AI tools for freelancers", font(56), 430, GREY)
    center(d, "Check each tool's pricing page before you sign up", font(46), 510, GREY)
    if t > 1.8:
        x0 = (W - 520) // 2
        d.rounded_rectangle([x0, 740, x0 + 520, 850], radius=55, fill=RED)
        f = font(56)
        w, h = text_size(d, "SUBSCRIBE", f)
        d.text((x0 + (520 - w) // 2, 740 + (110 - h) // 2 - 6), "SUBSCRIBE", font=f, fill=WHITE)


SCENE_FN = {"chapter": scene_chapter, "point": scene_point, "outro": scene_outro}


# ---------- Groq ----------
def groq_json(user_prompt):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise SystemExit("GROQ_API_KEY set nahi hai")
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
        r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=240)
        if r.status_code == 429:
            m = re.search(r"try again in ([\d.]+)s", r.text)
            wait = min((float(m.group(1)) if m else 15.0) + 3, 90)
            print(f"   Groq rate limit, {wait:.0f} sec ruk raha hoon... ({attempt})")
            time.sleep(wait)
            continue
        if r.status_code == 400 and "json_validate_failed" in r.text:
            print(f"   Groq ka JSON kharab tha, dobara try kar raha hoon... ({attempt})")
            continue
        if r.status_code != 200:
            raise SystemExit(f"Groq error {r.status_code}: {r.text}")
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, KeyError, IndexError):
            print(f"   JSON parse nahi hua, dobara try kar raha hoon... ({attempt})")
    raise SystemExit("Groq 7 tries mein bhi valid JSON nahi de paya.")


def build_outline(history):
    past = history["titles"][-100:]
    avoid = "\n".join(f"- {t}" for t in past) or "- (none yet)"
    prompt = f"""Niche: {config.NICHE}
Language: English (US audience)

Plan ONE long YouTube video of about 9 to 10 minutes with exactly {CHAPTER_COUNT} chapters.
It must be a practical guide to AI tools or AI workflows that save a freelancer or small business time or money.
The topic must be clearly different from these past titles:
{avoid}

Rules:
- Use only well-known tools. Never invent prices, features or statistics. For prices say "check their pricing page".
- Each chapter teaches one concrete thing the viewer can do this week.

Return JSON with exactly these keys:
"title": clickable YouTube title under 70 characters, no hashtags
"thumbnail_text": 3 to 5 words, all caps, a bold promise
"description": 150 to 200 words: a short summary, then a subscribe call to action, then 3 hashtags
"tags": a list of 15 to 20 tags
"chapters": a list of exactly {CHAPTER_COUNT} objects, each with "title" (max 5 words) and "point" (one sentence)"""
    for attempt in range(1, 4):
        data = groq_json(prompt)
        chapters = data.get("chapters") or []
        if (data.get("title") and data.get("thumbnail_text") and data.get("description")
                and data.get("tags") and len(chapters) == CHAPTER_COUNT
                and all(str(c.get("title", "")).strip() for c in chapters)):
            return data
        print(f"   Outline sahi nahi tha, dobara try kar raha hoon... ({attempt})")
    raise SystemExit("Outline 3 tries mein bhi sahi nahi bana")


def valid_chapter(ch, is_last):
    cn = str(ch.get("chapter_narration", "")).strip()
    if not 15 <= word_count(cn) <= 40:
        return False
    pts = ch.get("points") or []
    if len(pts) != 3:
        return False
    for p in pts:
        if not str(p.get("title", "")).strip() or not str(p.get("body", "")).strip():
            return False
        if not 40 <= word_count(p.get("narration", "")) <= 70:
            return False
    if is_last and not 20 <= word_count(ch.get("outro_narration", "")) <= 50:
        return False
    return True


def build_chapter(outline, idx, prev_text, is_last):
    titles = [c["title"] for c in outline["chapters"]]
    ch = outline["chapters"][idx]
    prompt = f"""Video title: {outline['title']}
All chapters in order: {' | '.join(titles)}
Niche: {config.NICHE}
Language: English (US audience)

You are writing CHAPTER {idx + 1} of {CHAPTER_COUNT}: "{ch['title']}".
This chapter teaches: {ch.get('point', '')}
Previous narration ended with: "...{prev_text[-300:]}"
Continue naturally. Do not repeat earlier points.

Return JSON with exactly these keys:
"chapter_narration": 20 to 30 words that introduce this chapter.
"points": exactly 3 objects. Each object has:
  "title": max 4 words
  "body": max 14 words, a short summary of the point
  "icon": one of bolt, check, clock, dollar, gear, star, arrow
  "narration": 50 to 60 words of spoken narration explaining the point, with one concrete action the viewer can do today
{'"outro_narration": 25 to 40 words. Summarize the key takeaways of the whole video and ask viewers to comment one question.' if is_last else ''}

Spoken words only. No headings, no emojis, no stage directions, no statistics."""
    for attempt in range(1, 4):
        data = groq_json(prompt)
        for p in data.get("points") or []:
            if p.get("icon") not in ICONS:
                p["icon"] = "check"
        if valid_chapter(data, is_last):
            return data
        print(f"   Chapter {idx + 1} ki length sahi nahi thi, dobara likh raha hoon... ({attempt})")
    raise SystemExit(f"Chapter {idx + 1} 3 tries mein bhi sahi nahi bana")


# ---------- Voice ----------
async def _tts(text, path):
    rate = f"+{round((VOICE_SPEED - 1) * 100)}%"
    await edge_tts.Communicate(text, config.VOICE, rate=rate).save(str(path))


def make_voice(text, path):
    try:
        asyncio.run(_tts(text, path))
    except Exception as e:
        print(f"   Edge TTS fail ({type(e).__name__}), gTTS use ho raha hai")
        raw = path.with_name("raw_" + path.name)
        gTTS(text=text, lang="en", tld="com").save(str(raw))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
             "-filter:a", f"atempo={VOICE_SPEED}", str(path)],
            check=True,
        )
        raw.unlink()


def audio_seconds(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# ---------- Render ----------
def render(scenes, durs, audio, pal, out_path):
    starts, acc = [], 0.0
    for dd in durs:
        starts.append(acc)
        acc += dd
    total = acc
    frames = int(total * FPS)
    bg = make_bg(pal)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    for f in range(frames):
        T = f / FPS
        i = bisect.bisect_right(starts, T) - 1
        i = max(0, min(i, len(scenes) - 1))
        t = T - starts[i]
        dur = durs[i]
        sc = scenes[i]

        img = bg.copy()
        d = ImageDraw.Draw(img)
        SCENE_FN[sc["type"]](d, sc, t, pal)
        draw_caption(d, sc["narration"], t, dur)
        draw_chrome(d, pal, T / total)

        k_ = min(1.0, t / 0.35, (dur - t) / 0.35)
        if k_ < 1.0:
            img = ImageEnhance.Brightness(img).enhance(max(0.0, k_))

        proc.stdin.write(img.tobytes())

    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise SystemExit("ffmpeg render fail hua")
    return starts


# ---------- Thumbnail ----------
def make_thumb(path, pal, text):
    w, h = 1280, 720
    img = make_bg(pal, w, h)
    d = ImageDraw.Draw(img)
    icon_bolt(d, 1020, 340, 190, pal["accent"])
    tf = font(40)
    tw, th = text_size(d, "AI TOOLS", tf)
    d.rounded_rectangle([60, 60, 60 + tw + 40, 60 + th + 26], radius=14, fill=RED)
    d.text((80, 70), "AI TOOLS", font=tf, fill=WHITE)
    f = font(120)
    lines = wrap(d, str(text).upper(), f, 900)
    y = 270 if len(lines) <= 2 else 200
    for i, line in enumerate(lines):
        color = pal["accent"] if i == len(lines) - 1 else WHITE
        d.text((60, y), line, font=f, fill=color, stroke_width=10, stroke_fill=DARK)
        y += 135
    img.save(path, "JPEG", quality=93)


# ---------- Main ----------
def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return {"titles": []}


def main():
    OUTPUT.mkdir(exist_ok=True)
    history = load_history()

    print("Outline bana raha hoon...")
    outline = build_outline(history)
    pal = palette_for(outline["title"])

    chapter_data = []
    prev_text = ""
    for i in range(CHAPTER_COUNT):
        is_last = i == CHAPTER_COUNT - 1
        print(f"Chapter {i + 1}/{CHAPTER_COUNT} likh raha hoon...")
        ch = build_chapter(outline, i, prev_text, is_last)
        chapter_data.append(ch)
        prev_text = str(ch["chapter_narration"]) + " " + " ".join(str(p["narration"]) for p in ch["points"])

    scenes = []
    n = len(chapter_data)
    for ci, (oc, cd) in enumerate(zip(outline["chapters"], chapter_data)):
        scenes.append({"type": "chapter", "index": ci, "total": n, "num": f"{ci + 1:02d}",
                       "title": str(oc["title"]), "narration": str(cd["chapter_narration"])})
        for p in cd["points"]:
            scenes.append({"type": "point", "chapter": ci + 1, "title": str(p["title"]),
                           "body": str(p["body"]), "icon": p.get("icon", "check"),
                           "narration": str(p["narration"])})
        if ci == n - 1:
            scenes.append({"type": "outro", "narration": str(cd["outro_narration"])})

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = OUTPUT / f"LONG_MOTION_{stamp}_{slugify(outline['title'])}"
    folder.mkdir(parents=True, exist_ok=True)

    full = " ".join(s["narration"] for s in scenes)
    (folder / "script.txt").write_text(full, encoding="utf-8")
    print(f"Total narration: {word_count(full)} words")

    print("Voice bana raha hoon...")
    audio = folder / "voice.mp3"
    make_voice(full, audio)
    total = audio_seconds(audio)

    counts = [max(1, word_count(s["narration"])) for s in scenes]
    durs = [total * c / sum(counts) for c in counts]

    print("Video render ho raha hai (lambi video hai, 15 se 25 minute lag sakte hain)...")
    starts = render(scenes, durs, audio, pal, folder / "video.mp4")

    print("Thumbnail bana raha hoon...")
    make_thumb(folder / "thumbnail.jpg", pal, outline["thumbnail_text"])

    chapter_times = []
    for idx, s in enumerate(scenes):
        if s["type"] == "chapter":
            chapter_times.append({"time": fmt_time(starts[idx]), "name": s["title"]})
    chapter_lines = "\n".join(f"{c['time']} {c['name']}" for c in chapter_times)

    desc = str(outline["description"]).strip()
    desc += "\n\nChapters:\n" + chapter_lines
    desc += "\n\nThis video is general information. Check each tool's own pricing page before you sign up."
    meta = {
        "title": str(outline["title"])[:100],
        "thumbnail_text": outline["thumbnail_text"],
        "description": desc,
        "tags": outline.get("tags", []),
        "chapters": chapter_times,
    }
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    history["titles"].append(outline["title"])
    HISTORY.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Ready: {folder}")


if __name__ == "__main__":
    main()
