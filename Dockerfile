# syntax=docker/dockerfile:1
FROM python:3.12-slim

# セキュリティ・安定性のためのPython環境変数
# - バイトコード（.pyc）のホスト混入や権限不整合を防止
# - ログ出力をバッファリングせず即時出力
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/home/sandbox/.local/bin:$PATH"

# 1. 非rootユーザーを作成 (UID/GID 1000)
RUN groupadd -g 1000 sandbox && \
    useradd -u 1000 -g sandbox -m -s /bin/bash sandbox

WORKDIR /app

# 2. 依存関係のインストール（ビルドキャッシュを活用）
# ※ root所有のままコピー・インストールすることで、非rootユーザー(sandbox)からの改ざんを防止
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# 3. 非特権ユーザーに切り替え
USER sandbox:sandbox

# 4. 常駐待機コマンド
CMD ["sleep", "infinity"]
