#!/bin/bash
# ニュースボードの取得停止を検知し、GitHub Actionsのscheduleが未発火
# だった場合の保険として手動でworkflow_dispatchする。
#
# 実測(2026-09-21、ユーザー報告「更新や取得が止まっている気がする」の
# 調査結果): update.ymlのcronは日中30分おき・夜間2時間おきの想定だが、
# 直近3.5日分の実行履歴を計測したところ、実際の間隔は26分〜462分
# (7.7時間)とばらつき、想定トリガー数に対して実際に発火したのは
# 約35%だけだった(全て成功はしているので、コード側の問題ではなく
# GitHub Actions自体のschedule triggerが高負荷時に遅延・ドロップされる
# という、GitHub公式ドキュメントにも明記された既知の制約)。ワークフロー
# 側のcron設定を変えても解決しないため、ユーザー自身のMac(既に
# jp-stock-dashboardのlaunchdジョブが同じ役割を local に担っている)で
# このウォッチドッグを走らせ、「最終更新から一定時間以上経っていたら
# 手動でworkflow_dispatchする」形でGitHub側の未発火を補う。
set -uo pipefail

NEWS_URL="https://tkynakanaka0506-ux.github.io/jp-news-dashboard/news.json"
REPO="tkynakanaka0506-ux/jp-news-dashboard"
STALE_THRESHOLD_MIN=180
LOG_FILE="$HOME/Library/Logs/jp-news-watchdog.log"
GH_BIN="/Users/takuya/bin/gh"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"; }

GENERATED_ISO=$(curl -fsSL --max-time 20 "$NEWS_URL" | /usr/bin/python3 -c "import json,sys; print(json.load(sys.stdin).get('generated_iso',''))" 2>/dev/null)
if [ -z "$GENERATED_ISO" ]; then
  log "⚠️ news.jsonの取得/パースに失敗。今回はスキップ(次回に委ねる)"
  exit 0
fi

AGE_MIN=$(/usr/bin/python3 -c "
from datetime import datetime
gen = datetime.fromisoformat('$GENERATED_ISO')
now = datetime.now(gen.tzinfo) if gen.tzinfo else datetime.now()
print(int((now - gen).total_seconds() / 60))
" 2>/dev/null)

if [ -z "$AGE_MIN" ]; then
  log "⚠️ 経過時間の計算に失敗(generated_iso=$GENERATED_ISO)。今回はスキップ"
  exit 0
fi

if [ "$AGE_MIN" -lt "$STALE_THRESHOLD_MIN" ]; then
  log "✅ 最終更新から${AGE_MIN}分。正常(閾値${STALE_THRESHOLD_MIN}分)"
  exit 0
fi

log "⏰ 最終更新から${AGE_MIN}分経過(閾値${STALE_THRESHOLD_MIN}分)。GitHub Actionsのscheduleが未発火の可能性 → workflow_dispatchで補う"
if "$GH_BIN" workflow run update.yml -R "$REPO" -f mode=evening >>"$LOG_FILE" 2>&1; then
  log "✅ workflow_dispatch成功"
else
  log "❌ workflow_dispatch失敗(gh認証・ネットワークを確認してください)"
fi
