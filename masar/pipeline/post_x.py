"""
Post today's Masar update to X (Twitter), automatically, once a day.

Reads <site>/social.json (written by export) and posts the post marked
"today". Does nothing until these GitHub secrets are added:
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
(from developer.x.com: an app with "Read and write" permission).

* At most one post per day: the date of the last post is kept in
  data/social/posted.json, so re-running the workflow never double-posts.
* X_DRY_RUN=1 prints the post instead of sending it.

    python -m masar.pipeline.post_x
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from masar import storage

API = "https://api.x.com/2/tweets"
MEDIA_API = "https://api.x.com/2/media/upload"
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def site_dir() -> Path:
    return Path(os.environ.get("MASAR_SITE_DIR", "site")).resolve()


def pick(social: dict) -> dict | None:
    return next((p for p in social.get("posts", []) if p["id"] == social.get("today")), None)


def make_image(post: dict, log=print) -> Path | None:
    """The post's card as a PNG (None if it can't be drawn: the post goes out as text)."""
    if not post.get("card"):
        return None
    try:
        from masar.pipeline import social_card
        d = storage.utc_now()
        target = site_dir() / "social" / f"{post['id']}.png"
        return social_card.render(post["card"], target, f"{d.day} {MONTHS_AR[d.month - 1]} {d.year}")
    except Exception as e:
        log(f"[post_x] image failed, posting text only: {str(e)[:150]}")
        return None


def _session():
    from requests_oauthlib import OAuth1Session   # only needed when really posting
    k = {name: os.environ[name].strip() for name in KEYS}
    return OAuth1Session(k["X_API_KEY"], client_secret=k["X_API_SECRET"],
                         resource_owner_key=k["X_ACCESS_TOKEN"], resource_owner_secret=k["X_ACCESS_SECRET"])


def upload(session, image: Path) -> str:
    with open(image, "rb") as fh:
        r = session.post(MEDIA_API, files={"media": (image.name, fh, "image/png")},
                         data={"media_category": "tweet_image", "media_type": "image/png"}, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"media upload HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    return str((data.get("data") or {}).get("id") or data.get("media_id_string") or data.get("media_id"))


def send(text: str, image: Path | None = None, log=print) -> str:
    session = _session()
    body = {"text": text}
    if image:
        try:
            body["media"] = {"media_ids": [upload(session, image)]}
        except Exception as e:      # a picture problem must not stop the post
            log(f"[post_x] {e}; posting text only.")
    r = session.post(API, json=body, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"X answered HTTP {r.status_code}: {r.text[:300]}")
    return r.json().get("data", {}).get("id", "")


def run(log=print) -> int:
    dry = os.environ.get("X_DRY_RUN") == "1"
    if not dry and not all(os.environ.get(k, "").strip() for k in KEYS):
        log("[post_x] no X keys set: skipped.")
        return 0
    social_file = site_dir() / "social.json"
    if not social_file.exists():
        log("[post_x] social.json not found (export did not run): skipped.")
        return 0
    social = json.loads(social_file.read_text(encoding="utf-8"))
    post = pick(social)
    if not post:
        log("[post_x] nothing to post today.")
        return 0
    state_file = storage.path("social", "posted.json")
    state = storage.read_json(state_file, default={}) or {}
    today = storage.iso()[:10]
    if state.get("last_date") == today and not dry:
        log(f"[post_x] already posted today ({state.get('last_id')}): skipped.")
        return 0
    image = make_image(post, log)
    if dry:
        log(f"[post_x] DRY RUN ({post['id']}, image: {image}):\n{post['text']}")
        return 0
    tweet_id = send(post["text"], image, log)
    history = (state.get("history") or [])[-60:] + [{"date": today, "kind": post["id"], "tweet_id": tweet_id}]
    storage.write_json_atomic(state_file, {"last_date": today, "last_id": tweet_id, "history": history})
    log(f"[post_x] posted {post['id']}: https://x.com/i/status/{tweet_id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as e:          # a failed post must never break the daily run
        print(f"[post_x] failed: {e}")
        sys.exit(1)
