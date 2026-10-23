import json
import os
from datetime import date, timedelta
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config

ROOT = Path(__file__).resolve().parent
HINT_FILE = ROOT / "topic_hint.json"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def get_creds():
    return Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=SCOPES,
    )


def fetch_top_videos():
    creds = get_creds()
    analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    end = date.today()
    start = end - timedelta(days=28)
    resp = analytics.reports().query(
        ids="channel==MINE",
        startDate=start.isoformat(),
        endDate=end.isoformat(),
        metrics="views,estimatedMinutesWatched,averageViewDuration",
        dimensions="video",
        sort="-views",
        maxResults=10,
    ).execute()
    rows = resp.get("rows", [])
    return [
        {"video_id": r[0], "views": r[1], "minutes": r[2], "avg_seconds": r[3]}
        for r in rows
    ]


def fetch_titles(video_ids):
    if not video_ids:
        return {}
    creds = get_creds()
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    titles = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        resp = yt.videos().list(part="snippet", id=",".join(chunk)).execute()
        for item in resp.get("items", []):
            titles[item["id"]] = item["snippet"]["title"]
    return titles


def ask_groq(winners):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    lines = "\n".join(
        f"- {w['title']} (views: {w['views']}, avg watch seconds: {round(w['avg_seconds'])})"
        for w in winners
    )
    prompt = (
        "These are the best performing videos of a YouTube channel about AI tools for freelancers "
        "and small businesses, from the last 28 days:\n"
        f"{lines}\n\n"
        "Suggest 3 new video topics that are close to what worked, but not copies of these titles. "
        "Use only well-known tools and never invent statistics or prices.\n"
        "Return JSON with exactly one key: \"topics\" (a list of 3 short topic strings)."
    )
    payload = {
        "model": config.GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=120,
    )
    if r.status_code != 200:
        print(f"Groq error {r.status_code}, hint skip")
        return None
    return json.loads(r.json()["choices"][0]["message"]["content"]).get("topics", [])


def main():
    try:
        top = fetch_top_videos()
    except Exception as e:
        print(f"Analytics fetch fail: {e}")
        top = []

    titles = fetch_titles([t["video_id"] for t in top])
    for t in top:
        t["title"] = titles.get(t["video_id"], "")

    topics = ask_groq(top[:3]) if top else None

    hint = {
        "top_titles": [t["title"] for t in top[:5]],
        "suggested_topics": topics or [],
        "generated_on": date.today().isoformat(),
    }
    HINT_FILE.write_text(json.dumps(hint, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Hint saved: {len(hint['top_titles'])} top titles, {len(hint['suggested_topics'])} topics")


if __name__ == "__main__":
    main()
