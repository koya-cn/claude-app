"""台帳の読み書き。

保存は一時ファイルへ書いてから置換する（AC-9）。
"""

import json
import os
import tempfile
from pathlib import Path

from .records import STATUSES, ValidationError, validate_record

LEDGER_ENV = "CLAUDE_SECRETARY_LEDGER"
DEFAULT_LEDGER = "~/.claude-secretary/ledger.json"

COLLECTED_FIELDS = ("title", "detail", "assignee", "last_seen")
HUMAN_FIELDS = ("status", "note", "status_changed_at")
IMMUTABLE_FIELDS = ("id", "source", "first_seen")


class LedgerCorruptError(Exception):
    """台帳のJSONが壊れている（AC-11）。"""


class RecordNotFoundError(Exception):
    """指定したidのレコードが無い（AC-28）。"""


def ledger_path():
    """台帳ファイルの場所を返す（AC-13 / AC-14）。"""
    override = os.environ.get(LEDGER_ENV)
    if override:
        return Path(override)
    return Path(DEFAULT_LEDGER).expanduser()


def load(path):
    """台帳を読む。ファイルが無ければ空として扱う（AC-10 / AC-11）。"""
    path = Path(path)
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LedgerCorruptError(f"台帳のJSONが壊れている: {path}") from exc
    if not isinstance(records, list):
        raise LedgerCorruptError(f"台帳が配列ではない: {path}")
    return records


def save(path, records):
    """台帳を原子的に保存する（AC-9 / AC-12 / AC-15）。

    書きかけの内容で既存の台帳を置き換えないよう、同じディレクトリの一時ファイルへ
    書き出してから置換する。置換に失敗した場合は一時ファイルを片付けて例外を投げ、
    既存の台帳には触らない。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(records, ensure_ascii=False, indent=2) + "\n"

    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
        os.replace(temp_name, str(path))
    except Exception:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def _new_record(validated, now):
    return {
        "id": validated["id"],
        "source": validated["source"],
        "title": validated["title"],
        "detail": validated["detail"],
        "assignee": validated["assignee"],
        "first_seen": now,
        "last_seen": now,
        "status": "open",
        "note": "",
        # 状態を一度も変えていないことを表す。「先週片付けたもの」を出すために要る
        "status_changed_at": None,
    }


def _apply_collected(existing, validated, now):
    """収集で更新する列だけを上書きする（AC-3 / AC-6 / AC-23）。"""
    existing["title"] = validated["title"]
    existing["assignee"] = validated["assignee"]
    existing["last_seen"] = now
    if validated["detail"]:
        existing["detail"] = validated["detail"]


def upsert(path, incoming, now):
    """レコードのまとまりを投入する。1呼び出しが原子性の単位（AC-1〜AC-8）。"""
    if not isinstance(incoming, list):
        raise ValidationError("投入はレコードの配列で渡す")

    # 1件でも不正なら台帳に触らない（AC-8）。検証を先に済ませる
    validated = [validate_record(raw) for raw in incoming]

    records = load(path)
    by_id = {record["id"]: record for record in records}

    added = updated = 0
    for item in validated:
        existing = by_id.get(item["id"])
        if existing is None:
            record = _new_record(item, now)
            records.append(record)
            by_id[record["id"]] = record
            added += 1
        else:
            _apply_collected(existing, item, now)
            updated += 1

    save(path, records)
    return {"added": added, "updated": updated}


def set_fields(path, record_id, status=None, note=None, now=None):
    """人が変更する列だけを書き換える（AC-27〜AC-29 / AC-33）。

    状態を変えたときだけ時刻を記録する。メモだけの変更では触らない。
    """
    if status is not None and status not in STATUSES:
        raise ValidationError(f"状態が不正: {status}")

    records = load(path)
    for record in records:
        if record["id"] != record_id:
            continue
        if status is not None:
            record["status"] = status
            record["status_changed_at"] = now
        if note is not None:
            record["note"] = note
        save(path, records)
        return record
    raise RecordNotFoundError(f"レコードが見つからない: {record_id}")
