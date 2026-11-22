#!/bin/bash
# Mac の初期設定（一度だけ）：GitHub への送信と、Claude の API キーの登録
cd "$(dirname "$0")"

echo ""
echo "===== 1/2 GitHub への送信 ====="
echo "Username を聞かれたら:  izumitakayanagi-cell  と入力して Enter"
echo "Password を聞かれたら:  GitHub で作ったトークン（github_pat_ で始まる）を貼り付けて Enter"
echo "（貼り付けても画面には何も表示されませんが、入っています）"
echo ""
if git push -u origin main; then
  echo ""
  echo "✅ GitHub への送信ができました（次回からは聞かれません）"
else
  echo ""
  echo "❌ GitHub への送信に失敗しました。このウインドウの内容を Claude に見せてください。"
  exit 1
fi

echo ""
echo "===== 2/2 Claude の API キーを Mac のキーチェーンに保存 ====="
echo "「password data for new item」と出たら、Claude の API キー（sk-ant- で始まる）を貼り付けて Enter"
echo "「retype password」と出たら、もう一度貼り付けて Enter"
echo ""
security delete-generic-password -s church-instagram-anthropic >/dev/null 2>&1
if security add-generic-password -s church-instagram-anthropic -a api -w; then
  echo ""
  echo "✅ すべて完了しました。このウインドウは閉じて大丈夫です。"
else
  echo ""
  echo "❌ キーの保存に失敗しました。このウインドウの内容を Claude に見せてください。"
fi
