"""Instagram API で画像を投稿する。

新しいMetaアプリは「Unified login」でシステムユーザーアクセストークン（無期限）を発行する。
このトークンは graph.facebook.com で使う（graph.instagram.com では使えない）。
https://developers.facebook.com/docs/instagram-platform/content-publishing
"""
from __future__ import annotations

import os
import time

import requests

FB_API = "https://graph.facebook.com/v23.0"   # システムユーザートークン（EAA...で始まる）
IG_API = "https://graph.instagram.com/v23.0"  # Instagramログインのトークン（IG...で始まる）


def _api(token: str) -> str:
    return os.environ.get("IG_API_BASE") or (IG_API if token.startswith("IG") else FB_API)


def _check(r: requests.Response) -> dict:
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Instagram API エラー ({r.status_code}): {data}")
    return data


def find_ig_user_id(token: str) -> str:
    """トークンに割り当てられた Instagram アカウントのIDを調べる。"""
    if token.startswith("IG"):
        return str(_check(requests.get(
            f"{IG_API}/me", params={"fields": "user_id,username", "access_token": token}, timeout=30,
        ))["user_id"])
    data = _check(requests.get(
        f"{FB_API}/me/assigned_instagram_accounts",
        params={"fields": "id,username", "access_token": token},
        timeout=30,
    )).get("data", [])
    if not data:
        raise RuntimeError("トークンに Instagram アカウントが割り当てられていません")
    print(f"投稿先: @{data[0].get('username')} (id={data[0]['id']})")
    return data[0]["id"]


def _wait(API: str, container: str, token: str) -> None:
    """メディアの処理が終わるまで待つ。"""
    for _ in range(30):
        status = _check(requests.get(
            f"{API}/{container}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )).get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"メディア処理に失敗しました: {status}")
        time.sleep(5)
    raise RuntimeError("メディア処理がタイムアウトしました")


def publish_image(ig_user_id: str, token: str, image_url: str, caption: str) -> str:
    return publish_images(ig_user_id, token, [image_url], caption)


def publish_images(ig_user_id: str, token: str, image_urls: list[str], caption: str) -> str:
    """1枚なら通常の投稿、2枚以上ならカルーセル（複数枚）投稿にする。"""
    API = _api(token)
    if not ig_user_id:
        ig_user_id = find_ig_user_id(token)

    if len(image_urls) == 1:
        # Instagram側が image_url から画像を取得する
        container = _check(requests.post(
            f"{API}/{ig_user_id}/media",
            data={"image_url": image_urls[0], "caption": caption, "access_token": token},
            timeout=60,
        ))["id"]
    else:
        children = []
        for url in image_urls[:10]:
            cid = _check(requests.post(
                f"{API}/{ig_user_id}/media",
                data={"image_url": url, "is_carousel_item": "true", "access_token": token},
                timeout=60,
            ))["id"]
            _wait(API, cid, token)
            children.append(cid)
        container = _check(requests.post(
            f"{API}/{ig_user_id}/media",
            data={"media_type": "CAROUSEL", "children": ",".join(children),
                  "caption": caption, "access_token": token},
            timeout=60,
        ))["id"]

    _wait(API, container, token)
    return _check(requests.post(
        f"{API}/{ig_user_id}/media_publish",
        data={"creation_id": container, "access_token": token},
        timeout=60,
    ))["id"]
