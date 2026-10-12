# ===== यहाँ से पूरा system control होता है =====

# Channel का niche (topic)
NICHE = "AI tools that save time and help freelancers and small businesses make more money"

# Script की भाषा
LANGUAGE = "English"

# हर video की लगभग length (minutes में)
TARGET_MINUTES = 9

# एक run में कितनी videos बनें
VIDEOS_PER_RUN = 2

# Groq का model (जो अभी चल रहा है)
GROQ_MODEL = "openai/gpt-oss-120b"

# Edge TTS की voice (GitHub पर block हो तो gTTS use होगा)
VOICE = "en-US-AndrewNeural"

# Fallback voice की speed (1.0 = normal, 1.12 = 12% तेज़)
VOICE_SPEED = 1.12

# Output और history files के नाम
OUTPUT_DIR = "output"
HISTORY_FILE = "history.json"
