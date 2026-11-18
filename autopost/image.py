"""Pillow で投稿画像（聖句カード）を生成する。"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

# 行頭に来てはいけない文字（簡易禁則処理）
_NO_LINE_START = set("、。，．・：；？！）」』】〉》ー…‥ぁぃぅぇぉっゃゅょァィゥェォッャュョ")


def _find_font(paths: list[str]) -> str:
    for p in paths:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"日本語フォントが見つかりません: {paths}")


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    line = ""
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


def render(config: dict, theme: dict, verse: dict, out_path: str) -> None:
    cfg = config["image"]
    w, h = cfg["width"], cfg["height"]
    font_path = _find_font(cfg["fonts"])
    img = _gradient(w, h, *theme["colors"])
    draw = ImageDraw.Draw(img)
    white, soft = (255, 255, 255), (235, 228, 214)
    margin = 110

    # 見出し
    f_head = ImageFont.truetype(font_path, 56)
    draw.text((w / 2, 170), theme["headline"], font=f_head, fill=soft, anchor="mm")
    draw.line([(w / 2 - 80, 230), (w / 2 + 80, 230)], fill=soft, width=3)

    # 聖句本文：長さに応じて文字サイズを調整
    size = 64
    while size > 36:
        f_body = ImageFont.truetype(font_path, size)
        lines = _wrap(verse["text"], f_body, w - margin * 2)
        line_h = int(size * 1.75)
        if len(lines) * line_h <= h - 620:
            break
        size -= 4
    block_h = len(lines) * line_h
    y = (h - block_h) / 2 + 20
    for ln in lines:
        draw.text((w / 2, y + line_h / 2), ln, font=f_body, fill=white, anchor="mm")
        y += line_h

    # 章節
    f_ref = ImageFont.truetype(font_path, 38)
    draw.text((w / 2, y + 60), f"― {verse['ref']}", font=f_ref, fill=soft, anchor="mm")

    # 教会名
    f_name = ImageFont.truetype(font_path, 44)
    draw.text((w / 2, h - 130), config["church"]["name"], font=f_name, fill=soft, anchor="mm")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "JPEG", quality=92)
