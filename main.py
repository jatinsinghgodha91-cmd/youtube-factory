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
