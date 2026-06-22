---
name: weekly-report
description: Claude Code のローカルセッションから週報を生成し、Asana に投稿するスキル。/weekly-report コマンドの実行時、または「週報を作って／まとめて」「先週（や指定週）の作業を月曜の報告会向けにまとめて」「セッションをプロジェクト別にまとめて Asana に投稿して」といった依頼があったら必ず使う。トリガーはコマンド実行のみで、雑談からの自動起動はしない。対象週（既定=前週 月〜金）のセッション収集、プロジェクト別グルーピング、ステータスと PR/commit の要約、Asana MCP を使った設定済み投稿先への投稿までをカバーする。
---

# 週報 (weekly-report)

実行済みの Claude Code セッションからプロジェクト別「ハイブリッド形式」の週報を生成し、（任意で）Asana に投稿する。月曜の報告会向けに、前週の作業をまとめることを想定している。

このスキルは **コマンド実行のみ**（`/weekly-report`）でトリガーする。ユーザーが明示的にコマンドを呼ぶか、はっきり週報の作成を依頼した場合以外は、自然文の会話から起動しない。

## 設計の意図（最初に読む）

このスキルは **将来チームでも使えるようにする** 前提なので、秘密情報に依存してはいけない。

- 設定はプロジェクト単位の `settings.json` に置く（共有可能・非秘密）。
- Asana 投稿は **Asana MCP** を各自の認証で利用する。**PAT は保存しないし不要**。
- ファイル名・PR 番号・commit を推測で書かない。すべての記述は実際のセッション根拠に基づくこと。

## 引数（すべて任意）

- `period` — 対象期間の上書き。`YYYY-MM-DD..YYYY-MM-DD` / `last-week`（既定）/ `this-week` を受け付ける。
- `post` — `task` / `comment` / `description` / `subtask` / `none`（生成のみ）。既定は設定（`WEEKLY_REPORT_POST_MODE`）に従う。
- `dryrun` — true なら下書きを提示し投稿しない。**既定 true。** 実投稿の前は必ず確認する。
- `--emoji` — 俯瞰用に絵文字付きの概要版を出す任意フラグ。既定はプレーン（絵文字なし、報告会・Asana 向け）。

以下のフェーズを順に実行する。Phase 0 を必ず最初に行う。

---

## Phase 0: Asana 投稿先設定の確認（最初に実施）

何よりも先に、週報の Asana 投稿先が設定されているかを確認する。設定はプロジェクト単位で、各プロジェクトの `.claude/settings.json` に置かれる（共有可・非秘密）。関連キー（`env` または専用キー）:

- `WEEKLY_REPORT_ASANA_PROJECT` — Asana プロジェクト gid
- `WEEKLY_REPORT_ASANA_SECTION` — Asana セクション gid（任意）
- `WEEKLY_REPORT_POST_MODE` — `task` / `comment` / `description` / `subtask`

判定フロー:
1. **現在のプロジェクトに設定あり** → それを投稿先として使用。
2. **現在のプロジェクトに設定なし** → 他の settings ファイルを `grep` で横断検索:
   ```bash
   grep -rl "WEEKLY_REPORT_ASANA_PROJECT" ~/.claude/settings.json ./.claude/settings.json ./.claude/settings.local.json 2>/dev/null
   # 見つからなければ範囲を広げる:
   grep -rl "WEEKLY_REPORT_ASANA_PROJECT" ~/.claude/ ./ 2>/dev/null
   ```
   他で見つかったら「このコマンドは『該当プロジェクト』での実行を想定」と案内し、そこでの実行を促す。投稿はしない。
3. **どこにも無い** → 投稿先未設定の旨を伝え、設定方法（上記キー）を提示。投稿はスキップして生成のみ行う。

検索対象の例: `~/.claude/settings.json`、各プロジェクトの `.claude/settings.json` / `.claude/settings.local.json`。

---

## Phase 1: 期間の決定

既定は **前週の月〜金**。当日基準で算出する。

```bash
dow=$(date +%u)                                   # 1=月 … 7=日
this_mon=$(date -d "today -$((dow-1)) days" +%F)  # 今週の月曜
prev_mon=$(date -d "$this_mon -7 days" +%F)       # 前週の月曜
prev_fri=$(date -d "$prev_mon +4 days" +%F)       # 前週の金曜
```

`period` 引数があればそれを優先:
- `YYYY-MM-DD..YYYY-MM-DD` → 開始..終了として使用。
- `this-week` → 今週 月〜金。
- `last-week` → 既定と同じ。

開始/終了（YYYY-MM-DD）を確定し、見出しに明記する。

---

## Phase 2: セッション収集

収集の基本は **`~/.claude/` 配下の実行済みセッション**。

- `~/.claude-resume.json` が存在すれば、その `dirs` 設定も収集範囲に加える。無ければ `~/.claude/` のみ。
- 収集ツールを実行（期間を十分カバーする件数を要求、例 60）:
  ```bash
  python3 <repo>/claude-resume/claude-resume.py -r 60
  ```
- 出力のヘッダー行 `[ n] MM/DD HH:MM | <project> - <title>` をパースし、**対象期間の MM/DD に該当するセッションのみ**抽出する。
- 抽出した各セッションのターン抜粋（🙋User / 🤖AI、`📝 N prompts`、resume コマンド）も取得し、要約材料にする。

**除外** — 実作業でないノイズは落とす:
- メタ操作: `/model` `/mcp` `/observer-start` `/login` 等。
- 中身のない 1 prompt のみの瑣末セッション（`📝 1 prompt`）。

フィルタ後にセッションが 0 件なら、その旨を率直に伝えて止める（内容を捏造しない）。

---

## Phase 3: 分類・要約

- セッションを **プロジェクト単位**でグルーピングする。
- 各プロジェクトについて抽出する: 実施内容（箇条書き）、関連 PR 番号 / commit ハッシュ、ステータス（完了 / 進行中 / レビュー待ち / マージ待ち）。
- 1週間の主軸テーマを 1〜2 文で概要化する。
- 未完・継続作業は「来週の予定」へ（優先度付き）。

セッションで確認できる根拠のみ使う。PR 番号やファイル名がセッションから確認できない場合は、推測せず省く。

---

## Phase 4: レポート生成（ハイブリッド形式）

以下のテンプレートを使う。**Markdown 表は使わない**（Asana で崩れるため）。箇条書きのみ。

```
# 週報 — {YYYY-MM-DD(月)}〜{YYYY-MM-DD(金)}

## 今週の概要
{主軸テーマを1〜2文}

## プロジェクト別

### {n}. {プロジェクト名} — {一言テーマ}
- ステータス: {完了 / 進行中 / レビュー待ち 等}
- {実施内容1}（{PR/commit}）
- {実施内容2}

## 来週の予定
- 【高】{タスク}
- 【中】{タスク}
- 【低】{タスク}
```

出力ルール:
- **Markdown 表は禁止。** 箇条書きのみ。
- 既定は **絵文字なしのプレーン**（報告会・Asana 向け）。`--emoji` で俯瞰用の絵文字版に切替可。
- PR 番号・commit ハッシュは既定で残す（省略オプション可）。
- 推測でファイル名/PR 番号を書かない（セッション根拠のみ）。

---

## Phase 5: Asana 投稿（Phase 0 で投稿先が確定し、`post != none` の場合）

既定は `dryrun=true`: **投稿前にタイトル/本文を画面提示し、明示的な確認を取る（BLOCKING）。** はっきりした GO がない限り投稿しない。

投稿は **Asana MCP**（PAT 不要・各自の認証を利用）:
- `task` → `asana_create_task`（設定セクション内に新規タスクを作成。`project_id` = `WEEKLY_REPORT_ASANA_PROJECT`、`section_id` = `WEEKLY_REPORT_ASANA_SECTION`、`name` = `週報 {開始}〜{終了}`、本文は `html_notes`/`notes`）。セクション運用の既定。
- `comment` → `asana_create_task_story`（対象タスクにコメント）
- `description` → `asana_update_task`（説明欄を更新）
- `subtask` → `asana_create_task`（親タスク配下に新規）

投稿先（プロジェクト/セクション/タスク）は Phase 0 の設定値を使用する。`task` モードはセクション gid が必須。`comment` / `description` / `subtask` は対象タスク gid が必要なため、その運用では Phase 0 の設定にタスク gid を追加すること。

---

## Phase 6: 報告

- 成功時: 投稿先 URL / コメント ID を簡潔に報告。
- 失敗時: Asana/HTTP エラー内容と対処を提示。

---

## 品質チェックリスト（確定前に確認）

- [ ] Asana 投稿先設定の有無を最初に確認した（現在プロジェクト → 他 settings.json grep → 未設定 の順）
- [ ] 対象期間（既定は前週 月〜金）が正しく算出され、見出しに明記されている
- [ ] 期間内の全プロジェクト/セッションを網羅（ノイズは除外）
- [ ] 各プロジェクトにステータスがある
- [ ] 未完項目が「来週の予定」に優先度付きで入っている
- [ ] PR/commit/ファイル名が実際の根拠に基づく（推測なし）
- [ ] Markdown 表を使っていない
- [ ] Asana 投稿前に下書き確認を行った（dryrun / BLOCKING）

## 参照

- 収集ツール: `<repo>/claude-resume/claude-resume.py`（`-r` の出力をパース）
- 関連コマンド: `/share`（Slack 共有）, `/commit`
