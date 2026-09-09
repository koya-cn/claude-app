"""ブリーフィングの材料づくりと、提示の頻度の判定。

整形はしない。人が読む形にするのはモデルの仕事（ADR 0008）。
"""

import json
import os
from datetime import datetime
from pathlib import Path

STATE_ENV = "CLAUDE_SECRETARY_BRIEF_STATE"
DEFAULT_STATE = "~/.claude-secretary/last-brief.json"

ASSIGNEE_ORDER = ("self", "group", "unknown", "others")
OPEN_STATUS = "open"

KIND_DAILY = "daily"
KIND_WEEKLY = "weekly"


def state_path():
    """提示の状態ファイルの場所を返す。"""
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override)
    return Path(DEFAULT_STATE).expanduser()


def load_state(path):
    """状態を読む。無い・壊れている場合は空として扱う（BR-11 / BR-12）。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_state(path, state):
    """状態を書く（BR-13）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _day_key(now):
    return now.date().isoformat()


def _week_key(now):
    # ISO の年と週。年をまたぐ週でも一意になる
    return "{}-W{:02d}".format(*now.isocalendar()[:2])


def next_state(now):
    """提示したあとの状態を返す。"""
    return {"daily": _day_key(now), "weekly": _week_key(now)}


def should_show(state, now):
    """提示するかと、日次か週次かを返す（BR-8〜BR-12）。

    日付が一致しないときに提示する。未来の日付が入っていても一致しないので提示に
    倒れる。時計のずれで永久に出なくなることを避けるため。
    """
    kind = KIND_DAILY if state.get("weekly") == _week_key(now) else KIND_WEEKLY
    return state.get("daily") != _day_key(now), kind


def _summarize(record):
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "detail": record.get("detail"),
        "source_ref": (record.get("source") or {}).get("ref"),
        "status_changed_at": record.get("status_changed_at"),
    }


def _parse(stamp):
    """時刻を読む。読めない値とタイムゾーンの無い値は None として扱う。

    台帳は人が直接編集できる形で保存しているため、手書きの時刻には
    タイムゾーンが付かないことがある。基準の時刻と比較できないので、
    落とさずに無いものとして扱う（events.py の終日予定と同じ方針）。
    """
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo is not None else None


def build(records, now, since):
    """台帳のレコードからブリーフィングの材料を組み立てる（BR-1〜BR-5）。"""
    open_items = {assignee: [] for assignee in ASSIGNEE_ORDER}
    counts = {}
    recently_done = []
    last_seen = None

    for rec in records:
        status = rec.get("status") or OPEN_STATUS
        counts[status] = counts.get(status, 0) + 1

        if status == OPEN_STATUS:
            assignee = rec.get("assignee") or "unknown"
            open_items.setdefault(assignee, []).append(_summarize(rec))
        else:
            changed_at = _parse(rec.get("status_changed_at"))
            if changed_at is not None and changed_at > since:
                recently_done.append(_summarize(rec))

        seen = _parse(rec.get("last_seen"))
        if seen is not None and (last_seen is None or seen > last_seen):
            last_seen = seen

    return {
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "open": open_items,
        "recently_done": recently_done,
        "ledger": {
            "last_collected": last_seen.isoformat() if last_seen else None,
            "stale_days": (now - last_seen).days if last_seen else None,
            "counts": {
                "open": counts.get("open", 0),
                "done": counts.get("done", 0),
                "dropped": counts.get("dropped", 0),
            },
        },
    }
