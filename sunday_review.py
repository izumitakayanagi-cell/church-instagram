"""日曜夜の確認ページ（Mac で動く）

使い方:
  .venv/bin/python sunday_review.py                 # 直近の日曜日の分を準備してブラウザで開く
  .venv/bin/python sunday_review.py --date 2026-11-22

やること:
  1. iCloud Drive の Pages フォルダから、その日付の説教ファイル（日本語・英語）を探す
  2. 「写真」フォルダから、その日に撮影された写真を探す
  3. AI が月曜朝の投稿文（日本語・英語）の下書きを作る
  4. ブラウザに確認ページを開く → 写真を選び、文章を確認して「予約する」
  5. 承認した内容を GitHub に送り、月曜 6:00 に自動投稿される
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from autopost import report  # noqa: E402

PORT = 8765
REVIEW_DIR = os.path.join(ROOT, "review")
LANGS = ("ja", "en")
LANG_NAME = {"ja": "日本語", "en": "English"}
MAX_PHOTOS = 4


def load_config() -> dict:
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_api_key() -> None:
    """ANTHROPIC_API_KEY が無ければ macOS のキーチェーンから読む。"""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        key = subprocess.run(["security", "find-generic-password", "-s", "church-instagram-anthropic", "-w"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
    except Exception:
        pass


def last_sunday(today: date) -> date:
    return today - timedelta(days=(today.weekday() + 1) % 7)


# ---------- 下書きの状態 ----------

class Review:
    def __init__(self, service_date: date):
        self.date = service_date
        self.monday = service_date + timedelta(days=1)
        self.path = os.path.join(REVIEW_DIR, f"{service_date.isoformat()}.json")
        self.config = load_config()
        self.lock = threading.Lock()
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.s = json.load(f)
            # 写真や説教ファイルが後から増えていたら取り込む
            self.s["photos"] += [p for p in report.find_photos(service_date) if p not in self.s["photos"]]
            known = {x["path"] for x in self.s["sermons"]}
            self.s["sermons"] += [x for x in report.find_sermons(service_date) if x["path"] not in known]
        else:
            self.s = {"date": service_date.isoformat(), "monday": self.monday.isoformat(),
                      "sermons": report.find_sermons(service_date),
                      "photos": report.find_photos(service_date),
                      "approved": False}
            for lang in LANGS:
                cand = [i for i, x in enumerate(self.s["sermons"]) if x["lang"] == lang]
                self.s[lang] = {"sermon": cand[0] if cand else None, "caption": "", "photos": [], "skip": False}
        self.save()

    def refresh_photos(self) -> None:
        """ページを開き直したときに、後から入れた写真も表示されるようにする。"""
        new = [p for p in report.find_photos(self.date) if p not in self.s["photos"]]
        if new:
            self.s["photos"] += sorted(new)  # 既存の番号（選択済み）は変えない
            self.save()

    def save(self) -> None:
        os.makedirs(REVIEW_DIR, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.s, f, ensure_ascii=False, indent=2)

    def make_caption(self, lang: str) -> None:
        idx = self.s[lang]["sermon"]
        if idx is None:
            self.s[lang]["caption"] = ""
            return
        sermon = self.s["sermons"][idx]
        body = report.sermon_text(sermon["path"])
        if not body:
            raise RuntimeError("説教ファイルから文章を読み取れませんでした。")
        res = report.generate_caption(self.config, lang, sermon["label"], body, self.date)
        self.s[lang]["caption"] = report.full_caption(self.config, lang, res["body"], res["hashtags"])

    def prepare_drafts(self) -> None:
        for lang in LANGS:
            if not self.s[lang]["caption"] and self.s[lang]["sermon"] is not None:
                print(f"{LANG_NAME[lang]}の文章を作成中…")
                self.make_caption(lang)
                self.save()

    def approve(self, data: dict) -> str:
        """承認された内容を posts/ に書き出して GitHub に送る。"""
        written = []
        for lang in LANGS:
            d = data[lang]
            self.s[lang].update({"caption": d["caption"], "photos": d["photos"], "skip": d["skip"]})
            if d["skip"]:
                continue
            if not (1 <= len(d["photos"]) <= MAX_PHOTOS):
                raise ValueError(f"{LANG_NAME[lang]}：写真を1〜{MAX_PHOTOS}枚選んでください。")
            if not d["caption"].strip():
                raise ValueError(f"{LANG_NAME[lang]}：文章が空です。")
            out = os.path.join(ROOT, "posts", f"{self.monday.isoformat()}-{lang}")
            meta_path = os.path.join(out, "meta.json")
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    if json.load(f).get("published_media_id"):
                        raise ValueError(f"{LANG_NAME[lang]}：すでに投稿済みです。")
            os.makedirs(out, exist_ok=True)
            for old in os.listdir(out):
                os.remove(os.path.join(out, old))
            names = []
            for i, photo_idx in enumerate(d["photos"], 1):
                name = f"image{i}.jpg"
                report.export_post_image(self.s["photos"][photo_idx], os.path.join(out, name))
                names.append(name)
            with open(os.path.join(out, "caption.txt"), "w", encoding="utf-8") as f:
                f.write(d["caption"].strip()[:2200])
            sermon_idx = self.s[lang]["sermon"]
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"date": self.monday.isoformat(), "lang": lang, "time": "06:00", "theme": "report",
                           "service_date": self.date.isoformat(), "images": names,
                           "sermon": self.s["sermons"][sermon_idx]["name"] if sermon_idx is not None else None,
                           "published_media_id": None}, f, ensure_ascii=False, indent=2)
            written.append(f"posts/{self.monday.isoformat()}-{lang}")
        self.s["approved"] = True
        self.save()
        if not written:
            return "今週は投稿しない設定にしました。"
        push_to_github(written, f"日曜礼拝の報告を予約: {self.date.isoformat()}")
        return f"{self.monday.month}月{self.monday.day}日（月）6:00 に投稿されるよう予約しました。"


def push_to_github(paths: list[str], message: str) -> None:
    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"GitHubへの送信に失敗しました: {r.stderr.strip()[-300:]}")
        return r.stdout
    git("pull", "--rebase", "--autostash")
    git("add", "-A", *paths)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0:
        git("commit", "-m", message)
    git("push")


# ---------- 画面 ----------

PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>日曜礼拝の投稿確認</title>
<style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#222;--sub:#666;--accent:#7a4e2d;--ok:#2e7d32;--line:#e2ddd3}
@media (prefers-color-scheme:dark){:root{--bg:#1c1b19;--card:#262421;--ink:#eee;--sub:#aaa;--accent:#d9a36b;--line:#3a3733}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,"Hiragino Sans",sans-serif}
header{padding:20px 24px;border-bottom:1px solid var(--line)}h1{margin:0;font-size:22px}
header p{margin:4px 0 0;color:var(--sub)}
main{display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:20px 24px}
@media (max-width:900px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
h2{margin:0 0 12px;font-size:19px}label{font-weight:600;display:block;margin:14px 0 6px}
select,textarea{width:100%;box-sizing:border-box;font:inherit;padding:8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
textarea{min-height:320px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px}
.ph{position:relative;cursor:pointer;border-radius:8px;overflow:hidden;border:3px solid transparent;aspect-ratio:4/5;background:#0002}
.ph img{width:100%;height:100%;object-fit:cover;display:block}
.ph.sel{border-color:var(--accent)}
.ph .n{position:absolute;top:4px;left:4px;background:var(--accent);color:#fff;border-radius:50%;width:26px;height:26px;display:none;align-items:center;justify-content:center;font-weight:700}
.ph.sel .n{display:flex}
.hint{color:var(--sub);font-size:14px}
button{font:inherit;padding:10px 16px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.main{background:var(--accent);color:#fff;border:none;font-size:18px;padding:14px 28px}
footer{padding:0 24px 32px;text-align:center}
#msg{margin:12px 0;font-weight:600}.skip{display:flex;gap:8px;align-items:center;margin-top:14px}
.off{opacity:.4;pointer-events:none}
</style></head><body>
<header><h1>日曜礼拝の投稿確認 — __DATE__</h1>
<p>写真を選んで（クリックした順に1枚目・2枚目…）、文章を確認したら、いちばん下の「予約する」を押してください。__MONDAY__ 6:00 に投稿されます。</p></header>
<main id="main"></main>
<footer><div id="msg"></div><button class="main" onclick="approve()">この内容で予約する</button></footer>
<script>
const S = __STATE__;
const NAMES = {ja:"日本語版", en:"English版"};
function render(){
  const m = document.getElementById('main'); m.innerHTML='';
  for (const lang of ['ja','en']){
    const st = S[lang];
    const sec = document.createElement('section');
    let opts = S.sermons.map((x,i)=>`<option value="${i}" ${st.sermon===i?'selected':''}>${x.lang==='en'?'[英語] ':'[日本語] '}${x.label}</option>`).join('');
    opts += `<option value="" ${st.sermon===null?'selected':''}>（説教なし）</option>`;
    const photos = S.photos.length ? S.photos.map((p,i)=>{
      const k = st.photos.indexOf(i);
      return `<div class="ph ${k>=0?'sel':''}" onclick="pick('${lang}',${i})"><img src="/thumb/${i}" loading="lazy"><span class="n">${k+1}</span></div>`}).join('')
      : '<p class="hint">この日に撮影された写真が「写真」フォルダにありません。写真を入れてから、このページを再読み込みしてください。</p>';
    sec.innerHTML = `<h2>${NAMES[lang]}</h2>
      <div class="${st.skip?'off':''}">
      <label>説教</label><select onchange="setSermon('${lang}',this.value)">${opts}</select>
      <label>写真（1〜__MAX__枚）</label><div class="grid">${photos}</div>
      <label>投稿文</label><textarea id="cap-${lang}" oninput="S['${lang}'].caption=this.value">${esc(st.caption)}</textarea>
      <p><button onclick="regen('${lang}')">文章を作り直す</button></p></div>
      <div class="skip"><input type="checkbox" id="skip-${lang}" ${st.skip?'checked':''} onchange="S['${lang}'].skip=this.checked;render()"><label for="skip-${lang}" style="margin:0">今週はこの言語を投稿しない</label></div>`;
    m.appendChild(sec);
  }
}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function pick(lang,i){const a=S[lang].photos;const k=a.indexOf(i);if(k>=0)a.splice(k,1);else if(a.length<__MAX__)a.push(i);else{msg('写真は__MAX__枚までです');return}render()}
function msg(t,ok){const e=document.getElementById('msg');e.textContent=t;e.style.color=ok?'var(--ok)':''}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw new Error(j.error||'エラー');return j}
async function setSermon(lang,v){S[lang].sermon=v===''?null:+v;await regen(lang)}
async function regen(lang){msg('文章を作っています…（30秒ほど）');try{const j=await post('/api/caption',{lang,sermon:S[lang].sermon});S[lang].caption=j.caption;render();msg('文章を作り直しました',true)}catch(e){msg(e.message)}}
async function approve(){
  if(!confirm('この内容で月曜6:00の投稿を予約しますか？'))return;
  msg('写真を準備してGitHubに送っています…');
  try{const j=await post('/api/approve',{ja:S.ja,en:S.en});msg(j.message+' このページは閉じて大丈夫です。',true)}catch(e){msg(e.message)}
}
render();
</script></body></html>"""


def make_handler(rv: Review):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 端末への出力を静かにする
            pass

        def _json(self, code: int, obj: dict) -> None:
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path == "/":
                with rv.lock:
                    rv.refresh_photos()
                html = (PAGE.replace("__STATE__", json.dumps(rv.s, ensure_ascii=False).replace("</", "<\\/"))
                        .replace("__DATE__", f"{rv.date.month}月{rv.date.day}日（日）")
                        .replace("__MONDAY__", f"{rv.monday.month}月{rv.monday.day}日（月）")
                        .replace("__MAX__", str(MAX_PHOTOS)))
                b = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b)
            elif self.path.startswith("/thumb/"):
                try:
                    b = report.thumbnail(rv.s["photos"][int(self.path.split("/")[-1])])
                except Exception:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(b)
            else:
                self.send_error(404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            try:
                with rv.lock:
                    if self.path == "/api/caption":
                        rv.s[data["lang"]]["sermon"] = data["sermon"]
                        rv.make_caption(data["lang"])
                        rv.save()
                        self._json(200, {"caption": rv.s[data["lang"]]["caption"]})
                    elif self.path == "/api/approve":
                        self._json(200, {"message": rv.approve(data)})
                    else:
                        self.send_error(404)
            except Exception as e:
                self._json(400, {"error": str(e)})
    return H


def notify(title: str, text: str) -> None:
    subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}" sound name "Glass"'],
                   capture_output=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="礼拝の日付 YYYY-MM-DD（省略時は直近の日曜日）")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--hours", type=float, default=12, help="確認ページを開いておく時間")
    a = p.parse_args()
    ensure_api_key()
    d = date.fromisoformat(a.date) if a.date else last_sunday(datetime.now().date())
    rv = Review(d)
    print(f"{d} の説教: {[x['label'] for x in rv.s['sermons']]}")
    print(f"{d} に撮影された写真: {len(rv.s['photos'])} 枚")
    try:
        rv.prepare_drafts()
    except Exception as e:
        print(f"文章の下書きを作れませんでした（確認ページで作り直せます）: {e}")

    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(rv))
    except OSError:
        print("確認ページはすでに開いています。")
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/"
    print(f"確認ページ: {url}")
    notify("日曜礼拝の投稿確認", f"{d.month}/{d.day}の写真と文章を確認して、月曜6時の投稿を予約してください")
    if not a.no_browser:
        webbrowser.open(url)
    deadline = time.time() + a.hours * 3600
    while time.time() < deadline:
        if rv.s.get("approved"):
            time.sleep(120)  # 承認後、少し待ってから終了
            break
        time.sleep(5)
    server.shutdown()


if __name__ == "__main__":
    main()
