"""Apple Pages（.pages）ファイルから本文テキストを取り出す（Pagesアプリ不要）。

.pages は zip で、本文は Index/Document.iwa に入っている。
IWA は「Snappy 圧縮されたチャンク」の連結で、中身は protobuf。
本文の文字列は protobuf の文字列フィールドとして入っているので、
いちばん長い文字列（＝本文ストレージ）を取り出す簡易的な方法をとる。
"""
from __future__ import annotations

import sys
import zipfile


def _snappy_decompress(data: bytes) -> bytes:
    """Snappy（フレームなし）の展開。"""
    pos = 0
    # 先頭は展開後の長さ（varint）
    length, shift = 0, 0
    while True:
        b = data[pos]
        pos += 1
        length |= (b & 0x7F) << shift
        if b < 0x80:
            break
        shift += 7
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:  # literal
            n = tag >> 2
            if n >= 60:
                nb = n - 59
                n = int.from_bytes(data[pos:pos + nb], "little")
                pos += nb
            n += 1
            out += data[pos:pos + n]
            pos += n
            continue
        if kind == 1:
            n = ((tag >> 2) & 7) + 4
            off = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == 2:
            n = (tag >> 2) + 1
            off = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        else:
            n = (tag >> 2) + 1
            off = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        start = len(out) - off
        for i in range(n):  # 重なりがあり得るので1バイトずつ
            out.append(out[start + i])
    return bytes(out)


def _iwa_decompress(data: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while pos + 4 <= len(data):
        n = int.from_bytes(data[pos + 1:pos + 4], "little")
        pos += 4
        out += _snappy_decompress(data[pos:pos + n])
        pos += n
    return bytes(out)


def _strings(buf: bytes, min_len: int = 20) -> list[str]:
    """protobuf を再帰的にたどって、UTF-8 として読める長い文字列を集める。"""
    found: list[str] = []

    def varint(p: int) -> tuple[int, int]:
        v, s = 0, 0
        while p < len(buf):
            b = buf[p]
            p += 1
            v |= (b & 0x7F) << s
            if b < 0x80:
                return v, p
            s += 7
        raise ValueError

    def walk(start: int, end: int, depth: int) -> bool:
        p = start
        try:
            while p < end:
                key, p = varint(p)
                wt = key & 7
                if wt == 0:
                    _, p = varint(p)
                elif wt == 1:
                    p += 8
                elif wt == 5:
                    p += 4
                elif wt == 2:
                    n, p = varint(p)
                    if p + n > end:
                        return False
                    chunk = buf[p:p + n]
                    ok_sub = depth < 12 and n > 1 and walk(p, p + n, depth + 1)
                    if not ok_sub and n >= min_len:
                        try:
                            found.append(chunk.decode("utf-8"))
                        except UnicodeDecodeError:
                            pass
                    p += n
                else:
                    return False
            return p == end
        except (ValueError, IndexError):
            return False

    # IWA の中身は「varint長さ＋ArchiveInfo＋メッセージ」の並び。全体を雑に走査する
    p = 0
    while p < len(buf):
        if not walk(p, len(buf), 0):
            p += 1
        else:
            break
    return found


def extract_text(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        raw = z.read("Index/Document.iwa")
    data = _iwa_decompress(raw)
    texts = _strings(data)
    if not texts:
        return ""
    # 本文はふつう最長の文字列。改行は U+2028 などが使われるので普通の改行にする
    body = max(texts, key=len)
    for ch in (" ", " ", "\r"):
        body = body.replace(ch, "\n")
    body = body.replace("￼", "")  # 図や画像の位置の印
    return body.strip()


if __name__ == "__main__":
    print(extract_text(sys.argv[1]))
