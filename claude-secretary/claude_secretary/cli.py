"""コマンドラインインターフェース。

終了コード: 0 成功 / 2 検証エラー / 3 保存失敗
"""

import argparse
import json
import sys
from datetime import datetime

from . import config, ledger
from .events import collect_attachments
from .ledger import LedgerCorruptError, RecordNotFoundError
from .notes import parse_next_steps
from .records import ValidationError

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_SAVE_FAILED = 3


def _now():
    """タイムゾーン付きの現在時刻（AC-2 / AC-25 と同じ形）。"""
    return datetime.now().astimezone().isoformat()


def _build_parser():
    parser = argparse.ArgumentParser(prog="claude-secretary")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("upsert", help="標準入力のレコード配列を台帳に投入する")
    subcommands.add_parser("list", help="台帳の全レコードをJSONで出す")

    setter = subcommands.add_parser("set", help="人が変更する列を書き換える")
    setter.add_argument("record_id")
    setter.add_argument("--status")
    setter.add_argument("--note")

    selector = subcommands.add_parser(
        "select-events", help="標準入力の予定を期間で絞り、抽出対象と未抽出に分ける"
    )
    selector.add_argument("--start", required=True)
    selector.add_argument("--end", required=True)

    noteparser = subcommands.add_parser(
        "parse-note", help="標準入力の会議メモから投入用のレコード配列を作る"
    )
    noteparser.add_argument("--source-ref", required=True)

    return parser


def _upsert(path, stdin):
    try:
        payload = json.loads(stdin.read())
    except json.JSONDecodeError as exc:
        raise ValidationError(f"標準入力がJSONとして読めない: {exc}") from exc
    return ledger.upsert(path, payload, now=_now())


def _read_json(stdin):
    try:
        return json.loads(stdin.read())
    except json.JSONDecodeError as exc:
        raise ValidationError(f"標準入力がJSONとして読めない: {exc}") from exc


def _select_events(args, stdin):
    try:
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
    except ValueError as exc:
        raise ValidationError(f"期間の指定が読めない: {exc}") from exc
    try:
        return collect_attachments(_read_json(stdin), start, end)
    except ValueError as exc:
        raise ValidationError(f"予定として読めない: {exc}") from exc


def _parse_note(args, stdin):
    """会議メモの本文から投入用のレコード配列を作る。

    本文は標準入力からのみ受け取り、出力にも残さない（本文をメインの文脈に
    載せないため）。
    """
    items = parse_next_steps(stdin.read(), self_email=config.self_email())
    return [
        {
            "title": item["title"],
            "source": {"type": "meeting", "ref": args.source_ref},
            "detail": item["detail"],
            "assignee": item["assignee"],
        }
        for item in items
    ]


def main(argv=None, stdin=None, stdout=None, stderr=None):
    """upsert / list / set を振り分ける。"""
    argv = sys.argv[1:] if argv is None else argv
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    try:
        args = _build_parser().parse_args(argv)
    except SystemExit:
        # argparse の使い方の誤りも検証エラーとして扱う
        return EXIT_VALIDATION

    path = ledger.ledger_path()
    try:
        if args.command == "upsert":
            summary = _upsert(path, stdin)
            print(json.dumps(summary, ensure_ascii=False), file=stdout)
        elif args.command == "list":
            records = ledger.load(path)
            print(json.dumps(records, ensure_ascii=False, indent=2), file=stdout)
        elif args.command == "set":
            ledger.set_fields(
                path,
                args.record_id,
                status=args.status,
                note=args.note,
                now=_now(),
            )
        elif args.command == "select-events":
            result = _select_events(args, stdin)
            print(json.dumps(result, ensure_ascii=False, indent=2), file=stdout)
        elif args.command == "parse-note":
            records = _parse_note(args, stdin)
            print(json.dumps(records, ensure_ascii=False, indent=2), file=stdout)
    except (ValidationError, RecordNotFoundError, LedgerCorruptError) as exc:
        print(exc, file=stderr)
        return EXIT_VALIDATION
    except OSError as exc:
        print(exc, file=stderr)
        return EXIT_SAVE_FAILED

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
