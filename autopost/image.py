"""Pillow で投稿画像（聖句カード）を生成する。

背景の優先順位:
  1. backgrounds/<季節>/ に置いた自分の写真
  2. Pixabay（商用無料・クレジット不要）から季節に合う写真
  3. 単色グラデーション
"""
from __future__ import annotations

import glob
import io
import os
import random
from datetime import date

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 行頭に来てはいけない文字（簡易禁則処理）
_NO_LINE_START = set("、。，．・：；？！）」』】〉》ー…‥ぁぃぅぇぉっゃゅょァィゥェォッャュョ")


def season_of(d: date) -> str:
    return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer"}.get(d.month, "autumn")


def _find_font(paths: list[str]) -> str:
    for p in paths:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"日本語フォントが見つかりません: {paths}")


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, lang: str) -> list[str]:
    lines: list[str] = []
    line = ""
    if lang == "en":  # 単語単位で折り返す
        for word in text.split():
            trial = f"{line} {word}".strip()
            if font.getlength(trial) <= max_width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
    else:  # 文字単位で折り返す
        for ch in text:
            if font.getlength(line + ch) <= max_width or ch in _NO_LINE_START:
                line += ch
            else:
                lines.append(line)
                line = ch
    if line:
        lines.append(line)
    return lines


def _gradient(w: int, h: int, top: str, bottom: str) -> Image.Image:
    t, b = _hex(top), _hex(bottom)
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        r = y / (h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(int(t[i] + (b[i] - t[i]) * r) for i in range(3)))
    return img


def _local_photo(season: str, used: set[str]) -> tuple[Image.Image, dict] | None:
    files = []
    for ext in ("jpg", "jpeg", "png", "JPG", "JPEG", "PNG"):
        files += glob.glob(os.path.join(ROOT, "backgrounds", season, f"*.{ext}"))
    if not files:
        return None
    fresh = [f for f in files if os.path.relpath(f, ROOT) not in used] or files
    path = random.choice(fresh)
    return Image.open(path), {"source": "local", "id": os.path.relpath(path, ROOT)}


def _stock_photo(queries: list[str], used: set[str]) -> tuple[Image.Image, dict] | None:
    """Pixabay（商用無料・クレジット表記不要）から写真を1枚選んでダウンロードする。"""
    key = os.environ.get("PIXABAY_API_KEY")
    if not key or not queries:
        return None
    query = random.choice(queries)
    r = requests.get(
        "https://pixabay.com/api/",
        params={"key": key, "q": query, "image_type": "photo", "orientation": "vertical",
                "safesearch": "true", "order": "popular", "per_page": 50},
        timeout=30,
    )
    r.raise_for_status()
    hits = r.json().get("hits", [])
    fresh = [h for h in hits if f"pixabay:{h['id']}" not in used] or hits
    if not fresh:
        return None
    h = random.choice(fresh)
    img_bytes = requests.get(h["largeImageURL"], timeout=60).content
    meta = {"source": "pixabay", "id": f"pixabay:{h['id']}", "query": query,
            "photographer": h.get("user"), "url": h.get("pageURL")}
    return Image.open(io.BytesIO(img_bytes)), meta


def background(config: dict, theme_key: str, post_date: date, used: set[str]) -> tuple[Image.Image, dict]:
    cfg = config["image"]
    w, h = cfg["width"], cfg["height"]
    season = season_of(post_date)
    photo = None
    try:
        photo = _local_photo(season, used) or _stock_photo(
            config.get("backgrounds", {}).get("search_queries", {}).get(season, []), used)
    except Exception as e:  # 写真が取れなくても投稿は止めない
        # URLにAPIキーが含まれるため、エラー内容のURL部分は表示しない
        msg = str(e).split(" for url:")[0]
        print(f"背景写真を取得できませんでした（単色で作成します）: {msg}")

    if photo is None:
        colors = cfg["fallback_colors"].get(theme_key, ["#333333", "#666666"])
        return _gradient(w, h, *colors), {"source": "gradient"}

    img, meta = photo
    img = ImageOps.fit(ImageOps.exif_transpose(img).convert("RGB"), (w, h), method=Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(6))  # 文字を読みやすくし、看板などの文字が読めないようにぼかす
    # 暗い幕（中央ほど濃く）
    alpha = int(255 * cfg.get("overlay_opacity", 0.55))
    shade = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(h):
        edge = abs(y - h / 2) / (h / 2)  # 0=中央, 1=上下端
        sd.line([(0, y), (w, y)], fill=int(alpha * (1.0 - 0.25 * edge)))
    img = Image.composite(Image.new("RGB", (w, h), (10, 10, 15)), img, shade)
    return img, meta


def render(config: dict, lang: str, theme_key: str, headline: str, ref: str, text: str,
           church_name: str, post_date: date, used_backgrounds: set[str], out_path: str) -> dict:
    cfg = config["image"]
    w, h = cfg["width"], cfg["height"]
    font_path = _find_font(cfg["fonts"])
    img, bg_meta = background(config, theme_key, post_date, used_backgrounds)
    draw = ImageDraw.Draw(img)
    white, soft = (255, 255, 255), (240, 234, 222)
    shadow = (0, 0, 0)
    margin = 110

    def text_c(xy, s, font, fill):
        x, y = xy
        draw.text((x + 2, y + 2), s, font=font, fill=shadow, anchor="mm")  # 影
        draw.text((x, y), s, font=font, fill=fill, anchor="mm")

    # 見出し
    f_head = ImageFont.truetype(font_path, 56)
    text_c((w / 2, 170), headline, f_head, soft)
    draw.line([(w / 2 - 80, 230), (w / 2 + 80, 230)], fill=soft, width=3)

    # 聖句本文：長さに応じて文字サイズを調整
    size = 64 if lang == "ja" else 60
    while size > 34:
        f_body = ImageFont.truetype(font_path, size)
        lines = _wrap(text, f_body, w - margin * 2, lang)
        line_h = int(size * (1.75 if lang == "ja" else 1.5))
        if len(lines) * line_h <= h - 620:
            break
        size -= 4
    y = (h - len(lines) * line_h) / 2 + 20
    for ln in lines:
        text_c((w / 2, y + line_h / 2), ln, f_body, white)
        y += line_h

    # 章節
    f_ref = ImageFont.truetype(font_path, 38)
    text_c((w / 2, y + 60), f"― {ref}", f_ref, soft)

    # 教会名
    f_name = ImageFont.truetype(font_path, 44 if lang == "ja" else 40)
    text_c((w / 2, h - 130), church_name, f_name, soft)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "JPEG", quality=92)
    return bg_meta
