"""statusline.sh の使用率色分けのテスト。

仕様: docs/work/statusline-usage-colors/spec.md
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "statusline.sh"

ESC = "\033"
RESET = f"{ESC}[0m"
# 数値の使用率はどの段階でも太字（SGR 1）にする
GREEN = f"{ESC}[1;32m"
YELLOW = f"{ESC}[1;33m"
ORANGE = f"{ESC}[1;38;5;208m"
RED = f"{ESC}[1;31m"
GRAY = f"{ESC}[90m"

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# 残り時間の表示が実行時間のぶれで変わらないよう、境界から余裕を持たせる
FIVE_HOUR_OFFSET = 2 * 3600 + 30  # 残2h0m
SEVEN_DAY_OFFSET = 2 * 86400 + 600  # 残2d0h


def build_input(five=None, five_reset=None, week=None, week_reset=None,
                ctx=None, rate_limits=True):
    data = {
        "cwd": "/work/proj",
        "git": {"branch": "main"},
        "model": {"display_name": "Opus"},
    }
    if rate_limits:
        limits = {}
        five_hour = {}
        if five is not None:
            five_hour["used_percentage"] = five
        if five_reset is not None:
            five_hour["resets_at"] = five_reset
        if five_hour:
            limits["five_hour"] = five_hour
        seven_day = {}
        if week is not None:
            seven_day["used_percentage"] = week
        if week_reset is not None:
            seven_day["resets_at"] = week_reset
        if seven_day:
            limits["seven_day"] = seven_day
        data["rate_limits"] = limits
    if ctx is not None:
        data["context_window"] = {"used_percentage": ctx}
    return data


@pytest.fixture
def run(tmp_path):
    """JSON を標準入力に渡して statusline.sh の出力（末尾改行なし）を返す。"""

    def _run(data, vertex=False):
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDE_CODE_USE_VERTEX"}
        # 共有ファイルの値が混ざらないよう、テストごとに空のキャッシュを使う
        env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
        if vertex:
            env["CLAUDE_CODE_USE_VERTEX"] = "1"
        result = subprocess.run(
            ["sh", str(SCRIPT)],
            input=json.dumps(data),
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return result.stdout.rstrip("\n")

    return _run


def future(offset):
    return int(time.time()) + offset


# --- 正常系 -------------------------------------------------------------

def test_5h_low_usage_colors_only_the_percentage_green(run):
    out = run(build_input(five=12, five_reset=future(FIVE_HOUR_OFFSET)))
    assert f"5h: {GREEN}12%{RESET} 残2h0m" in out


def test_7d_colors_yellow(run):
    out = run(build_input(week=35, week_reset=future(SEVEN_DAY_OFFSET)))
    assert f"7d: {YELLOW}35%{RESET} 残2d0h" in out


def test_ctx_colors_orange(run):
    out = run(build_input(ctx=62, rate_limits=False))
    assert out.endswith(f"ctx: {ORANGE}62%{RESET}")


# --- 境界値 -------------------------------------------------------------

@pytest.mark.parametrize("pct, shown, color", [
    (0, "0", GREEN),
    (29.4, "29", GREEN),
    (29.6, "30", YELLOW),  # 四捨五入後の表示値で判定する
    (30, "30", YELLOW),
    (49, "49", YELLOW),
    (50, "50", ORANGE),
    (79, "79", ORANGE),
    (80, "80", RED),
    (100, "100", RED),
    (120, "120", RED),
])
def test_5h_tier_boundaries(run, pct, shown, color):
    out = run(build_input(five=pct, five_reset=future(FIVE_HOUR_OFFSET)))
    assert f"5h: {color}{shown}%{RESET} " in out


@pytest.mark.parametrize("pct, color", [
    (29, GREEN),
    (30, YELLOW),
    (50, ORANGE),
    (80, RED),
])
def test_7d_and_ctx_share_the_same_tiers(run, pct, color):
    out = run(build_input(week=pct, week_reset=future(SEVEN_DAY_OFFSET),
                          ctx=pct))
    assert f"7d: {color}{pct}%{RESET} " in out
    assert out.endswith(f"ctx: {color}{pct}%{RESET}")


# --- 表示が欠けるケース -------------------------------------------------

def test_unknown_percentage_is_gray(run):
    out = run(build_input(five_reset=future(FIVE_HOUR_OFFSET)))
    assert f"5h: {GRAY}--%{RESET} 残2h0m" in out


def test_expired_window_is_gray(run):
    out = run(build_input(five=90, five_reset=future(-60)))
    assert f"5h: {GRAY}--% 期限切れ{RESET}" in out


def test_no_rate_limits_omits_5h_and_7d(run):
    out = run(build_input(ctx=10, rate_limits=False))
    assert "5h:" not in out
    assert "7d:" not in out


# --- 既存表示の回帰 -----------------------------------------------------

def test_plain_text_is_unchanged_when_ansi_is_stripped(run):
    out = run(build_input(five=12, five_reset=future(FIVE_HOUR_OFFSET),
                          week=40, week_reset=future(SEVEN_DAY_OFFSET),
                          ctx=45))
    assert ANSI_RE.sub("", out) == (
        "proj | main | Opus | 5h: 12% 残2h0m | 7d: 40% 残2d0h | ctx: 45%"
    )


def test_vertex_badge_is_unchanged(run):
    out = run(build_input(ctx=10, rate_limits=False), vertex=True)
    assert f"| {ESC}[1;33mVertex{RESET} |" in out
