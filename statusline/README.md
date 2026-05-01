# statusline

Claude Codeのステータスラインにプロジェクト名・gitブランチを表示するスクリプト。

## 表示内容

```
claude-app | main | 5h: 23% 残2h30m | 7d: 10% | ctx: 45%
```

| 項目 | 説明 |
|------|------|
| プロジェクト名 | カレントディレクトリのbasename |
| ブランチ | 現在のgitブランチ |
| 5h / 7d | レート制限の使用率と残時間 |
| ctx | コンテキストウィンドウの使用率 |

## インストール

```sh
bash ~/wsl-workspace/claude-app/statusline/install.sh
```

既存の `statusLine` 設定がある場合は上書き確認あり。インストール後はClaude Codeを再起動して適用。

## 要件

- `jq`
