# 🔍 claude-resume

Claude CLI の過去のセッション履歴（`~/.claude/`）から、キーワードでプロンプトやAIの回答を検索し、当時の作業ディレクトリに移動してセッションを再開（resume）するためのツールです。

## ✨ 特徴

* **ユーザー＆AI 双方の出力を検索**: 自分が打ったプロンプト（🙋）だけでなく、AIの返答（🤖）も検索対象です。
* **ピンポイント抽出**: ヒットした「その行」だけを表示するため、会話が長くてもノイズがありません。
* **プロジェクト単位のグループ化**: `cwd`（実行ディレクトリ）ごとに結果をまとめるので、文脈が追いやすいです。
* **自動コマンド生成**: `cd <path> && claude --resume <id>` を出力。コピペで即復帰。

## 🚀 インストール方法

1. スクリプトの内容を `claude-resume.py` として保存します。
2. パスの通ったディレクトリ（例: `/usr/local/bin`）へ、**拡張子を除いた名前**でコピーします。
   ```bash
   sudo cp claude-resume.py /usr/local/bin/claude-resume
   ```
3. コピーしたファイルに実行権限を付与します。
   ```bash
   sudo chmod +x /usr/local/bin/claude-resume
   ```

## 💻 使い方

ターミナルからキーワードを入力して実行します。

```bash
claude-resume <検索キーワード>
```

### 実行例

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

## 🛠 カスタマイズ

スクリプト内の以下の箇所を書き換えることで調整可能です。

* **アイコンの変更**:
  `icon = "🙋" if role == "user" else "🤖"` の部分を書き換え。
* **検索感度（大文字小文字）**:
  `keyword.lower() in text_line.lower()` の `.lower()` を消すと、大文字小文字を厳密に区別（完全一致に近い挙動）します。
