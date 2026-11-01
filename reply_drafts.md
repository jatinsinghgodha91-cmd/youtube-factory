import json
import os
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config

ROOT = Path(__file__).resolve().parent
COMMENTS_DIR = ROOT / "comments"
DONE_FILE = ROOT / "replied_ids.json"
DRAFT_FILE = ROOT / "reply_drafts.md"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SCOPES = ["https://www.googleapis.com/auth/youtube"]
MAX_DRAFTS = 20


def get_youtube():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def load_done():
    if DONE_FILE.exists():
        try:
            return set(json.loads(DONE_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            return set()
    return set()


def load_all_comments():
    out = []
    if not COMMENTS_DIR.exists():
        return out
    for p in sorted(COMMENTS_DIR.glob("*.json")):
        try:
            out.extend(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return sorted(out, key=lambda c: c.get("date", ""), reverse=True)


def draft_reply(text):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return ""
    prompt = (
        "A viewer left this comment on a YouTube channel about AI tools for freelancers and small businesses:\n"
        f'"{text}"\n\n'
        "Write a short, friendly reply in simple English (2 sentences max). "
        "If they asked a question, answer it briefly using only well-known tools and never invent "
        "statistics or prices. If it is spam or rude, reply with an empty string. "
        'Return JSON with exactly one key: "reply".'
    )
    payload = {
        "model": config.GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=120)
    if r.status_code != 200:
        return ""
    try:
        return str(json.loads(r.json()["choices"][0]["message"]["content"]).get("reply", "")).strip()
    except (json.JSONDecodeError, KeyError, IndexError):
        return ""


def main():
    done = load_done()
    todo = [c for c in load_all_comments() if c.get("id") not in done][:MAX_DRAFTS]
    if not todo:
        print("Naye comments nahi hain, draft skip")
        return

    lines = ["# Reply drafts", ""]
    new_done = set(done)
    for c in todo:
        reply = draft_reply(c.get("text", ""))
        lines.append(f"## {c.get('date', '')}")
        lines.append(f"**Comment:** {c.get('text', '')}")
        lines.append("")
        lines.append(f"**Draft reply:** {reply if reply else '(skip karo, spam ya bekar comment)'}")
        lines.append("")
        lines.append("---")
        lines.append("")
        new_done.add(c.get("id"))

    existing = DRAFT_FILE.read_text(encoding="utf-8") if DRAFT_FILE.exists() else ""
    header_end = existing.find("\n## ")
    body = existing[header_end:] if header_end != -1 else ""
    DRAFT_FILE.write_text("\n".join(lines) + body, encoding="utf-8")
    DONE_FILE.write_text(json.dumps(sorted(x for x in new_done if x), indent=2), encoding="utf-8")
    print(f"Reply drafts bane: {len(todo)}")


if __name__ == "__main__":
    main()
