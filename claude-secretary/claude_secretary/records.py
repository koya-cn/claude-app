"""レコードの検証・正規化・id導出。

コネクタ応答や台帳ファイルに触らない純関数だけを置く。
"""

import hashlib
import re
import unicodedata

SOURCE_TYPES = ("meeting", "session", "task_service")
ASSIGNEES = ("self", "others", "group", "unknown")
STATUSES = ("open", "done", "dropped")

_WHITESPACE = re.compile(r"\s+")

# 出所とタイトルの区切り。タイトルに現れない制御文字を使う
_ID_SEPARATOR = "\x1f"
_ID_LENGTH = 16


class ValidationError(Exception):
    """必須項目の欠落や不正な値（AC-7）。"""


def normalize_title(title):
    """同一性判定に使う正規化後のタイトルを返す（AC-4）。

    NFKC で全角英数と半角カナの揺れを吸収したうえで、空白の連続を1個に畳み込む。
    全角空白とタブは NFKC の段階で通常の空白に寄る。
    """
    normalized = unicodedata.normalize("NFKC", title)
    return _WHITESPACE.sub(" ", normalized).strip()


def derive_id(source_type, source_ref, normalized_title):
    """出所と正規化後タイトルから決定的なidを導出する（ADR 0003）。"""
    seed = _ID_SEPARATOR.join((source_type, source_ref, normalized_title))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_ID_LENGTH]


def _require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} が欠けている")
    return value


def validate_record(raw):
    """投入レコード1件を検証し、idを付けて返す（AC-7）。"""
    if not isinstance(raw, dict):
        raise ValidationError("レコードが辞書ではない")

    source = raw.get("source")
    if not isinstance(source, dict):
        raise ValidationError("出所が欠けている")

    source_type = _require_text(source.get("type"), "出所の種別")
    source_ref = _require_text(source.get("ref"), "出所の参照先")
    if source_type not in SOURCE_TYPES:
        raise ValidationError(f"出所の種別が不正: {source_type}")

    title = normalize_title(_require_text(raw.get("title"), "タイトル"))
    if not title:
        raise ValidationError("正規化後のタイトルが空文字になる")

    assignee = raw.get("assignee") or "unknown"
    if assignee not in ASSIGNEES:
        raise ValidationError(f"担当が不正: {assignee}")

    detail = raw.get("detail") or ""
    if not isinstance(detail, str):
        raise ValidationError("本文が文字列ではない")

    return {
        "id": derive_id(source_type, source_ref, title),
        "source": {"type": source_type, "ref": source_ref},
        "title": title,
        "detail": detail,
        "assignee": assignee,
    }
