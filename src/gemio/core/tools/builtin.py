"""
Gemio 標準組み込みツール定義モジュール。
"""

import datetime

from gemio.core.tools.registry import Toolset

BUILTIN_TOOLSET_NAME = "datetime"


def get_current_date() -> str:
    """
    現在の日付を "YYYY-MM-DD" 形式の文字列で取得します。
    Args:
        なし
    Returns:
        "YYYY-MM-DD" 形式の現在の日付文字列。
    """
    return datetime.date.today().isoformat()


def get_current_time() -> str:
    """
    現在の時間を "HH:MM:SS" 形式の文字列で取得します。
    Args:
        なし
    Returns:
        "HH:MM:SS" 形式の現在の時間文字列。
    """
    return datetime.datetime.now().strftime("%H:%M:%S")


agent_tools = [
    get_current_date,
    get_current_time,
]


def get_builtin_toolset() -> Toolset:
    """標準の日時取得ツールセット（常時有効）を生成して返します。"""
    return Toolset.from_callables(
        name=BUILTIN_TOOLSET_NAME,
        description="現在の日付および時刻を取得する標準ユーティリティツールセット",
        funcs=agent_tools,
        always_active=True,
    )
