import asyncio
import datetime
import hashlib
import json
import math
import os
import random
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
HISTORY = ROOT / "motion_history.json"
HINT_FILE = ROOT / "topic_hint.json"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
HINT_CHANCE = 0.6
WEBSITE_URL = "jatinsinghgodha91-cmd.github.io/invoice-tool"
WEBSITE_NAME = "our free invoice tool"

W, H = 1080, 1920
FPS = 30
MAX_SECONDS = 58
VOICE_SPEED = 1.08

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
RED = (232, 38, 38)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_fonts = {}

SYSTEM_PROMPT = """You write short, clear, accurate YouTube Shorts tutorial scripts for a US audience.
Narration is read aloud by a text-to-speech voice: spoken words only.
Never invent prices, features or statistics. Always return valid JSON only."""


def font(size):
    if size not in _fonts:
        path = None
        for p in FONT_CANDIDATES:
            if Path(p).exists():
                path = p
                break
        if path:
            _fonts[size] = ImageFont.truetype(path, size)
        else:
            _fonts[size] = ImageFont.load_default(size=size)
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
    return s[:50] or "short"


def make_bg(pal):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    c1, c2 = pal["bg1"], pal["bg2"]
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([int(W * 0.55), int(-H * 0.12), int(W * 1.35), int(H * 0.22)], fill=(*pal["accent"], 40))
    od.ellipse([int(-W * 0.4), int(H * 0.72), int(W * 0.45), int(H * 1.18)], fill=(255, 255, 255, 22))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


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


def draw_mockup(d, x, y, w, h, pal, label, progress):
    d.rounded_rectangle([x, y, x + w, y + h], radius=22, fill=(246, 248, 252), outline=(190, 198, 216), width=3)
    d.rounded_rectangle([x, y, x + w, y + 52], radius=22, fill=(222, 228, 240))
    d.rectangle([x, y + 30, x + w, y + 52], fill=(222, 228, 240))
    for i, c in enumerate([(255, 95, 87), (255, 189, 46), (40, 201, 64)]):
        d.ellipse([x + 22 + i * 26, y + 17, x + 38 + i * 26, y + 33], fill=c)
    rows = 4
    spacing = (h - 180) // rows
    for i in range(rows):
        appear = ease(progress * rows - i)
        if appear <= 0:
            continue
        ry = y + 90 + i * spacing
        bar_w = int((w - 80) * appear)
        d.rounded_rectangle([x + 40, ry, x + 40 + bar_w, ry + 26], radius=13, fill=(196, 204, 222))
    by = y + h - 104
    d.rounded_rectangle([x + 40, by, x + w - 40, by + 66], radius=18, fill=pal["accent"])
    f = font(36)
    text = str(label).upper()[:18]
    tw, th = text_size(d, text, f)
    d.text((x + (w - tw) // 2, by + (66 - th) // 2 - 4), text, font=f, fill=DARK)


def draw_header(d, pal, progress):
    label = "AI TOOLS"
    f = font(44)
    tw, th = text_size(d, label, f)
    d.rounded_rectangle([60, 70, 60 + tw + 40, 70 + th + 24], radius=14, fill=pal["accent"])
    d.text((80, 82), label, font=f, fill=DARK)
    d.rectangle([0, H - 14, W, H], fill=TRACK)
    d.rectangle([0, H - 14, int(W * progress), H], fill=pal["accent"])


def draw_caption(d, narration, t, dur):
    words = str(narration).split()
    if not words:
        return
    idx = min(len(words) - 1, int(t / max(dur, 0.1) * len(words)))
    start = (idx // 5) * 5
    group = " ".join(words[start:start + 5])
    f = font(54)
    lines = wrap(d, group, f, W - 220)[:2]
    line_h = text_size(d, "Ag", f)[1] + 14
    box_w = max(text_size(d, line, f)[0] for line in lines) + 70
    box_h = line_h * len(lines) + 22
    bottom = H - 420
    top = bottom - box_h
    left = (W - box_w) // 2
    d.rounded_rectangle([left, top, left + box_w, top + box_h], radius=16, fill=DARK)
    y = top + 11
    for line in lines:
        tw, _ = text_size(d, line, f)
        d.text(((W - tw) // 2, y), line, font=f, fill=WHITE)
        y += line_h


def scene_hook(d, sc, t, pal):
    p = ease(t / 0.8)
    pulse = 1 + 0.05 * math.sin(t * 5)
    r = int(150 * pulse * p)
    if r > 5:
        icon_bolt(d, W // 2, 500, r, pal["accent"])
    f = font(120)
    y = 800 + int((1 - p) * 60)
    for line in wrap(d, sc["title"].upper(), f, W - 160):
        center(d, line, f, y, WHITE, stroke=8)
        y += 132
    fb = font(56)
    y += 20
    for line in wrap(d, sc["body"], fb, W - 220):
        center(d, line, fb, y, GREY)
        y += 74


def scene_step(d, sc, t, pal):
    p = ease(t / 0.7)
    off = int((1 - p) * 80)
    center(d, sc["number"], font(200), 120 + off, pal["accent"], stroke=6)
    draw_mockup(d, 90, 420 + off, 900, 600, pal, sc.get("ui_label", "NEXT"), ease((t - 0.3) / 1.2))
    ft = font(80)
    y = 1100 + off
    for line in wrap(d, sc["title"].upper(), ft, W - 160)[:2]:
        center(d, line, ft, y, WHITE, stroke=5)
        y += 96
    fb = font(46)
    y += 14
    for line in wrap(d, sc["body"], fb, W - 240)[:3]:
        center(d, line, fb, y, GREY)
        y += 60


def scene_outro(d, sc, t, pal):
    p = ease(t / 0.8)
    center(d, "FOLLOW FOR MORE", font(88), 430 + int((1 - p) * 50), pal["accent"], stroke=6)
    y = 560
    for line in wrap(d, sc["body"], font(50), W - 260)[:3]:
        center(d, line, font(50), y, GREY)
        y += 68
    if t > 1.0:
        center(d, "Try it free at", font(44), 800, GREY)
        url_font = font(40)
        url_w, _ = text_size(d, WEBSITE_URL, url_font)
        while url_w > W - 120 and url_font.size > 20:
            url_font = font(url_font.size - 2)
            url_w, _ = text_size(d, WEBSITE_URL, url_font)
        center(d, WEBSITE_URL, url_font, 860, pal["accent"], stroke=3)
    if t > 1.8:
        x0 = (W - 480) // 2
        d.rounded_rectangle([x0, 1200, x0 + 480, 1290], radius=45, fill=RED)
        f = font(48)
        w, h = text_size(d, "SUBSCRIBE", f)
        d.text((x0 + (480 - w) // 2, 1200 + (90 - h) // 2 - 6), "SUBSCRIBE", font=f, fill=WHITE)


SCENE_FN = {"hook": scene_hook, "step": scene_step, "outro": scene_outro}


def load_hint():
    if HINT_FILE.exists():
        try:
            data = json.loads(HINT_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def hint_text_for_prompt():
    hint = load_hint()
    top = [str(t) for t in hint.get("top_titles", []) if str(t).strip()]
    suggested = [str(t) for t in hint.get("suggested_topics", []) if str(t).strip()]
    parts = []
    if top:
        lines = "\n".join(f"- {t}" for t in top[:5])
        parts.append(
            "These titles performed best on this channel recently. Learn the style and topic area, "
            "but do NOT copy them:\n" + lines
        )
    if suggested and random.random() < HINT_CHANCE:
        parts.append(f"Preferred topic direction for this video: {random.choice(suggested)}")
    return "\n\n".join(parts)


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
        try:
            r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=240)
        except requests.RequestException as e:
            print(f"   Groq connection error ({type(e).__name__}), dobara try kar raha hoon... ({attempt})")
            time.sleep(20)
            continue
        if r.status_code in (429, 500, 502, 503, 504, 522, 524):
            m = re.search(r"try again in ([\d.]+)s", r.text)
            wait = min((float(m.group(1)) if m else 20.0) + 3, 90)
            print(f"   Groq {r.status_code}, {wait:.0f} sec ruk kar dobara try kar raha hoon... ({attempt})")
            time.sleep(wait)
            continue
        if r.status_code == 400 and "json_validate_failed" in r.text:
            print(f"   Groq ka JSON kharab tha, dobara try kar raha hoon... ({attempt})")
            continue
        if r.status_code != 200:
            raise SystemExit(f"Groq error {r.status_code}: {r.text[:300]}")
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, KeyError, IndexError):
            print(f"   JSON parse nahi hua, dobara try kar raha hoon... ({attempt})")
    raise SystemExit("Groq 7 tries mein bhi valid JSON nahi de paya.")


def valid_scenes(scenes):
    if [s.get("type") for s in scenes] != ["hook", "step", "step", "step", "outro"]:
        return False
    for s in scenes:
        if not str(s.get("body", "")).strip() or not str(s.get("narration", "")).strip():
            return False
        if s["type"] != "outro" and not str(s.get("title", "")).strip():
            return False
    return True


def build_script(history):
    past = history["titles"][-100:]
    avoid = "\n".join(f"- {t}" for t in past) or "- (none yet)"
    prompt = (
        f"Niche: {config.NICHE}\n"
        "Language: English (US audience)\n\n"
        "Make ONE vertical YouTube Shorts TUTORIAL script, about 50 seconds when spoken. "
        "Total spoken words across all narration: 120 to 135.\n\n"
        "Topic: one specific, practical AI tool or AI workflow that saves a freelancer or small business "
        "time or money this week. It must be clearly different from these past titles:\n"
        f"{avoid}\n\n"
        "Rules:\n"
        "- Use only well-known tools. Never invent prices, features or statistics. "
        "For pricing, say 'check their pricing page'.\n"
        "- Each step must be one concrete action the viewer can do today, shown as a real screen.\n"
        "- Narration is spoken words only. No emojis, no stage directions.\n"
        "- For step scenes, icon must be one of: bolt, check, clock, dollar, gear, star, arrow.\n"
        "- For step scenes, ui_label is the 1 to 2 word text on the button the viewer clicks, "
        "all caps (for example: CREATE, SAVE, SEND, DOWNLOAD, CONNECT).\n"
        "- Hook title: max 6 words. Step titles: max 4 words.\n"
        "- Body: max 12 words for hook, max 14 words for each step, max 16 words for outro.\n"
        f"- The outro narration must end with a line that tells viewers to try {WEBSITE_NAME} "
        "for free, for example: 'You can try a free invoice tool too, link in the description.'\n\n"
        "Return JSON with exactly these keys: title, description, tags, scenes.\n"
        "title: a clickable YouTube title under 60 characters, no hashtags. Start with a benefit or a number "
        "(for example: 'Save 2 Hours on Proposals with AI'). Use strong words like Free, Instant, Save, Fast.\n"
        "description: 2 short sentences, then 3 relevant hashtags.\n"
        "tags: a list of 8 to 12 tags.\n"
        "scenes: a list of exactly 5 objects in this order: hook, step, step, step, outro.\n"
        "Each scene object has: type, title, body, narration. Step scenes also have: number (01, 02, 03), "
        "icon and ui_label.\n"
        "The outro has type, body and narration (title can be empty)."
    )
    hint = hint_text_for_prompt()
    if hint:
        prompt += "\n\n" + hint
    for attempt in range(1, 4):
        data = groq_json(prompt)
        scenes = data.get("scenes") or []
        words = sum(len(str(s.get("narration", "")).split()) for s in scenes)
        if data.get("title") and valid_scenes(scenes) and 100 <= words <= 170:
            for s in scenes:
                if s["type"] == "step":
                    s["number"] = str(s.get("number") or "01")
                    if s.get("icon") not in ICONS:
                        s["icon"] = "check"
                    s["ui_label"] = str(s.get("ui_label") or "NEXT")[:18]
            return data
        print(f"   Script format sahi nahi tha (words: {words}), dobara try kar raha hoon... ({attempt})")
    raise SystemExit("Motion script 3 tries mein bhi sahi nahi bana")


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
