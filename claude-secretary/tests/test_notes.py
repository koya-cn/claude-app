"""自動生成された会議メモのパース（AC-18、ADR 0005 / 0006）。"""

from claude_secretary.notes import parse_next_steps, parse_participants

from conftest import SELF_EMAIL


class TestParseParticipants:
    """会議メモ冒頭の参加者一覧から表示名とメールの対応を解決する。"""

    def test_表示名とメールアドレスの対応を返す(self, note_body):
        participants = parse_participants(note_body)
        assert participants["山田太郎"] == "taro.yamada@example.com"
        assert participants["佐藤花子"] == "hanako.sato@example.com"

    def test_参加者一覧が無ければ空を返す(self):
        assert parse_participants("# 見出しだけのメモ\n") == {}


class TestGeneratedNoteFormat:
    """実測（2026-09-09）の自動生成メモの形に合わせた検証。

    参加者は見出しの配下ではなく `招待済み` 行に並び、節の見出しは太字記号を含む。
    """

    def test_招待済み行から参加者を解決する(self, note_body_generated):
        participants = parse_participants(note_body_generated)
        assert participants["山田太郎"] == "taro.yamada@example.com"

    def test_太字の見出しでも節を取れる(self, note_body_generated):
        items = parse_next_steps(note_body_generated, self_email=SELF_EMAIL)
        assert len(items) == 5

    def test_自分が担当の項目をselfにする(self, note_body_generated):
        items = parse_next_steps(note_body_generated, self_email=SELF_EMAIL)
        assert items[0]["assignee"] == "self"

    def test_英語のグループ表記もgroupにする(self, note_body_generated):
        items = parse_next_steps(note_body_generated, self_email=SELF_EMAIL)
        assert items[1]["assignee"] == "group"

    def test_複数名のラベルに自分が含まれればself(self, note_body_generated):
        items = parse_next_steps(note_body_generated, self_email=SELF_EMAIL)
        assert items[2]["assignee"] == "self"

    def test_参加者一覧に無い名前はothers(self, note_body_generated):
        items = parse_next_steps(note_body_generated, self_email=SELF_EMAIL)
        assert items[3]["assignee"] == "others"

    def test_担当欄が無ければunknown(self, note_body_generated):
        items = parse_next_steps(note_body_generated, self_email=SELF_EMAIL)
        assert items[4]["assignee"] == "unknown"

    def test_詳細節の本文を項目に混ぜない(self, note_body_generated):
        items = parse_next_steps(note_body_generated, self_email=SELF_EMAIL)
        assert all("文字起こし" not in item["detail"] for item in items)


class TestParseNextSteps:
    """AC-18: 「次のステップ」節から項目を取り出し、担当を4値に分類する。"""

    def test_節の項目をすべて取り出す(self, note_body):
        items = parse_next_steps(note_body, self_email=SELF_EMAIL)
        assert len(items) == 4

    def test_タイトルと詳細を分けて取り出す(self, note_body):
        items = parse_next_steps(note_body, self_email=SELF_EMAIL)
        assert items[0]["title"] == "議事録の共有"
        assert items[0]["detail"] == "今週中に共有する"

    def test_自分の表示名と一致すればself(self, note_body):
        items = parse_next_steps(note_body, self_email=SELF_EMAIL)
        assert items[0]["assignee"] == "self"

    def test_自分以外の表示名ならothers(self, note_body):
        items = parse_next_steps(note_body, self_email=SELF_EMAIL)
        assert items[1]["assignee"] == "others"

    def test_グループならgroup(self, note_body):
        items = parse_next_steps(note_body, self_email=SELF_EMAIL)
        assert items[2]["assignee"] == "group"

    def test_担当欄が無ければunknown(self, note_body):
        items = parse_next_steps(note_body, self_email=SELF_EMAIL)
        assert items[3]["assignee"] == "unknown"
        assert items[3]["title"] == "移行手順の整理"

    def test_担当を問わず全項目を返す(self, note_body):
        """ADR 0006: others も group も落とさない。"""
        items = parse_next_steps(note_body, self_email=SELF_EMAIL)
        assert [item["assignee"] for item in items] == [
            "self",
            "others",
            "group",
            "unknown",
        ]

    def test_角括弧がエスケープされていても同じ結果になる(
        self, note_body, note_body_escaped
    ):
        """判別規則: Markdown 変換で [ ] がエスケープされる場合がある。"""
        assert parse_next_steps(note_body_escaped, self_email=SELF_EMAIL) == (
            parse_next_steps(note_body, self_email=SELF_EMAIL)
        )

    def test_節が無ければ0件で正常終了する(self, note_body_without_next_steps):
        assert parse_next_steps(note_body_without_next_steps, self_email=SELF_EMAIL) == []

    def test_自分のメールが未設定なら全項目をunknownにする(self, note_body):
        """判別規則: 未設定のときは全項目を unknown として扱う。"""
        items = parse_next_steps(note_body, self_email=None)
        assert {item["assignee"] for item in items} == {"unknown"}

    def test_文字起こしの本文を項目に混ぜない(self, note_body):
        items = parse_next_steps(note_body, self_email=SELF_EMAIL)
        assert all("文字起こし" not in item["detail"] for item in items)
