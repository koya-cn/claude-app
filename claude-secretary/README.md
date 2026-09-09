# 🗂 claude-secretary

やり残しを1か所に集約して棚卸しするための台帳ツール。

複数の場所に散らばったやり残しを拾い切ることが目的で、優先順位付けはしない。
第1スライスの入力は、カレンダーの予定に添付された自動生成の会議メモのみ。

## ✨ 特徴

- **台帳が独自**。既存のタスク管理サービスを台帳にしない（[ADR 0003](../docs/permanent/adr/0003-secretary-owns-its-ledger.md)）
- **再収集で増殖しない**。出所と正規化したタイトルから決定的なidを導出する
- **人がつけた状態を壊さない**。完了にしたものが翌日の収集で未完了に戻らない
- **見送った添付を隠さない**。抽出しなかった添付は件数と参照先を報告する（[ADR 0007](../docs/permanent/adr/0007-manual-docs-out-of-extraction-scope.md)）
- 標準ライブラリのみで動く

## 🚀 インストール方法

依存パッケージなし。Python 3.10 以上。

```bash
python3 -m claude_secretary.cli list
```

## 💻 使い方

台帳の入出力だけがコマンドラインツールになっている。カレンダーと会議メモには
コネクタ経由でしか到達できないため、**収集はスキルの手順書側**が担う。

### 投入 (`upsert`)

標準入力からレコードの配列を受け取る。1回の呼び出しが原子性の単位で、
1件でも不正があれば台帳は一切書き換わらない。

```bash
echo '[{"title":"議事録の共有","source":{"type":"meeting","ref":"<文書ID>"},
      "detail":"今週中に共有する","assignee":"self"}]' \
  | python3 -m claude_secretary.cli upsert
```

### 一覧 (`list`)

全レコードを JSON で標準出力に書く。`status` では絞り込まない。
人が読む画面は持たず、整形は呼び出し側（Claude）に任せる。

```bash
python3 -m claude_secretary.cli list
```

### 状態の変更 (`set`)

`status` と `note` だけを書き換える。収集で更新される列と不変列には触らない。

```bash
python3 -m claude_secretary.cli set <id> --status done
python3 -m claude_secretary.cli set <id> --note "先に片付けた"
```

`status` は `open`（初期値）/ `done` / `dropped`。状態を変えた時刻は
`status_changed_at` に残るので、「先週片付けたもの」を後から出せる。

### 終了コード

| コード | 意味 |
|---|---|
| `0` | 成功 |
| `2` | 検証エラー（台帳は不変更） |
| `3` | 保存失敗（台帳は不変更） |

### ブリーフィングの材料 (`brief`)

台帳から「今やること」の材料を JSON で出す。台帳には書き込まない。

```bash
python3 -m claude_secretary.cli brief
python3 -m claude_secretary.cli brief --now 2026-09-09T09:00:00+09:00 --since 2026-09-02T09:00:00+09:00
```

未完了を担当ごとに分け、最近片付けたもの、台帳の鮮度（最終収集からの経過日数）を返す。
**整形はしない。**人が読む形にするのはモデルの仕事（[ADR 0008](../docs/permanent/adr/0008-briefing-via-session-start-hook.md)）。

## 🔔 起動時のブリーフィング

Claude Code を起動したときに、台帳の中身を自動で提示させられる。
`~/.claude/settings.json` の `hooks.SessionStart` に次を足す。

```json
{
  "hooks": [
    {
      "type": "command",
      "command": "python3 \"<リポジトリのパス>/claude-secretary/hooks/session-start-briefing.py\"",
      "timeout": 10
    }
  ]
}
```

- 同じ日の2回目以降は出ない。日付が変われば出る。週が変われば週次の形になる
- 提示した記録は `~/.claude-secretary/last-brief.json` に残る。消せばもう一度出る
- 台帳が無い・壊れている場合も**黙らずに理由を出す**。人が気づけないため
- 何が起きても**終了コードは 0**。フックの失敗で起動が止まらないようにしている
- フックからコネクタは呼べないので、**収集は自動では走らない**。台帳が古ければ
  収集を促すだけ

## ⚙️ 台帳ファイル (`~/.claude-secretary/ledger.json`)

環境変数 `CLAUDE_SECRETARY_LEDGER` で置き場所を差し替えられる。
UTF-8・インデント付きで、日本語をエスケープせずに書くので直接編集できる。
ただし**手でレコードを足すときは `id` が必要**なので、追加は `upsert` を通す。
`id` の無いレコードがあると、台帳を壊れているものとして扱い書き換えを拒否する。

| フィールド | 分類 | 内容 |
|---|---|---|
| `id` | 不変 | 出所と正規化後タイトルから導出 |
| `source.type` / `source.ref` | 不変 | 出所の種別と参照先 |
| `first_seen` | 不変 | 初回に台帳へ入った時刻 |
| `title` / `detail` / `assignee` / `last_seen` | 収集で更新 | 再投入で上書きする |
| `status` / `note` | 人が変更 | 再投入で上書きしない |
| `status_changed_at` | 人が変更 | `status` を変えた時刻。未変更なら `null` |

台帳・設定・実行結果はリポジトリに含めない（[ADR 0001](../docs/permanent/adr/0001-public-repo-with-secrets-isolated.md)）。

## 🧪 テスト

```bash
python3 -m pytest claude-secretary/tests -q
```

`tests/test_no_secrets.py` はリポジトリ全体を走査して非公開情報の混入を検出する。
リポジトリには**構造のパターンだけ**を置き、組織固有の文字列は
`~/.claude-secretary/forbidden-patterns.json` から読む。

## 📄 仕様

- 要件定義: [docs/work/task-secretary/spec.md](../docs/work/task-secretary/spec.md)
- 決定記録: [docs/permanent/adr/](../docs/permanent/adr/)
- ブリーフィングの要件定義: [docs/work/task-secretary-briefing/spec.md](../docs/work/task-secretary-briefing/spec.md)
