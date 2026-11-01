import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config

ROOT = Path(__file__).resolve().parent
HINT_FILE = ROOT / "topic_hint.json"
COMMENTS_DIR = ROOT / "comments"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube",
]
COMMENT_DAYS = 56
MAX_COMMENTS = 300
STOP = set(
    "that this with your from have what when which where will would could should about into "
    "than then them they their there these those just more most make using used very also "
    "does dont cant how are was were been being its the and for not you any all out get got can".split()
)


def get_creds():
    return Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=SCOPES,
    )


# ---------- Analytics: top videos ----------
def fetch_top_videos():
    analytics = build("youtubeAnalytics", "v2", credentials=get_creds(), cache_discovery=False)
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
    return [
        {"video_id": r[0], "views": r[1], "minutes": r[2], "avg_seconds": r[3]}
        for r in resp.get("rows", [])
    ]


def fetch_titles(video_ids):
    if not video_ids:
        return {}
    yt = build("youtube", "v3", credentials=get_creds(), cache_discovery=False)
    titles = {}
    for i in range(0, len(video_ids), 50):
        resp = yt.videos().list(part="snippet", id=",".join(video_ids[i:i + 50])).execute()
        for item in resp.get("items", []):
            titles[item["id"]] = item["snippet"]["title"]
    return titles


# ---------- Groq helpers ----------
def groq_topics(prompt):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return []
    payload = {
        "model": config.GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=120)
    if r.status_code != 200:
        print(f"Groq error {r.status_code}, is step ka topic skip")
        return []
    try:
        return json.loads(r.json()["choices"][0]["message"]["content"]).get("topics", [])
    except (json.JSONDecodeError, KeyError, IndexError):
        return []


def topics_from_analytics(winners):
    if not winners:
        return []
    lines = "\n".join(
        f"- {w['title']} (views: {w['views']}, avg watch seconds: {round(w['avg_seconds'])})"
        for w in winners
    )
    prompt = (
        "These are the best performing videos of a YouTube channel about AI tools for freelancers "
        "and small businesses, from the last 28 days:\n" + lines + "\n\n"
        "Suggest 3 new video topics that are close to what worked, but not copies of these titles. "
        "Use only well-known tools and never invent statistics or prices.\n"
        'Return JSON with exactly one key: "topics" (a list of 3 short topic strings).'
    )
    return groq_topics(prompt)


# ---------- Comments: fetch, save weekly, find questions ----------
def fetch_recent_comments(yt):
    channel = yt.channels().list(part="id", mine=True).execute()
    items = channel.get("items", [])
    if not items:
        raise RuntimeError("channel nahi mila")
    channel_id = items[0]["id"]
    out, token = [], None
    while len(out) < MAX_COMMENTS:
        resp = yt.commentThreads().list(
            part="snippet",
            allThreadsRelatedToChannelId=channel_id,
            maxResults=100,
            order="time",
            textFormat="plainText",
            pageToken=token,
        ).execute()
        for it in resp.get("items", []):
            top = it["snippet"]["topLevelComment"]
            sn = top["snippet"]
            out.append({
                "id": top["id"],
                "date": sn.get("publishedAt", "")[:10],
                "text": sn.get("textOriginal", "").strip()[:300],
            })
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def week_key(day_str):
    try:
        d = datetime.strptime(day_str, "%Y-%m-%d").date()
    except ValueError:
        d = date.today()
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def save_comments(fresh):
    COMMENTS_DIR.mkdir(exist_ok=True)
    groups = {}
    for c in fresh:
        groups.setdefault(week_key(c["date"]), []).append(c)
    added = 0
    for key, items in groups.items():
        path = COMMENTS_DIR / f"{key}.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        seen = {e["id"] for e in existing}
        new = [c for c in items if c["id"] not in seen]
        if new:
            existing.extend(new)
            path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
            added += len(new)
    return added


def load_recent_comments(days):
    if not COMMENTS_DIR.exists():
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for p in COMMENTS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.extend(c for c in data if c.get("date", "") >= cutoff)
    return sorted(out, key=lambda c: c.get("date", ""), reverse=True)


def is_question(text):
    t = text.strip().lower()
    return "?" in t or bool(re.match(r"^(how|what|why|which|can|does|do|is|are|should|where|when)\b", t))


def question_list(comments, limit=30):
    seen, out = set(), []
    for c in comments:
        t = c["text"]
        key = re.sub(r"\W+", " ", t.lower()).strip()
        if is_question(t) and key not in seen:
            seen.add(key)
            out.append(t)
            if len(out) >= limit:
                break
    return out


def repeated_keywords(questions, top=8):
    counts = Counter()
    for q in questions:
        for w in set(re.findall(r"[a-z]{4,}", q.lower())):
            if w not in STOP:
                counts[w] += 1
    return [w for w, n in counts.most_common(top) if n >= 2]


def topics_from_audience(questions, keywords):
    if not questions:
        return []
    qlines = "\n".join(f"- {q}" for q in questions[:15])
    prompt = (
        "These are real questions viewers asked on a YouTube channel about AI tools for freelancers "
        "and small businesses:\n" + qlines + "\n\n"
        "Repeated keywords: " + (", ".join(keywords) if keywords else "none") + "\n\n"
        "Suggest 3 video topics that answer the most repeated questions. Each must be a specific, "
        "practical tutorial. Use only well-known tools and never invent statistics or prices.\n"
        'Return JSON with exactly one key: "topics" (a list of 3 short topic strings).'
    )
    return groq_topics(prompt)


# ---------- Main ----------
def main():
    top_titles, analytics_topics = [], []
    try:
        top = fetch_top_videos()
        titles = fetch_titles([t["video_id"] for t in top])
        for t in top:
            t["title"] = titles.get(t["video_id"], "")
        top_titles = [t["title"] for t in top[:5]]
        analytics_topics = topics_from_analytics(top[:3])
    except Exception as e:
        print(f"Analytics fetch fail: {e}")

    questions, keywords, audience_topics = [], [], []
    try:
        yt = build("youtube", "v3", credentials=get_creds(), cache_discovery=False)
        fresh = fetch_recent_comments(yt)
        added = save_comments(fresh)
        print(f"Comments: {len(fresh)} mile, {added} naye save hue")
        recent = load_recent_comments(COMMENT_DAYS)
        questions = question_list(recent)
        keywords = repeated_keywords(questions)
        audience_topics = topics_from_audience(questions, keywords)
    except Exception as e:
        print(f"Comments fetch fail: {e}")

    merged = []
    for t in audience_topics + analytics_topics:
        if t and t not in merged:
            merged.append(t)

    hint = {
        "top_titles": top_titles,
        "suggested_topics": merged[:6],
        "audience_questions": questions[:10],
        "repeated_keywords": keywords,
        "generated_on": date.today().isoformat(),
    }
    HINT_FILE.write_text(json.dumps(hint, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Hint saved: {len(top_titles)} top titles, {len(merged[:6])} topics, {len(questions)} sawal")


if __name__ == "__main__":
    main()
