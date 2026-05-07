#!/usr/bin/env python3
import argparse
import json
import os
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


def summarize_with_claude(prompt_text):
    if not shutil.which("claude"):
        print("⚠️  `claude` コマンドが見つかりません。Claude CLI がインストール済みか確認してください。")
        return None

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "claude-haiku-4-5-20251001", "--no-session-persistence"],
            input=prompt_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if result.returncode != 0:
            print(f"⚠️  Claude の呼び出しに失敗しました (exit {result.returncode})")
            if result.stderr:
                print(result.stderr[:200])
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("⚠️  Claude の応答がタイムアウトしました（60秒）")
        return None


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
        <div className="ld-logo">
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
        </div>
      </div>

      <div className="ld-main">
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
            summary = summarize_with_claude(prompt_text)
            if summary is None:
                self.wfile.write(json.dumps({"error": "claude unavailable"}, ensure_ascii=False).encode('utf-8'))
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

    # --summary が指定された場合（一覧 + 要約）
    if args.summary is not None:
        sessions = load_recent_sessions(args.summary, debug=args.debug)
        if not sessions:
            return
        display_recent_sessions(sessions)
        print()
        print("🤖 Claude による要約を生成中...")
        prompt_text = build_summary_prompt(sessions)
        summary = summarize_with_claude(prompt_text)
        if summary:
            print()
            print("🤖 Claude による要約:")
            print("=" * 60)
            print(summary)
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
