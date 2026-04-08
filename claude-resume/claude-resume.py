#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta

CONFIG_FILE = Path.home() / ".claude-resume.json"

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

JST = timezone(timedelta(hours=9))

def _format_timestamp(ts):
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""

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

                                    # 【修正点】キーワードが実際に含まれている行を探して抽出
                                    hit_line = ""
                                    for text_line in content.split('\n'):
                                        if keyword.lower() in text_line.lower():
                                            hit_line = text_line.strip()
                                            break

                                    # 万が一見つからなければ最初の行をフォールバックとして使う
                                    display_text = hit_line if hit_line else content.split('\n')[0]

                                    # 長すぎる場合は省略
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
                time_str = _format_timestamp(ts)
                if time_str:
                    print(f"   {icon} [{time_str}] {text}")
                else:
                    print(f"   {icon} {text}")

            if sid:
                if project_dir != "(Global/No Directory)":
                    print(f"   🚀 コマンド: cd {project_dir} && claude --resume {sid}")
                else:
                    print(f"   🚀 コマンド: claude --resume {sid}")
            print("   " + "-" * 57)

    print(f"🎯 計 {hit_count} 件のメッセージが見つかりました。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Claudeセッションをキーワード検索",
        epilog=f"検索対象ディレクトリは {CONFIG_FILE} の 'dirs' キーで設定できます。"
    )
    parser.add_argument("keyword", help="検索キーワード")
    args = parser.parse_args()

    fuzzy_search_prompts(args.keyword)
