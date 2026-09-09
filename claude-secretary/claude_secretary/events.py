"""カレンダーのコネクタ応答から、対象の添付を列挙する。

コネクタが返す添付のフィールドは fileUrl と title の2つだけ（実測 2026-09-09）。
"""

import re
from datetime import datetime

GENERATED_NOTE_MARKER = "usp=meet_tnfm_calendar"

_DOCUMENT_ID = re.compile(r"/d/(?P<document_id>[A-Za-z0-9_-]+)")


def normalize_events(payload):
    """コネクタ応答を予定の配列に揃える。

    コネクタは {"events": [...]} の封筒で返す。手順書から素のまま渡されるので、
    封筒付きと配列の両方を受ける。読めない形は ValueError にして、呼び出し側が
    検証エラーとして扱えるようにする（未捕捉の例外で落とさない）。
    """
    if isinstance(payload, dict):
        payload = payload.get("events")
    if not isinstance(payload, list):
        raise ValueError("予定の配列として読めない")
    if not all(isinstance(event, dict) for event in payload):
        raise ValueError("予定でない要素が混ざっている")
    return payload


def is_generated_note(file_url):
    """自動生成された会議メモかを判別する（AC-16）。"""
    return GENERATED_NOTE_MARKER in (file_url or "")


def extract_document_id(file_url):
    """Google ドキュメントのURLからファイルIDを取り出す。"""
    matched = _DOCUMENT_ID.search(file_url or "")
    return matched.group("document_id") if matched else None


def _started_at(event):
    """予定の開始時刻を返す。時刻を持たない予定は None。

    コネクタは終日予定を start.date（日付のみ）で返す。日付だけの値は
    タイムゾーンを持たず、期間の境界と比較できない。収集の対象は時刻を持つ
    会議に限るため、そうした予定は対象外として扱う。
    """
    start = event.get("start") or {}
    stamp = start.get("dateTime")
    if not stamp:
        return None
    started_at = datetime.fromisoformat(stamp)
    return started_at if started_at.tzinfo is not None else None


def collect_attachments(events, start, end):
    """期間内の予定の全添付を列挙する（AC-16 / AC-25 / AC-30）。

    出欠ステータスでは絞り込まない（AC-17）。抽出対象にしなかった添付も捨てず、
    未抽出として返す（AC-30）。
    """
    targets = []
    unextracted = []

    for event in normalize_events(events):
        started_at = _started_at(event)
        # 開始を含み終了を含まない半開区間（AC-25）
        if started_at is None or not (start <= started_at < end):
            continue

        for attachment in event.get("attachments") or []:
            file_url = attachment.get("fileUrl")
            entry = {
                "event_id": event.get("id"),
                "title": attachment.get("title"),
                "file_url": file_url,
                "document_id": extract_document_id(file_url),
            }
            if is_generated_note(file_url):
                targets.append(entry)
            else:
                unextracted.append(entry)

    return {"targets": targets, "unextracted": unextracted}
