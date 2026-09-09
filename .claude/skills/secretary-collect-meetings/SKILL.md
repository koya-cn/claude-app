---
name: secretary-collect-meetings
description: >
  Collect leftover tasks from calendar meetings into the secretary's ledger.
  Fetches events for a period over the Google Calendar connector, enumerates
  every attachment, reads only the auto-generated meeting notes (in an isolated
  subagent so their bodies never reach the main context), extracts the "next
  steps" items, and writes them to the ledger via the claude-secretary CLI.
  Reports which attachments were left unextracted instead of dropping them
  silently.
  Trigger when the user says "先週の会議からタスク集めて", "会議のやり残しを台帳に入れて",
  "collect tasks from meetings", or invokes the secretary's collection.
  Do NOT use for reading a single meeting note's content (just read it directly).
  Do NOT use for the other input sources (task service, Claude Code sessions) —
  those are separate slices and not implemented yet.
user-invocable: true
---

# 会議からの収集

カレンダーの予定に添付された自動生成の会議メモから、やり残しを抽出して台帳に入れる。

抽出しない添付も**捨てずに報告する**。見送ったことを隠さないのが、この収集の前提
（[ADR 0007](../../../docs/permanent/adr/0007-manual-docs-out-of-extraction-scope.md)）。

## 前提の確認

1. `claude-secretary` が使えることを確認する

   ```bash
   python3 -m claude_secretary.cli list
   ```

   `claude-secretary/` をカレントに含める必要がある。リポジトリのルートから実行する場合は
   `PYTHONPATH=claude-secretary` を付ける。

2. `~/.claude-secretary/config.json` に自分のメールアドレスがあることを確認する。
   無い場合は担当の判定ができず、全項目が担当不明になる。止める必要はないが、
   その旨を報告に含める

   ```json
   { "self_email": "自分のアドレス" }
   ```

## 手順

### 0. 作業ディレクトリの残骸を確認する

一時ファイルの置き場に**前回の実行結果が残っていないか**を先に見る。残っていると、
今回取得したものと混ざって誤ったレコードを投入する。`ls -la` で mtime を確認し、
今回の実行より古いものは退避するか消す。

### 1. 期間を決める

指示に期間が無ければ**直近7日**を既定にする。タイムゾーンは JST（`+09:00`）。
開始を含み、終了を含まない。

### 2. 予定を取る

`mcp__claude_ai_Google_Calendar__list_events` で期間内の予定を取る。
出欠ステータスでは絞らない（欠席した会議のメモにもやり残しは入る）。

取得そのものが失敗した場合は、**台帳を変更せずに停止する**。部分的に成功した状態で
先へ進めない。

### 3. 添付を仕分ける

コネクタ応答をそのまま `select-events` に渡す。

```bash
echo "$EVENTS_JSON" | PYTHONPATH=claude-secretary python3 -m claude_secretary.cli \
  select-events --start 2026-09-01T00:00:00+09:00 --end 2026-09-08T00:00:00+09:00
```

返るのは2つの配列。

| 配列 | 中身 | この後の扱い |
|---|---|---|
| `targets` | 自動生成の会議メモ | 本文を読んで抽出する |
| `unextracted` | それ以外の添付（手動のアジェンダ、資料など） | 読まない。件数と参照先を報告する |

終日予定と添付のない予定は、ここで自動的に外れる。

### 4. 会議メモを読んで抽出する（サブエージェントに隔離）

**`targets` の本文をこの文脈に載せてはいけない。** 会議メモは要約と文字起こしを含み、
1件で数万文字になる。部分取得の手段がないので、読む処理はサブエージェントに閉じる。

`targets` をまとめて1つのサブエージェントに渡し、次を指示する。

- 各 `document_id` を `mcp__claude_ai_Google_Drive__read_file_content` で読む
- 本文を一時ファイルに書き、`parse-note` に標準入力から渡す

  ```bash
  cat "$BODY_FILE" | PYTHONPATH=claude-secretary python3 -m claude_secretary.cli \
    parse-note --source-ref "$DOCUMENT_ID"
  ```

- 出力（レコードの配列）を1つにまとめて返す
- **本文・抜粋・文字起こしの内容は返さない。** 返すのは `parse-note` の出力と、
  読めなかった `document_id` とその理由だけ

読めないメモ（権限がない、削除済み）は**その1件だけ飛ばして続ける**。理由は報告に含める。
「次のステップ」節が無いメモは0件で正常。エラーにしない。

### 5. 台帳に入れる

サブエージェントが返した配列を1回の `upsert` にまとめて渡す。1回の呼び出しが
原子性の単位なので、分割せずまとめる。

```bash
echo "$RECORDS_JSON" | PYTHONPATH=claude-secretary python3 -m claude_secretary.cli upsert
```

終了コードで結果が分かる。

| コード | 意味 | 対応 |
|---|---|---|
| `0` | 成功 | `{"added": n, "updated": n}` が返る |
| `2` | 検証エラー | 台帳は変わっていない。入力を直して再実行する |
| `3` | 保存失敗 | 台帳は変わっていない。原因を報告する |

同じ期間を2回実行しても台帳のタスク数は増えない。出所と正規化したタイトルから
idを導出しているため、再投入は既存レコードの更新になる。

### 6. 報告する

次の形で報告する。**会議メモの本文は含めない。**

```
予定 20件（9/1〜9/8）
  添付あり 15件 / 添付なし 5件
  自動生成メモ 14件を読んだ（読めなかった 0件）

台帳に反映: 12件（新規 9件 / 既存 3件）
  担当 self 5件 / others 4件 / group 2件 / unknown 1件

未抽出の添付 9件（手動作成の資料）:
  - <タイトル> <URL>
```

未抽出の添付は**必ず出す**。件数だけで済ませず、参照先も出す。読まれなければ
無視しているのと同じになるため。

### 7. 一時ファイルを片付ける

会議メモの本文を書き出した一時ファイルを削除する。社内の会議内容がそのまま残るため、
報告が終わったら残さない。

## やってはいけないこと

- 会議メモの本文・抜粋をメインの文脈に載せる（サブエージェントの外に出す）
- 未抽出の添付を報告から省く
- 手動作成の資料を読んでタスクを抽出する。実測で抽出可能な項目が存在しなかったため
  対象外にしている（[ADR 0007](../../../docs/permanent/adr/0007-manual-docs-out-of-extraction-scope.md)）。
  方針を変えるなら決定を記録してから
- カレンダーの取得が途中で失敗した状態で `upsert` を実行する
- 台帳を直接書き換える。CLI を通す（人が手で直す場合を除く）
- 会議メモ本文の一時ファイルを消さずに残す
- 実データの抽出結果をリポジトリ内の文書やコミットメッセージに転記する
  （[ADR 0001](../../../docs/permanent/adr/0001-public-repo-with-secrets-isolated.md)）

## 関連

- 要件定義: [docs/work/task-secretary/spec.md](../../../docs/work/task-secretary/spec.md)
- ツール: [claude-secretary/README.md](../../../claude-secretary/README.md)
