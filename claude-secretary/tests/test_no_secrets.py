"""非公開情報の混入検出（AC-21 / AC-22 / AC-26、ADR 0001）。"""

from pathlib import Path

import pytest

from claude_secretary.secrets_scan import (
    FINDING_EXTERNAL_EMAIL,
    FINDING_HOME_PATH,
    FINDING_LEDGER_FILE,
    FINDING_SERVICE_IDENTIFIER,
    SELF_EXCLUDED_PATHS,
    repo_files,
    scan_repo,
    scan_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestScanText:
    """検出すべきものを検出する。"""

    def test_ホームディレクトリの絶対パスを検出する(self):
        assert FINDING_HOME_PATH in scan_text(f"台帳は {Path.home()}/x にある")

    def test_架空以外のドメインのメールアドレスを検出する(self):
        assert FINDING_EXTERNAL_EMAIL in scan_text("連絡先は taro@somewhere.co.jp")

    def test_外部サービスの識別子らしい文字列を検出する(self):
        assert FINDING_SERVICE_IDENTIFIER in scan_text(
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz01234-56789AbCdEf/edit"
        )


class TestScanTextFalsePositives:
    """検出してはいけないものを検出しない。"""

    @pytest.mark.parametrize(
        "text",
        [
            "連絡先は taro.yamada@example.com",
            "参加者 [山田太郎](mailto:hanako.sato@example.org)",
        ],
        ids=["example.com", "example.org"],
    )
    def test_架空のメールアドレスは検出しない(self, text):
        assert scan_text(text) == []

    def test_パッケージのバージョン指定をメールと誤検出しない(self):
        assert scan_text('"react-dom@18.3.1"') == []

    def test_サブリソース完全性のハッシュを識別子と誤検出しない(self):
        assert scan_text(
            '<script integrity="sha384-u6aeetuaXnQ38mYT8rp6sbXaQe3NL9tIBXmnYxwkUI2Hw">'
        ) == []

    def test_ハイフン区切りの長いファイル名を識別子と誤検出しない(self):
        assert scan_text("0007-manual-docs-out-of-extraction-scope.md") == []

    def test_チルダ表記のホームパスは検出しない(self):
        assert scan_text("台帳の既定は ~/.claude-secretary/ledger.json") == []


class TestScanRepo:
    """AC-21: リポジトリ全体を走査する。"""

    def test_走査対象からgit配下を除く(self):
        assert not any(".git/" in str(path) for path in repo_files(REPO_ROOT))

    def test_走査対象からgitignore済みのパスを除く(self):
        assert not any(
            "__pycache__" in str(path) for path in repo_files(REPO_ROOT)
        )

    def test_台帳や設定の実体がリポジトリに無い(self):
        findings = scan_repo(REPO_ROOT)
        offenders = {
            path for path, kinds in findings.items() if FINDING_LEDGER_FILE in kinds
        }
        assert offenders == set()

    def test_リポジトリに非公開情報が混入していない(self):
        assert scan_repo(REPO_ROOT) == {}

    def test_パターン定義とその試験は走査から除く(self):
        """検出器自身は、検出対象の形をした文字列を必然的に含む。"""
        scanned = [str(path) for path in repo_files(REPO_ROOT)]
        for excluded in SELF_EXCLUDED_PATHS:
            assert not any(path.endswith(excluded) for path in scanned), excluded


class TestGitignore:
    """AC-22: 台帳・設定・実行結果の除外設定がある。"""

    @pytest.mark.parametrize(
        "pattern",
        [".claude-secretary/", "ledger.json", ".pytest_cache/"],
        ids=["設定と台帳のディレクトリ", "台帳ファイル", "テストの実行結果"],
    )
    def test_gitignoreに除外設定がある(self, pattern):
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert pattern in text


class TestFixtures:
    """AC-26: フィクスチャは架空データのみ。"""

    def test_フィクスチャに非公開情報が混入していない(self):
        fixtures = Path(__file__).parent / "fixtures"
        for path in sorted(fixtures.iterdir()):
            assert scan_text(path.read_text(encoding="utf-8"), path=path) == [], path
