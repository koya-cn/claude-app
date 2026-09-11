"""ブリーフィングの材料と提示頻度（BR-1〜BR-13）。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from claude_secretary.briefing import (
    KIND_DAILY,
    claim,
    KIND_WEEKLY,
    build,
    load_state,
    next_state,
    save_state,
    should_show,
    state_path,
)

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=JST)      # 水曜
SINCE = NOW - timedelta(days=1)


def record(**overrides):
    base = {
        "id": "id-1",
        "source": {"type": "meeting", "ref": "doc-1"},
        "title": "議事録の共有",
        "detail": "今週中に共有する",
        "assignee": "self",
        "first_seen": "2026-09-08T10:00:00+09:00",
        "last_seen": "2026-09-09T10:00:00+09:00",
        "status": "open",
        "note": "",
        "status_changed_at": None,
    }
    base.update(overrides)
    return base


class TestBuild:
    """BR-1〜BR-5: 材料の生成。"""

    def test_担当ごとに分ける(self):
        records = [
            record(id="a", assignee="self"),
            record(id="b", assignee="group"),
            record(id="c", assignee="others"),
            record(id="d", assignee="unknown"),
        ]
        material = build(records, now=NOW, since=SINCE)
        assert [item["id"] for item in material["open"]["self"]] == ["a"]
        assert [item["id"] for item in material["open"]["group"]] == ["b"]
        assert [item["id"] for item in material["open"]["others"]] == ["c"]
        assert [item["id"] for item in material["open"]["unknown"]] == ["d"]

    @pytest.mark.parametrize("status", ["done", "dropped"])
    def test_完了と破棄は未完了の一覧に出さない(self, status):
        records = [record(id="a", status=status)]
        material = build(records, now=NOW, since=SINCE)
        assert material["open"]["self"] == []

    def test_指定した時点より後に完了した項目を出す(self):
        records = [
            record(id="a", status="done",
                   status_changed_at="2026-09-09T09:00:00+09:00"),
            record(id="b", status="done",
                   status_changed_at="2026-09-01T09:00:00+09:00"),
        ]
        material = build(records, now=NOW, since=SINCE)
        assert [item["id"] for item in material["recently_done"]] == ["a"]

    def test_完了時刻が無い項目は最近片付けたものに出さない(self):
        records = [record(id="a", status="done", status_changed_at=None)]
        material = build(records, now=NOW, since=SINCE)
        assert material["recently_done"] == []

    def test_台帳が空でも失敗しない(self):
        material = build([], now=NOW, since=SINCE)
        assert material["open"]["self"] == []
        assert material["recently_done"] == []
        assert material["ledger"]["counts"]["open"] == 0

    def test_最終収集時刻と経過日数を出す(self):
        records = [
            record(id="a", last_seen="2026-09-07T10:00:00+09:00"),
            record(id="b", last_seen="2026-09-08T10:00:00+09:00"),
        ]
        material = build(records, now=NOW, since=SINCE)
        assert material["ledger"]["last_collected"] == "2026-09-08T10:00:00+09:00"
        assert material["ledger"]["stale_days"] == 1

    def test_台帳が空なら最終収集時刻は空になる(self):
        material = build([], now=NOW, since=SINCE)
        assert material["ledger"]["last_collected"] is None
        assert material["ledger"]["stale_days"] is None

    def test_状態ごとの件数を出す(self):
        records = [
            record(id="a", status="open"),
            record(id="b", status="done", status_changed_at="2026-09-09T09:00:00+09:00"),
            record(id="c", status="dropped", status_changed_at="2026-09-09T09:00:00+09:00"),
        ]
        material = build(records, now=NOW, since=SINCE)
        assert material["ledger"]["counts"] == {"open": 1, "done": 1, "dropped": 1}


class TestHandEditedTimestamps:
    """台帳は人が直接編集できる。手書きの時刻にはタイムゾーンが無いことがある。"""

    def test_タイムゾーンの無い最終収集時刻で落ちない(self):
        records = [record(id="a", last_seen="2026-09-08T10:00:00")]
        material = build(records, now=NOW, since=SINCE)
        assert material["ledger"]["stale_days"] is None

    def test_タイムゾーンの無い完了時刻で落ちない(self):
        records = [
            record(id="a", status="done", status_changed_at="2026-09-09T09:00:00")
        ]
        material = build(records, now=NOW, since=SINCE)
        assert material["recently_done"] == []

    def test_時刻として読めない値でも落ちない(self):
        records = [record(id="a", last_seen="きのう")]
        material = build(records, now=NOW, since=SINCE)
        assert material["ledger"]["last_collected"] is None

    def test_タイムゾーン付きの項目は正しく扱う(self):
        """タイムゾーンの無い項目が混ざっても、他の項目は落とさない。"""
        records = [
            record(id="a", last_seen="2026-09-08T10:00:00"),
            record(id="b", last_seen="2026-09-09T10:00:00+09:00"),
        ]
        material = build(records, now=NOW, since=SINCE)
        assert material["ledger"]["last_collected"] == "2026-09-09T10:00:00+09:00"


class TestShouldShow:
    """BR-8〜BR-12: 提示するかの判定。"""

    def test_状態が無ければ提示する(self):
        show, _ = should_show({}, now=NOW)
        assert show is True

    def test_初回は週次の形にする(self):
        _, kind = should_show({}, now=NOW)
        assert kind == KIND_WEEKLY

    def test_同じ日の2回目以降は提示しない(self):
        state = next_state(NOW)
        show, _ = should_show(state, now=NOW + timedelta(hours=3))
        assert show is False

    def test_日付が変われば提示する(self):
        state = next_state(NOW)
        show, _ = should_show(state, now=NOW + timedelta(days=1))
        assert show is True

    def test_同じ週の翌日は日次の形にする(self):
        state = next_state(NOW)
        _, kind = should_show(state, now=NOW + timedelta(days=1))
        assert kind == KIND_DAILY

    def test_週が変われば週次の形にする(self):
        state = next_state(NOW)
        _, kind = should_show(state, now=NOW + timedelta(days=7))
        assert kind == KIND_WEEKLY

    def test_状態の日付が未来でも提示する(self):
        """時計のずれで永久に出なくなることを避ける。"""
        state = next_state(NOW + timedelta(days=3))
        show, _ = should_show(state, now=NOW)
        assert show is True

    def test_壊れた状態でも提示する(self):
        show, _ = should_show({"daily": 12345}, now=NOW)
        assert show is True


class TestState:
    """BR-11〜BR-13: 状態ファイルの読み書き。"""

    def test_既定はホーム配下(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_SECRETARY_BRIEF_STATE", raising=False)
        assert state_path().name == "last-brief.json"

    def test_環境変数で差し替えられる(self, monkeypatch, tmp_path):
        target = tmp_path / "別の状態.json"
        monkeypatch.setenv("CLAUDE_SECRETARY_BRIEF_STATE", str(target))
        assert state_path() == target

    def test_ファイルが無ければ空を返す(self, tmp_path):
        assert load_state(tmp_path / "ない.json") == {}

    def test_壊れていても空を返す(self, tmp_path):
        path = tmp_path / "壊れた.json"
        path.write_text("{壊れている", encoding="utf-8")
        assert load_state(path) == {}

    def test_書いたものを読み戻せる(self, tmp_path):
        path = tmp_path / "深い" / "state.json"
        save_state(path, next_state(NOW))
        assert load_state(path) == next_state(NOW)

    def test_保存後は同じ日に提示しない(self, tmp_path):
        path = tmp_path / "state.json"
        save_state(path, next_state(NOW))
        show, _ = should_show(load_state(path), now=NOW)
        assert show is False


class TestClaim:
    """BR-19: 同時に起動しても提示は1つだけ。

    セッションが複数同時に再開されると、読んでから書くまでの間に他が割り込む。
    実測（2026-09-11）では3つのセッションが5ミリ秒の間に発火し、全部が提示した。
    """

    def test_取れたときだけTrueを返す(self, tmp_path):
        path = tmp_path / "state.json"
        assert claim(path, NOW)[0] is True
        assert claim(path, NOW)[0] is False

    def test_日付が変われば取れる(self, tmp_path):
        path = tmp_path / "state.json"
        claim(path, NOW)
        assert claim(path, NOW + timedelta(days=1))[0] is True

    def test_初回は週次の形になる(self, tmp_path):
        assert claim(tmp_path / "state.json", NOW)[1] == KIND_WEEKLY

    def test_同じ週の翌日は日次の形になる(self, tmp_path):
        path = tmp_path / "state.json"
        claim(path, NOW)
        assert claim(path, NOW + timedelta(days=1))[1] == KIND_DAILY

    def test_同時に呼ばれても1つしか取れない(self, tmp_path):
        """本番で起きたのはこれ。8つ同時に走らせて1つだけが勝つこと。"""
        import concurrent.futures

        path = tmp_path / "state.json"
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim(path, NOW)[0], range(8)))
        assert results.count(True) == 1, results

    def test_別プロセスから同時に呼ばれても1つしか取れない(self, tmp_path):
        """セッションごとに別プロセスなので、スレッドだけでは足りない。"""
        import subprocess
        import sys
        import textwrap

        path = tmp_path / "state.json"
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
            from datetime import datetime, timedelta, timezone
            from claude_secretary.briefing import claim
            now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone(timedelta(hours=9)))
            print(claim({str(path)!r}, now)[0])
        """)
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
            )
            for _ in range(8)
        ]
        outs = [p.communicate()[0].strip() for p in procs]
        assert outs.count("True") == 1, outs
