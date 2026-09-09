"""非公開情報の混入を検出する（AC-21、ADR 0001）。

リポジトリには**構造のパターンだけ**を置く。組織固有の文字列（ドメイン、人名、
プロジェクト名）をここに書くと、混入を防ぐための仕組み自体が混入源になる。
具体的な文字列は ~/.claude-secretary/ 配下の設定から読み、無ければ構造パターンだけで走る。
"""

import json
import re
import subprocess
from pathlib import Path

EXTRA_PATTERNS_FILE = "~/.claude-secretary/forbidden-patterns.json"

# 架空データにのみ許すメールのドメイン（AC-26）
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")

# 台帳と設定の実体。リポジトリに存在してはいけないファイル名
PRIVATE_FILE_NAMES = ("ledger.json", "config.json", "forbidden-patterns.json")

FINDING_PRIVATE_FILE = "private_file"
FINDING_HOME_PATH = "home_path"
FINDING_EXTERNAL_EMAIL = "external_email"
FINDING_SERVICE_IDENTIFIER = "service_identifier"
FINDING_FORBIDDEN_PATTERN = "forbidden_pattern"

# パターン定義とその試験は、検出対象の形をした文字列を必然的に含むため走査から除く。
# 除外はこの2ファイルに限る。増やすと検出網に穴が開く。
SELF_EXCLUDED_PATHS = (
    "claude-secretary/claude_secretary/secrets_scan.py",
    "claude-secretary/tests/test_no_secrets.py",
)


# サブリソース完全性のハッシュは長い識別子と同じ形をしている。行単位で除く
_INTEGRITY_HINT = "integrity"

# 30文字以上で、大文字・小文字・数字がすべて混じる連続。外部サービスのIDの形
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{30,}")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")


def repo_files(root):
    """走査対象のファイルを返す。.git と gitignore 済みのパスを除く。"""
    root = Path(root)
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ValueError(
            f"git の管理下として読めない: {root}。走査は git リポジトリで行う"
        ) from exc

    files = []
    for relative in listed.stdout.splitlines():
        if not relative or relative in SELF_EXCLUDED_PATHS:
            continue
        path = root / relative
        if path.is_file():
            files.append(path)
    return files


def _looks_like_identifier(token):
    return (
        any(char.islower() for char in token)
        and any(char.isupper() for char in token)
        and any(char.isdigit() for char in token)
    )


def _extra_patterns():
    """組織固有の文字列はリポジトリに置かず、ホーム配下の設定から読む（ADR 0001）。"""
    path = Path(EXTRA_PATTERNS_FILE).expanduser()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("patterns", [])
    except (json.JSONDecodeError, OSError):
        return []


def scan_text(text, path=None):
    """1ファイル分を走査し、検出した種別の一覧を返す。

    path を渡した場合はファイル名も見る。台帳や設定の実体はファイル名で判る。
    """
    findings = set()

    if path is not None and Path(path).name in PRIVATE_FILE_NAMES:
        findings.add(FINDING_PRIVATE_FILE)

    if str(Path.home()) in text:
        findings.add(FINDING_HOME_PATH)

    for matched in _EMAIL.finditer(text):
        domain = matched.group(0).split("@")[1].lower()
        if domain not in ALLOWED_EMAIL_DOMAINS:
            findings.add(FINDING_EXTERNAL_EMAIL)
            break

    for line in text.splitlines():
        if _INTEGRITY_HINT in line:
            continue
        if any(_looks_like_identifier(t) for t in _IDENTIFIER.findall(line)):
            findings.add(FINDING_SERVICE_IDENTIFIER)
            break

    for pattern in _extra_patterns():
        if pattern and pattern in text:
            findings.add(FINDING_FORBIDDEN_PATTERN)
            break

    return sorted(findings)


def scan_repo(root):
    """リポジトリ全体を走査し、パスごとの検出結果を返す。"""
    root = Path(root)
    result = {}

    for path in repo_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            text = ""

        findings = scan_text(text, path=path)
        if findings:
            result[str(path.relative_to(root))] = findings

    return result
