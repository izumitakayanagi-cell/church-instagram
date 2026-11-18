"""Claude API でキャプション本文とハッシュタグを生成する（日本語・英語）。

聖句本文・礼拝時間・住所などの「事実」はAIに書かせず、
設定ファイルの値をそのまま後から付け足す。AIが書くのは黙想文・招きの文章と、
候補リストからのハッシュタグ選択のみ。
"""
from __future__ import annotations

import json
from datetime import date

import anthropic

SYSTEM_PROMPT = {
    "ja": """あなたは日本のプロテスタント教会「{name}」のInstagram担当です。
教会の公式アカウントとして、温かく、落ち着いた、押しつけがましくない日本語で投稿文を書きます。

守ること:
- 以下に与えられた情報（教会情報・聖句・行事予定）以外の事実（日時・場所・人名・行事・数字）を書かない。
- 聖句の本文は書き写さない（画像とキャプション末尾に別途正確な本文を載せるため）。章節を言及するのは構わない。
- 礼拝の時間・住所・アクセス・WebサイトのURLは本文に書かない（投稿の末尾に自動で付くため、重複になる）。
- 他宗教・他教派・政治的話題に触れない。献金や入信を迫る表現をしない。
- 絵文字は2〜3個まで。本文中にハッシュタグは書かない。
- 本文は{max_chars}字程度、読みやすいよう適度に改行する。
- Instagramの検索は本文の言葉も対象になるため、「高田馬場」「教会」「聖書」「礼拝」など
  探している人が使いそうな言葉を、不自然にならない範囲で本文に含める。

ハッシュタグは候補リストから、今回の投稿内容に最も合うものを{n_tags}個選ぶ。
場所・礼拝・聖書・人生の問いなど、種類が偏らないようにする。

教会情報:
{church_info}
""",
    "en": """You run the Instagram account of "{name}", a Protestant church in Tokyo, Japan.
Write in warm, calm, natural English that is never pushy. Many readers are international residents
or visitors in Tokyo, and some have never been to church.

Rules:
- Do not state any facts (dates, places, names, events, numbers) beyond the church info, verse and events given below.
- Do not quote the verse text itself (the exact text is added to the image and the end of the caption). Mentioning the reference is fine.
- Do not write service times, address, access or the website URL in the body (they are appended automatically).
- Do not mention other religions, other denominations or politics. Never pressure anyone to give or convert.
- Use at most 2–3 emojis. No hashtags in the body.
- About {max_chars} characters, with line breaks for readability.
- Instagram search also matches words in captions, so naturally include words people might search for,
  such as "Tokyo", "church", "Bible", "English service", "Takadanobaba".

Choose {n_tags} hashtags from the candidate list that best fit this post, with a balanced mix
(place, worship, Bible, life topics).

Church info:
{church_info}
""",
}

USER_TEMPLATE = {
    "ja": """投稿日: {date}（{weekday}曜日）
テーマ: {headline}
指示: {instruction}

今回の聖句: {ref}
「{text}」

近日の行事予定（自然に触れられる場合のみ触れる。無理に入れなくてよい）:
{events}

ハッシュタグ候補:
{pool}
""",
    "en": """Post date: {date} ({weekday})
Theme: {headline}
Instructions: {instruction}

Verse: {ref}
"{text}"

Upcoming events (mention only if it fits naturally):
{events}

Hashtag candidates:
{pool}
""",
}

WEEKDAY_NAMES = {
    "ja": "月火水木金土日",
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
LABELS = {
    "ja": {"denomination": "教派", "address": "所在地", "access": "アクセス", "website": "Webサイト", "none": "（なし）"},
    "en": {"denomination": "Denomination", "address": "Address", "access": "Access", "website": "Website", "none": "(none)"},
}


def verse_for(verse: dict, lang: str, suffix: str = "") -> tuple[str, str]:
    """(章節, 本文) を言語に合わせて返す。suffix は訳名の表記（例: " (ESV)"）。"""
    if lang == "en":
        return verse["ref_en"] + suffix, verse["text_en"]
    return verse["ref"] + suffix, verse["text"]


def _church_info(church: dict, lang: str) -> str:
    lab = LABELS[lang]
    lines = [f"Name: {church['name']}" if lang == "en" else f"名称: {church['name']}"]
    for key in ("denomination", "address", "access", "website"):
        if church.get(key):
            lines.append(f"{lab[key]}: {church[key]}")
    for s in church.get("services", []):
        lines.append(f"{s['name']}: {s['time']}")
    return "\n".join(lines)


def _n_pool_tags(cap: dict) -> int:
    return max(0, cap.get("max_hashtags", 5) - len(cap.get("fixed_hashtags", [])))


def generate_body(config: dict, lang: str, theme: dict, verse: dict, events: list[dict],
                  post_date: date) -> tuple[str, list[str]]:
    """(本文, 選ばれたハッシュタグ) を返す。"""
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
    lc = config["languages"][lang]
    cap = lc["caption"]
    pool = cap.get("hashtag_pool", [])
    n_tags = min(_n_pool_tags(cap), len(pool))
    ref, text = verse_for(verse, lang)

    schema = {
        "type": "object",
        "properties": {
            "body": {"type": "string", "description": "Caption body (no hashtags, no verse text)"},
            "hashtags": {
                "type": "array",
                "items": {"type": "string", "enum": pool} if pool else {"type": "string"},
                "description": f"{n_tags} hashtags chosen from the candidate list",
            },
        },
        "required": ["body", "hashtags"],
        "additionalProperties": False,
    }

    events_text = "\n".join(f"- {e['date']} {e['title']}: {e.get('detail', '')}" for e in events) \
        or LABELS[lang]["none"]
    user_msg = USER_TEMPLATE[lang].format(
        date=post_date.isoformat(), weekday=WEEKDAY_NAMES[lang][post_date.weekday()],
        headline=theme["headline"], instruction=theme["instruction"],
        ref=ref, text=text, events=events_text, pool=" ".join(pool),
    )

    response = client.beta.messages.create(
        model=config["ai"]["model"],
        max_tokens=16000,
        system=SYSTEM_PROMPT[lang].format(
            name=lc["church"]["name"], max_chars=cap["max_chars"], n_tags=n_tags,
            church_info=_church_info(lc["church"], lang),
        ),
        messages=[{"role": "user", "content": user_msg}],
        output_config={
            "effort": config["ai"].get("effort", "medium"),
            "format": {"type": "json_schema", "schema": schema},
        },
        # 安全分類器による誤った拒否時に自動で別モデルへ切り替える
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"AIが生成を拒否しました: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("AIの出力が途中で切れました")

    data = json.loads("".join(b.text for b in response.content if b.type == "text"))
    tags = [t for t in dict.fromkeys(data.get("hashtags", [])) if t in pool][:n_tags]
    return data["body"].strip(), tags


def build_caption(config: dict, lang: str, body: str, verse: dict, chosen_tags: list[str]) -> str:
    lc = config["languages"][lang]
    church = lc["church"]
    ref, text = verse_for(verse, lang, lc["caption"].get("verse_suffix", ""))
    quote = f'"{text}"' if lang == "en" else f"「{text}」"
    parts = [body, "", f"📖 {ref}", quote, ""]

    for s in church.get("services", []):
        parts.append(f"⛪ {s['name']} | {s['time']}" if lang == "en" else f"⛪ {s['name']}｜{s['time']}")
    if church.get("welcome_message"):
        parts.append(church["welcome_message"])
    for key, icon in (("address", "📍"), ("access", "🚃"), ("website", "🌐")):
        if church.get(key):
            parts.append(f"{icon} {church[key]}")

    cap = lc["caption"]
    tags = list(dict.fromkeys(cap.get("fixed_hashtags", []) + chosen_tags))[: cap.get("max_hashtags", 5)]
    parts += ["", " ".join(tags)]
    return "\n".join(parts)[:2200]  # Instagramのキャプション上限
