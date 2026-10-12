# ===== सब settings यहीं से control होती हैं =====

# Channel का niche (topic)
NICHE = "AI tools that save time and help freelancers and small businesses make more money"

# Script की भाषा
LANGUAGE = "English"

# Long video: कितने chapters (हर chapter = एक slide + एक हिस्सा script)
LONG_CHAPTERS = 8

# हर chapter में लगभग कितने words (8 × 200 = 1600 words ≈ 9 से 10 minute)
LONG_WORDS_PER_CHAPTER = 200

# हर run में कितनी long videos
LONG_VIDEOS_PER_RUN = 1

# हर run में कितने Shorts
SHORTS_PER_RUN = 2

# Shorts की script में लगभग कितने words (130 words ≈ 45 सेकंड)
SHORT_WORDS = 130

# Groq का model
GROQ_MODEL = "openai/gpt-oss-120b"

# Edge TTS की voice (GitHub पर block हो तो gTTS use होगा)
VOICE = "en-US-AndrewNeural"

# Fallback voice की speed (1.12 = 12% तेज़)
VOICE_SPEED = 1.12

# Output और history files के नाम
OUTPUT_DIR = "output"
HISTORY_FILE = "history.json"
