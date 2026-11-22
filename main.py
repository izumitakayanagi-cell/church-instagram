"""東京ソフィア長老教会 Instagram 自動投稿

使い方:
  python main.py prepare --lang ja             # 今日の日本語投稿を posts/日付-ja/ に作成
  python main.py prepare --lang en --skip-ai   # AIを使わずサンプル文で作成（見た目の確認用）
  python main.py prepare --lang ja --date 2026-11-21
  python main.py publish --lang ja             # posts/日付-ja/ の内容を Instagram に投稿
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
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


def pick_verse(verses: list[dict], history: list[dict], lang: str) -> dict:
    """この言語で最後に使った日が最も古い（または未使用の）聖句を選ぶ。"""
    last_used = {h["verse_ref"]: h["date"] for h in history if h.get("lang", "ja") == lang}
    return min(verses, key=lambda v: last_used.get(v["ref"], ""))


def upcoming_events(events: list[dict], post_date: date, days: int = 14) -> list[dict]:
    out = []
    for e in events:
        d = e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"]))
        if post_date <= d <= post_date + timedelta(days=days):
            out.append({**e, "date": d.isoformat()})
    return out


def post_dir(d: date, lang: str) -> str:
    return os.path.join(ROOT, "posts", f"{d.isoformat()}-{lang}")


def _matches(entry: dict, d: date) -> bool:
    if entry.get("date"):
        return str(entry["date"]) == d.isoformat()
    return entry.get("weekday") == WEEKDAYS[d.weekday()]


def find_slot(config: dict, post_date: date, lang: str) -> dict:
    entries = config.get("schedule", [])
    # 日付指定（臨時・予約投稿）を曜日指定より優先する
    for s in sorted(entries, key=lambda e: 0 if e.get("date") else 1):
        if _matches(s, post_date) and s["lang"] == lang:
            return s
    # 予定外の日に手動で実行したときは「今週のみことば」として作る
    print(f"{post_date} の {lang} 投稿は予定外のため、臨時投稿（今週のみことば）として作成します。")
    return {"time": "臨時", "lang": lang, "theme": "midweek"}


def _grace(s: dict) -> timedelta:
    """予定時刻から、どれだけ遅れても投稿してよいか。
    GitHub の定時実行は何時間も遅れることがあるため、礼拝報告（内容が時刻に左右されない）は長めにとる。"""
    return timedelta(hours=15) if s.get("theme") == "report" else timedelta(hours=3)


def due_slots(config: dict, now: datetime) -> list[dict]:
    """今の時刻に投稿すべき予定（予定時刻の5分前〜猶予時間）を返す。"""
    today = now.date()
    out = []
    for s in config.get("schedule", []):
        if not _matches(s, today):
            continue
        hh, mm = map(int, str(s["time"]).split(":"))
        slot_dt = datetime(today.year, today.month, today.day, hh, mm, tzinfo=JST)
        if slot_dt - timedelta(minutes=5) <= now <= slot_dt + _grace(s):
            out.append(s)
    # 同じ日の日本語・英語が重なったら、時刻順→日本語→英語の順にする
    seen, result = set(), []
    for s in sorted(out, key=lambda e: (str(e["time"]), e["lang"] != "ja")):
        if s["lang"] not in seen:
            seen.add(s["lang"])
            result.append(s)
    return result


def cmd_prepare(post_date: date, lang: str, skip_ai: bool, remake: bool = False) -> None:
    from autopost import caption as cap
    from autopost import image

    config = load_yaml("config.yaml")
    slot = find_slot(config, post_date, lang)
    lc = config["languages"][lang]
    theme_key = slot["theme"]
    if theme_key == "report":
        # 日曜礼拝の報告は、Mac の確認ページ（sunday_review.py）で承認された内容を使う
        if os.path.exists(os.path.join(post_dir(post_date, lang), "meta.json")):
            print(f"{post_date} {lang}：日曜の確認ページで承認された内容を使います。")
        else:
            print(f"{post_date} {lang}：日曜の確認ページでまだ承認されていません。")
        return
    theme = lc["themes"][theme_key]

    out = post_dir(post_date, lang)
    history = load_history()
    meta_path = os.path.join(out, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            published = json.load(f).get("published_media_id")
        if not remake:
            print(f"{out} は作成済みです。スキップします。")
            return
        if published:
            print(f"{out} はすでにInstagramに投稿済みのため、作り直しません。")
            return
        print(f"{out} を作り直します。")
        shutil.rmtree(out)
        history = [h for h in history if not (h["date"] == post_date.isoformat() and h.get("lang", "ja") == lang)]

    verse = pick_verse(load_yaml("verses.yaml"), history, lang)
    events = upcoming_events(load_yaml("events.yaml"), post_date)

    if skip_ai:
        body = (f"(Sample text without AI)\n{theme['headline']}" if lang == "en"
                else f"（AIを使わないテスト用の本文です）\n{theme['headline']}をお届けします。")
        tags = lc["caption"].get("hashtag_pool", [])[:3]
    else:
        body, tags = cap.generate_body(config, lang, theme, verse, events, post_date)
    caption = cap.build_caption(config, lang, body, verse, tags)

    ref, text = cap.verse_for(verse, lang, lc["caption"].get("verse_suffix", ""))
    used_bg = {h["background"] for h in history if h.get("background")}
    bg = image.render(config, lang, theme_key, theme["headline"], ref, text, lc["church"]["name"],
                      post_date, used_bg, os.path.join(out, "image.jpg"))

    with open(os.path.join(out, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(caption)
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"date": post_date.isoformat(), "lang": lang, "time": slot["time"], "theme": theme_key,
                   "verse_ref": verse["ref"], "background": bg, "published_media_id": None},
                  f, ensure_ascii=False, indent=2)

    if not skip_ai:
        history.append({"date": post_date.isoformat(), "lang": lang, "verse_ref": verse["ref"],
                        "background": bg.get("id")})
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
        with open(HISTORY, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"作成しました: {out}\n背景: {bg}\n---\n{caption}\n---")


def cmd_publish(post_date: date, lang: str) -> None:
    from autopost import instagram

    out = post_dir(post_date, lang)
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
    names = meta.get("images") or ["image.jpg"]  # 日曜礼拝の報告は複数枚
    image_urls = [f"{base}/posts/{os.path.basename(out)}/{n}" for n in names]
    with open(os.path.join(out, "caption.txt"), encoding="utf-8") as f:
        caption = f.read()

    ig_user_id = os.environ.get("IG_USER_ID") or str(load_yaml("config.yaml").get("instagram", {}).get("user_id", ""))
    media_id = instagram.publish_images(ig_user_id, os.environ["IG_ACCESS_TOKEN"], image_urls, caption)
    meta["published_media_id"] = media_id
    meta["published_at"] = datetime.now(JST).isoformat(timespec="seconds")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"投稿しました: media_id={media_id}")


def cmd_auto(stage: str) -> None:
    """定時実行用：今の時刻に予定されている投稿を作成（prepare）または投稿（publish）する。"""
    config = load_yaml("config.yaml")
    now = datetime.now(JST)
    slots = due_slots(config, now)
    if not slots:
        print(f"{now:%Y-%m-%d %H:%M} に予定されている投稿はありません。")
        return
    for s in slots:
        print(f"予定: {now.date()} {s['time']} {s['lang']}")
        if stage == "prepare":
            cmd_prepare(now.date(), s["lang"], skip_ai=False)
        else:
            cmd_publish(now.date(), s["lang"])


MISSED_STATE = os.path.join(ROOT, "state", "missed.json")
MISSED_REPORT = os.path.join(ROOT, "missed_report.md")


def cmd_check_missed() -> None:
    """今日・昨日の予定のうち、投稿できる時間（予定時刻＋3時間）を過ぎても投稿されていないものを報告する。
    見つかったら missed_report.md を書き出す（ワークフローがGitHubのIssueにして、メールで知らせる）。"""
    config = load_yaml("config.yaml")
    now = datetime.now(JST)
    reported = []
    if os.path.exists(MISSED_STATE):
        with open(MISSED_STATE, encoding="utf-8") as f:
            reported = json.load(f)
    new = []
    for d in (now.date() - timedelta(days=1), now.date()):
        for s in config.get("schedule", []):
            if not _matches(s, d):
                continue
            hh, mm = map(int, str(s["time"]).split(":"))
            slot_dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=JST)
            key = f"{d.isoformat()} {s['time']} {s['lang']}"
            if now <= slot_dt + _grace(s) or key in reported:
                continue
            meta_path = os.path.join(post_dir(d, s["lang"]), "meta.json")
            published = None
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    published = json.load(f).get("published_media_id")
            if not published:
                new.append(key)
    if not new:
        print("投稿漏れはありません。")
        return
    lang_name = {"ja": "日本語", "en": "英語"}
    lines = ["次の予定の投稿が、時間内に行われませんでした。", ""]
    for k in new:
        d, t, lang = k.split()
        lines.append(f"- {d} {t} {lang_name.get(lang, lang)}")
    lines += ["", "考えられる原因：GitHubの定時実行が大幅に遅れた、またはエラーで止まった。",
              "Actions タブで、その時間帯の実行結果を確認してください。"]
    with open(MISSED_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.makedirs(os.path.dirname(MISSED_STATE), exist_ok=True)
    with open(MISSED_STATE, "w", encoding="utf-8") as f:
        json.dump((reported + new)[-100:], f, ensure_ascii=False, indent=2)
    print("\n".join(lines))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["prepare", "publish", "auto-prepare", "auto-publish", "check-missed"])
    p.add_argument("--lang", choices=["ja", "en"])
    p.add_argument("--date", help="YYYY-MM-DD（省略時は日本時間の今日）")
    p.add_argument("--skip-ai", action="store_true")
    p.add_argument("--remake", action="store_true", help="未投稿の内容を作り直す")
    a = p.parse_args()
    if a.command == "check-missed":
        cmd_check_missed()
        return
    if a.command.startswith("auto-"):
        cmd_auto(a.command.split("-", 1)[1])
        return
    if not a.lang:
        p.error("--lang を指定してください")
    d = date.fromisoformat(a.date) if a.date else datetime.now(JST).date()
    if a.command == "prepare":
        cmd_prepare(d, a.lang, a.skip_ai, a.remake)
    else:
        cmd_publish(d, a.lang)


if __name__ == "__main__":
    sys.exit(main())
