import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
GRAPH = "https://graph.facebook.com/v21.0"
GRAPH_VIDEO = "https://graph-video.facebook.com/v21.0"
PAGE_ID = os.environ["FB_PAGE_ID"].strip()
USER_TOKEN = os.environ["FB_PAGE_TOKEN"].strip()
KIND = sys.argv[1] if len(sys.argv) > 1 else "short"


def get_page_token():
    r = requests.get(
        f"{GRAPH}/me/accounts",
        params={"fields": "id,name,access_token", "limit": 100, "access_token": USER_TOKEN},
        timeout=60,
    )
    if r.status_code != 200:
        raise SystemExit(f"me/accounts error: {r.text}")
    pages = r.json().get("data", [])
    print("Token ko ye Pages dikh rahi hain:", [(p.get("id"), p.get("name")) for p in pages])
    for p in pages:
        if str(p.get("id")) == PAGE_ID:
            return p["access_token"]
    raise SystemExit(
        "AI Freelancer Toolkit Page is token ko assign nahi hai. "
        "Business settings mein autopost ko Page ka access dein, phir naya token banayein."
    )


def find_video():
    if KIND == "short":
        prefix, video_name = "SHORT_MOTION_", "short.mp4"
    else:
        prefix, video_name = "LONG_MOTION_", "video.mp4"
    folders = sorted(p for p in OUTPUT.glob(prefix + "*") if (p / video_name).exists())
    if not folders:
        raise SystemExit(f"{prefix} folder ya {video_name} nahi mila")
    folder = folders[-1]
    return folder, folder / video_name


def main():
    page_token = get_page_token()
    folder, video = find_video()
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    title = str(meta.get("title", ""))[:100]
    desc = str(meta.get("description", "")).replace("#Shorts", "").strip()
    if KIND == "long":
        desc = desc[:4900]

    print("Facebook Page par video upload kar raha hoon...")
    with video.open("rb") as f:
        r = requests.post(
            f"{GRAPH_VIDEO}/{PAGE_ID}/videos",
            data={"title": title, "description": desc, "access_token": page_token},
            files={"source": f},
            timeout=900,
        )
    if r.status_code != 200:
        raise SystemExit(f"Facebook upload error: {r.text}")
    print(f"✅ Facebook par video upload ho gayi. ID: {r.json().get('id')}")


if __name__ == "__main__":
    main()
