import asyncio
import bisect
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
HISTORY = ROOT / "motion_long_history.json"
HINT_FILE = ROOT / "topic_hint.json"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

W, H = 1920, 1080
FPS = 24
VOICE_SPEED = 1.08
CHAPTER_COUNT = 8
HINT_CHANCE = 0.6

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
        r
