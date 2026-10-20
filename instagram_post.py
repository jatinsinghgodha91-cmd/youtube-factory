import base64
import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
GRAPH = "https://graph.facebook.com/v21.0"

IG_USER_ID = os.environ["IG_USER_ID"]
IG_TOKEN = os.environ["IG_ACCESS_TOKEN"]
MEDIA_REPO = os.environ["MEDIA_REPO"]
MEDIA_TOKEN = os.environ["MEDIA_REPO_TOKEN"]
GH_API = f"https://api.github.com/repos/{MEDIA_REPO}/contents"
GH_HEADERS = {
    "Authorization": f"Bearer {MEDIA_TOKEN}",
    "Accept": "application/vnd.github+json",
}


def latest_short():
    folders = sorted(p for p in OUTPUT.glob("SHORT_MOTION_*") if (p / "short.mp4").exists())
    if not folders:
        raise SystemExit("SHORT_MOTION folder ya short.mp4 nahi mila")
    return folders[-1]


def caption_for(folder):
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    desc = meta.get("description", "").replace("#Shorts", "").strip()
    tags = " ".join("#" + t.replace(" ", "") for t in meta.get("tags", [])[:3])
    text = f"{meta.get('title', '')}\n\n{desc}\n\n{tags}".strip()
    return text[:2200]


def upload_to_media_repo(video_path, name):
    content = base64.b64encode(video_path.read_bytes()).decode()
    r = requests.put(
        f"{GH_API}/{name}",
        headers=GH_HEADERS,
        json={"message": f"add {name}", "content": content},
        timeout=300,
    )
    r.raise_for_status()
    return f"https://raw.githubusercontent.com/{MEDIA_REPO}/main/{name}"


def wait_public(url):
    for _ in range(20):
        r = requests.head(url, timeout=30, allow_redirects=True)
        if r.status_code == 200:
            return
        time.sleep(15)
    raise SystemExit("Video ka public link 5 minute mein bhi ready nahi hua")


def create_reel(video_url, caption):
    r = requests.post(
        f"{GRAPH}/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": IG_TOKEN,
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise SystemExit(f"Container error: {r.text}")
    return r.json()["id"]


def wait_ready(creation_id):
    for _ in range(60):
        r = requests.get(
            f"{GRAPH}/{creation_id}",
            params={"fields": "status_code", "access_token": IG_TOKEN},
            timeout=30,
        )
        code = r.json().get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise SystemExit(f"Instagram processing fail hua: {r.text}")
        time.sleep(10)
    raise SystemExit("Instagram processing 10 minute mein bhi finish nahi hui")


def publish(creation_id):
    r = requests.post(
        f"{GRAPH}/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": IG_TOKEN},
        timeout=60,
    )
    if r.status_code != 200:
        raise SystemExit(f"Publish error: {r.text}")
    return r.json()["id"]


def delete_from_media_repo(name):
    url = f"{GH_API}/{name}"
    r = requests.get(url, headers=GH_HEADERS, timeout=30)
    if r.status_code == 200:
        requests.delete(
            url,
            headers=GH_HEADERS,
            json={"message": f"remove {name}", "sha": r.json()["sha"]},
            timeout=30,
        )


def main():
    folder = latest_short()
    name = f"short_{int(time.time())}.mp4"
    print("Video public repo par upload kar raha hoon...")
    url = upload_to_media_repo(folder / "short.mp4", name)
    try:
        wait_public(url)
        print("Instagram par Reel bana raha hoon...")
        cid = create_reel(url, caption_for(folder))
        wait_ready(cid)
        mid = publish(cid)
        print(f"✅ Instagram par post ho gayi. Media id: {mid}")
    finally:
        delete_from_media_repo(name)


if __name__ == "__main__":
    main()
