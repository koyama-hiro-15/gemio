"""
Gemio コアパッケージ共通例外定義モジュール。
外部SDK（google-genai 等）やUIライブラリに依存しない軽量な例外クラスを定義します。
"""


class AgentExecutionAborted(Exception):
    """
    ユーザーの指示（Abort）または承認拒否無視の検知（サーキットブレーカー）等により、
    エージェントの現在の作業ループを強制中止するための例外。
    """

    pass
