import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_secretary import config, ledger  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

# 架空の利用者。フィクスチャの参加者一覧に含まれる（AC-26）。
SELF_EMAIL = "taro.yamada@example.com"
SELF_DISPLAY_NAME = "山田太郎"

NOW = "2026-09-09T12:00:00+09:00"
LATER = "2026-09-10T12:00:00+09:00"

PERIOD_START = "2026-09-01T00:00:00+09:00"
PERIOD_END = "2026-09-08T00:00:00+09:00"


@pytest.fixture
def note_body():
    return (FIXTURES / "meeting_note.md").read_text(encoding="utf-8")


@pytest.fixture
def note_body_generated():
    """実測の自動生成メモに合わせた形。見出しは太字、参加者は招待済み行。"""
    return (FIXTURES / "meeting_note_generated.md").read_text(encoding="utf-8")


@pytest.fixture
def note_body_escaped():
    return (FIXTURES / "meeting_note_escaped.md").read_text(encoding="utf-8")


@pytest.fixture
def note_body_without_next_steps():
    return (FIXTURES / "meeting_note_no_next_steps.md").read_text(encoding="utf-8")


@pytest.fixture
def events():
    return json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))


@pytest.fixture
def ledger_file(tmp_path, monkeypatch):
    """環境変数で差し替えた台帳の置き場所（AC-14）。ファイルはまだ作らない。"""
    path = tmp_path / "ledger" / "ledger.json"
    monkeypatch.setenv(ledger.LEDGER_ENV, str(path))
    return path


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """環境変数で差し替えた設定の置き場所。ファイルはまだ作らない。"""
    path = tmp_path / "config" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(config.CONFIG_ENV, str(path))
    return path


@pytest.fixture
def task():
    """投入レコードを組み立てる。既定は会議由来の1件。"""
    def build(**overrides):
        record = {
            "title": "議事録の共有",
            "source": {"type": "meeting", "ref": "fakedoc-note-a"},
            "detail": "今週中に共有する",
            "assignee": "self",
        }
        record.update(overrides)
        return record
    return build
