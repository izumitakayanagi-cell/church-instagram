"""東京ソフィア長老教会 Instagram 自動投稿

使い方:
  python main.py prepare            # 今日の投稿（画像・キャプション）を posts/日付/ に作成
  python main.py prepare --skip-ai  # AIを使わずサンプル文で作成（見た目の確認用）
  python main.py prepare --date 2026-11-21
  python main.py publish            # posts/日付/ の内容を Instagram に投稿
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
HISTORY = os.path.join(ROOT, "state", "history.json")


def load_yaml(name: str):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def load_history() -> list[dict]:
    if os.path.exists(HISTORY):
        with open(HISTORY, encoding="utf-8") as f:
            return json.load(f)
    return []


def pick_verse(verses: list[dict], history: list[dict]) -> dict:
    """最後に使った日が最も古い（または未使用の）聖句を選ぶ。"""
    last_used = {}
    for h in history:
        last_used[h["verse_ref"]] = h["date"]
    return min(verses, key=lambda v: last_used.get(v["ref"], ""))


def upcoming_events(events: list[dict], post_date: date, days: int = 14) -> list[dict]:
    out = []
    for e in events:
        d = e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"]))
        if post_date <= d <= post_date + timedelta(days=days):
            out.append({**e, "date": d.isoformat()})
    return out


def post_dir(d: date) -> str:
    return os.path.join(ROOT, "posts", d.isoformat())


def cmd_prepare(post_date: date, skip_ai: bool) -> None:
    from autopost import caption as cap
    from autopost import image

    config = load_yaml("config.yaml")
    theme_key = WEEKDAYS[post_date.weekday()]
    theme = config["themes"].get(theme_key)
    if theme is None:
        print(f"{post_date} ({theme_key}) は投稿日として設定されていません。スキップします。")
        return

    out = post_dir(post_date)
    if os.path.exists(os.path.join(out, "meta.json")):
        print(f"{out} は作成済みです。スキップします。")
        return

    history = load_history()
    verse = pick_verse(load_yaml("verses.yaml"), history)
    events = upcoming_events(load_yaml("events.yaml"), post_date)

    if skip_ai:
        body = f"（AIを使わないテスト用の本文です）\n{theme['headline']}をお届けします。"
        tags = config["caption"].get("hashtag_pool", [])[:3]
    else:
        body, tags = cap.generate_body(config, theme, verse, events, post_date)
    caption = cap.build_caption(config, body, verse, tags)

    image.render(config, theme, verse, os.path.join(out, "image.jpg"))
    with open(os.path.join(out, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(caption)
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"date": post_date.isoformat(), "theme": theme_key, "verse_ref": verse["ref"],
                   "published_media_id": None}, f, ensure_ascii=False, indent=2)

    if not skip_ai:
        history.append({"date": post_date.isoformat(), "verse_ref": verse["ref"]})
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
        with open(HISTORY, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"作成しました: {out}\n---\n{caption}\n---")


def cmd_publish(post_date: date) -> None:
    from autopost import instagram

    out = post_dir(post_date)
    meta_path = os.path.join(out, "meta.json")
    if not os.path.exists(meta_path):
        print(f"{out} に投稿データがありません。スキップします。")
        return
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("published_media_id"):
        print("投稿済みです。スキップします。")
        return

    base = os.environ["IMAGE_BASE_URL"].rstrip("/")  # 例: https://raw.githubusercontent.com/OWNER/REPO/<commit>
    image_url = f"{base}/posts/{post_date.isoformat()}/image.jpg"
    with open(os.path.join(out, "caption.txt"), encoding="utf-8") as f:
        caption = f.read()

    media_id = instagram.publish_image(
        os.environ.get("IG_USER_ID") or str(load_yaml("config.yaml").get("instagram", {}).get("user_id", "")), os.environ["IG_ACCESS_TOKEN"], image_url, caption
    )
    meta["published_media_id"] = media_id
    meta["published_at"] = datetime.now(JST).isoformat(timespec="seconds")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"投稿しました: media_id={media_id}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["prepare", "publish"])
    p.add_argument("--date", help="YYYY-MM-DD（省略時は日本時間の今日）")
    p.add_argument("--skip-ai", action="store_true")
    a = p.parse_args()
    d = date.fromisoformat(a.date) if a.date else datetime.now(JST).date()
    if a.command == "prepare":
        cmd_prepare(d, a.skip_ai)
    else:
        cmd_publish(d)


if __name__ == "__main__":
    sys.exit(main())
