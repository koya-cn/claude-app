# statusline

Claude Codeのステータスラインにプロジェクト名・gitブランチ・選択中のモデル名を表示するスクリプト。

## 表示内容

```
claude-app | main | Opus 4.8 | 5h: 23% 残2h30m | 7d: 10% | ctx: 45%
```

| 項目 | 説明 |
|------|------|
| プロジェクト名 | カレントディレクトリのbasename |
| ブランチ | 現在のgitブランチ |
| モデル名 | 現在選択中のモデル（`/model` での切替時に自動反映） |
| 5h / 7d | レート制限の使用率と残時間 |
| ctx | コンテキストウィンドウの使用率 |

## 更新タイミング

Claude Code のステータスラインはイベント駆動（メッセージ送受信など）でしか再描画されないため、
そのままではレート制限に当たって操作が止まっている間、`残Xh` の表示が固まったままになる。

これを避けるため、`settings.json` に `refreshInterval` を設定して定期的に再実行させている。

```json
"statusLine": {
  "type": "command",
  "command": "bash /path/to/statusline.sh",
  "refreshInterval": 60
}
```

`refreshInterval` は秒単位（最小1）で、イベント駆動の更新に加えて N 秒ごとにコマンドを再実行する。
`install.sh` が 60 秒でセットする。

## インストール

```sh
bash ~/wsl-workspace/claude-app/statusline/install.sh
```

既存の `statusLine` 設定がある場合は上書き確認あり。インストール後はClaude Codeを再起動して適用。

## 要件

- `jq`
