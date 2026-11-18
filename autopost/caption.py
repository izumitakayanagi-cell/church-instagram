"""Claude API でキャプション本文とハッシュタグを生成する。

聖句本文・礼拝時間・住所などの「事実」はAIに書かせず、
設定ファイルの値をそのまま後から付け足す。AIが書くのは黙想文・招きの文章と、
候補リストからのハッシュタグ選択のみ。
"""
from __future__ import annotations

import json
from datetime import date

import anthropic

SYSTEM_PROMPT = """あなたは日本のプロテスタント教会「{name}」のInstagram担当です。
教会の公式アカウントとして、温かく、落ち着いた、押しつけがましくない日本語で投稿文を書きます。

守ること:
- 以下に与えられた情報（教会情報・聖句・行事予定）以外の事実（日時・場所・人名・行事・数字）を書かない。
- 聖句の本文は書き写さない（画像とキャプション末尾に別途正確な本文を載せるため）。章節を言及するのは構わない。
- 他宗教・他教派・政治的話題に触れない。献金や入信を迫る表現をしない。
- 絵文字は2〜3個まで。本文中にハッシュタグは書かない。
- 本文は{max_chars}字程度、読みやすいよう適度に改行する。
- Instagramの検索は本文の言葉も対象になるため、「高田馬場」「教会」「聖書」「礼拝」など
  探している人が使いそうな言葉を、不自然にならない範囲で本文に含める。

ハッシュタグは候補リストから、今回の投稿内容に最も合うものを{n_tags}個選ぶ。
場所・礼拝・聖書・人生の問いなど、種類が偏らないようにする。

教会情報:
{church_info}
"""


def _church_info(church: dict) -> str:
    lines = [f"名称: {church['name']}"]
    for key, label in (("denomination", "教派"), ("address", "所在地"), ("access", "アクセス"), ("website", "Webサイト")):
        val = church.get(key, "")
        if val and "要記入" not in val:
            lines.append(f"{label}: {val}")
    for s in church.get("services", []):
        lines.append(f"{s['name']}: {s['time']}")
    return "\n".join(lines)


def _n_pool_tags(config: dict) -> int:
    cap = config["caption"]
    return max(0, cap.get("max_hashtags", 5) - len(cap.get("fixed_hashtags", [])))


def generate_body(config: dict, theme: dict, verse: dict, events: list[dict], post_date: date) -> tuple[str, list[str]]:
    """(本文, 選ばれたハッシュタグ) を返す。"""
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
    pool = config["caption"].get("hashtag_pool", [])
    n_tags = min(_n_pool_tags(config), len(pool))

    schema = {
        "type": "object",
        "properties": {
            "body": {"type": "string", "description": "投稿本文（ハッシュタグ・聖句本文なし）"},
            "hashtags": {
                "type": "array",
                "items": {"type": "string", "enum": pool} if pool else {"type": "string"},
                "description": f"候補リストから選んだハッシュタグ {n_tags}個",
            },
        },
        "required": ["body", "hashtags"],
        "additionalProperties": False,
    }

    events_text = "\n".join(f"- {e['date']} {e['title']}：{e.get('detail', '')}" for e in events) or "（なし）"
    user_msg = f"""投稿日: {post_date.isoformat()}（{"月火水木金土日"[post_date.weekday()]}曜日）
テーマ: {theme['headline']}
指示: {theme['instruction']}

今回の聖句: {verse['ref']}
「{verse['text']}」

近日の行事予定（自然に触れられる場合のみ触れる。無理に入れなくてよい）:
{events_text}

ハッシュタグ候補:
{" ".join(pool)}
"""

    response = client.beta.messages.create(
        model=config["ai"]["model"],
        max_tokens=16000,
        system=SYSTEM_PROMPT.format(
            name=config["church"]["name"],
            max_chars=config["caption"]["max_chars"],
            n_tags=n_tags,
            church_info=_church_info(config["church"]),
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

    text = "".join(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    tags = [t for t in dict.fromkeys(data.get("hashtags", [])) if t in pool][:n_tags]
    return data["body"].strip(), tags


def build_caption(config: dict, body: str, verse: dict, chosen_tags: list[str]) -> str:
    church = config["church"]
    parts = [body, "", f"📖 {verse['ref']}", f"「{verse['text']}」", ""]

    for s in church.get("services", []):
        parts.append(f"⛪ {s['name']}｜{s['time']}")
    if church.get("welcome_message"):
        parts.append(church["welcome_message"])
    for key, icon in (("address", "📍"), ("access", "🚃"), ("website", "🌐")):
        val = church.get(key, "")
        if val and "要記入" not in val:
            parts.append(f"{icon} {val}")

    cap = config["caption"]
    tags = list(dict.fromkeys(cap.get("fixed_hashtags", []) + chosen_tags))[: cap.get("max_hashtags", 5)]
    parts += ["", " ".join(tags)]
    return "\n".join(parts)[:2200]  # Instagramのキャプション上限
