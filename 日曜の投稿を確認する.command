#!/bin/bash
# ダブルクリックすると、直近の日曜日の「投稿確認ページ」を開きます
cd "$(dirname "$0")"
.venv/bin/python sunday_review.py "$@"
