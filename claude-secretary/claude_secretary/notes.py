"""自動生成された会議メモのパース。

機械パースに限定する（ADR 0005）。コネクタ応答を引数に取る純関数。
"""

import re

NEXT_STEPS_HEADING = "次のステップ"

# グループ宛の表記。実測では日本語と英語の両方が出る
GROUP_LABELS = ("グループ", "the group", "group")

# 担当欄に複数名が並ぶときの区切り
_ASSIGNEE_SEPARATOR = re.compile(r"[,、]")

# read_file_content は Docs を Markdown 風に変換して返し、角括弧をエスケープする
_ESCAPED = {"\\[": "[", "\\]": "]"}

_HEADING = re.compile(r"^\s*#{1,6}\s*(?P<text>.+?)\s*$")
_ITEM = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$")
_LABELLED = re.compile(r"^\[(?P<label>[^\]]+)\]\s*(?P<rest>.*)$")
_MENTION = re.compile(r"\[(?P<name>[^\]]+)\]\(mailto:(?P<email>[^)]+)\)")
_TITLE_DETAIL = re.compile(r"[:：]")


def _unescape(text):
    for escaped, plain in _ESCAPED.items():
        text = text.replace(escaped, plain)
    return text


def _section(body, heading):
    """見出しの配下の行を返す。見出しが無ければ空リスト。"""
    collected = []
    inside = False
    for line in _unescape(body).splitlines():
        matched = _HEADING.match(line)
        if matched:
            if inside:
                break
            inside = heading in matched.group("text")
            continue
        if inside:
            collected.append(line)
    return collected


def parse_participants(body):
    """表示名とメールアドレスの対応を返す。

    実測では出席者は冒頭の `招待済み` 行に並ぶが、見出しの配下ではない。
    節を限定すると1件も取れないため、本文全体から `[表示名](mailto:...)` を集める。
    「次のステップ」節の担当欄はこの形を取らないので、混ざる心配はない。
    """
    participants = {}
    for matched in _MENTION.finditer(_unescape(body)):
        participants[matched.group("name").strip()] = matched.group("email").strip()
    return participants


def _classify(label, participants, self_email):
    """担当を4値に分類する（AC-18、ADR 0006）。

    判別規則: 自分のメールアドレスが未設定のときは全項目を unknown として扱う。
    グループは照合を要しないため単独では判定できるが、spec がそう定めていない。

    担当欄に複数名が並ぶ場合、自分が含まれていれば self とする。拾い漏らす損失が
    拾いすぎる損失より大きいため（ADR 0006 と同じ前提）。
    """
    if self_email is None or label is None:
        return "unknown"
    if label.strip().lower() in GROUP_LABELS:
        return "group"

    names = [name.strip() for name in _ASSIGNEE_SEPARATOR.split(label) if name.strip()]
    if any(participants.get(name) == self_email for name in names):
        return "self"
    return "others"


def parse_next_steps(body, self_email=None):
    """「次のステップ」節から項目を取り出す（AC-18）。"""
    participants = parse_participants(body)

    items = []
    for line in _section(body, NEXT_STEPS_HEADING):
        matched = _ITEM.match(line)
        if not matched:
            continue

        content = matched.group("body")
        labelled = _LABELLED.match(content)
        if labelled:
            label = labelled.group("label").strip()
            content = labelled.group("rest").strip()
        else:
            label = None

        parts = _TITLE_DETAIL.split(content, maxsplit=1)
        title = parts[0].strip()
        detail = parts[1].strip() if len(parts) > 1 else ""

        items.append({
            "title": title,
            "detail": detail,
            "assignee": _classify(label, participants, self_email),
        })
    return items
