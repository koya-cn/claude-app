# 🔍 claude-resume

Claude CLI の過去のセッション履歴（`~/.claude/`）から、キーワードでプロンプトやAIの回答を検索し、当時の作業ディレクトリに移動してセッションを再開（resume）するためのツールです。

## ✨ 特徴

* **ユーザー＆AI 双方の出力を検索**: 自分が打ったプロンプト（🙋）だけでなく、AIの返答（🤖）も検索対象です。
* **ピンポイント抽出**: ヒットした「その行」だけを表示するため、会話が長くてもノイズがありません。
* **プロジェクト単位のグループ化**: `cwd`（実行ディレクトリ）ごとに結果をまとめるので、文脈が追いやすいです。
* **自動コマンド生成**: `cd <path> && claude --resume <id>` を出力。コピペで即復帰。
* **直近セッション一覧**: `--recent` で「最近何をしていたか」を時系列で確認。
* **Claude による要約**: `--summary` で直近の作業内容を Claude が自動要約。

## 🚀 インストール方法

1. スクリプトの内容を `claude-resume.py` として保存します。
2. パスの通ったディレクトリ（例: `/usr/local/bin`）へインストールします。

   **コピーする場合:**
   ```bash
   sudo cp claude-resume.py /usr/local/bin/claude-resume
   sudo chmod +x /usr/local/bin/claude-resume
   ```

   **シンボリックリンクを使う場合（スクリプト更新時に再コピー不要）:**
   ```bash
   sudo ln -s /path/to/claude-resume.py /usr/local/bin/claude-resume
   chmod +x /path/to/claude-resume.py
   ```

## 💻 使い方

```bash
claude-resume <検索キーワード>    # キーワード検索
claude-resume -r [N]             # 直近 N セッションの一覧（デフォルト: 5）
claude-resume --recent [N]       # 同上
claude-resume -s [N]             # 直近 N セッションを一覧 + Claude 要約（デフォルト: 5）
claude-resume --summary [N]      # 同上
```

### キーワード検索

```text
📁 Project: /home/user/project-abc
   🙋 新規機能のPR作成用コマンドを教えて
   🚀 コマンド: cd /home/user/project-abc && claude --resume 9651632f-5d44-4042-bcdb-693b3422914d
   ---------------------------------------------------------
📁 Project: /home/user/zenn-docs
   🤖 PR作成完了です。こちらのURLから確認できます。
   🚀 コマンド: cd /home/user/zenn-docs && claude --resume 7680cafd-cfd1-4dc4-b2bc-efad777ac0d5
   ---------------------------------------------------------
```

### 直近セッション一覧 (`-r` / `--recent`)

```text
📋 直近のセッション (5件)
============================================================
 [ 1] 04/10 12:12 | claude-app
      💬 オプション追加したい。例えば直近なにしていたかの...
      📝 5 prompts
      🚀 cd /home/user/claude-app && claude --resume fe2dce6d-...
      ------------------------------------------------------
 [ 2] 04/10 00:41 | wseminar2_live
      💬 削除は後でやるよ
      📝 12 prompts
      🚀 cd /home/user/wseminar2_live && claude --resume 8d684213-...
      ------------------------------------------------------
```

### Claude による要約 (`-s` / `--summary`)

```text
📋 直近のセッション (5件)
...（一覧表示）...

🤖 Claude による要約を生成中...

🤖 Claude による要約:
============================================================
1. claude-app: resume ツールに直近一覧と要約機能を追加する実装を検討
2. wseminar2_live: スキルをグローバルからプロジェクト固有に移動する作業
3. ...
```

## ⚙️ 設定ファイル (`~/.claude-resume.json`)

ホームディレクトリに `~/.claude-resume.json` を作成することで動作をカスタマイズできます。

### 設定項目

| キー | 型 | デフォルト | 説明 |
|------|----|-----------|------|
| `dirs` | 文字列の配列 | `["~/.claude"]` | 全操作の対象ディレクトリ（キーワード検索・直近一覧・要約） |

### 設定例

```json
{
  "dirs": [
    "~/.claude",
    "/path/to/another/claude-sessions"
  ]
}
```

`dirs` を省略した場合や設定ファイルが存在しない場合は、デフォルトの `~/.claude` のみが対象となります。

> **補足**: `dirs` に `sessions/*.json` しかないディレクトリ（Windows の Claude Desktop など）を指定した場合、`--recent` / `--summary` ではアクティブセッションのみが表示されます（`history.jsonl` がないためプロンプト文字列は表示されません）。

## 🛠 カスタマイズ

スクリプト内の以下の箇所を書き換えることで調整可能です。

* **アイコンの変更**:
  `icon = "🙋" if role == "user" else "🤖"` の部分を書き換え。
* **検索感度（大文字小文字）**:
  `keyword.lower() in text_line.lower()` の `.lower()` を消すと、大文字小文字を厳密に区別（完全一致に近い挙動）します。
