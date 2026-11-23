"""日曜礼拝の報告投稿（月曜朝）の材料集め：説教ファイル・写真・文章。

この処理は Mac 上で動く（iCloud Drive の Pages と、写真フォルダを読むため）。
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from datetime import date, datetime

import anthropic
from PIL import Image, ImageOps

from . import pages_text

try:  # iPhone の HEIC 写真を Pillow で直接開けるようにする
    import pillow_heif
    pillow_heif.register_heif_opener()
    _HEIF = True
except ImportError:
    _HEIF = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES_DIR = os.path.expanduser("~/Library/Mobile Documents/com~apple~Pages/Documents")
PHOTO_DIR = os.path.join(ROOT, "写真")
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".heic", ".heif")
_EN_MARK = re.compile(r"(_|\[|\b)(en|eng|english)(\]|\b|$)", re.IGNORECASE)


# ---------- 説教ファイル ----------

def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def find_sermons(d: date, pages_dir: str = PAGES_DIR) -> list[dict]:
    """その日付（YYYYMMDD）を名前に含む Pages ファイルを探す。"""
    key = d.strftime("%Y%m%d")
    out = []
    for path in glob.glob(os.path.join(pages_dir, "*.pages")):
        name = _nfc(os.path.splitext(os.path.basename(path))[0])
        if key not in name:
            continue
        lang = "en" if _EN_MARK.search(name) else "ja"
        out.append({"path": path, "name": name, "lang": lang,
                    "label": _label(name, key)})
    return sorted(out, key=lambda x: (x["lang"], x["name"]))


def _label(name: str, key: str) -> str:
    """ファイル名から日付・言語の印を取り除いて「聖書箇所 タイトル」にする。"""
    s = name.replace(key, "")
    s = _EN_MARK.sub(" ", s)
    s = re.sub(r"[_＿\s]+", " ", s).strip()
    return s or name


def sermon_text(path: str) -> str:
    return pages_text.extract_text(path)


# ---------- 写真 ----------

def _taken_date(path: str) -> date | None:
    """撮影日：EXIF（JPEG/PNG）→ Spotlight（HEICなど）→ ファイル日時 の順に調べる。"""
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            raw = ex.get_ifd(0x8769).get(36867) or ex.get(36867) or ex.get(306)
            if raw:
                return datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S").date()
    except Exception:
        pass
    try:
        out = subprocess.run(["mdls", "-raw", "-name", "kMDItemContentCreationDate", path],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if out and out != "(null)":
            # 例: 2026-11-22 03:15:00 +0000（UTC）→ 現地時刻に直す
            return datetime.strptime(out, "%Y-%m-%d %H:%M:%S %z").astimezone().date()
    except Exception:
        pass
    return datetime.fromtimestamp(os.path.getmtime(path)).date()


_DATE_IN_NAME = re.compile(r"(20\d{2})(\d{2})(\d{2})")


def photo_date(path: str) -> date | None:
    """写真の日付：ファイル名の日付（例 20261122）を最優先し、なければ撮影日。"""
    m = _DATE_IN_NAME.search(_nfc(os.path.basename(path)))
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    return _taken_date(path)


def find_photos(d: date, photo_dir: str = PHOTO_DIR) -> list[str]:
    files = []
    for root, _, names in os.walk(photo_dir):
        for n in names:
            if n.lower().endswith(PHOTO_EXT) and not n.startswith("."):
                files.append(os.path.join(root, n))
    return sorted((p for p in files if photo_date(p) == d), key=lambda p: _nfc(os.path.basename(p)))


def _open_any(path: str) -> Image.Image:
    """HEIC も開けるよう、必要なら macOS の sips で JPEG に変換してから開く。"""
    if path.lower().endswith((".heic", ".heif")) and not _HEIF:
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
        subprocess.run(["sips", "-s", "format", "jpeg", path, "--out", tmp],
                       capture_output=True, check=True)
        im = Image.open(tmp)
        im.load()
        os.unlink(tmp)
        return im
    im = Image.open(path)
    im.load()
    return im


def thumbnail(path: str, size: int = 360) -> bytes:
    import io
    im = ImageOps.exif_transpose(_open_any(path)).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def export_post_image(path: str, out: str, w: int = 1080, h: int = 1350) -> None:
    """Instagram 用に 4:5（1080×1350）へ中央で切り抜いて保存。複数枚投稿は縦横比をそろえる必要がある。"""
    im = ImageOps.exif_transpose(_open_any(path)).convert("RGB")
    im = ImageOps.fit(im, (w, h), method=Image.LANCZOS)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    im.save(out, "JPEG", quality=90)


# ---------- 文章 ----------

PROMPT = {
    "ja": """あなたは日本のプロテスタント教会「{name}」のInstagram担当です。
昨日（{date}）の日曜礼拝の様子を伝える、月曜朝の投稿文を書きます。
写真（礼拝の様子）と一緒に投稿されます。

書き方:
- 短く、柔らかく、温かい日本語で。本文は{max_chars}字程度。適度に改行する。
- 読点「、」は少なめにする。一文を短く区切り、読点で文をつなげすぎない（AIが書いたような硬さを避けるため）。
- 「昨日の礼拝では」のように、昨日の礼拝をふり返る形で書く。
- 説教の題・聖書箇所に触れ、説教の中心のメッセージを1つだけ、やさしい言葉で分かち合う。
- 説教原稿にない事実（人数・行事・人名・食事など）は書かない。写真の内容にも触れない（どの写真かは分からないため）。
- 説教原稿の言葉を長く引用しない。聖句本文も長く書き写さない。
- 礼拝の時間・住所・URLは書かない（末尾に自動で付く）。ハッシュタグは本文に書かない。
- 絵文字は1〜2個まで。最後に「今週も〜」のような、読む人への短い祝福や招きで締める。

ハッシュタグは候補から、内容に合うものを{n_tags}個選ぶ。
""",
    "en": """You run the Instagram account of "{name}", a Protestant church in Tokyo.
Write a Monday-morning post looking back on yesterday's ({date}) Sunday English Service.
It will be posted together with photos of the service.

Guidelines:
- Short, gentle and warm English, about {max_chars} characters, with line breaks.
- Look back on "yesterday's service".
- Mention the sermon title and Bible passage, and share just one central message in simple words.
- Do not state facts not in the sermon manuscript (attendance, events, names, meals). Do not describe the photos (you cannot see them).
- Do not quote the manuscript or the Bible at length.
- No service times, address or URL (appended automatically). No hashtags in the body.
- At most 1–2 emojis. End with a short blessing or invitation for the week.

Choose {n_tags} hashtags from the candidate list that fit the post.
""",
}


def generate_caption(config: dict, lang: str, sermon_label: str, sermon_body: str, service_date: date,
                     sermon_lang: str | None = None) -> dict:
    """{"body": 本文, "hashtags": [...]} を返す。"""
    lc = config["languages"][lang]
    cap = lc["caption"]
    rep = cap.get("report", {})
    pool = cap.get("hashtag_pool", [])
    n_tags = max(0, cap.get("max_hashtags", 5) - len(cap.get("fixed_hashtags", [])))
    schema = {
        "type": "object",
        "properties": {
            "body": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string", "enum": pool} if pool else {"type": "string"}},
        },
        "required": ["body", "hashtags"],
        "additionalProperties": False,
    }
    note = ""
    if lang == "en" and sermon_lang == "ja":
        note = ("\n\nNote: This week the manuscript is the Japanese sermon. Write the post in natural English, "
                "translating the key message. Refer to it as yesterday's Sunday service (not specifically the English Service).")
    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=config["ai"]["model"],
        max_tokens=16000,
        system=PROMPT[lang].format(name=lc["church"]["name"], date=service_date.isoformat(),
                                   max_chars=rep.get("max_chars", 200 if lang == "ja" else 350),
                                   n_tags=n_tags) + note,
        messages=[{"role": "user", "content":
                   f"説教ファイル名（聖書箇所・題）: {sermon_label}\n\nハッシュタグ候補: {' '.join(pool)}\n\n"
                   f"--- 説教原稿 ---\n{sermon_body}"}],
        output_config={"effort": config["ai"].get("effort", "medium"),
                       "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("AIが文章の作成を断りました。もう一度お試しください。")
    data = json.loads("".join(b.text for b in resp.content if b.type == "text"))
    tags = [t for t in dict.fromkeys(data.get("hashtags", [])) if t in pool][:n_tags]
    return {"body": data["body"].strip(), "hashtags": tags}


def full_caption(config: dict, lang: str, body: str, chosen_tags: list[str]) -> str:
    """本文の後ろに、礼拝案内・場所・ハッシュタグを付ける。"""
    lc = config["languages"][lang]
    church = lc["church"]
    parts = [body.strip(), ""]
    for s in church.get("services", []):
        parts.append(f"⛪ {s['name']} | {s['time']}" if lang == "en" else f"⛪ {s['name']}｜{s['time']}")
    for key, icon in (("address", "📍"), ("access", "🚃"), ("website", "🌐")):
        if church.get(key):
            parts.append(f"{icon} {church[key]}")
    cap = lc["caption"]
    tags = list(dict.fromkeys(cap.get("fixed_hashtags", []) + chosen_tags))[: cap.get("max_hashtags", 5)]
    parts += ["", " ".join(tags)]
    return "\n".join(parts)[:2200]
