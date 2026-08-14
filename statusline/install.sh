#!/bin/sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATUSLINE_SH="$SCRIPT_DIR/statusline.sh"
SETTINGS="$HOME/.claude/settings.json"

echo "Installing statusline..."

if [ ! -f "$SETTINGS" ]; then
  echo "Error: $SETTINGS not found"
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is required but not installed"
  exit 1
fi

# 既存の statusLine 設定を確認
existing=$(jq -r '.statusLine.command // empty' "$SETTINGS")

if [ -n "$existing" ]; then
  echo ""
  echo "既存の statusLine 設定が見つかりました:"
  echo "  $existing"
  echo ""
  printf "上書きしますか？ [y/N] "
  read answer
  case "$answer" in
    [yY]|[yY][eE][sS]) ;;
    *)
      echo "インストールをキャンセルしました。"
      exit 0
      ;;
  esac
fi

tmp=$(mktemp)
# refreshInterval を入れないとイベント発生時しか再描画されず、
# レート制限で操作が止まっている間は残時間表示が固まったままになる
jq --arg cmd "bash $STATUSLINE_SH" \
  '.statusLine = {"type": "command", "command": $cmd, "refreshInterval": 60}' \
  "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"

echo "Done: statusLine -> $STATUSLINE_SH"
echo "Restart Claude Code to apply changes."
