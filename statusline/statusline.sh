#!/bin/sh
input=$(cat)
now=$(date +%s)
esc=$(printf '\033')

# プロジェクト名とブランチ
cwd=$(echo "$input" | jq -r '.cwd // empty')
branch=$(echo "$input" | jq -r '.git.branch // empty')

# 選択中のモデル
model=$(echo "$input" | jq -r '.model.display_name // empty')

# 上限に当たったときは Vertex へ切り替えて使うため、どちら経由かを見分けられるようにする。
# statusline の入力 JSON には接続先が含まれないので、Claude Code 自身が参照する環境変数で判定する。
case "$(printf '%s' "${CLAUDE_CODE_USE_VERTEX:-}" | tr 'A-Z' 'a-z')" in
  1|true|yes) vertex=1 ;;
  *) vertex="" ;;
esac

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

# 使用率は API 応答でしか更新されず、アイドル中のセッションは古い値を持ち続ける。
# 各セッションが最新値を共有ファイルへ集約し、より新しいほうを採用する。
share_dir="${XDG_CACHE_HOME:-$HOME/.cache}/claude-statusline"
share_file="$share_dir/ratelimits"
shared_line=$(head -n 1 "$share_file" 2>/dev/null)

# 採用ルール: resets_at が未来のほうが新しい情報を持つ。
# 同一ウィンドウ内では使用率が単調増加するため、resets_at が同じなら % が大きいほうが新しい。
# 最大値を取る演算なので、同時に書き込まれても値は収束する。
merged=$(awk -v mf="$five" -v mfr="$five_reset" -v mw="$week" -v mwr="$week_reset" \
             -v shared="$shared_line" '
function num(v, d) { return (v == "" || v == "-") ? d : v + 0 }
function merge(mp, mr, sp, sr,   mpn, mrn, spn, srn, wp, wr) {
  mpn = num(mp, -1); mrn = num(mr, 0)
  spn = num(sp, -1); srn = num(sr, 0)
  if (mrn > srn)      { wp = mpn; wr = mrn }
  else if (mrn < srn) { wp = spn; wr = srn }
  else                { wr = mrn; wp = (mpn >= spn) ? mpn : spn }
  return (wp < 0 ? "-" : wp) " " (wr <= 0 ? "-" : sprintf("%d", wr))
}
BEGIN {
  if (split(shared, s, " ") != 4) { s[1] = "-"; s[2] = "-"; s[3] = "-"; s[4] = "-" }
  print merge(mf, mfr, s[1], s[2]) " " merge(mw, mwr, s[3], s[4])
}')

# 同一ディレクトリ内の mv で原子的に差し替える
if [ "$merged" != "$shared_line" ]; then
  if mkdir -p "$share_dir" 2>/dev/null; then
    tmp="$share_file.$$"
    if printf '%s\n' "$merged" > "$tmp" 2>/dev/null; then
      mv -f "$tmp" "$share_file" 2>/dev/null || rm -f "$tmp"
    fi
  fi
fi

set -- $merged
five=$1
five_reset=$2
week=$3
week_reset=$4
[ "$five" = "-" ] && five=""
[ "$five_reset" = "-" ] && five_reset=""
[ "$week" = "-" ] && week=""
[ "$week_reset" = "-" ] && week_reset=""

format_remaining() {
  reset_at="$1"
  diff=$((reset_at - now))
  if [ "$diff" -le 0 ]; then
    echo "期限切れ"
  elif [ "$diff" -lt 60 ]; then
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

# 文字列を SGR コードで色付けし、後続に色が漏れないよう必ずリセットする
paint() {
  printf '%s[%sm%s%s[0m' "$esc" "$1" "$2" "$esc"
}

# 上限が近いことを数字を読まずに気づけるよう、表示上の整数値で 4 段階に色分けする。
# 色は暗い背景で読みやすいものを選び、数値は太字にして区切りの中から拾いやすくする。
colorize_pct() {
  n=$(printf '%.0f' "$1")
  if [ "$n" -ge 80 ]; then
    c='31'
  elif [ "$n" -ge 50 ]; then
    c='38;5;208'
  elif [ "$n" -ge 30 ]; then
    c='33'
  else
    c='32'
  fi
  paint "1;$c" "${n}%"
}

# ウィンドウ終了後の使用率は実態を表さないので伏せる
render_limit() {
  name="$1"
  pct="$2"
  reset_at="$3"
  [ -z "$pct" ] && [ -z "$reset_at" ] && return
  if [ -n "$reset_at" ] && [ "$reset_at" -le "$now" ]; then
    echo "${name}: $(paint 90 '--% 期限切れ')"
    return
  fi
  if [ -n "$pct" ]; then
    label="${name}: $(colorize_pct "$pct")"
  else
    label="${name}: $(paint 90 '--%')"
  fi
  [ -n "$reset_at" ] && label="$label $(format_remaining "$reset_at")"
  echo "$label"
}

out=""

# プロジェクト＋ブランチ
if [ -n "$project" ] && [ -n "$branch" ]; then
  out="${project} | ${branch}"
elif [ -n "$project" ]; then
  out="${project}"
fi

if [ -n "$model" ]; then
  [ -n "$out" ] && out="$out | "
  out="${out}${model}"
fi

# Vertex のときだけバッジを出す（通常の API 経由では何も足さない）
if [ -n "$vertex" ]; then
  [ -n "$out" ] && out="$out | "
  out="${out}$(paint '1;33' Vertex)"
fi

five_label=$(render_limit "5h" "$five" "$five_reset")
if [ -n "$five_label" ]; then
  [ -n "$out" ] && out="$out | "
  out="$out$five_label"
fi

week_label=$(render_limit "7d" "$week" "$week_reset")
if [ -n "$week_label" ]; then
  [ -n "$out" ] && out="$out | "
  out="$out$week_label"
fi

if [ -n "$used" ]; then
  [ -n "$out" ] && out="$out | "
  out="${out}ctx: $(colorize_pct "$used")"
fi

echo "$out"
