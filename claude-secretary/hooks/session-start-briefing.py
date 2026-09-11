#!/usr/bin/env python3
"""SessionStart フック: 台帳のブリーフィングを文脈に注入する。

標準出力は利用者の画面ではなくモデルの文脈に入る（ADR 0008）。だからここでは
整形しない。人が読む形にするのはモデルの仕事で、そのための指示を材料に添える。

起動を妨げないため、何が起きても終了コードは 0 で終わる。
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_secretary import briefing, ledger  # noqa: E402
from claude_secretary.ledger import LedgerCorruptError  # noqa: E402

STALE_DAYS = 1

INSTRUCTION = """\
秘書のブリーフィング。最初の応答の冒頭で、下の材料を人が読む形にして提示すること。
利用者の指示が別のことでも、まず一度だけ短く出してから本題に入る。

- 自分が担当（self）のものを先に出す。担当が全員宛（group）と担当不明（unknown）が続く
- 他人が担当（others）は既定で畳み、件数だけ添える
- 詳細（detail）は長ければ要約してよい。台帳の文言をそのまま出す必要はない
- 完了にしたいものがあれば `claude-secretary set <id> --status done` を案内する
"""

COLLECT_HINT = """\
台帳が {days} 日更新されていない。会議からの収集を促すこと
（`secretary-collect-meetings` スキル）。利用者が断ったら二度は勧めない。
"""

NO_LEDGER = """\
秘書の台帳がまだ無い。会議からの収集を一度も実行していないことを伝え、
`secretary-collect-meetings` スキルでの収集を促すこと。
"""

BROKEN_LEDGER = """\
秘書の台帳が壊れていて読めない（{path}）。そのことを利用者に伝えること。
黙って進めない。
"""


def emit(text):
    sys.stdout.write(text.rstrip() + "\n")


def run():
    state_file = briefing.state_path()
    now = datetime.now().astimezone()

    # 提示の権利は排他で取る。セッションが同時に再開されても1つだけが出す
    show, kind = briefing.claim(state_file, now)
    if not show:
        return

    ledger_file = ledger.ledger_path()
    if not Path(ledger_file).exists():
        emit(NO_LEDGER)
        return

    try:
        records = ledger.load(ledger_file)
    except LedgerCorruptError:
        emit(BROKEN_LEDGER.format(path=ledger_file))
        return

    span = 7 if kind == briefing.KIND_WEEKLY else 1
    material = briefing.build(records, now=now, since=now - timedelta(days=span))

    parts = [INSTRUCTION]
    stale = material["ledger"]["stale_days"]
    if stale is not None and stale >= STALE_DAYS:
        parts.append(COLLECT_HINT.format(days=stale))
    parts.append(f"種別: {kind}")
    parts.append(json.dumps(material, ensure_ascii=False, indent=2))

    emit("\n".join(parts))


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass
    try:
        run()
    except Exception:
        # 起動を妨げない。フックの失敗でセッションが始まらないほうが困る
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
