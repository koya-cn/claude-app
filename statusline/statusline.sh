#!/bin/sh
input=$(cat)

# プロジェクト名とブランチ
cwd=$(echo "$input" | jq -r '.cwd // empty')
branch=$(echo "$input" | jq -r '.git.branch // empty')

if [ -z "$cwd" ]; then
  cwd=$(pwd)
fi
project=$(basename "$cwd")

if [ -z "$branch" ]; then
  branch=$(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null)
fi

# レート制限・コンテキスト
five=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
five_reset=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
week=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
week_reset=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')
used=$(echo "$input" | jq -r '.context_window.used_percentage // empty')

format_remaining() {
  reset_at="$1"
  now=$(date +%s)
  diff=$((reset_at - now))
  if [ "$diff" -le 0 ]; then
    echo "まもなく"
  elif [ "$diff" -lt 3600 ]; then
    echo "残$((diff / 60))m"
  elif [ "$diff" -lt 86400 ]; then
    h=$((diff / 3600))
    m=$(( (diff % 3600) / 60 ))
    echo "残${h}h${m}m"
  else
    d=$((diff / 86400))
    h=$(( (diff % 86400) / 3600 ))
    echo "残${d}d${h}h"
  fi
}

out=""

# プロジェクト＋ブランチ
if [ -n "$project" ] && [ -n "$branch" ]; then
  out="${project} | ${branch}"
elif [ -n "$project" ]; then
  out="${project}"
fi

if [ -n "$five" ]; then
  [ -n "$out" ] && out="$out | "
  label="5h: $(printf '%.0f' "$five")%"
  [ -n "$five_reset" ] && label="$label $(format_remaining "$five_reset")"
  out="$out$label"
fi

if [ -n "$week" ]; then
  [ -n "$out" ] && out="$out | "
  label="7d: $(printf '%.0f' "$week")%"
  [ -n "$week_reset" ] && label="$label $(format_remaining "$week_reset")"
  out="$out$label"
fi

if [ -n "$used" ]; then
  [ -n "$out" ] && out="$out | "
  out="${out}ctx: $(printf '%.0f' "$used")%"
fi

echo "$out"
