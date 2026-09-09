"""秘書の設定。

組織固有の値（自分のメールアドレスなど）はリポジトリに置かず、ホーム配下から読む。
"""

import json
import os
from pathlib import Path

CONFIG_ENV = "CLAUDE_SECRETARY_CONFIG"
DEFAULT_CONFIG = "~/.claude-secretary/config.json"

SELF_EMAIL_KEY = "self_email"


def config_path():
    """設定ファイルの場所を返す。"""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    return Path(DEFAULT_CONFIG).expanduser()


def _read():
    """設定を読む。読めなければ空として扱い、収集を止めない。"""
    path = config_path()
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def self_email():
    """自分のメールアドレスを返す。未設定なら None。"""
    return _read().get(SELF_EMAIL_KEY) or None
