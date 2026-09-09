"""起動時フックの振る舞い（BR-14〜BR-17）。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "session-start-briefing.py"

RECORD = {
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


@pytest.fixture
def hook_env(tmp_path, monkeypatch):
    """フックを走らせる環境。台帳と状態ファイルを一時領域に向ける。"""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "CLAUDE_SECRETARY_LEDGER": str(tmp_path / "ledger.json"),
        "CLAUDE_SECRETARY_BRIEF_STATE": str(tmp_path / "state.json"),
        "CLAUDE_SECRETARY_CONFIG": str(tmp_path / "config.json"),
    }
    return env


def run_hook(env, stdin_text='{"session_id":"s1","cwd":"/tmp","source":"startup"}'):
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
    )
    return result


def write_ledger(env, records):
    Path(env["CLAUDE_SECRETARY_LEDGER"]).write_text(
        json.dumps(records, ensure_ascii=False), encoding="utf-8"
    )


class TestOutput:
    """BR-14 / BR-15: 提示するときだけ書く。"""

    def test_材料とモデルへの指示を出す(self, hook_env):
        write_ledger(hook_env, [RECORD])
        result = run_hook(hook_env)
        assert result.returncode == 0
        assert "議事録の共有" in result.stdout
        assert "提示" in result.stdout

    def test_同じ日の2回目は何も書かない(self, hook_env):
        write_ledger(hook_env, [RECORD])
        first = run_hook(hook_env)
        second = run_hook(hook_env)
        assert first.stdout.strip()
        assert second.stdout.strip() == ""
        assert second.returncode == 0

    def test_材料はJSONとして読める形で埋め込む(self, hook_env):
        write_ledger(hook_env, [RECORD])
        result = run_hook(hook_env)
        start = result.stdout.index("{")
        end = result.stdout.rindex("}") + 1
        material = json.loads(result.stdout[start:end])
        assert material["ledger"]["counts"]["open"] == 1


class TestNeverBlocksStartup:
    """BR-16: 何があっても終了コードは 0。"""

    def test_台帳が無くても0で終わる(self, hook_env):
        result = run_hook(hook_env)
        assert result.returncode == 0

    def test_台帳が無いことを伝える(self, hook_env):
        """黙って終わると、収集していないことに人が気づけない。"""
        result = run_hook(hook_env)
        assert "収集" in result.stdout

    def test_台帳が壊れていても0で終わる(self, hook_env):
        Path(hook_env["CLAUDE_SECRETARY_LEDGER"]).write_text(
            "{壊れている", encoding="utf-8"
        )
        result = run_hook(hook_env)
        assert result.returncode == 0

    def test_台帳が壊れていることを伝える(self, hook_env):
        Path(hook_env["CLAUDE_SECRETARY_LEDGER"]).write_text(
            "{壊れている", encoding="utf-8"
        )
        result = run_hook(hook_env)
        assert "壊れて" in result.stdout

    def test_標準入力が空でも0で終わる(self, hook_env):
        write_ledger(hook_env, [RECORD])
        result = run_hook(hook_env, stdin_text="")
        assert result.returncode == 0

    def test_状態ファイルが壊れていても提示する(self, hook_env):
        write_ledger(hook_env, [RECORD])
        Path(hook_env["CLAUDE_SECRETARY_BRIEF_STATE"]).write_text(
            "{壊れている", encoding="utf-8"
        )
        result = run_hook(hook_env)
        assert result.returncode == 0
        assert "議事録の共有" in result.stdout


class TestStaleLedger:
    """BR-17: 台帳が古ければ収集を促す。"""

    def test_古い台帳では収集を促す(self, hook_env):
        stale = dict(RECORD, last_seen="2026-01-01T10:00:00+09:00")
        write_ledger(hook_env, [stale])
        result = run_hook(hook_env)
        assert "収集" in result.stdout

    def test_やり残しが0件でも無言にしない(self, hook_env):
        write_ledger(hook_env, [dict(RECORD, status="done")])
        result = run_hook(hook_env)
        assert result.stdout.strip()
