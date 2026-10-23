import datetime
import json
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import config

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / config.OUTPUT_DIR
LOG_FILE = ROOT / "video_log.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


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


def limit_tags(tags):
    out, total = [], 0
    for t in tags:
        t = str(t).strip()[:30]
        if not t:
            continue
        if total + len(t) + 1 > 480:
            break
        out.append(t)
        total += len(t) + 1
    return out


def upload_video(yt, video_path: Path, meta: dict) -> str:
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta["description"][:4900],
            "tags": limit_tags(meta.get("tags", [])),
            "categoryId": config.YT_CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": config.YT_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
    )
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   upload {int(status.progress() * 100)}%")
    return response["id"]


def set_thumbnail(yt, video_id: str, thumb_path: Path):
    yt.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(thumb_path), mimetype="image/jpeg"),
    ).execute()


def load_log():
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save_log(entries):
    LOG_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    log = load_log()
    save_log(log)

    if not OUTPUT.exists():
        print("output folder nahi mila, upload skip")
        return

    yt = get_youtube()
    done, failed = 0, 0

    for folder in sorted(OUTPUT.iterdir()):
        if not folder.is_dir() or (folder / "uploaded.json").exists():
            continue
        meta_file = folder / "metadata.json"
        if not meta_file.exists():
            continue

        is_long = folder.name.startswith("LONG")
        video = folder / ("video.mp4" if is_long else "short.mp4")
        if not video.exists():
            continue

        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        print(f"Upload ho raha hai: {folder.name}")
        try:
            vid = upload_video(yt, video, meta)
        except HttpError as e:
            print(f"   ❌ Upload fail: {e}")
            failed += 1
            continue

        if is_long and (folder / "thumbnail.jpg").exists():
            try:
                set_thumbnail(yt, vid, folder / "thumbnail.jpg")
                print("   Thumbnail laga diya")
            except HttpError as e:
                print(f"   Thumbnail skip: {e}")

        url = f"https://youtu.be/{vid}"
        (folder / "uploaded.json").write_text(
            json.dumps({"video_id": vid, "url": url}, indent=2), encoding="utf-8"
        )

        log.append({
            "video_id": vid,
            "url": url,
            "kind": folder.name.split("_")[0],
            "title": meta.get("title", ""),
            "folder": folder.name,
            "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        })
        save_log(log)

        print(f"   ✅ {url}")
        done += 1

    print(f"Upload complete. Successful: {done}, Failed: {failed}")
    if failed and not done:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
