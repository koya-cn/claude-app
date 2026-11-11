# 0001. claude-resume から Herdr.app で会話を開く方式

- Status: accepted
- Date: 2026-11-11
- Decider: 開発者（本人）

## Context

claude-resume の Web 版には「Herdrで開く」ボタンがあり、旧 herdr 0.7 系（TUI 版）の Socket API（`~/.config/herdr/herdr.sock`）を直接叩いて `claude --resume <id>` を復元している。

デスクトップ版の Herdr.app（herdr-core / `hc`）を導入したが、Herdr.app は旧ソケットを持たないため既存ボタンでは開けない。Herdr.app の画面（Space / タブ / ペイン）は Windows 側の `herdr-app.exe` CLI で操作でき、`do tab.new` / `do space.new` は起動するプログラムを `argv` で直接受け取れる（2026-11-11 実測）。

一方、Herdr.app の Space が持つ `folder` は作成後に実際の作業フォルダとずれていることがあり（例: `claude-app` という名前の Space の folder が別プロジェクト）、フォルダでの照合は信用できない。

## Decision

1. 旧「Herdrで開く」（Socket API 版）は残し、別ボタン「Herdr.appで開く」を新設して併存させる
2. Herdr.app の操作は `herdr-app.exe` CLI（`ls` / `status` / `do tab.new` / `do space.new` / `do space.select` / `do tab.select`）経由で行う
3. 会話の復元は `argv: ["claude", "--resume", <session_id>]` で直接起動する。旧版の「シェルへコマンド行を送る（send_text + cd）」方式は使わない
4. 開く場所は、プロジェクト名（パスの basename）と同名の WSL Space があればそこに新タブ、無ければ WSL Space を新規作成する。照合は Space 名のみで行い、folder は使わない
5. Herdr.app が起動していなければ `herdr-app.exe` を起動し、応答するまで待ってから開く

却下した案:
- 旧版を置き換える / 1 ボタンで自動判別 — 旧 herdr をまだ併用する可能性があり、どちらで開いたかが利用者に見えなくなる
- 常に新 Space / 表示中の Space にタブ — Space が増えすぎる、あるいはプロジェクトと無関係な Space に混ざる
- 未起動時はエラーのみ — ボタン1つで完結させたい
- `hc run --label herdr.space=…` で起動 — ラベル無しだと画面に出ない等の癖があり、画面配置は `herdr-app` に任せる方が素直

## Consequences

- シェルを介さず argv で起動するため、プロジェクトパスのクォートやシェル注入を気にしなくてよい
- Windows 側の `herdr-app.exe` の場所（`%LOCALAPPDATA%\Programs\herdr-app\`）に依存する。WSL の PATH には無いため探索処理が要る
- 同名 Space が複数あると先頭の WSL Space を使う。名前を変えた Space には当たらない
- 旧 herdr を使わなくなった時点で、旧「Herdrで開く」の削除を別途判断する

## Changelog

- 2026-11-11: 初版（accepted）
