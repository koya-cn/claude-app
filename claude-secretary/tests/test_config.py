"""設定の読み込み。自分のメールアドレスはホーム配下から読む。"""

import json
from pathlib import Path

from claude_secretary import config
from claude_secretary.config import config_path, self_email


class TestConfigPath:
    def test_既定はホーム配下(self, monkeypatch):
        monkeypatch.delenv(config.CONFIG_ENV, raising=False)
        assert config_path() == Path.home() / ".claude-secretary" / "config.json"

    def test_環境変数で差し替えられる(self, monkeypatch, tmp_path):
        target = tmp_path / "別の設定.json"
        monkeypatch.setenv(config.CONFIG_ENV, str(target))
        assert config_path() == target


class TestSelfEmail:
    def test_設定にあれば返す(self, config_file):
        config_file.write_text(
            json.dumps({"self_email": "taro.yamada@example.com"}), encoding="utf-8"
        )
        assert self_email() == "taro.yamada@example.com"

    def test_設定ファイルが無ければNone(self, config_file):
        assert self_email() is None

    def test_設定にメールの項目が無ければNone(self, config_file):
        config_file.write_text(json.dumps({}), encoding="utf-8")
        assert self_email() is None

    def test_設定が壊れていても失敗させない(self, config_file):
        """担当を判定しないだけで、収集そのものは止めない。"""
        config_file.write_text("{壊れている", encoding="utf-8")
        assert self_email() is None
