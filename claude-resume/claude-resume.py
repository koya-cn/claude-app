#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


CONFIG_FILE = Path.home() / ".claude-resume.json"
JST = timezone(timedelta(hours=9))


def load_target_dirs(debug=False):
    if debug:
        print(f"[DEBUG] CONFIG_FILE: {CONFIG_FILE}")
        print(f"[DEBUG] exists: {CONFIG_FILE.exists()}")

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            dirs = config.get("dirs", [])
            if debug:
                print(f"[DEBUG] dirs in config: {dirs}")
            if dirs:
                resolved = [Path(d).expanduser() for d in dirs]
                if debug:
                    for p in resolved:
                        print(f"[DEBUG] resolved path: {p} (exists: {p.exists()})")
                return resolved
        except (json.JSONDecodeError, KeyError) as e:
            if debug:
                print(f"[DEBUG] config parse error: {e}")
    default = [Path.home() / ".claude"]
    if debug:
        print(f"[DEBUG] using default: {default[0]} (exists: {default[0].exists()})")
    return default


def _format_timestamp(ts):
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


# ─── キーワード検索（既存機能） ───────────────────────────────────────────────

def fuzzy_search_prompts(keyword, target_dirs=None, debug=False, return_data=False):
    if target_dirs is None:
        target_dirs = load_target_dirs(debug=debug)

    if not return_data:
        print(f"🔍 検索キーワード: '{keyword}' (ユーザー＆AIの出力を検索)")
        print("=" * 60)

    hit_count = 0
    results_by_project = defaultdict(list)

    for target_dir in target_dirs:
        if not target_dir.exists() or not target_dir.is_dir():
            print(f"ディレクトリが見つかりません: {target_dir}")
            continue

        for filepath in target_dir.rglob("*"):
            if filepath.suffix not in ['.json', '.jsonl'] or not filepath.is_file():
                continue

            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line.startswith('{'):
                            continue

                        try:
                            data = json.loads(line)

                            role = None
                            if data.get("type") == "user" or (isinstance(data.get("message"), dict) and data["message"].get("role") == "user"):
                                role = "user"
                            elif data.get("type") == "assistant" or (isinstance(data.get("message"), dict) and data["message"].get("role") == "assistant"):
                                role = "assistant"

                            if role:
                                content = ""
                                if "message" in data and "content" in data["message"]:
                                    raw_content = data["message"]["content"]
                                    if isinstance(raw_content, str):
                                        content = raw_content
                                    elif isinstance(raw_content, list):
                                        content = " ".join([
                                            item.get("text", "")
                                            for item in raw_content
                                            if isinstance(item, dict) and item.get("type") == "text"
                                        ])

                                if keyword.lower() in content.lower():
                                    session_id = data.get("sessionId")
                                    cwd = data.get("cwd", "")
                                    project_key = cwd if cwd else "(Global/No Directory)"

                                    hit_line = ""
                                    for text_line in content.split('\n'):
                                        if keyword.lower() in text_line.lower():
                                            hit_line = text_line.strip()
                                            break

                                    display_text = hit_line if hit_line else content.split('\n')[0]

                                    if len(display_text) > 80:
                                        display_text = display_text[:80] + "..."

                                    timestamp = data.get("timestamp", "")

                                    results_by_project[project_key].append({
                                        "role": role,
                                        "prompt": display_text,
                                        "session_id": session_id,
                                        "timestamp": timestamp
                                    })
                                    hit_count += 1

                        except json.JSONDecodeError:
                            continue
            except UnicodeDecodeError:
                pass

    if return_data:
        # API用: 構造化データを返す
        result = []
        for project_dir, matches in results_by_project.items():
            for match in matches:
                result.append({
                    "project": project_dir,
                    "role": match["role"],
                    "prompt": match["prompt"],
                    "session_id": match["session_id"],
                    "timestamp": _format_timestamp(match["timestamp"])
                })
        return result

    # CLI用: 表示
    for project_dir, matches in results_by_project.items():
        print(f"📁 Project: {project_dir}")

        sessions_in_project = {}
        for match in matches:
            sid = match["session_id"]
            if sid not in sessions_in_project:
                sessions_in_project[sid] = []

            item = (match["role"], match["prompt"], match["timestamp"])
            if item not in sessions_in_project[sid]:
                sessions_in_project[sid].append(item)

        for sid, items in sessions_in_project.items():
            for role, text, ts in items:
                icon = "🙋" if role == "user" else "🤖"
                ts_str = f" [{_format_timestamp(ts)}]" if ts else ""
                print(f"   {icon}{ts_str} {text}")

            if sid:
                if project_dir != "(Global/No Directory)":
                    print(f"   🚀 コマンド: cd {project_dir} && claude --resume {sid}")
                else:
                    print(f"   🚀 コマンド: claude --resume {sid}")
            print("   " + "-" * 57)

    print(f"🎯 計 {hit_count} 件のメッセージが見つかりました。")


# ─── 直近セッション一覧（新機能） ────────────────────────────────────────────

def load_recent_sessions(count=5, debug=False):
    target_dirs = load_target_dirs(debug=debug)

    sessions = {}      # sessionId -> dict (history.jsonl 由来)
    win_sessions = {}  # sessionId -> dict (sessions/*.json 由来)

    for base_dir in target_dirs:
        if not base_dir.exists() or not base_dir.is_dir():
            if debug:
                print(f"[DEBUG] skip (not found): {base_dir}")
            continue

        # ── A) history.jsonl ──────────────────────────────
        history_file = base_dir / "history.jsonl"
        if history_file.exists():
            if debug:
                print(f"[DEBUG] reading history.jsonl: {history_file}")
            with open(history_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    sid = entry.get("sessionId")
                    if not sid:
                        continue

                    display   = entry.get("display", "").strip()
                    timestamp = entry.get("timestamp", 0)
                    project   = entry.get("project", "")

                    if sid not in sessions:
                        sessions[sid] = {
                            "session_id":   sid,
                            "project":      project,
                            "project_name": Path(project).name if project else "(不明)",
                            "session_name": display if display else "(名前なし)",
                            "first_ts":     timestamp,
                            "last_ts":      timestamp,
                            "prompts":      [],
                        }

                    sess = sessions[sid]
                    if timestamp < sess["first_ts"]:
                        sess["first_ts"] = timestamp
                    if timestamp > sess["last_ts"]:
                        sess["last_ts"]      = timestamp
                        sess["project"]      = project
                        sess["project_name"] = Path(project).name if project else "(不明)"
                    if display:
                        sess["prompts"].append({"display": display, "timestamp": timestamp})

        # ── B) sessions/*.json ────────────────────────────
        sessions_dir = base_dir / "sessions"
        if sessions_dir.exists() and sessions_dir.is_dir():
            if debug:
                print(f"[DEBUG] reading sessions/: {sessions_dir}")
            for json_file in sessions_dir.glob("*.json"):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        entry = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue

                sid = entry.get("sessionId")
                if not sid:
                    continue

                timestamp = entry.get("startedAt", 0)
                cwd       = entry.get("cwd", "")
                try:
                    project_name = Path(cwd).name if cwd else "(不明)"
                except Exception:
                    project_name = "(不明)"

                if sid not in win_sessions:
                    win_sessions[sid] = {
                        "session_id":   sid,
                        "project":      cwd,
                        "project_name": project_name,
                        "session_name": "(名前なし)",
                        "first_ts":     timestamp,
                        "last_ts":      timestamp,
                        "prompts":      [],
                    }
                else:
                    w = win_sessions[sid]
                    if timestamp < w["first_ts"]:
                        w["first_ts"] = timestamp
                    if timestamp > w["last_ts"]:
                        w["last_ts"]      = timestamp
                        w["project"]      = cwd
                        w["project_name"] = project_name

    # ── 重複排除: history.jsonl 側を優先 ─────────────────
    for sid, entry in win_sessions.items():
        if sid not in sessions:
            sessions[sid] = entry

    # ── /resume 等のスラッシュコマンドだけのセッションを除外 ──
    _SKIP_CMDS = {'/resume', '/new', '/clear', '/help'}

    def _is_slash_only(sess):
        meaningful = [p["display"].strip() for p in sess.get("prompts", []) if p["display"].strip()]
        return bool(meaningful) and all(d in _SKIP_CMDS for d in meaningful)

    sessions = {sid: s for sid, s in sessions.items() if not _is_slash_only(s)}

    if not sessions:
        print("セッション履歴が見つかりません。")
        return []

    sorted_sessions = sorted(sessions.values(), key=lambda s: s["last_ts"], reverse=True)
    result = []
    for sess in sorted_sessions:
        sess["messages"] = load_session_messages(sess["session_id"], target_dirs)
        if sess["messages"]:
            result.append(sess)
        if len(result) >= count:
            break
    return result


def load_session_messages(session_id, target_dirs):
    """セッションの全メッセージを (role, text) リストで返す"""
    for base_dir in target_dirs:
        projects_dir = base_dir / "projects"
        if not projects_dir.exists():
            continue
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            jsonl_file = project_dir / f"{session_id}.jsonl"
            if not jsonl_file.exists():
                continue
            messages = []
            try:
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        msg = entry.get('message', {})
                        if not msg:
                            continue
                        role = msg.get('role')
                        content = msg.get('content', '')
                        if isinstance(content, list):
                            text = '\n'.join(
                                c.get('text', '')
                                for c in content
                                if isinstance(c, dict) and c.get('type') == 'text'
                            )
                        else:
                            text = str(content)
                        text = text.strip()
                        if role in ('user', 'assistant') and text:
                            messages.append((role, text))
            except (OSError, UnicodeDecodeError):
                pass
            return messages
    return []


def format_timestamp(ts_ms):
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(JST)
    return dt.strftime("%m/%d %H:%M")


def _group_turns(messages):
    """messages を (user_msg, ai_msg) のターンリストにグループ化する"""
    turns = []
    i = 0
    while i < len(messages):
        if messages[i][0] == 'user':
            user_msg = messages[i]
            ai_msg = None
            if i + 1 < len(messages) and messages[i + 1][0] == 'assistant':
                ai_msg = messages[i + 1]
                i += 2
            else:
                i += 1
            turns.append((user_msg, ai_msg))
        else:
            i += 1
    return turns


def display_recent_sessions(sessions):
    print(f"📋 直近のセッション ({len(sessions)}件)")
    print("=" * 60)

    for i, sess in enumerate(sessions, 1):
        ts_str = format_timestamp(sess["last_ts"])
        project = sess["project"]
        project_name = sess["project_name"]
        session_name = sess.get("session_name", "(名前なし)")
        sid = sess["session_id"]
        prompt_count = len(sess["prompts"])

        print(f" [{i:2}] {ts_str} | {project_name} - {session_name}")

        messages = sess.get("messages", [])
        if messages:
            all_turns = _group_turns(messages)
            turns = all_turns[-5:]
            turn_offset = len(all_turns) - len(turns) + 1
            for t_idx, (user_msg, ai_msg) in enumerate(turns, turn_offset):
                print(f"      ── Turn {t_idx} ──")
                for icon, label, msg_data in [("🙋", "User", user_msg), ("🤖", "AI  ", ai_msg)]:
                    if not msg_data:
                        continue
                    lines = [l for l in msg_data[1].split('\n') if l.strip()]
                    cur_icon, cur_label = icon, label
                    for line in lines[:5]:
                        if len(line) > 72:
                            line = line[:72] + "..."
                        print(f"      {cur_icon} {cur_label}: {line}")
                        cur_icon, cur_label = "  ", "    "
                    if len(lines) > 5:
                        print(f"               ...")
        else:
            if sess["prompts"]:
                latest = max(sess["prompts"], key=lambda p: p["timestamp"])
                last_prompt = latest["display"]
                if len(last_prompt) > 60:
                    last_prompt = last_prompt[:60] + "..."
                print(f"      💬 {last_prompt}")

        print(f"      📝 {prompt_count} prompts")
        if project:
            print(f"      🚀 cd {project} && claude --resume {sid}")
        else:
            print(f"      🚀 claude --resume {sid}")
        print("      " + "-" * 54)


# ─── Claude 要約（新機能） ────────────────────────────────────────────────────

def build_summary_prompt(sessions):
    lines = [
        "以下は最近のClaude CLIセッションの履歴です。",
        "各セッションで何をしていたか、日本語で箇条書き（1行ずつ）で簡潔に要約してください。",
        "フォーマット例: `1. プロジェクト名: 作業内容`",
        "",
    ]

    for i, sess in enumerate(sessions, 1):
        ts_str = format_timestamp(sess["last_ts"])
        project_name = sess["project_name"]
        lines.append(f"Session {i} ({project_name}, {ts_str}):")

        messages = sess.get("messages", [])
        if messages:
            role_label = {"user": "User", "assistant": "AI"}
            for user_msg, ai_msg in _group_turns(messages)[-5:]:
                for msg in [user_msg, ai_msg]:
                    if not msg:
                        continue
                    label = role_label.get(msg[0], msg[0])
                    text = msg[1].replace("\n", " ").strip()
                    if len(text) > 100:
                        text = text[:100] + "..."
                    lines.append(f"  [{label}] {text}")
        else:
            sorted_prompts = sorted(sess["prompts"], key=lambda p: p["timestamp"], reverse=True)
            for p in sorted_prompts[:3]:
                text = p["display"].replace("\n", " ").strip()
                if len(text) > 100:
                    text = text[:100] + "..."
                lines.append(f"  - {text}")
        lines.append("")

    return "\n".join(lines)


def build_single_session_summary_prompt(messages):
    """単一セッションの要約用プロンプトを生成。

    先頭3ターン + 直近5ターンに絞り、各メッセージは500文字超を切り詰める。
    """
    turns = _group_turns(messages)
    head_n, tail_n = 8, 5

    if len(turns) <= head_n + tail_n:
        selected = [(idx + 1, t) for idx, t in enumerate(turns)]
        elided = False
    else:
        head = [(idx + 1, turns[idx]) for idx in range(head_n)]
        tail = [(len(turns) - tail_n + idx + 1, turns[len(turns) - tail_n + idx]) for idx in range(tail_n)]
        selected = head + tail
        elided = True

    role_label = {"user": "User", "assistant": "AI"}
    lines = [
        "以下は Claude CLI セッションの会話ログです。",
        "セッション全体を通じて何をしていたかを、主要なトピックが複数ある場合はそれぞれ含めて、",
        "日本語で 3〜5 行の箇条書きに簡潔にまとめてください。",
        "前置きや締めの文は不要、箇条書きのみ出力してください。",
        "",
    ]

    last_idx = None
    for turn_idx, (user_msg, ai_msg) in selected:
        if elided and last_idx is not None and turn_idx != last_idx + 1:
            lines.append("(中略)")
        lines.append(f"── Turn {turn_idx} ──")
        for msg in (user_msg, ai_msg):
            if not msg:
                continue
            label = role_label.get(msg[0], msg[0])
            text = msg[1].strip()
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"[{label}] {text}")
        lines.append("")
        last_idx = turn_idx

    return "\n".join(lines)


def summarize_with_claude(prompt_text, timeout=60):
    """Claude(Haiku) で要約。成功時は (text, None)、失敗時は (None, 理由文字列) を返す。"""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        msg = "`claude` コマンドが見つかりません（PATH 未設定の可能性）。Claude CLI を確認してください。"
        print(f"⚠️  {msg}")
        return None, msg

    try:
        result = subprocess.run(
            [claude_bin, "-p", "--model", "claude-haiku-4-5-20251001", "--no-session-persistence"],
            input=prompt_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip()[:300]
            msg = f"Claude 実行に失敗しました (exit {result.returncode}): {err}"
            print(f"⚠️  {msg}")
            return None, msg
        return result.stdout.strip(), None
    except subprocess.TimeoutExpired:
        msg = f"Claude の応答がタイムアウトしました（{timeout}秒）。候補が多い場合は --strict で件数を絞ってください。"
        print(f"⚠️  {msg}")
        return None, msg
    except Exception as e:
        msg = f"Claude 呼び出しでエラー: {e}"
        print(f"⚠️  {msg}")
        return None, msg


# ─── 未完了検出（新機能） ─────────────────────────────────────────────────────

# やり残しを示唆する表現（末尾AI発話に含まれると未完了の疑い）
_LEFTOVER_PHRASES = [
    "あとで", "後で", "残り", "残っ", "未実装", "未対応", "未完了", "未着手",
    "次は", "次に", "TODO", "todo", "保留", "一旦", "ひとまず", "とりあえず",
    "続き", "中断", "お願いします", "確認してください", "直して", "修正が必要",
    # 外部・他者待ち（blocked）を示す表現
    "対応待ち", "返答待ち", "確認待ち", "承認待ち", "結果待ち", "回答待ち",
    "問い合わせ中", "ペンディング", "待っている間",
    # 次工程・積み残しを示す表現（「〇〇完了。次は△△」型の取りこぼし対策）
    "次のステップ", "次のフェーズ", "次セッション", "未処理",
]
# 質問・確認待ちで終わる語尾（弱シグナル）
_QUESTION_TAILS = [
    "?", "？", "ますか", "でしょうか", "どちら", "よろしい",
    "進めて", "いいですか", "どうし", "教えてください",
]
# 完了報告を示す表現（末尾AI発話の末尾付近にあれば「完了」とみなす）
_COMPLETION_PHRASES = [
    "完了しました", "完了です", "完了！", "できました", "成功しました",
    "マージしました", "マージ済み", "マージ完了", "デプロイしました",
    "完成しました", "解決しました", "対応しました", "終わりました",
    "問題ありません", "問題ないです", "正常に", "✅",
    # commit / push / PR 作成も完了とみなす（ユーザー方針）。過去形に限定し
    # 「PR作成しますか?」等の予定・質問を拾わないようにする。
    "コミットしました", "コミット完了", "プッシュしました", "プッシュ完了",
    "コミット・プッシュ", "PRを作成しました", "PR作成完了", "PRを出しました",
    "プルリクを作成", "プルリクエストを作成", "PR を作成しました",
]
# スキル/コマンド実行ログ（ノイズ）: 初回ユーザー発話がこれで始まるものは除外
_NOISE_PREFIXES = [
    "# Observer Start", "# Instinct", "# Evolve", "# Usage Report",
    "# Team", "# Code Review Workflow", "# Promote", "# Daily",
    "/instinct", "/observer", "/evolve", "/usage-report", "/team-",
    "IMPORTANT: You are running in non-interactive",
]


# メタメッセージ（コマンド展開・caveat・system-reminder 等）の先頭マーカー
_META_PREFIXES = (
    "<command-message>", "<command-name>", "<command-args>",
    "<local-command-",  # stdout / stderr / caveat をまとめて除外
    "<bash-", "<system-reminder>",
    "Caveat: The messages below",
)


def _is_meta(text):
    """ユーザー発話に紛れるメタ/コマンド注入メッセージか判定する。"""
    t = text.strip()
    return (not t) or t.startswith(_META_PREFIXES)


def _first_line(text, maxlen=110):
    """先頭の意味ある1行を抜き出す。"""
    for line in text.split("\n"):
        s = line.strip()
        if s:
            return (s[:maxlen] + "…") if len(s) > maxlen else s
    return ""


def _snippet_with(text, needle, maxlen=110):
    """text の中で needle を含む行（なければ前後）を短く抜き出す。needle 無しは先頭行。"""
    if not needle:
        return _first_line(text, maxlen)
    for line in text.split("\n"):
        if needle in line:
            s = line.strip()
            return (s[:maxlen] + "…") if len(s) > maxlen else s
    idx = text.find(needle)
    if idx >= 0:
        start = max(0, idx - 25)
        s = text[start:idx + maxlen].strip().replace("\n", " ")
        return ("…" if start > 0 else "") + s
    return ""


def _last_line(text, maxlen=110):
    """末尾の意味ある1行を抜き出す。"""
    for line in reversed(text.split("\n")):
        s = line.strip()
        if s:
            return (s[:maxlen] + "…") if len(s) > maxlen else s
    return ""


def detect_incomplete(messages):
    """(role, text) 列から未完了シグナルをヒューリスティック抽出する（2層判定）。

    返り値: dict
      is_candidate: 未完了の疑いがあるか
      tier:         "strong"（明確なシグナル有） / "weak"（完了報告が無いだけ） / ""
      signals:      検出根拠のリスト
      evidence:     [{label, text}] 各シグナルが一致した実際の行（根拠）
      strength:     優先度スコア（大きいほど未完了の確度が高い）
      last_ai:      末尾AI発話の抜粋
      intent:       最初の意味あるユーザー発話
    """
    empty = {"is_candidate": False, "tier": "", "signals": [], "evidence": [], "strength": 0, "last_ai": "", "intent": ""}
    if not messages:
        return empty

    # メタ/コマンド注入メッセージを除いた実会話のみで判定する
    cleaned = [(r, t) for (r, t) in messages if not _is_meta(t)]
    if not cleaned:
        return empty

    # intent: 最初の意味あるユーザー発話
    intent = ""
    for role, text in cleaned:
        if role == "user" and text.strip():
            intent = text.strip()
            break

    # ノイズ（スキル実行ログ等）は除外
    if any(intent.startswith(p) for p in _NOISE_PREFIXES):
        return {**empty, "intent": intent}

    last_role, last_text = cleaned[-1]
    last_ai = ""
    for role, text in cleaned:
        if role == "assistant" and text.strip():
            last_ai = text

    signals = []
    evidence = []
    strength = 0

    # 1) 末尾がユーザー発言（AIが応答せず終了）
    if last_role == "user" and last_text.strip():
        signals.append("AI未応答で終了")
        evidence.append({"label": "未応答のまま終了した発言", "text": _snippet_with(last_text, "")})
        strength += 3

    # 2) やり残しを示唆する表現が末尾AI発話に含まれる
    hit_pos = sorted((last_ai.find(w), w) for w in _LEFTOVER_PHRASES if w in last_ai)
    if hit_pos:
        hit_words = [w for _, w in hit_pos]
        signals.append("やり残し表現: " + ", ".join(sorted(set(hit_words))[:5]))
        evidence.append({"label": "一致した行", "text": _snippet_with(last_ai, hit_words[0])})
        strength += 2

    # 3) 末尾AI発話に未チェックのチェックリスト（- [ ]）が残っている
    if "[ ]" in last_ai:
        signals.append("未対応のチェックリスト項目あり")
        evidence.append({"label": "未チェック項目", "text": _snippet_with(last_ai, "[ ]")})
        strength += 2

    # 4) 質問・確認待ちで終了（弱シグナル）
    tail = last_ai.strip()[-40:]
    if last_role == "assistant" and any(q in tail for q in _QUESTION_TAILS):
        signals.append("確認/承認待ち")
        evidence.append({"label": "末尾の問いかけ", "text": _last_line(last_ai)})
        strength += 1

    result = {
        "last_ai": last_ai[-300:].strip(),
        "intent": intent[:160],
    }

    # ── strong: 明確なシグナルあり ──
    if signals:
        return {**result, "is_candidate": True, "tier": "strong",
                "signals": signals, "evidence": evidence, "strength": strength}

    # ── weak: シグナルは無いが「完了報告」も無い（＝言い切れていない） ──
    # 完了報告で締めくくられていれば完了とみなして除外
    done_tail = last_ai.strip()[-120:]
    if any(p in done_tail for p in _COMPLETION_PHRASES):
        return {**result, "is_candidate": False, "tier": "", "signals": [], "evidence": [], "strength": 0}
    # 極端に短い問い（雑談・あいさつ・一問一答）は weak からも除外
    if len(intent.strip()) < 8:
        return {**result, "is_candidate": False, "tier": "", "signals": [], "evidence": [], "strength": 0}

    return {**result, "is_candidate": True, "tier": "weak",
            "signals": ["完了報告なし"],
            "evidence": [{"label": "末尾AI発話", "text": _last_line(last_ai)}],
            "strength": 0}


def scan_incomplete_sessions(limit=200, strict=False, debug=False):
    """全セッションを走査し、未完了候補を優先度（strength→新しさ）順で返す。

    strict=True のときは tier="strong"（明確なシグナル有）のみを返す。
    strict=False（既定）は weak（完了報告が無いだけ）も含めた高リコール。
    """
    sessions = load_recent_sessions(limit, debug=debug)
    candidates = []
    for sess in sessions:
        info = detect_incomplete(sess.get("messages", []))
        if not info["is_candidate"]:
            continue
        if strict and info["tier"] != "strong":
            continue
        candidates.append({
            "session_id":   sess["session_id"],
            "project":      sess["project"],
            "project_name": sess["project_name"],
            "session_name": sess.get("session_name", ""),
            "last_ts":      sess["last_ts"],
            "tier":         info["tier"],
            "signals":      info["signals"],
            "evidence":     info.get("evidence", []),
            "strength":     info["strength"],
            "last_ai":      info["last_ai"],
            "intent":       info["intent"],
        })
    candidates.sort(key=lambda c: (c["strength"], c["last_ts"]), reverse=True)
    return candidates


def incomplete_signature(candidates):
    """候補集合の同一性を表す署名。キャッシュ無効化判定に使う。"""
    parts = [f'{c["session_id"]}:{c["last_ts"]}' for c in candidates]
    return f"{len(candidates)}|" + "|".join(sorted(parts))


def build_incomplete_triage_prompt(candidates):
    """未完了候補を Claude に渡してトリアージ（分類・ノイズ除去）させるプロンプト。

    出力は JSON 配列のみ。各要素はセッション番号(n)・分類(cat)・残作業(note)。
    """
    lines = [
        "以下は Claude CLI のセッションのうち、未完了の疑いがあるものの一覧です。",
        "各セッションが本当に「やり残し」かを判断し、ノイズ（会話の自然な終了・",
        "質問への回答済み・既に完了）は除外してください。",
        "残ったものを次の3カテゴリに分類してください。",
        "  A: 中断・保留中（再開待ち）",
        "  B: ほぼ完了・最終工程が残る（PR作成 / マージ / 実機確認 など）",
        "  C: 議論・調査のみで未着手",
        "signals が「完了報告なし」のみの候補は確信度が低い。明確に完了済み・雑談・",
        "回答済みなら遠慮なく除外してよい（配列に含めない）。",
        "",
        "出力は JSON 配列のみ。前置き・説明・コードフェンス(```)は一切付けないこと。",
        '各要素の形式: {"n": セッション番号(整数), "cat": "A" | "B" | "C", "note": "残作業を簡潔に(日本語・60字以内)"}',
        "優先度（やり残しの確度・重要度）の高い順に並べること。",
        "",
    ]
    for i, c in enumerate(candidates, 1):
        ts = format_timestamp(c["last_ts"])
        tier = c.get("tier", "")
        lines.append(f"Session {i} ({c['project_name']}, {ts}, tier={tier}):")
        lines.append(f"  intent: {c['intent']}")
        lines.append(f"  signals: {', '.join(c['signals'])}")
        last_ai = c["last_ai"].replace("\n", " ").strip()
        lines.append(f"  末尾AI: {last_ai[:300]}")
        lines.append("")
    return "\n".join(lines)


def parse_triage_items(raw, candidates):
    """モデルの JSON 出力を構造化アイテムへ変換する。

    セッション番号(n)を candidates の index に対応付け、session_id 等を補完する。
    パースに失敗したら None（呼び出し側でフリーテキストにフォールバック）。
    """
    if not raw:
        return None
    text = raw.strip()
    # コードフェンスが付いた場合に備えて除去
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(arr, list):
        return None
    items = []
    for obj in arr:
        if not isinstance(obj, dict):
            continue
        try:
            idx = int(obj.get("n")) - 1
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(candidates):
            continue
        c = candidates[idx]
        cat = str(obj.get("cat", "")).strip().upper()
        if cat not in ("A", "B", "C"):
            cat = "C"
        items.append({
            "session_id":   c["session_id"],
            "project":      c["project"],
            "project_name": c["project_name"],
            "category":     cat,
            "note":         str(obj.get("note", "")).strip(),
        })
    return items


def _print_incomplete_item(i, c):
    ts = format_timestamp(c["last_ts"])
    print(f" [{i:2}] {ts} | {c['project_name']}  (強度 {c['strength']})")
    intent = c["intent"]
    if len(intent) > 70:
        intent = intent[:70] + "..."
    print(f"      💬 {intent}")
    for s in c["signals"]:
        print(f"      ⚠ {s}")
    for ev in c.get("evidence", []):
        if ev.get("text"):
            print(f"         └ {ev['label']}: {ev['text']}")
    if c["project"] and c["project"] != "(Global/No Directory)":
        print(f"      🚀 cd {c['project']} && claude --resume {c['session_id']}")
    else:
        print(f"      🚀 claude --resume {c['session_id']}")
    print("      " + "-" * 54)


def display_incomplete(candidates):
    """CLI 表示: 未完了候補の一覧（strong / weak の2層で区切る）。"""
    strong = [c for c in candidates if c.get("tier") == "strong"]
    weak = [c for c in candidates if c.get("tier") == "weak"]

    print(f"⚠️  未完了の疑いがあるセッション ({len(candidates)}件)")
    print("=" * 60)
    if not candidates:
        print("未完了の候補は見つかりませんでした。")
        return

    n = 0
    if strong:
        print(f"── 未完了のシグナルあり ({len(strong)}件) ──")
        for c in strong:
            n += 1
            _print_incomplete_item(n, c)
    if weak:
        print()
        print(f"── 完了報告のない作業（弱い候補・要確認 {len(weak)}件） ──")
        for c in weak:
            n += 1
            _print_incomplete_item(n, c)


# ─── Web UI 機能 ──────────────────────────────────────────────────────────────

def sessions_to_json_compatible(sessions):
    """セッションデータをJSON互換形式に変換"""
    result = []
    for s in sessions:
        d = dict(s)
        # tuple を list に変換
        d["messages"] = [[role, text] for role, text in s.get("messages", [])]
        result.append(d)
    return result


HTML_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8" />
<title>Claude Resume</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;450;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: #0e0e10; color: #e6e6e8; font-family: 'Inter', -apple-system, sans-serif; overflow: hidden; }
  #root { width: 100vw; height: 100vh; }
  * { box-sizing: border-box; }

  .ld-root { width: 100%; height: 100%; background: #0e0e10; color: #e6e6e8; font-family: 'Inter', -apple-system, sans-serif; font-size: 12.5px; display: flex; flex-direction: column; overflow: hidden; letter-spacing: -0.01em; position: relative; }

  .ld-top { height: 42px; border-bottom: 1px solid #1f1f23; display: flex; align-items: center; padding: 0 16px; gap: 14px; background: #0e0e10; flex-shrink: 0; }
  .ld-logo { font-family: 'JetBrains Mono', monospace; font-size: 12.5px; color: #fff; letter-spacing: 0.04em; font-weight: 600; display: flex; align-items: center; gap: 8px; white-space: nowrap; }
  .ld-logo .sq { width: 13px; height: 13px; background: linear-gradient(135deg, #f97316, #fb923c); border-radius: 3px; flex-shrink: 0; }
  .ld-search-bar { flex: 1; max-width: 560px; height: 28px; background: #18181b; border: 1px solid #26262b; border-radius: 5px; display: flex; align-items: center; padding: 0 9px; gap: 6px; }
  .ld-search-bar input { flex: 1; background: transparent; border: none; outline: none; color: #e6e6e8; font: inherit; font-size: 12.5px; }
  .kbd { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: #777; background: #232328; padding: 2px 5px; border-radius: 3px; }
  .ld-actions { display: flex; gap: 6px; align-items: center; }
  .ld-btn { height: 26px; padding: 0 10px; background: transparent; border: 1px solid #26262b; border-radius: 4px; color: #ccc; font: inherit; font-size: 11.5px; cursor: pointer; display: flex; align-items: center; gap: 5px; }
  .ld-btn.primary { background: #f97316; border-color: #f97316; color: #1a0a00; font-weight: 500; }
  .ld-btn:hover { background: #1a1a1f; border-color: #36363e; }
  .ld-btn.primary:hover { background: #fb923c; border-color: #fb923c; }
  .ld-btn:disabled { opacity: 0.5; cursor: default; }

  .ld-main { flex: 1; display: flex; min-height: 0; }
  .ld-side { width: 200px; border-right: 1px solid #1f1f23; padding: 10px 4px; font-size: 11.5px; overflow-y: auto; flex-shrink: 0; }
  .ld-side .grp { color: #666; padding: 8px 10px 4px; text-transform: uppercase; letter-spacing: 0.06em; font-size: 10px; }
  .ld-side .item { padding: 4px 10px; color: #bbb; cursor: pointer; border-radius: 4px; margin: 0 4px; display: flex; justify-content: space-between; line-height: 1.7; }
  .ld-side .item:hover { background: #18181b; }
  .ld-side .item.act { background: #1d1d22; color: #fff; }
  .ld-side .count { color: #555; font-family: 'JetBrains Mono', monospace; font-size: 10.5px; }

  .ld-list { flex: 1; overflow: auto; min-width: 0; }
  .ld-list-head { display: grid; grid-template-columns: 24px 110px minmax(0,1fr) 60px 90px; gap: 14px; padding: 0 16px; height: 30px; align-items: center; font-size: 10.5px; color: #666; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #1f1f23; background: #0e0e10; position: sticky; top: 0; z-index: 1; }
  .ld-row { display: grid; grid-template-columns: 24px 110px minmax(0,1fr) 60px 90px; gap: 14px; padding: 0 16px; height: 36px; align-items: center; border-bottom: 1px solid #16161a; cursor: pointer; }
  .ld-row:hover { background: #131316; }
  .ld-row.sel { background: #1a1a20; box-shadow: inset 2px 0 0 #f97316; }

  .ld-status { width: 7px; height: 7px; border-radius: 50%; }
  .ld-status.active { background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.18); }
  .ld-status.idle { background: #444; }

  .ld-proj { font-family: 'JetBrains Mono', monospace; font-size: 10.5px; color: #93c5fd; background: #11243a; padding: 2px 6px; border-radius: 3px; display: inline-block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
  .ld-title { font-size: 12.5px; color: #fff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 450; }
  .ld-title .pv { color: #555; font-weight: 400; margin-left: 8px; font-size: 11.5px; }
  .ld-stats { font-size: 10.5px; color: #888; font-family: 'JetBrains Mono', monospace; text-align: right; }
  .ld-time { font-size: 11px; color: #777; text-align: right; }

  .ld-foot { height: 28px; border-top: 1px solid #1f1f23; display: flex; align-items: center; padding: 0 16px; gap: 16px; font-size: 10.5px; color: #666; background: #0a0a0c; flex-shrink: 0; }
  .ld-foot .kbd { color: #aaa; background: #1a1a1f; }

  .ld-loading { padding: 40px; text-align: center; color: #555; font-size: 12px; }

  .ld-modal { position: absolute; inset: 0; background: rgba(0,0,0,0.6); backdrop-filter: blur(4px); display: flex; align-items: center; justify-content: center; z-index: 50; animation: ld-fade 0.12s ease-out; }
  @keyframes ld-fade { from {opacity:0} to {opacity:1} }
  .ld-sheet { width: min(860px, 92%); max-height: 88%; background: #0e0e10; border: 1px solid #2a2a30; border-radius: 8px; box-shadow: 0 20px 60px rgba(0,0,0,0.6); display: flex; flex-direction: column; overflow: hidden; }
  .ld-sheet-head { padding: 14px 18px 12px; border-bottom: 1px solid #1f1f23; background: #131316; }
  .ld-sheet-title { font-size: 17px; font-weight: 500; color: #fff; letter-spacing: -0.01em; line-height: 1.4; margin: 6px 0 8px; }
  .ld-sheet-meta { display: flex; gap: 8px; font-size: 11px; color: #888; font-family: 'JetBrains Mono', monospace; flex-wrap: wrap; overflow: hidden; }
  .ld-transcript { flex: 1; overflow: auto; padding: 16px 20px 22px; display: flex; flex-direction: column; gap: 14px; }

  .ld-turn-num { font-size: 10px; color: #444; text-transform: uppercase; letter-spacing: 0.06em; margin: 8px 0 6px; font-family: 'JetBrains Mono', monospace; }
  .ld-msg-head { display: flex; gap: 10px; align-items: baseline; margin-bottom: 4px; }
  .ld-msg-role { font-family: 'JetBrains Mono', monospace; font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }
  .ld-msg-user .ld-msg-role { color: #fbbf24; }
  .ld-msg-assistant .ld-msg-role { color: #f97316; }
  .ld-msg-body { font-size: 13px; color: #d8d8da; line-height: 1.65; white-space: pre-wrap; padding: 9px 13px; background: #16161a; border-radius: 5px; border-left: 2px solid #26262b; word-break: break-word; max-height: 400px; overflow-y: auto; }
  .ld-msg-user .ld-msg-body { border-left-color: #fbbf24; background: #1a1709; }
  .ld-msg-assistant .ld-msg-body { border-left-color: #f97316; }
  .ld-msg-assistant { margin-top: 8px; }

  .ld-summary-panel { margin: 10px 18px 0; padding: 10px 14px; background: #0d1a0d; border: 1px solid #1a3a1a; border-radius: 6px; }
  .ld-summary-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 10.5px; color: #22c55e; font-family: 'JetBrains Mono', monospace; text-transform: uppercase; letter-spacing: 0.06em; }
  .ld-summary-text { font-size: 12.5px; color: #c9f7c9; line-height: 1.7; white-space: pre-wrap; }
  .ld-summary-footer { margin-top: 8px; display: flex; gap: 6px; align-items: center; }
  .ld-summary-time { font-size: 10px; color: #4a6a4a; font-family: 'JetBrains Mono', monospace; }
  .ld-summary-stale { font-size: 10px; color: #f97316; font-family: 'JetBrains Mono', monospace; }

  .ld-sr-container { padding: 16px; }
  .ld-sr-header { color: #888; font-size: 11px; margin-bottom: 12px; }
  .ld-search-result { background: #131316; border: 1px solid #1f1f23; border-radius: 6px; padding: 12px 14px; margin-bottom: 10px; }
  .ld-role-badge { display: inline-block; padding: 2px 7px; border-radius: 3px; font-size: 10px; font-weight: 600; margin-right: 8px; font-family: 'JetBrains Mono', monospace; text-transform: uppercase; }
  .ld-role-badge.user { background: #1a1709; color: #fbbf24; }
  .ld-role-badge.assistant { background: #1a0e05; color: #f97316; }
  .ld-sr-time { font-size: 11px; color: #666; font-family: 'JetBrains Mono', monospace; }
  .ld-sr-text { margin-top: 6px; color: #ccc; font-size: 12.5px; line-height: 1.5; }
  .ld-sr-actions { display: flex; gap: 8px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
  .ld-cmd-row { display: flex; gap: 8px; align-items: center; flex: 1; background: #0a0a0c; border: 1px solid #1f1f23; border-radius: 4px; padding: 6px 10px; min-width: 0; }
  .ld-cmd-text { flex: 1; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #93c5fd; overflow-x: auto; white-space: nowrap; }
  .ld-btn.orange { background: #c2410c; border-color: #c2410c; color: #fff; }
  .ld-btn.orange:hover { background: #ea580c; border-color: #ea580c; }
  .ld-btn.warn { border-color: #7c5410; color: #fbbf24; }
  .ld-btn.warn:hover { background: #1f1709; border-color: #b8860b; }

  .ld-inc-intro { background: #1a1709; border: 1px solid #3a2e0f; border-radius: 6px; padding: 12px 14px; margin-bottom: 14px; }
  .ld-inc-intro-title { font-size: 13px; color: #fbbf24; font-weight: 600; margin-bottom: 6px; }
  .ld-inc-intro-desc { font-size: 11.5px; color: #b9a87a; line-height: 1.7; }
  .ld-inc-strength { font-size: 10px; color: #fbbf24; background: #2a2109; padding: 2px 6px; border-radius: 3px; margin-left: 8px; font-family: 'JetBrains Mono', monospace; }
  .ld-inc-signals { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .ld-inc-sig { font-size: 10.5px; color: #f0b35a; background: #211a0a; border: 1px solid #3a2e0f; padding: 2px 7px; border-radius: 3px; }
  .ld-inc-weak { margin-top: 16px; border-top: 1px dashed #2a2620; padding-top: 12px; }
  .ld-inc-weak > summary { cursor: pointer; color: #b9a87a; font-size: 11.5px; padding: 4px 0; user-select: none; }
  .ld-inc-weak > summary:hover { color: #fbbf24; }
  .ld-inc-card { cursor: pointer; transition: border-color .12s; }
  .ld-inc-card:hover { border-color: #7c5410; }
  .ld-inc-card.ld-inc-active { border-color: #fbbf24; background: #1a1709; box-shadow: 0 0 0 1px rgba(251,191,36,0.35); }
  .ld-triage-item.act { background: #2a2109; }
  .ld-triage-item.act .ld-triage-note { color: #fff; }
  .ld-inc-restale { font-size: 11.5px; color: #fbbf24; background: #221a06; border: 1px solid #3a2e0f; border-radius: 5px; padding: 8px 11px; margin-bottom: 10px; line-height: 1.6; }
  .ld-inc-restale-list { margin-top: 5px; font-size: 10.5px; color: #b9a87a; font-family: 'JetBrains Mono', monospace; word-break: break-word; }
  .ld-triage-list { display: flex; flex-direction: column; gap: 11px; }
  .ld-triage-cat { font-size: 10.5px; color: #9a8b5e; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
  .ld-triage-cat.cat-A { color: #fbbf24; }
  .ld-triage-cat.cat-B { color: #6ee7a8; }
  .ld-triage-cat.cat-C { color: #93c5fd; }
  .ld-triage-item { display: flex; gap: 9px; align-items: baseline; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; line-height: 1.5; }
  .ld-triage-item:hover { background: #1f1709; }
  .ld-triage-proj { flex-shrink: 0; max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10.5px; color: #f0b35a; font-family: 'JetBrains Mono', monospace; }
  .ld-triage-note { color: #d2d2d2; }
  .ld-triage-item:hover .ld-triage-note { color: #fff; }
  .ld-inc-flash { animation: incflash 1.2s ease; }
  @keyframes incflash { 0% { border-color: #fbbf24; box-shadow: 0 0 0 2px rgba(251,191,36,0.4); } 100% { border-color: #1f1f23; box-shadow: none; } }
  .ld-inc-evidence { margin-top: 6px; font-size: 11px; color: #cbb88a; background: #16130b; border-left: 2px solid #7c5410; padding: 6px 10px; border-radius: 3px; line-height: 1.6; word-break: break-word; }
  .ld-inc-evidence .lbl { color: #7c6b3f; margin-right: 6px; }
  .ld-inc-cardsum { margin-top: 10px; padding: 8px 11px; background: #0d1a0d; border: 1px solid #1a3a1a; border-radius: 5px; }
  .ld-inc-cardsum-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 10px; color: #22c55e; font-family: 'JetBrains Mono', monospace; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 5px; }
  .ld-inc-cardsum-text { font-size: 12px; color: #c9f7c9; line-height: 1.65; white-space: pre-wrap; }
  .ld-inc-error { font-size: 11px; color: #fca5a5; background: #1f0d0d; border: 1px solid #3a1a1a; border-radius: 4px; padding: 6px 9px; line-height: 1.6; word-break: break-word; }
</style>
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" integrity="sha384-hD6/rw4ppMLGNu3tX5cjIb+uRZ7UkRJ6BPkLpg4hAu/6onKUg4lLsHAs9EBPT82L" crossorigin="anonymous"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" integrity="sha384-u6aeetuaXnQ38mYT8rp6sbXaQe3NL9t+IBXmnYxwkUI2Hw4bsp2Wvmx4yRQF1uAm" crossorigin="anonymous"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" integrity="sha384-m08KidiNqLdpJqLq95G/LEi8Qvjl/xUYll3QILypMoQ65QorJ9Lvtp2RXYGBFj1y" crossorigin="anonymous"></script>
</head>
<body>
<div id="root"></div>
<script type="text/babel">
const App = () => {
  const [sessions, setSessions] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [query, setQuery] = React.useState('');
  const [searchResults, setSearchResults] = React.useState(null);
  const [searching, setSearching] = React.useState(false);
  const [selected, setSelected] = React.useState(0);
  const [filterProject, setFilterProject] = React.useState('all');
  const [filterTime, setFilterTime] = React.useState('all');
  const [openSession, setOpenSession] = React.useState(null);
  const [modalMessages, setModalMessages] = React.useState(null);
  const [loadingModal, setLoadingModal] = React.useState(false);
  const [summary, setSummary] = React.useState(null);
  const [summaryLoading, setSummaryLoading] = React.useState(false);
  const [summaryStale, setSummaryStale] = React.useState(false);
  const [cachedSummaries, setCachedSummaries] = React.useState({});

  // 未完了検出ビュー
  const [view, setView] = React.useState('sessions'); // 'sessions' | 'incomplete'
  const [incCandidates, setIncCandidates] = React.useState(null);
  const [incLoading, setIncLoading] = React.useState(false);
  const [incSignature, setIncSignature] = React.useState('');
  const [incTriage, setIncTriage] = React.useState(null);
  const [incTriageLoading, setIncTriageLoading] = React.useState(false);
  const [incTriageStale, setIncTriageStale] = React.useState(false);
  const [incTriageError, setIncTriageError] = React.useState('');
  // 未完カードごとのセッション要約（TOPの要約と localStorage を共有）
  const [cardSummaries, setCardSummaries] = React.useState({});
  const [cardSummaryLoading, setCardSummaryLoading] = React.useState({});
  const [cardSummaryError, setCardSummaryError] = React.useState({});
  // 未完ビューのプロジェクト絞り込み
  const [incFilterProject, setIncFilterProject] = React.useState('all');
  // 前回トリアージ後に進行・追加されたセッション（再走査を促すため）
  const [incStaleSessions, setIncStaleSessions] = React.useState([]);
  // 内部リンクで選択中のセッション（カードを継続ハイライト）
  const [incActiveSid, setIncActiveSid] = React.useState(null);

  const INC_KEY = 'claude-resume:incomplete:v2';

  React.useEffect(() => {
    fetch('/api/sessions?n=50')
      .then(r => r.json())
      .then(data => { setSessions(data); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    const map = {};
    const prefix = 'claude-resume:summary:';
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith(prefix)) continue;
      try {
        const v = JSON.parse(localStorage.getItem(k));
        if (v && typeof v.message_count === 'number') {
          map[k.slice(prefix.length)] = v.message_count;
        }
      } catch {}
    }
    setCachedSummaries(map);
  }, []);

  const doSearch = async () => {
    const kw = query.trim();
    if (!kw) { setSearchResults(null); return; }
    setSearching(true);
    try {
      const res = await fetch('/api/search?q=' + encodeURIComponent(kw));
      const data = await res.json();
      // 日時降順でソート
      data.sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
      setSearchResults(data);
    } catch (e) { setSearchResults([]); }
    setSearching(false);
  };

  const clearSearch = () => { setQuery(''); setSearchResults(null); };

  const SUMMARY_KEY = (sid) => `claude-resume:summary:${sid}`;

  const loadCachedSummary = (sid, msgCount) => {
    try {
      const raw = localStorage.getItem(SUMMARY_KEY(sid));
      if (!raw) return null;
      const cached = JSON.parse(raw);
      if (cached.message_count !== msgCount) return null;
      return cached;
    } catch (e) { return null; }
  };

  const saveSummaryCache = (sid, summaryText, msgCount) => {
    try {
      localStorage.setItem(SUMMARY_KEY(sid), JSON.stringify({
        summary: summaryText,
        message_count: msgCount,
        generated_at: new Date().toISOString(),
      }));
      setCachedSummaries(prev => ({ ...prev, [sid]: msgCount }));
    } catch (e) {}
  };

  const openDetail = async (s) => {
    setOpenSession(s);
    setLoadingModal(true);
    setModalMessages(null);
    setSummary(null);
    setSummaryStale(false);
    try {
      const res = await fetch('/api/session/' + s.session_id);
      const data = await res.json();
      const msgs = data.messages || [];
      setModalMessages(msgs);
      // キャッシュ確認
      const cached = loadCachedSummary(s.session_id, msgs.length);
      if (cached) {
        setSummary(cached);
        setSummaryStale(false);
      }
    } catch (e) { setModalMessages([]); }
    setLoadingModal(false);
  };

  const generateSummary = async (sessionId, msgCount) => {
    setSummaryLoading(true);
    setSummary(null);
    try {
      const res = await fetch('/api/summary?session_id=' + encodeURIComponent(sessionId));
      const data = await res.json();
      if (data.summary) {
        const cached = { summary: data.summary, message_count: data.message_count, generated_at: new Date().toISOString() };
        setSummary(cached);
        saveSummaryCache(sessionId, data.summary, data.message_count);
      }
    } catch (e) {}
    setSummaryLoading(false);
  };

  // ── 未完了検出ビュー ──
  const loadIncompleteCache = () => {
    try {
      const raw = localStorage.getItem(INC_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  };

  // キャッシュ署名(N|sid:ts|…)と現在の候補を突き合わせ、進行・追加されたセッションを返す
  const diffStaleSessions = (oldSignature, currentCands) => {
    const prev = {};
    (oldSignature || '').split('|').slice(1).forEach(p => {
      const idx = p.indexOf(':');
      if (idx > 0) prev[p.slice(0, idx)] = p.slice(idx + 1);
    });
    return (currentCands || []).filter(c => {
      const pt = prev[c.session_id];
      return pt === undefined || String(pt) !== String(c.last_ts);
    });
  };

  const saveIncompleteCache = (obj) => {
    try {
      localStorage.setItem(INC_KEY, JSON.stringify(obj));
    } catch (e) {}
  };

  // トリアージ項目からカードへスクロール（内部リンク）。対象を継続ハイライトする。
  const jumpToCard = (sid) => {
    setIncActiveSid(sid);
    const el = document.getElementById('inc-card-' + sid);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('ld-inc-flash');
    setTimeout(() => el.classList.remove('ld-inc-flash'), 1200);
  };

  const openIncomplete = async () => {
    setView('incomplete');
    setIncFilterProject('all');
    clearSearch();
    setIncLoading(true);
    setIncTriage(null);
    setIncTriageStale(false);
    try {
      const res = await fetch('/api/incomplete');
      const data = await res.json();
      const cands = data.candidates || [];
      setIncCandidates(cands);
      setIncSignature(data.signature || '');
      // TOP一覧/詳細で生成済みのセッション要約があれば未完カードにも反映
      const sm = {};
      cands.forEach(c => {
        try {
          const raw = localStorage.getItem(SUMMARY_KEY(c.session_id));
          if (raw) { const v = JSON.parse(raw); if (v && v.summary) sm[c.session_id] = v.summary; }
        } catch (e) {}
      });
      setCardSummaries(sm);
      // キャッシュ済みトリアージがあれば常に保持して表示する。
      // 署名が一致しなくても破棄せず、進行・追加分を検出して再走査を促す。
      const cached = loadIncompleteCache();
      if (cached && cached.signature === data.signature) {
        setIncTriage(cached);
        setIncTriageStale(false);
        setIncStaleSessions([]);
      } else if (cached) {
        setIncTriage(cached);
        setIncTriageStale(true);
        setIncStaleSessions(diffStaleSessions(cached.signature, cands));
      } else {
        setIncStaleSessions([]);
      }
    } catch (e) { setIncCandidates([]); }
    setIncLoading(false);
  };

  const generateTriage = async () => {
    setIncTriageLoading(true);
    setIncTriage(null);
    setIncTriageError('');
    try {
      const res = await fetch('/api/incomplete/summary');
      const data = await res.json();
      if (data.error) {
        setIncTriageError(data.error);
      } else if (data.items !== undefined || data.summary !== undefined) {
        const cached = {
          signature: data.signature,
          items: data.items || null,
          summary: data.summary || '',
          generated_at: new Date().toISOString(),
        };
        setIncTriage(cached);
        setIncTriageStale(false);
        setIncStaleSessions([]);
        setIncSignature(data.signature || '');
        saveIncompleteCache(cached);
      }
    } catch (e) { setIncTriageError('トリアージの取得に失敗しました: ' + e); }
    setIncTriageLoading(false);
  };

  // 未完カードからセッション要約を生成（/api/summary を再利用、TOPとキャッシュ共有）
  const generateSummaryForCard = async (sid) => {
    setCardSummaryLoading(prev => ({ ...prev, [sid]: true }));
    setCardSummaryError(prev => ({ ...prev, [sid]: '' }));
    try {
      const res = await fetch('/api/summary?session_id=' + encodeURIComponent(sid));
      const data = await res.json();
      if (data.summary) {
        saveSummaryCache(sid, data.summary, data.message_count);
        setCardSummaries(prev => ({ ...prev, [sid]: data.summary }));
      } else if (data.error) {
        setCardSummaryError(prev => ({ ...prev, [sid]: data.error }));
      }
    } catch (e) { setCardSummaryError(prev => ({ ...prev, [sid]: '要約取得に失敗: ' + e })); }
    setCardSummaryLoading(prev => ({ ...prev, [sid]: false }));
  };

  const renderIncCard = (c, i) => {
    const proj = c.project && c.project !== '(Global/No Directory)' ? c.project : '';
    const cmd = proj
      ? 'cd ' + proj + ' && claude --resume ' + c.session_id
      : 'claude --resume ' + c.session_id;
    const stop = (e) => e.stopPropagation();
    const openCard = () => openDetail({
      session_id: c.session_id,
      project: c.project,
      project_name: c.project_name,
      session_name: (c.intent || '').slice(0, 60) || '(未完了セッション)',
      last_ts: c.last_ts,
      messages: [],
    });
    return (
      <div key={c.session_id + i} id={'inc-card-' + c.session_id}
           className={`ld-search-result ld-inc-card ${incActiveSid === c.session_id ? 'ld-inc-active' : ''}`}
           onClick={openCard} title="クリックで詳細を表示">
        <span className="ld-proj" title={c.project}>{c.project_name}</span>
        <span className="ld-sr-time" style={{marginLeft:8}}>{relTime(c.last_ts)}</span>
        <span className="ld-inc-strength" title="未完了スコア">強度 {c.strength}</span>
        <div className="ld-sr-text" style={{marginTop:8}}>{c.intent}</div>
        <div className="ld-inc-signals">
          {(c.signals || []).map((s, j) => <span key={j} className="ld-inc-sig">{s}</span>)}
        </div>
        {(c.evidence || []).filter(ev => ev.text).map((ev, j) => (
          <div key={j} className="ld-inc-evidence"><span className="lbl">{ev.label}:</span>{ev.text}</div>
        ))}
        <div className="ld-sr-actions" onClick={stop}>
          <button className="ld-btn orange" onClick={() => launchTerminal(proj, c.session_id)}>ターミナルで開く</button>
          <div className="ld-cmd-row">
            <span className="ld-cmd-text">{cmd}</span>
            <button className="ld-btn" onClick={() => copyToClipboard(cmd)}>コピー</button>
          </div>
        </div>
        {cardSummaries[c.session_id] ? (
          <div className="ld-inc-cardsum" onClick={stop}>
            <div className="ld-inc-cardsum-head">
              <span>⚡ AI 要約</span>
              <button className="ld-btn" style={{height:'18px', fontSize:'9.5px', padding:'0 6px'}}
                      disabled={cardSummaryLoading[c.session_id]}
                      onClick={() => generateSummaryForCard(c.session_id)}>再生成</button>
            </div>
            <div className="ld-inc-cardsum-text">{cardSummaries[c.session_id]}</div>
          </div>
        ) : (
          <div onClick={stop}>
            <button className="ld-btn" style={{marginTop:8, fontSize:'10.5px'}}
                    disabled={cardSummaryLoading[c.session_id]}
                    onClick={() => generateSummaryForCard(c.session_id)}>
              {cardSummaryLoading[c.session_id] ? '要約生成中…' : (cardSummaryError[c.session_id] ? '⚡ 再試行' : '⚡ このセッションを要約')}
            </button>
            {cardSummaryError[c.session_id] && <div className="ld-inc-error" style={{marginTop:6}}>⚠ {cardSummaryError[c.session_id]}</div>}
          </div>
        )}
      </div>
    );
  };

  // 検索結果からモーダルを開く
  const openDetailFromSearch = (r) => {
    const proj = r.project && r.project !== '(Global/No Directory)' ? r.project : '';
    const projName = proj ? proj.split('/').pop() : '(不明)';
    openDetail({
      session_id: r.session_id,
      project: proj,
      project_name: projName,
      session_name: r.prompt.slice(0, 60),
      last_ts: Date.now(),
      messages: [],
    });
  };

  const launchTerminal = (project, sessionId) => {
    const p = new URLSearchParams({ project, session_id: sessionId });
    fetch('/api/launch?' + p);
  };

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text).catch(() => {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    });
  };

  const now = Date.now();
  const todayStart = new Date().setHours(0, 0, 0, 0);
  const weekStart = now - 7 * 24 * 3600000;

  const filtered = React.useMemo(() => sessions.filter(s => {
    if (filterProject !== 'all' && s.project_name !== filterProject) return false;
    if (filterTime === 'today' && s.last_ts < todayStart) return false;
    if (filterTime === 'week' && s.last_ts < weekStart) return false;
    return true;
  }), [sessions, filterProject, filterTime]);

  const projects = React.useMemo(() => [...new Set(sessions.map(s => s.project_name))], [sessions]);

  // 未完候補のプロジェクト別件数（多い順）
  const incProjects = React.useMemo(() => {
    const counts = {};
    (incCandidates || []).forEach(c => { counts[c.project_name] = (counts[c.project_name] || 0) + 1; });
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [incCandidates]);

  const relTime = (ts) => {
    const d = now - ts;
    const m = Math.floor(d / 60000);
    const h = Math.floor(d / 3600000);
    const dy = Math.floor(d / 86400000);
    if (m < 1) return 'たった今';
    if (m < 60) return m + '分前';
    if (h < 24) return h + '時間前';
    if (dy === 1) return '昨日';
    return dy + '日前';
  };

  const getPreview = (s) => {
    if (!s.messages || s.messages.length === 0) return s.session_name || '';
    for (const [role, text] of s.messages) {
      if (role === 'user') return text.replace(/\s+/g, ' ').slice(0, 70);
    }
    return '';
  };

  const groupTurns = (msgs) => {
    const turns = [];
    let i = 0;
    while (i < msgs.length) {
      if (msgs[i][0] === 'user') {
        const ai = (i + 1 < msgs.length && msgs[i+1][0] === 'assistant') ? msgs[i+1] : null;
        turns.push([msgs[i], ai]);
        i += ai ? 2 : 1;
      } else i++;
    }
    return turns;
  };

  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        if (openSession) setOpenSession(null);
        else if (searchResults) clearSearch();
        return;
      }
      // 入力フィールドにフォーカス中はリストナビゲーションを無視
      if (e.target.tagName === 'INPUT') return;
      if (!openSession) {
        if (e.key === 'ArrowDown') setSelected(s => Math.min(s + 1, filtered.length - 1));
        if (e.key === 'ArrowUp') setSelected(s => Math.max(s - 1, 0));
        if (e.key === 'Enter' && filtered[selected]) openDetail(filtered[selected]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [openSession, selected, filtered, searchResults]);

  const todayCount = sessions.filter(s => s.last_ts >= todayStart).length;
  const weekCount = sessions.filter(s => s.last_ts >= weekStart).length;

  const setFilter = (time, project) => {
    setFilterTime(time);
    setFilterProject(project);
    setSearchResults(null);
    setQuery('');
  };

  return (
    <div className="ld-root">
      <div className="ld-top">
        <div className="ld-logo" style={{cursor:'pointer'}} title="一覧（TOP）へ"
             onClick={() => { setView('sessions'); clearSearch(); }}>
          <span className="sq"></span>
          Claude Resume
        </div>
        <div className="ld-search-bar">
          <span style={{color:'#555', fontSize:13}}>⌕</span>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => { if(e.key==='Enter') doSearch(); if(e.key==='Escape') clearSearch(); }}
            placeholder="セッションを検索… (Enter で実行)"
          />
          {query
            ? <span className="kbd" style={{cursor:'pointer'}} onClick={clearSearch}>✕</span>
            : <span className="kbd">Enter</span>
          }
        </div>
        <div className="ld-actions">
          <button className="ld-btn primary" onClick={doSearch} disabled={searching}>
            {searching ? '検索中…' : '検索'}
          </button>
          {view === 'incomplete'
            ? <button className="ld-btn" onClick={() => setView('sessions')}>← 一覧へ戻る</button>
            : <button className="ld-btn warn" onClick={openIncomplete}>⚠ 未完了を調べる</button>
          }
        </div>
      </div>

      <div className="ld-main">
        {view === 'incomplete' ? (
          <>
          <div className="ld-side">
            <div className="grp">Projects</div>
            <div className={`item ${incFilterProject==='all' ? 'act' : ''}`}
                 onClick={() => setIncFilterProject('all')}>
              <span>すべて</span>
              <span className="count">{(incCandidates || []).length}</span>
            </div>
            {incProjects.map(([name, n]) => (
              <div key={name}
                   className={`item ${incFilterProject===name ? 'act' : ''}`}
                   onClick={() => setIncFilterProject(name)}>
                <span style={{overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{name}</span>
                <span className="count">{n}</span>
              </div>
            ))}
          </div>
          <div className="ld-list">
            <div className="ld-sr-container">
              <div className="ld-inc-intro">
                <div className="ld-inc-intro-title">⚠ 未完了の作業を調べる</div>
                <div className="ld-inc-intro-desc">
                  過去の Claude セッションを走査し、「やり残し」の疑いがある作業を検出します
                  （AI未応答で終了・やり残し表現・確認待ちなどを手がかりに抽出）。
                  下の一覧はヒューリスティック検出の候補です。「AIでトリアージ」を押すと、
                  ノイズを除外し A:中断/保留・B:最終工程残り・C:未着手 に分類した要約を生成します。
                  結果はブラウザに保存され、次回は即表示されます。
                </div>
              </div>

              {/* トリアージ要約パネル */}
              <div className="ld-summary-panel" style={{margin:'0 0 14px'}}>
                <div className="ld-summary-header"><span>⚡ AI トリアージ</span></div>
                {incTriageLoading ? (
                  <div style={{color:'#4a6a4a', fontSize:'12px'}}>分類を生成中…（数十秒かかることがあります）</div>
                ) : incTriage ? (
                  <>
                    {incTriageStale && (
                      <div className="ld-inc-restale">
                        ⟳ 前回トリアージ後に {incStaleSessions.length} 件のセッションが進行・追加されています。
                        最新の状況を反映するには再走査（再生成）してください。
                        {incStaleSessions.length > 0 && (
                          <div className="ld-inc-restale-list">
                            {[...new Set(incStaleSessions.map(c => c.project_name))].slice(0, 6).join(' / ')}
                            {new Set(incStaleSessions.map(c => c.project_name)).size > 6 ? ' …' : ''}
                          </div>
                        )}
                      </div>
                    )}
                    {(() => {
                      // 構造化失敗時は旧フリーテキストにフォールバック
                      if (!incTriage.items) {
                        return <div className="ld-summary-text">{incTriage.summary || '（該当なし）'}</div>;
                      }
                      if (incTriage.items.length === 0) {
                        return <div className="ld-summary-text">やり残しは検出されませんでした 🎉</div>;
                      }
                      const items = incTriage.items.filter(it => incFilterProject === 'all' || it.project_name === incFilterProject);
                      if (items.length === 0) {
                        return <div className="ld-summary-text">このプロジェクトに該当するやり残しはありません</div>;
                      }
                      const cats = [['A', '中断・保留中'], ['B', '最終工程が残る'], ['C', '未着手']];
                      return (
                        <div className="ld-triage-list">
                          {cats.map(([cat, label]) => {
                            const group = items.filter(it => it.category === cat);
                            if (group.length === 0) return null;
                            return (
                              <div key={cat} className="ld-triage-group">
                                <div className={`ld-triage-cat cat-${cat}`}>{cat}: {label}（{group.length}）</div>
                                {group.map((it, j) => (
                                  <a key={it.session_id + j}
                                     className={`ld-triage-item ${incActiveSid === it.session_id ? 'act' : ''}`}
                                     title="クリックで下のカードへ移動"
                                     onClick={() => jumpToCard(it.session_id)}>
                                    <span className="ld-triage-proj">{it.project_name}</span>
                                    <span className="ld-triage-note">{it.note || '(内容不明)'}</span>
                                  </a>
                                ))}
                              </div>
                            );
                          })}
                        </div>
                      );
                    })()}
                    <div className="ld-summary-footer">
                      <span className="ld-summary-time">
                        {incTriage.generated_at ? new Date(incTriage.generated_at).toLocaleString('ja-JP', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) + ' 生成' : ''}
                      </span>
                      {incTriageStale && <span className="ld-summary-stale">要再走査</span>}
                      <button className="ld-btn" style={{height:'20px', fontSize:'10px', padding:'0 7px'}}
                              onClick={generateTriage}>再走査</button>
                    </div>
                  </>
                ) : (
                  <>
                    {incTriageStale && <div className="ld-summary-stale" style={{marginBottom:6}}>前回から候補が変化しています。再生成してください。</div>}
                    {incTriageError && <div className="ld-inc-error" style={{marginBottom:6}}>⚠ {incTriageError}</div>}
                    <button className="ld-btn primary" style={{fontSize:'11.5px'}}
                            onClick={generateTriage}
                            disabled={!incCandidates || incCandidates.length === 0}>
                      {incTriageError ? '再試行' : 'AI でトリアージ'}
                    </button>
                  </>
                )}
              </div>

              {(() => {
                if (incLoading) {
                  return <><div className="ld-sr-header">スキャン中…</div><div className="ld-loading">セッションを走査中…</div></>;
                }
                const all = incCandidates || [];
                if (all.length === 0) {
                  return <div className="ld-loading">未完了の候補は見つかりませんでした 🎉</div>;
                }
                const cands = all.filter(c => incFilterProject === 'all' || c.project_name === incFilterProject);
                if (cands.length === 0) {
                  return <div className="ld-loading">このプロジェクトに未完了の候補はありません</div>;
                }
                // 優先度（strength→新しさ）順に全件表示
                return (
                  <>
                    <div className="ld-sr-header">
                      未完了の候補: {cands.length}件{incFilterProject !== 'all' ? `（${incFilterProject}）` : ''}・優先度順
                    </div>
                    {cands.map((c, i) => renderIncCard(c, i))}
                  </>
                );
              })()}
            </div>
          </div>
          </>
        ) : (
        <>
        <div className="ld-side">
          <div className="grp">Filters</div>
          <div
            className={`item ${filterTime==='all' && filterProject==='all' && !searchResults ? 'act' : ''}`}
            onClick={() => setFilter('all', 'all')}>
            <span>すべて</span>
            <span className="count">{sessions.length}</span>
          </div>
          <div className={`item ${filterTime==='today' ? 'act' : ''}`} onClick={() => setFilter('today', 'all')}>
            <span>今日</span>
            <span className="count">{todayCount}</span>
          </div>
          <div className={`item ${filterTime==='week' ? 'act' : ''}`} onClick={() => setFilter('week', 'all')}>
            <span>今週</span>
            <span className="count">{weekCount}</span>
          </div>
          <div className="grp" style={{marginTop:8}}>Projects</div>
          {projects.map(p => (
            <div key={p}
                 className={`item ${filterProject===p ? 'act' : ''}`}
                 onClick={() => setFilter('all', p)}>
              <span style={{overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{p}</span>
              <span className="count">{sessions.filter(s => s.project_name === p).length}</span>
            </div>
          ))}
        </div>

        <div className="ld-list">
          {searchResults !== null ? (
            <div className="ld-sr-container">
              <div className="ld-sr-header">
                検索結果: {searchResults.length}件（日時降順）
                <span style={{cursor:'pointer', color:'#f97316', marginLeft:12}} onClick={clearSearch}>← 戻る</span>
              </div>
              {searchResults.length === 0
                ? <div className="ld-loading">結果が見つかりませんでした</div>
                : searchResults.map((r, i) => {
                  const proj = r.project && r.project !== '(Global/No Directory)' ? r.project : '';
                  const cmd = proj
                    ? 'cd ' + proj + ' && claude --resume ' + r.session_id
                    : 'claude --resume ' + r.session_id;
                  return (
                    <div key={i} className="ld-search-result">
                      <span className={`ld-role-badge ${r.role}`}>{r.role==='user' ? 'User' : 'AI'}</span>
                      <span className="ld-sr-time">{r.timestamp}</span>
                      <div className="ld-sr-text">{r.prompt}</div>
                      <div className="ld-sr-actions">
                        <button className="ld-btn primary" onClick={() => openDetailFromSearch(r)}>全文表示</button>
                        <button className="ld-btn orange" onClick={() => launchTerminal(proj, r.session_id)}>ターミナルで開く</button>
                        <div className="ld-cmd-row">
                          <span className="ld-cmd-text">{cmd}</span>
                          <button className="ld-btn" onClick={() => copyToClipboard(cmd)}>コピー</button>
                        </div>
                      </div>
                    </div>
                  );
                })
              }
            </div>
          ) : (
            <>
              <div className="ld-list-head">
                <span></span>
                <span>Project</span>
                <span>Session</span>
                <span style={{textAlign:'right'}}>Msgs</span>
                <span style={{textAlign:'right'}}>Time</span>
              </div>
              {loading
                ? <div className="ld-loading">読み込み中…</div>
                : filtered.length === 0
                  ? <div className="ld-loading">セッションが見つかりません</div>
                  : filtered.map((s, i) => (
                    <div key={s.session_id}
                         className={`ld-row ${i===selected ? 'sel' : ''}`}
                         onClick={() => { setSelected(i); openDetail(s); }}>
                      <span className={`ld-status ${
                        cachedSummaries[s.session_id] === (s.messages ? s.messages.length : 0)
                          ? 'active' : 'idle'
                      }`} title={
                        cachedSummaries[s.session_id] === (s.messages ? s.messages.length : 0)
                          ? 'AI 要約済み' : ''
                      }></span>
                      <span className="ld-proj" title={s.project}>{s.project_name}</span>
                      <span className="ld-title">
                        {s.session_name || '(名前なし)'}
                        <span className="pv">{getPreview(s)}</span>
                      </span>
                      <span className="ld-stats">{s.messages ? s.messages.length : 0}</span>
                      <span className="ld-time">{relTime(s.last_ts)}</span>
                    </div>
                  ))
              }
            </>
          )}
        </div>
        </>
        )}
      </div>

      {openSession && (
        <div className="ld-modal" onClick={() => setOpenSession(null)}>
          <div className="ld-sheet" onClick={e => e.stopPropagation()}>
            <div className="ld-sheet-head">
              <div style={{display:'flex', gap:8, alignItems:'center', marginBottom:6}}>
                <span className="ld-proj" title={openSession.project}>{openSession.project_name}</span>
                <span style={{fontSize:'10.5px', color:'#555', fontFamily:"'JetBrains Mono',monospace"}}>
                  [{openSession.session_id.slice(0,8)}]
                </span>
                <span style={{fontSize:'10.5px', color:'#888'}}>
                  {new Date(openSession.last_ts).toLocaleString('ja-JP', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'})}
                </span>
                <span style={{marginLeft:'auto', display:'flex', gap:6}}>
                  <button className="ld-btn orange"
                          onClick={() => launchTerminal(openSession.project, openSession.session_id)}>
                    ターミナルで開く
                  </button>
                  <button className="ld-btn" onClick={() => setOpenSession(null)}>✕</button>
                </span>
              </div>
              <div className="ld-cmd-row" style={{marginBottom:4}}>{(() => {
                const proj = openSession.project && openSession.project !== '(Global/No Directory)' ? openSession.project : '';
                const cmd = proj ? 'cd ' + proj + ' && claude --resume ' + openSession.session_id : 'claude --resume ' + openSession.session_id;
                return (<>
                  <span className="ld-cmd-text">{cmd}</span>
                  <button className="ld-btn" onClick={() => copyToClipboard(cmd)}>コピー</button>
                </>);
              })()}
              </div>
              <div className="ld-sheet-title">{openSession.session_name || '(名前なし)'}</div>
              <div className="ld-sheet-meta">
                <span title={openSession.project}>{openSession.project || '—'}</span>
                <span>·</span>
                <span>{openSession.messages ? openSession.messages.length : 0} msgs</span>
              </div>
            </div>

            {/* AI 要約パネル */}
            {!loadingModal && modalMessages !== null && (
              <div className="ld-summary-panel">
                <div className="ld-summary-header">
                  <span>⚡ AI 要約</span>
                </div>
                {summaryLoading ? (
                  <div style={{color:'#4a6a4a', fontSize:'12px'}}>要約を生成中…</div>
                ) : summary ? (
                  <>
                    <div className="ld-summary-text">{summary.summary}</div>
                    <div className="ld-summary-footer">
                      <span className="ld-summary-time">
                        {summary.generated_at ? new Date(summary.generated_at).toLocaleString('ja-JP', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) + ' 生成' : ''}
                      </span>
                      <button className="ld-btn" style={{height:'20px', fontSize:'10px', padding:'0 7px'}}
                              onClick={() => generateSummary(openSession.session_id, modalMessages.length)}>
                        再生成
                      </button>
                    </div>
                  </>
                ) : (
                  <button className="ld-btn primary" style={{fontSize:'11.5px'}}
                          onClick={() => generateSummary(openSession.session_id, modalMessages.length)}>
                    AI 要約を生成
                  </button>
                )}
              </div>
            )}

            <div className="ld-transcript">
              {loadingModal
                ? <div className="ld-loading">読み込み中…</div>
                : modalMessages && modalMessages.length > 0
                  ? groupTurns(modalMessages).map(([um, am], i) => (
                    <div key={i}>
                      <div className="ld-turn-num">── Turn {i+1} ──</div>
                      {um && (
                        <div className="ld-msg ld-msg-user">
                          <div className="ld-msg-head">
                            <span className="ld-msg-role">you</span>
                          </div>
                          <div className="ld-msg-body">{um[1]}</div>
                        </div>
                      )}
                      {am && (
                        <div className="ld-msg ld-msg-assistant">
                          <div className="ld-msg-head">
                            <span className="ld-msg-role">claude</span>
                          </div>
                          <div className="ld-msg-body">{am[1]}</div>
                        </div>
                      )}
                    </div>
                  ))
                  : <div className="ld-loading">メッセージがありません</div>
              }
            </div>
          </div>
        </div>
      )}

      <div className="ld-foot">
        <span><span className="kbd">↑↓</span> 移動</span>
        <span><span className="kbd">↵</span> 詳細</span>
        <span><span className="kbd">Esc</span> 閉じる</span>
        <span style={{color:'#555', fontSize:'10px', marginLeft:8}}>● AI 要約済み</span>
        <span style={{marginLeft:'auto'}}>{filtered.length} / {sessions.length} sessions</span>
      </div>
    </div>
  );
};

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
</script>
</body>
</html>
"""


class ClaudeResumeHandler(BaseHTTPRequestHandler):
    count = 10
    debug = False
    target_dirs = None

    def log_message(self, format, *args):
        pass  # ログを抑制

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode('utf-8'))

        elif parsed.path == '/api/sessions':
            query = parse_qs(parsed.query)
            n = int(query.get('n', [self.count])[0])
            sessions = load_recent_sessions(n, debug=self.debug)
            sessions_json = sessions_to_json_compatible(sessions)

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(sessions_json, ensure_ascii=False).encode('utf-8'))

        elif parsed.path == '/api/search':
            query = parse_qs(parsed.query)
            keyword = query.get('q', [''])[0]
            if keyword:
                results = fuzzy_search_prompts(keyword, target_dirs=self.target_dirs, debug=self.debug, return_data=True)
            else:
                results = []

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(results, ensure_ascii=False).encode('utf-8'))

        elif parsed.path == '/api/launch':
            query = parse_qs(parsed.query)
            project = query.get('project', [''])[0]
            session_id = query.get('session_id', [''])[0]

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()

            if session_id:
                try:
                    script_path = f'/tmp/cr_{session_id[:8]}.sh'
                    lines = ['#!/bin/zsh', 'source ~/.zshrc 2>/dev/null']
                    if project:
                        lines.append(f'cd {shlex.quote(project)}')
                    lines.append(f'rm -f {shlex.quote(script_path)}')
                    lines.append(f'claude --resume {session_id}')
                    lines.append('exec zsh')

                    with open(script_path, 'w') as f:
                        f.write('\n'.join(lines) + '\n')
                    os.chmod(script_path, 0o755)

                    subprocess.Popen(
                        ['/mnt/c/Windows/System32/cmd.exe', '/c', 'start', 'wt.exe',
                         '--window', '0', 'new-tab', '--',
                         'wsl.exe', '-e', script_path],
                        cwd='/mnt/c/'
                    )
                    self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
                except Exception as e:
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({"ok": False, "error": "session_id required"}).encode('utf-8'))

        elif parsed.path.startswith('/api/session/'):
            # セッションIDを抽出
            session_id = parsed.path.split('/api/session/')[1]
            messages = load_session_messages(session_id, self.target_dirs)

            # tuple を list に変換
            messages_json = [[role, text] for role, text in messages]

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({"messages": messages_json}, ensure_ascii=False).encode('utf-8'))

        elif parsed.path == '/api/incomplete':
            # 全セッションを走査して未完了候補を返す（ヒューリスティック・軽量）
            query = parse_qs(parsed.query)
            strict = query.get('strict', ['0'])[0] in ('1', 'true')
            candidates = scan_incomplete_sessions(200, strict=strict, debug=self.debug)

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({
                "candidates": candidates,
                "signature": incomplete_signature(candidates),
            }, ensure_ascii=False).encode('utf-8'))

        elif parsed.path == '/api/incomplete/summary':
            # 未完了候補を Claude(Haiku) でトリアージ・分類した結果を返す
            query = parse_qs(parsed.query)
            strict = query.get('strict', ['0'])[0] in ('1', 'true')
            candidates = scan_incomplete_sessions(200, strict=strict, debug=self.debug)
            signature = incomplete_signature(candidates)

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            if not candidates:
                self.wfile.write(json.dumps(
                    {"summary": "", "signature": signature, "count": 0},
                    ensure_ascii=False).encode('utf-8'))
                return

            prompt_text = build_incomplete_triage_prompt(candidates)
            # 候補数に応じてタイムアウトを延長（大きいプロンプト対策）
            timeout = 90 + 2 * len(candidates)
            summary, err = summarize_with_claude(prompt_text, timeout=timeout)
            if summary is None:
                self.wfile.write(json.dumps(
                    {"error": err or "claude unavailable", "signature": signature},
                    ensure_ascii=False).encode('utf-8'))
                return

            # 構造化（内部リンク・プロジェクト絞り込み用）。失敗時はフリーテキストにフォールバック。
            items = parse_triage_items(summary, candidates)
            self.wfile.write(json.dumps(
                {"items": items, "summary": summary, "signature": signature, "count": len(candidates)},
                ensure_ascii=False).encode('utf-8'))

        elif parsed.path == '/api/summary':
            query = parse_qs(parsed.query)
            session_id = query.get('session_id', [''])[0]

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            if not session_id:
                self.wfile.write(json.dumps({"error": "session_id required"}, ensure_ascii=False).encode('utf-8'))
                return

            messages = load_session_messages(session_id, self.target_dirs)
            if not messages:
                self.wfile.write(json.dumps({"error": "session not found"}, ensure_ascii=False).encode('utf-8'))
                return

            prompt_text = build_single_session_summary_prompt(messages)
            summary, err = summarize_with_claude(prompt_text)
            if summary is None:
                self.wfile.write(json.dumps({"error": err or "claude unavailable"}, ensure_ascii=False).encode('utf-8'))
                return

            self.wfile.write(json.dumps(
                {"summary": summary, "message_count": len(messages)},
                ensure_ascii=False
            ).encode('utf-8'))

        else:
            self.send_response(404)
            self.end_headers()


def start_web_server(port=8080, count=10, debug=False):
    """Web UIサーバーを起動"""
    ClaudeResumeHandler.count = count
    ClaudeResumeHandler.debug = debug
    ClaudeResumeHandler.target_dirs = load_target_dirs(debug=debug)

    server = HTTPServer(('127.0.0.1', port), ClaudeResumeHandler)
    url = f'http://localhost:{port}'

    print(f"🌐 Web UI を起動しました: {url}")
    print("   Ctrl+C で停止")
    print()

    # ブラウザを開く（失敗時のエラーメッセージを抑制）
    import os
    import sys
    try:
        # stderr を一時的に /dev/null にリダイレクト
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(devnull, 2)

        webbrowser.open(url)

        # stderr を元に戻す
        os.dup2(old_stderr, 2)
        os.close(devnull)
        os.close(old_stderr)

        print(f"✅ ブラウザを開きました")
    except:
        # ブラウザ自動起動失敗（WSL2などの環境）
        print(f"💡 ブラウザで以下のURLを開いてください: {url}")
        # stderr を元に戻す（例外時）
        try:
            os.dup2(old_stderr, 2)
            os.close(devnull)
            os.close(old_stderr)
        except:
            pass

    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n🛑 サーバーを停止しました")


# ─── エントリポイント ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="claude-resume",
        description="Claude CLI セッション履歴の検索・一覧・要約ツール",
    )
    parser.add_argument(
        "keyword", nargs="?", default=None,
        help="検索キーワード（ユーザー＆AI両方の出力を対象）",
    )
    parser.add_argument(
        "-r", "--recent", nargs="?", type=int, const=5, default=None,
        metavar="N", help="直近 N セッションの一覧を表示（デフォルト: 5）",
    )
    parser.add_argument(
        "-s", "--summary", nargs="?", type=int, const=5, default=None,
        metavar="N", help="直近 N セッションを一覧表示し Claude で要約（デフォルト: 5）",
    )
    parser.add_argument(
        "-w", "--web", nargs="?", type=int, const=8080, default=None,
        metavar="PORT", help="ブラウザUIを起動（デフォルトポート: 8080）",
    )
    parser.add_argument(
        "-t", "--todo", nargs="?", type=int, const=200, default=None,
        metavar="N", help="未完了の疑いがあるセッションを検出して一覧（最大 N 件走査、デフォルト: 200）",
    )
    parser.add_argument(
        "--ai", action="store_true",
        help="--todo と併用: Claude(Haiku) でA/B/Cに分類・トリアージしたレポートを出力",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="--todo と併用: 明確なシグナルのある候補のみ表示（弱い候補=完了報告なしを除外）",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="設定ファイルの読み込みとパス解決のデバッグ情報を表示",
    )

    args = parser.parse_args()

    # --web が指定された場合（Web UI起動）
    if args.web is not None:
        port = args.web
        try:
            start_web_server(port=port, count=10, debug=args.debug)
        except OSError as e:
            print(f"⚠️  ポート {port} でサーバーを起動できませんでした: {e}")
            print(f"   別のポートを試してください: claude-resume --web <ポート番号>")
        return

    # --todo が指定された場合（未完了検出）
    if args.todo is not None:
        candidates = scan_incomplete_sessions(args.todo, strict=args.strict, debug=args.debug)
        display_incomplete(candidates)
        if args.ai and candidates:
            print()
            print("🤖 Claude によるトリアージを生成中...")
            prompt_text = build_incomplete_triage_prompt(candidates)
            summary, err = summarize_with_claude(prompt_text, timeout=90 + 2 * len(candidates))
            if summary:
                print()
                print("🤖 トリアージ結果（A: 中断/保留, B: 最終工程残り, C: 未着手）:")
                print("=" * 60)
                print(summary)
            elif err:
                print(f"⚠️  {err}")
        return

    # --summary が指定された場合（一覧 + 要約）
    if args.summary is not None:
        sessions = load_recent_sessions(args.summary, debug=args.debug)
        if not sessions:
            return
        display_recent_sessions(sessions)
        print()
        print("🤖 Claude による要約を生成中...")
        prompt_text = build_summary_prompt(sessions)
        summary, err = summarize_with_claude(prompt_text)
        if summary:
            print()
            print("🤖 Claude による要約:")
            print("=" * 60)
            print(summary)
        elif err:
            print(f"⚠️  {err}")
        return

    # --recent が指定された場合（一覧のみ）
    if args.recent is not None:
        sessions = load_recent_sessions(args.recent, debug=args.debug)
        if sessions:
            display_recent_sessions(sessions)
        return

    # キーワード検索（既存機能）
    if args.keyword:
        fuzzy_search_prompts(args.keyword, debug=args.debug)
        return

    if args.debug:
        load_target_dirs(debug=True)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
