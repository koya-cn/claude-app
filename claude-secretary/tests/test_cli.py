"""コマンドラインインターフェース（終了コードと入出力）。"""

import io
import json
import os

import pytest

from claude_secretary.cli import EXIT_OK, EXIT_SAVE_FAILED, EXIT_VALIDATION, main
from claude_secretary.ledger import load, set_fields

from conftest import PERIOD_END, PERIOD_START, SELF_EMAIL


def run(argv, stdin_text=""):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(
        argv,
        stdin=io.StringIO(stdin_text),
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


class TestUpsert:
    """標準入力から配列を受け取り、1呼び出しを原子性の単位とする。"""

    def test_成功したら0を返す(self, ledger_file, task):
        code, _, _ = run(["upsert"], json.dumps([task()]))
        assert code == EXIT_OK
        assert len(load(ledger_file)) == 1

    def test_配列で複数件を受け取れる(self, ledger_file, task):
        payload = json.dumps([task(), task(title="見積の確認")])
        code, _, _ = run(["upsert"], payload)
        assert code == EXIT_OK
        assert len(load(ledger_file)) == 2

    def test_検証エラーは2を返す(self, ledger_file):
        code, _, stderr = run(["upsert"], json.dumps([{"title": "出所が無い項目"}]))
        assert code == EXIT_VALIDATION
        assert stderr

    def test_検証エラーのとき台帳は書き換わらない(self, ledger_file, task):
        payload = json.dumps([task(), {"title": "出所が無い項目"}])
        code, _, _ = run(["upsert"], payload)
        assert code == EXIT_VALIDATION
        assert load(ledger_file) == []

    def test_標準入力が不正なJSONなら2を返す(self, ledger_file):
        code, _, _ = run(["upsert"], "{壊れている")
        assert code == EXIT_VALIDATION

    def test_保存に失敗したら3を返す(self, ledger_file, task, monkeypatch):
        def fail_replace(*args, **kwargs):
            raise OSError("置換に失敗した")

        monkeypatch.setattr(os, "replace", fail_replace)
        code, _, _ = run(["upsert"], json.dumps([task()]))
        assert code == EXIT_SAVE_FAILED


class TestList:
    """台帳の全レコードをJSONで標準出力に書く。"""

    def test_全レコードをJSONで出す(self, ledger_file, task):
        run(["upsert"], json.dumps([task(), task(title="見積の確認")]))
        code, stdout, _ = run(["list"])
        assert code == EXIT_OK
        assert len(json.loads(stdout)) == 2

    def test_statusで絞り込まない(self, ledger_file, task):
        run(["upsert"], json.dumps([task()]))
        record_id = load(ledger_file)[0]["id"]
        set_fields(ledger_file, record_id, status="done")

        _, stdout, _ = run(["list"])
        assert [record["status"] for record in json.loads(stdout)] == ["done"]

    def test_台帳が無ければ空の配列を出す(self, ledger_file):
        code, stdout, _ = run(["list"])
        assert code == EXIT_OK
        assert json.loads(stdout) == []


class TestSelectEvents:
    """カレンダーのコネクタ応答を期間で絞り、抽出対象と未抽出に分ける。台帳には触らない。"""

    def test_抽出対象と未抽出を返す(self, events):
        code, stdout, _ = run(
            ["select-events", "--start", PERIOD_START, "--end", PERIOD_END],
            json.dumps(events),
        )
        assert code == EXIT_OK
        result = json.loads(stdout)
        assert len(result["targets"]) == 3
        assert len(result["unextracted"]) == 2

    def test_台帳を作らない(self, ledger_file, events):
        run(
            ["select-events", "--start", PERIOD_START, "--end", PERIOD_END],
            json.dumps(events),
        )
        assert not ledger_file.exists()

    def test_標準入力が不正なJSONなら2を返す(self):
        code, _, _ = run(
            ["select-events", "--start", PERIOD_START, "--end", PERIOD_END],
            "{壊れている",
        )
        assert code == EXIT_VALIDATION

    def test_コネクタの封筒をそのまま渡せる(self, events):
        code, stdout, _ = run(
            ["select-events", "--start", PERIOD_START, "--end", PERIOD_END],
            json.dumps({"events": events}),
        )
        assert code == EXIT_OK
        assert len(json.loads(stdout)["targets"]) == 3

    @pytest.mark.parametrize(
        "payload",
        ['{"items": []}', '"文字列"', '["予定でない文字列"]'],
        ids=["別のキー", "文字列", "要素が辞書でない"],
    )
    def test_予定として読めない入力は2を返す(self, payload):
        """規約外の終了コードや未捕捉の例外にしない。"""
        code, _, stderr = run(
            ["select-events", "--start", PERIOD_START, "--end", PERIOD_END], payload
        )
        assert code == EXIT_VALIDATION
        assert stderr

    def test_期間の指定が不正なら2を返す(self, events):
        code, _, _ = run(
            ["select-events", "--start", "きのう", "--end", PERIOD_END],
            json.dumps(events),
        )
        assert code == EXIT_VALIDATION


class TestParseNote:
    """会議メモの本文から、upsert にそのまま渡せる配列を作る。台帳には触らない。"""

    def test_投入できる形の配列を返す(self, config_file, note_body):
        code, stdout, _ = run(
            ["parse-note", "--source-ref", "fakedoc-note-a"], note_body
        )
        assert code == EXIT_OK
        records = json.loads(stdout)
        assert len(records) == 4
        assert records[0]["source"] == {"type": "meeting", "ref": "fakedoc-note-a"}

    def test_出力をそのまま投入できる(self, ledger_file, config_file, note_body):
        _, stdout, _ = run(["parse-note", "--source-ref", "fakedoc-note-a"], note_body)
        code, _, _ = run(["upsert"], stdout)
        assert code == EXIT_OK
        assert len(load(ledger_file)) == 4

    def test_設定にメールがあれば担当を判定する(self, config_file, note_body):
        config_file.write_text(
            json.dumps({"self_email": SELF_EMAIL}), encoding="utf-8"
        )
        _, stdout, _ = run(["parse-note", "--source-ref", "fakedoc-note-a"], note_body)
        assert [r["assignee"] for r in json.loads(stdout)] == [
            "self",
            "others",
            "group",
            "unknown",
        ]

    def test_設定が無ければ担当を判定しない(self, config_file, note_body):
        _, stdout, _ = run(["parse-note", "--source-ref", "fakedoc-note-a"], note_body)
        assert {r["assignee"] for r in json.loads(stdout)} == {"unknown"}

    def test_節が無ければ空の配列を返す(self, config_file, note_body_without_next_steps):
        code, stdout, _ = run(
            ["parse-note", "--source-ref", "fakedoc-note-a"],
            note_body_without_next_steps,
        )
        assert code == EXIT_OK
        assert json.loads(stdout) == []

    def test_参照先の指定が無ければ2を返す(self, config_file, note_body):
        code, _, _ = run(["parse-note"], note_body)
        assert code == EXIT_VALIDATION

    def test_台帳を作らない(self, ledger_file, config_file, note_body):
        run(["parse-note", "--source-ref", "fakedoc-note-a"], note_body)
        assert not ledger_file.exists()


class TestSet:
    """人が変更する列だけを書き換える。"""

    def test_完了にできる(self, ledger_file, task):
        run(["upsert"], json.dumps([task()]))
        record_id = load(ledger_file)[0]["id"]

        code, _, _ = run(["set", record_id, "--status", "done"])
        assert code == EXIT_OK
        assert load(ledger_file)[0]["status"] == "done"

    def test_メモを付けられる(self, ledger_file, task):
        run(["upsert"], json.dumps([task()]))
        record_id = load(ledger_file)[0]["id"]

        code, _, _ = run(["set", record_id, "--note", "先に片付けた"])
        assert code == EXIT_OK
        assert load(ledger_file)[0]["note"] == "先に片付けた"

    def test_存在しないidは2を返す(self, ledger_file, task):
        run(["upsert"], json.dumps([task()]))
        code, _, stderr = run(["set", "存在しないid", "--status", "done"])
        assert code == EXIT_VALIDATION
        assert stderr

    def test_不正なstatusは2を返す(self, ledger_file, task):
        run(["upsert"], json.dumps([task()]))
        record_id = load(ledger_file)[0]["id"]
        code, _, _ = run(["set", record_id, "--status", "存在しない状態"])
        assert code == EXIT_VALIDATION
