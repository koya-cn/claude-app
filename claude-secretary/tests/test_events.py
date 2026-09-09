"""カレンダーのコネクタ応答からの添付の列挙（AC-16 / AC-17 / AC-25 / AC-30）。"""

from datetime import datetime

import pytest

from claude_secretary.events import (
    collect_attachments,
    extract_document_id,
    is_generated_note,
    normalize_events,
)

from conftest import PERIOD_END, PERIOD_START

GENERATED = "https://docs.google.com/document/d/fakedoc-note-a/edit?usp=meet_tnfm_calendar"
MANUAL = "https://docs.google.com/document/d/fakedoc-agenda-b/edit"


@pytest.fixture
def period():
    return datetime.fromisoformat(PERIOD_START), datetime.fromisoformat(PERIOD_END)


class TestDiscrimination:
    """判別規則: 自動生成された会議メモかどうか。"""

    def test_マーカーがあれば自動生成メモ(self):
        assert is_generated_note(GENERATED) is True

    def test_マーカーが無ければ自動生成メモではない(self):
        assert is_generated_note(MANUAL) is False

    def test_URLからファイルIDを取り出す(self):
        assert extract_document_id(GENERATED) == "fakedoc-note-a"

    def test_ファイルIDを含まないURLはNoneを返す(self):
        assert extract_document_id("https://example.com/なにか") is None


class TestNormalizeEvents:
    """コネクタは {"events": [...]} の封筒で返す。封筒付きと配列の両方を受ける。"""

    def test_封筒付きでも配列を取り出す(self, events):
        assert normalize_events({"events": events}) == events

    def test_配列をそのまま受ける(self, events):
        assert normalize_events(events) == events

    @pytest.mark.parametrize(
        "payload",
        [{}, {"items": []}, "文字列", 42, None, ["予定でない文字列"]],
        ids=["空の辞書", "別のキー", "文字列", "数値", "None", "要素が辞書でない"],
    )
    def test_予定の配列として読めない入力は例外(self, payload):
        with pytest.raises(ValueError):
            normalize_events(payload)

    def test_封筒付きのまま仕分けできる(self, events, period):
        wrapped = collect_attachments({"events": events}, *period)
        assert len(wrapped["targets"]) == 3


class TestCollectAttachments:
    """AC-16 / AC-30: 全添付を列挙し、抽出対象と未抽出に分ける。"""

    def test_自動生成メモを抽出対象として返す(self, events, period):
        result = collect_attachments(events, *period)
        assert {target["document_id"] for target in result["targets"]} == {
            "fakedoc-note-a",
            "fakedoc-note-b",
            "fakedoc-note-e",
        }

    def test_それ以外の添付を未抽出として返す(self, events, period):
        result = collect_attachments(events, *period)
        assert {item["document_id"] for item in result["unextracted"]} == {
            "fakedoc-agenda-b",
            "fakedoc-runsheet-c",
        }

    def test_自動生成メモを持たない予定を対象外にしない(self, events, period):
        """AC-16: 予定を添付の種類で除外しない。"""
        result = collect_attachments(events, *period)
        event_ids = {item["event_id"] for item in result["unextracted"]}
        assert "evt-manual-only" in event_ids

    def test_未抽出の添付は参照先を持つ(self, events, period):
        result = collect_attachments(events, *period)
        assert all(item["file_url"] for item in result["unextracted"])

    def test_添付を1件も持たない予定は0件として扱う(self, events, period):
        result = collect_attachments(events, *period)
        listed = {
            item["event_id"] for item in result["targets"] + result["unextracted"]
        }
        assert "evt-no-attachment" not in listed

    def test_台帳に入れるのは抽出対象だけである(self, events, period):
        """AC-30: 未抽出は報告に出すが台帳には入れない。"""
        result = collect_attachments(events, *period)
        assert len(result["targets"]) == 3
        assert len(result["unextracted"]) == 2
        target_ids = [target["document_id"] for target in result["targets"]]
        unextracted_ids = [item["document_id"] for item in result["unextracted"]]
        assert set(target_ids).isdisjoint(unextracted_ids)


class TestPeriod:
    """AC-25: 開始を含み終了を含まない半開区間。"""

    def test_開始と同時刻の予定は含む(self, events, period):
        result = collect_attachments(events, *period)
        event_ids = {target["event_id"] for target in result["targets"]}
        assert "evt-on-start-boundary" in event_ids

    def test_終了と同時刻の予定は含まない(self, events, period):
        result = collect_attachments(events, *period)
        listed = {
            item["event_id"] for item in result["targets"] + result["unextracted"]
        }
        assert "evt-on-end-boundary" not in listed


class TestAllDayEvents:
    """終日予定は対象にしない。収集の対象は時刻を持つ会議に限る。"""

    def test_終日予定を対象から外す(self, events, period):
        result = collect_attachments(events, *period)
        listed = {
            item["event_id"] for item in result["targets"] + result["unextracted"]
        }
        assert "evt-all-day" not in listed

    def test_終日予定が混ざっていても落ちない(self, events, period):
        """開始が日付だけの予定はタイムゾーンを持たないため、比較で落ちうる。"""
        result = collect_attachments(events, *period)
        assert len(result["targets"]) == 3


class TestResponseStatus:
    """AC-17: 出欠ステータスで絞り込まない。"""

    def test_欠席の予定も対象にする(self, events, period):
        result = collect_attachments(events, *period)
        event_ids = {target["event_id"] for target in result["targets"]}
        assert "evt-note-and-manual" in event_ids

    def test_未回答の予定も対象にする(self, events, period):
        result = collect_attachments(events, *period)
        event_ids = {item["event_id"] for item in result["unextracted"]}
        assert "evt-manual-only" in event_ids
