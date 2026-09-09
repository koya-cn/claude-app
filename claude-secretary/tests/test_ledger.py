"""台帳の読み書き（AC-1〜AC-3 / AC-5〜AC-15 / AC-23 / AC-24 / AC-27〜AC-29）。"""

import json
import os
from pathlib import Path

import pytest

from claude_secretary import ledger
from claude_secretary.ledger import (
    LedgerCorruptError,
    RecordNotFoundError,
    load,
    ledger_path,
    save,
    set_fields,
    upsert,
)
from claude_secretary.records import ValidationError, derive_id

from conftest import LATER, NOW


class TestIdentity:
    """AC-1 / AC-2 / AC-5: 同一性と冪等性。"""

    def test_同じ内容を2回投入してもレコード数は増えない(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        upsert(ledger_file, [task()], now=LATER)
        assert len(load(ledger_file)) == 1

    def test_再投入でlast_seenは更新されfirst_seenは変わらない(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        upsert(ledger_file, [task()], now=LATER)
        record = load(ledger_file)[0]
        assert record["first_seen"] == NOW
        assert record["last_seen"] == LATER

    @pytest.mark.parametrize(
        "source",
        [
            {"type": "session", "ref": "fakedoc-note-a"},
            {"type": "meeting", "ref": "fakedoc-note-b"},
        ],
        ids=["種別が違う", "参照先が違う"],
    )
    def test_出所が違えば同じタイトルでも別レコードになる(self, ledger_file, task, source):
        upsert(ledger_file, [task()], now=NOW)
        upsert(ledger_file, [task(source=source)], now=NOW)
        assert len(load(ledger_file)) == 2

    def test_タイトルの揺れは同一レコードとして扱われる(self, ledger_file, task):
        upsert(ledger_file, [task(title="議事録の 共有")], now=NOW)
        upsert(ledger_file, [task(title=" 議事録の　　共有 ")], now=LATER)
        assert len(load(ledger_file)) == 1


class TestUpdatePolicy:
    """AC-3 / AC-6 / AC-23 / AC-24: どの列を上書きするか。"""

    def test_収集で更新する列は再投入で取り込まれる(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        upsert(
            ledger_file,
            [task(detail="来週に延ばした", assignee="others")],
            now=LATER,
        )
        record = load(ledger_file)[0]
        assert record["detail"] == "来週に延ばした"
        assert record["assignee"] == "others"
        assert record["last_seen"] == LATER

    def test_タイトルは正規化した形で保存される(self, ledger_file, task):
        upsert(ledger_file, [task(title=" 議事録の  共有 ")], now=NOW)
        assert load(ledger_file)[0]["title"] == "議事録の 共有"

    def test_人が変更した列は再投入で上書きされない(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        record_id = load(ledger_file)[0]["id"]
        set_fields(ledger_file, record_id, status="done", note="先に片付けた")

        upsert(ledger_file, [task()], now=LATER)

        record = load(ledger_file)[0]
        assert record["status"] == "done"
        assert record["note"] == "先に片付けた"

    @pytest.mark.parametrize("detail", [None, ""], ids=["欠落", "空文字"])
    def test_detailが空の再投入では既存の本文を維持する(self, ledger_file, task, detail):
        upsert(ledger_file, [task(detail="今週中に共有する")], now=NOW)

        incoming = task()
        if detail is None:
            del incoming["detail"]
        else:
            incoming["detail"] = detail
        upsert(ledger_file, [incoming], now=LATER)

        assert load(ledger_file)[0]["detail"] == "今週中に共有する"

    def test_タイトルの文言が変わったら別レコードとして追加し旧レコードを残す(
        self, ledger_file, task
    ):
        upsert(ledger_file, [task(title="議事録の共有")], now=NOW)
        upsert(ledger_file, [task(title="議事録の共有と展開")], now=LATER)

        titles = {record["title"] for record in load(ledger_file)}
        assert titles == {"議事録の共有", "議事録の共有と展開"}


class TestAtomicity:
    """AC-8 / AC-9: 途中で失敗しても台帳を壊さない。"""

    def test_配列に1件でも不正があれば台帳は書き換わらない(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        before = ledger_file.read_bytes()

        with pytest.raises(ValidationError):
            upsert(
                ledger_file,
                [task(title="見積の確認"), {"title": "出所が無い項目"}],
                now=LATER,
            )

        assert ledger_file.read_bytes() == before

    def test_保存が失敗しても既存の台帳のバイト列は変わらない(
        self, ledger_file, task, monkeypatch
    ):
        upsert(ledger_file, [task()], now=NOW)
        before = ledger_file.read_bytes()

        def fail_replace(*args, **kwargs):
            raise OSError("置換に失敗した")

        # AC-9 は一時ファイルへ書いてから置換する実装を前提にしている
        monkeypatch.setattr(os, "replace", fail_replace)

        with pytest.raises(OSError):
            upsert(ledger_file, [task(title="見積の確認")], now=LATER)

        assert ledger_file.read_bytes() == before


class TestRobustness:
    """AC-10 / AC-11 / AC-12: 読み込みと保存の堅牢性。"""

    def test_台帳ファイルが無ければ空として扱う(self, ledger_file):
        assert load(ledger_file) == []

    def test_壊れたJSONは例外にする(self, ledger_file):
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        ledger_file.write_text("{壊れている", encoding="utf-8")
        with pytest.raises(LedgerCorruptError):
            load(ledger_file)

    def test_壊れたJSONを空として上書きしない(self, ledger_file, task):
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        ledger_file.write_text("{壊れている", encoding="utf-8")
        before = ledger_file.read_bytes()

        with pytest.raises(LedgerCorruptError):
            upsert(ledger_file, [task()], now=NOW)

        assert ledger_file.read_bytes() == before

    def test_日本語をエスケープせずインデント付きで保存する(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        text = ledger_file.read_text(encoding="utf-8")
        assert "議事録の共有" in text
        assert "\\u" not in text
        assert "\n  " in text

    def test_保存した台帳をJSONとして読み戻せる(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        assert isinstance(json.loads(ledger_file.read_text(encoding="utf-8")), list)


class TestLocation:
    """AC-13 / AC-14 / AC-15: 台帳の置き場所。"""

    def test_既定の場所はホーム配下(self, monkeypatch):
        monkeypatch.delenv(ledger.LEDGER_ENV, raising=False)
        assert ledger_path() == Path.home() / ".claude-secretary" / "ledger.json"

    def test_環境変数で置き場所を差し替えられる(self, monkeypatch, tmp_path):
        target = tmp_path / "別の台帳.json"
        monkeypatch.setenv(ledger.LEDGER_ENV, str(target))
        assert ledger_path() == target

    def test_環境変数が空文字なら既定の場所を使う(self, monkeypatch):
        monkeypatch.setenv(ledger.LEDGER_ENV, "")
        assert ledger_path() == Path.home() / ".claude-secretary" / "ledger.json"

    def test_親ディレクトリが無ければ作る(self, tmp_path, task):
        target = tmp_path / "深い" / "階層" / "ledger.json"
        save(target, [])
        assert target.exists()


class TestSetFields:
    """AC-27 / AC-28 / AC-29: 人が変更する列だけを書き換える。"""

    def test_完了にできる(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        record_id = load(ledger_file)[0]["id"]
        set_fields(ledger_file, record_id, status="done")
        assert load(ledger_file)[0]["status"] == "done"

    def test_初期値はopen(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        assert load(ledger_file)[0]["status"] == "open"

    @pytest.mark.parametrize("status", ["done", "dropped"])
    def test_変更後に再投入してもopenに戻らない(self, ledger_file, task, status):
        upsert(ledger_file, [task()], now=NOW)
        record_id = load(ledger_file)[0]["id"]
        set_fields(ledger_file, record_id, status=status)

        upsert(ledger_file, [task()], now=LATER)

        assert load(ledger_file)[0]["status"] == status

    def test_存在しないidは例外にする(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        with pytest.raises(RecordNotFoundError):
            set_fields(ledger_file, "存在しないid", status="done")

    def test_存在しないidの変更で台帳は書き換わらない(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        before = ledger_file.read_bytes()
        with pytest.raises(RecordNotFoundError):
            set_fields(ledger_file, "存在しないid", status="done")
        assert ledger_file.read_bytes() == before

    def test_状態変更は不変列と収集で更新する列を変えない(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        before = load(ledger_file)[0]

        set_fields(ledger_file, before["id"], status="done", note="片付けた")

        after = load(ledger_file)[0]
        for field in ("id", "source", "first_seen", "last_seen", "title", "detail", "assignee"):
            assert after[field] == before[field]

    def test_状態を変えた時刻を記録する(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        record_id = load(ledger_file)[0]["id"]
        set_fields(ledger_file, record_id, status="done", now=LATER)
        assert load(ledger_file)[0]["status_changed_at"] == LATER

    def test_状態を変えていないレコードは空のまま(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        assert load(ledger_file)[0]["status_changed_at"] is None

    def test_状態を変えた時刻は再投入で書き換わらない(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        record_id = load(ledger_file)[0]["id"]
        set_fields(ledger_file, record_id, status="done", now=LATER)

        upsert(ledger_file, [task()], now="2026-09-11T12:00:00+09:00")

        assert load(ledger_file)[0]["status_changed_at"] == LATER

    def test_メモだけの変更では時刻を更新しない(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        record_id = load(ledger_file)[0]["id"]
        set_fields(ledger_file, record_id, note="あとで見る", now=LATER)
        assert load(ledger_file)[0]["status_changed_at"] is None

    def test_idは出所と正規化後タイトルから導出される(self, ledger_file, task):
        upsert(ledger_file, [task()], now=NOW)
        assert load(ledger_file)[0]["id"] == derive_id(
            "meeting", "fakedoc-note-a", "議事録の共有"
        )
