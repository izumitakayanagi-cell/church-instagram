# 東京ソフィア長老教会 Instagram 自動投稿

日本語・英語の投稿を週4回、Instagram に自動投稿します（季節の写真を背景にした聖句カード画像＋AIが書いた投稿文）。

| 曜日・時刻（日本時間） | 言語 | テーマ |
|---|---|---|
| 火曜 18:00 | 日本語 | 今週のみことば ＋ 日本語礼拝へのお誘い |
| 水曜 18:00 | 英語 | Verse of the Week ＋ English Service へのお誘い |
| 土曜 17:00 | 日本語 | 主日礼拝のご案内（日本語礼拝） |
| 土曜 18:00 | 英語 | Sunday Worship（English Service） |

スケジュールは `config.yaml` の `schedule` と `.github/workflows/post.yml` の `cron` で変更できます。

## 背景写真

1. **自分の写真を使う（優先）**：リポジトリの `backgrounds/季節名/` フォルダに写真（.jpg / .png）を入れる
   - `backgrounds/spring/`（3〜5月）、`summer/`（6〜8月）、`autumn/`（9〜11月）、`winter/`（12〜2月）
   - その季節のフォルダの写真から、最近使っていないものが選ばれます
2. **写真がない季節**：Pixabay（商用無料・クレジット表記不要）から「東京の街並み・季節の風景」を自動で選びます（`PIXABAY_API_KEY` が必要）
3. どちらもない場合は単色のグラデーションになります

## しくみ

1. GitHub Actions が決まった時刻に起動
2. `verses.yaml` から最近使っていない聖句を選ぶ
3. 聖句カード画像（1080×1350）を作る
4. Claude（AI）が投稿文を書く。**聖句本文・礼拝時間・住所・ハッシュタグはAIに書かせず、設定ファイルの値をそのまま付ける**ので、事実の誤りが入りません
5. 画像と文章を `posts/日付/` に保存（後から確認できます）
6. Instagram API で投稿

## 編集するファイル

| ファイル | 内容 |
|---|---|
| `config.yaml` | 教会情報・礼拝時間・テーマ・色・ハッシュタグ |
| `verses.yaml` | 投稿に使う聖句（増やすほどバリエーションが増えます） |
| `events.yaml` | 行事予定（14日以内のものが投稿文に反映されます） |

GitHub の画面上で直接編集して保存（Commit）すれば、次回の投稿から反映されます。

---

## 初期設定（最初に一度だけ）

### 1. Instagram をプロアカウントに切り替える（無料）

Instagram アプリ → プロフィール → ≡ → **アカウントの種類とツール** → **プロアカウントに切り替える** → 「ビジネス」を選択（カテゴリは「宗教団体」など）。
Facebook ページとの連携は **不要** です。

### 2. Meta 開発者アプリを作り、アクセストークンを取得する

1. https://developers.facebook.com/ に教会の Instagram を管理する人の Facebook アカウントでログインし、開発者登録
2. **アプリを作成** → ユースケースで **「Instagramでメッセージやコンテンツを管理」** を選択
3. ユースケース → カスタマイズ → **アクセス許可と機能** で `instagram_business_basic` と `instagram_business_content_publish` を追加
4. **API setup with Unified login** を開き、教会のビジネスポートフォリオ（Tokyo Sophia Int'l Church）をリンク
5. **新しい設定を作成** → ビジネスアセットに教会の Instagram アカウントを選択 → 完了後に表示される **システムユーザーアクセストークン** をコピー（これが `IG_ACCESS_TOKEN`、**無期限**）
   - Instagram アカウントの ID はプログラムが自動で調べるので不要です

> 自分の教会アカウントへの投稿だけならアプリ審査（App Review）は不要です（アプリに登録したアカウントのみ利用可）。

### 3. Anthropic（Claude）の API キーを取得する

https://platform.claude.com/ でアカウント作成 → API Keys でキーを作成（これが `ANTHROPIC_API_KEY`）。
費用の目安：1回の投稿で数円程度（週2回で月数十円程度）。

### 4. GitHub リポジトリを作る

1. GitHub で **公開（Public）リポジトリ** を作成し、このフォルダの中身をすべてアップロード
   - Instagram が画像を取りに来るため、画像を公開URLに置く必要があります。教会の公開投稿用の画像・文章しか置かないので、公開で問題ありません。**APIキーなどの秘密情報はリポジトリには入りません**（次の Secrets に保存）
2. リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を登録

| 名前 | 値 |
|---|---|
| `ANTHROPIC_API_KEY` | 手順3のキー |
| `IG_ACCESS_TOKEN` | 手順2のトークン |
| `PIXABAY_API_KEY` | https://pixabay.com/api/docs/ に無料登録・ログインすると表示されるキー（背景写真用） |

### 5. 動作確認

1. `config.yaml` の「（要記入）」を書き換える
2. リポジトリの **Actions** タブ → **Instagram 自動投稿** → **Run workflow**
   - まず **dry_run にチェック**、date に次の水曜か土曜の日付を入れて実行 → `posts/` に作られた画像と文章を確認
   - 問題なければ dry_run なしで実行すると実際に投稿されます（同じ日付は二重投稿されません）
3. 以降は水曜・土曜 18:00 に自動で投稿されます

> 注：GitHub Actions の定時実行は混雑時に数分〜数十分遅れることがあります。
> また、リポジトリに60日間更新がないと定時実行が止まりますが、このプログラムは毎回 `posts/` を更新するので止まりません。

---

## よくある変更

- **投稿の曜日・時刻を変える**：`.github/workflows/post.yml` の `cron` と、`config.yaml` の `themes`（曜日名のキー）を合わせて変更。cron は UTC 表記なので日本時間から9時間引きます（例：日本時間 金曜 7:00 → `0 22 * * 4`）
- **投稿文の雰囲気を変える**：`config.yaml` の各テーマの `instruction` を書き換える
- **画像の色を変える**：`config.yaml` の `colors`
- **その回だけ止めたい**：Actions タブで該当ワークフローを一時的に Disable

## 手元のパソコンで試す（任意）

```bash
pip install -r requirements.txt
python main.py prepare --skip-ai --date 2026-11-21   # AIなしで画像の見た目を確認
ANTHROPIC_API_KEY=... python main.py prepare --date 2026-11-21   # AIで文章も作成
```
