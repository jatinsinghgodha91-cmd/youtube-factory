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
        path = next((p for p in
                     
