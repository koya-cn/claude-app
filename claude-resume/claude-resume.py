#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict


CONFIG_FILE = Path.home() / ".claude-resume.json"
JST = timezone(timedelta(hours=9))


def load_target_dirs():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            dirs = config.get("dirs", [])
            if dirs:
                return [Path(d).expanduser() for d in dirs]
        except (json.JSONDecodeError, KeyError):
            pass
    return [Path.home() / ".claude"]


def _format_timestamp(ts):
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


# ─── キーワード検索（既存機能） ───────────────────────────────────────────────

def fuzzy_search_prompts(keyword, target_dirs=None):
    if target_dirs is None:
        target_dirs = load_target_dirs()

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

def load_recent_sessions(count=5):
    history_file = Path.home() / ".claude" / "history.jsonl"

    if not history_file.exists():
        print(f"履歴ファイルが見つかりません: {history_file}")
        return []

    sessions = {}  # sessionId -> dict

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

            display = entry.get("display", "").strip()
            timestamp = entry.get("timestamp", 0)
            project = entry.get("project", "")

            if sid not in sessions:
                sessions[sid] = {
                    "session_id": sid,
                    "project": project,
                    "project_name": Path(project).name if project else "(不明)",
                    "first_ts": timestamp,
                    "last_ts": timestamp,
                    "prompts": [],
                }

            sess = sessions[sid]
            if timestamp < sess["first_ts"]:
                sess["first_ts"] = timestamp
            if timestamp > sess["last_ts"]:
                sess["last_ts"] = timestamp
                sess["project"] = project
                sess["project_name"] = Path(project).name if project else "(不明)"

            if display:
                sess["prompts"].append({"display": display, "timestamp": timestamp})

    sorted_sessions = sorted(sessions.values(), key=lambda s: s["last_ts"], reverse=True)
    return sorted_sessions[:count]


def format_timestamp(ts_ms):
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(JST)
    return dt.strftime("%m/%d %H:%M")


def display_recent_sessions(sessions):
    print(f"📋 直近のセッション ({len(sessions)}件)")
    print("=" * 60)

    for i, sess in enumerate(sessions, 1):
        last_prompt = ""
        if sess["prompts"]:
            latest = max(sess["prompts"], key=lambda p: p["timestamp"])
            last_prompt = latest["display"]
            if len(last_prompt) > 60:
                last_prompt = last_prompt[:60] + "..."

        ts_str = format_timestamp(sess["last_ts"])
        project = sess["project"]
        project_name = sess["project_name"]
        sid = sess["session_id"]
        prompt_count = len(sess["prompts"])

        print(f" [{i:2}] {ts_str} | {project_name}")
        if last_prompt:
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

        sorted_prompts = sorted(sess["prompts"], key=lambda p: p["timestamp"], reverse=True)
        for p in sorted_prompts[:3]:
            text = p["display"].replace("\n", " ").strip()
            if len(text) > 100:
                text = text[:100] + "..."
            lines.append(f"  - {text}")
        lines.append("")

    return "\n".join(lines)


def summarize_with_claude(prompt_text):
    if not shutil.which("claude"):
        print("⚠️  `claude` コマンドが見つかりません。Claude CLI がインストール済みか確認してください。")
        return None

    try:
        result = subprocess.run(
            ["claude", "-p", "--no-session-persistence"],
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

    args = parser.parse_args()

    # --summary が指定された場合（一覧 + 要約）
    if args.summary is not None:
        sessions = load_recent_sessions(args.summary)
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
        sessions = load_recent_sessions(args.recent)
        if sessions:
            display_recent_sessions(sessions)
        return

    # キーワード検索（既存機能）
    if args.keyword:
        fuzzy_search_prompts(args.keyword)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
