"""レコードの検証・正規化・id導出（AC-4 / AC-7）。"""

import pytest

from claude_secretary.records import (
    ValidationError,
    derive_id,
    normalize_title,
    validate_record,
)


class TestNormalizeTitle:
    """AC-4: タイトルの空白の揺れとNFKCを吸収する。"""

    @pytest.mark.parametrize(
        "raw",
        [
            "  議事録の共有  ",
            "\t議事録の共有\t",
            "\n議事録の共有\n",
            "　議事録の共有　",
        ],
        ids=["半角空白", "タブ", "改行", "全角空白"],
    )
    def test_前後の空白を除去する(self, raw):
        assert normalize_title(raw) == "議事録の共有"

    @pytest.mark.parametrize(
        "raw",
        [
            "議事録の  共有",
            "議事録の　　共有",
            "議事録の \t 共有",
        ],
        ids=["半角空白の連続", "全角空白の連続", "空白とタブの混在"],
    )
    def test_連続する空白を1個に畳み込む(self, raw):
        assert normalize_title(raw) == "議事録の 共有"

    def test_全角英数を半角に揃える(self):
        assert normalize_title("ＡＰＩの確認") == "APIの確認"

    def test_半角カナを全角に揃える(self):
        assert normalize_title("ﾃﾞｰﾀ移行") == "データ移行"

    def test_揺れの違う2件は同じ正規化結果になる(self):
        assert normalize_title(" 議事録の  共有 ") == normalize_title("議事録の 共有")


class TestDeriveId:
    """出所と正規化後タイトルから決定的にidを導出する（ADR 0003）。"""

    def test_同じ入力なら同じidになる(self):
        first = derive_id("meeting", "fakedoc-note-a", "議事録の共有")
        second = derive_id("meeting", "fakedoc-note-a", "議事録の共有")
        assert first == second

    @pytest.mark.parametrize(
        "source_type,source_ref,title",
        [
            ("session", "fakedoc-note-a", "議事録の共有"),
            ("meeting", "fakedoc-note-b", "議事録の共有"),
            ("meeting", "fakedoc-note-a", "見積の確認"),
        ],
        ids=["種別が違う", "参照先が違う", "タイトルが違う"],
    )
    def test_1要素でも違えば別のidになる(self, source_type, source_ref, title):
        base = derive_id("meeting", "fakedoc-note-a", "議事録の共有")
        assert derive_id(source_type, source_ref, title) != base


class TestValidateRecord:
    """AC-7: 必須項目が欠けた入力は例外で拒否する。"""

    def test_必須項目が揃っていればidが付く(self, task):
        validated = validate_record(task())
        assert validated["id"] == derive_id("meeting", "fakedoc-note-a", "議事録の共有")

    @pytest.mark.parametrize(
        "broken",
        [
            {"source": {"type": "meeting", "ref": "fakedoc-note-a"}},
            {"title": "議事録の共有", "source": {"ref": "fakedoc-note-a"}},
            {"title": "議事録の共有", "source": {"type": "meeting"}},
            {"title": "議事録の共有"},
        ],
        ids=["タイトルが無い", "出所の種別が無い", "出所の参照先が無い", "出所が無い"],
    )
    def test_必須項目が欠けていたら例外(self, broken):
        with pytest.raises(ValidationError):
            validate_record(broken)

    @pytest.mark.parametrize(
        "title", ["", "   ", "　　", "\t\n"],
        ids=["空文字", "半角空白のみ", "全角空白のみ", "タブと改行のみ"],
    )
    def test_正規化後に空文字になるタイトルは欠落として扱う(self, task, title):
        with pytest.raises(ValidationError):
            validate_record(task(title=title))
