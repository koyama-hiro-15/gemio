# 🐳 Gemio向け Docker サンドボックスコンテナ作成マニュアル

**作成日:** 2026年11月03日  
**最終更新日:** 2026年11月14日  
**ステータス:** 運用中  
**対象システム:** Gemio 開発者モード (`compose.yaml`, `Dockerfile`, `.dockerignore`, `manuals/04_SANDBOX_PROTECTION_GUIDELINE.md`, `src/gemio/project/developer.py`, `src/gemio/project/base.py`)  

### 【要約】
本マニュアルは、Gemioの「開発者モード（`--dev`）」および「トラストモード（`--trust`）」で安全・高速にコード検証やテスト（`run_sandbox_command`）を実行するためのDockerコンテナおよびDev Container環境を構築するガイドです。Gemioのセーフティゲート（安全基準）を満たす堅牢化設定、`04_SANDBOX_PROTECTION_GUIDELINE.md` に基づく4分類のファイル・フォルダ保護（設定・Git領域の `:ro` 化、仮想環境 `.venv` の隔離、機密ファイルのマスク）、両モード共通でのコンテナ内APIキー・機密環境変数診断（強い警告通知）とDev Container内起動時のセキュア入力仕様、多言語（Node.js/TypeScript, Go, Rust等）への応用ポイント、およびトラブルシューティングを網羅しています。

---

## 目次
1. [はじめに：Gemio向けコンテナの必須セキュリティ要件](#1-はじめにgemio向けコンテナの必須セキュリティ要件)
   - [1.1 Gemioセーフティゲート（SandboxSecurityErrorの回避）](#11-gemioセーフティゲートsandboxsecurityerrorの回避)
   - [1.2 推奨する最高強度の隔離構成（`--network none`）](#12-推奨する最高強度の隔離構成--network-none)
   - [1.3 バインドマウント時の4分類保護基準（何をAIに編集させ、何を保護するか）](#13-バインドマウント時の4分類保護基準何をaiに編集させ何を保護するか)
2. [方法1. コマンド1発で素早く立ち上げる（即時検証用）](#2-方法1-コマンド1発で素早く立ち上げる即時検証用)
   - [2.1 Python標準サンドボックスコンテナの起動](#21-python標準サンドボックスコンテナの起動)
   - [2.2 さらに強固にするオプション（ルートFS読み取り専用化・設定ファイル保護・マスクマウント）](#22-さらに強固にするオプションルートfs読み取り専用化設定ファイル保護マスクマウント)
   - [2.3 Gemioからの接続とマウント状態の自動診断 (`/sandbox`)](#23-gemioからの接続とマウント状態の自動診断-sandbox)
3. [方法2. Dockerfileで専用サンドボックスを作成する（標準）](#3-方法2-dockerfileで専用サンドボックスを作成する標準)
   - [3.1 Python環境向け堅牢化Dockerfileと `.dockerignore`](#31-python環境向け堅牢化dockerfileと-dockerignore)
   - [3.2 イメージのビルドと起動](#32-イメージのビルドと起動)
4. [方法3. Docker Composeで管理する（日常開発向け・最推奨）](#4-方法3-docker-composeで管理する日常開発向け最推奨)
   - [4.1 `compose.yaml` の安全な定義例（本プロジェクト標準構成）](#41-composeyaml-の安全な定義例本プロジェクト標準構成)
   - [4.2 起動・停止・Gemioからの接続](#42-起動停止gemioからの接続)
   - [4.3 方法4. VS Code Dev Containers (`.devcontainer`) で管理する](#43-方法4-vs-code-dev-containers-devcontainer-で管理する)
5. [多言語環境への応用ポイント（Node.js / Go / Rust）](#5-多言語環境への応用ポイントnodejs--go--rust)
   - [5.1 Node.js / TypeScript サンドボックス](#51-nodejs--typescript-サンドボックス)
   - [5.2 Go 言語サンドボックス](#52-go-言語サンドボックス)
   - [5.3 Rust サンドボックス](#53-rust-サンドボックス)
   - [5.4 多言語開発におけるUID/GIDとパーミッション対策](#54-多言語開発におけるuidgidとパーミッション対策)
6. [Gemioでの実践ワークフローと安全機能の連携](#6-gemioでの実践ワークフローと安全機能の連携)
   - [6.1 ホストOS起動・コンテナ委譲（最推奨パターン）](#61-ホストos起動コンテナ委譲最推奨パターン)
   - [6.2 トラストモード（`--trust`）の安全な活用](#62-トラストモード--trustの安全な活用)
   - [6.3 Web検索（`/search`）とオフライン検証の連携](#63-web検索-searchとオフライン検証の連携)
7. [安全面のチェックリストとトラブルシューティング](#7-安全面のチェックリストとトラブルシューティング)

---

## 1. はじめに：Gemio向けコンテナの必須セキュリティ要件

Gemioの開発者モード（`DeveloperProject`）は、AIが生成したコードのテストやビルドコマンドを**Dockerコンテナ内で実行**します。万が一AIが悪意のあるコードやバグを含むコマンドを実行した場合でも、ホストOSや開発者の資産を守るため、厳格なセーフティゲートが内蔵されています。

### 1.1 Gemioセーフティゲート（`SandboxSecurityError`の回避）
Gemioは接続先のコンテナを起動時に検査し、**以下のいずれかに該当した場合は即座に起動をブロック（`SandboxSecurityError` を送出）**します。

| ❌ 絶対にやってはいけない設定 | 理由・危険性 |
| :--- | :--- |
| **特権モード（`--privileged`）** | コンテナがホストの全デバイスやカーネル機能にアクセス可能となり、コンテナ脱獄の原因となります。 |
| **Dockerソケット（`/var/run/docker.sock`）のマウント** | コンテナ内からホストのDockerデーモンを乗っ取り、任意のホストコマンドを実行できてしまいます。 |
| **ホストネットワーク共有（`--network host`）** | ホストOSのすべての開放ポートやネットワークインターフェースに直接アクセスできてしまいます。 |
| **ホストルート（`/` や `C:\`）のマウント** | ホストOSのシステム全体がコンテナ内に露出してしまいます。 |

*(※なお、コンテナ内の環境変数に各種APIキーやトークンが設定されている場合は、開発対象アプリのテスト等を妨げないよう起動はブロックしませんが、ホストOS起動・コンテナ内起動の両方でキー名を自動検出し、診断パネル上で **`🔴 危険（danger）` の強い警告**として通知します)*

### 1.2 推奨する最高強度の隔離構成（`--network none`）
GemioはホストOS側でGemini APIと通信し、コマンド実行のみをプロセス間通信（`docker exec`）経由でコンテナへ送信します。
そのため、**コンテナ自体のネットワークを完全に遮断（`--network none`）しても100%正常に動作します**。

* **外部C2サーバーへの通信遮断**: AIコードによる情報漏洩を物理的に防止。
* **SSRF攻撃・クラウドメタデータ（`169.254.169.254` 等）のアクセス遮断**: クラウド認証情報の窃取を防止。
* **ローカルループバック（`localhost`）は有効**: コンテナ内完結のテストやローカル通信はそのまま実行可能。
* **※やむを得ずブリッジネットワークを使用する場合の注意**:
  `--network none` を外して通常のブリッジ接続にする場合は、コンテナからホストOS上のローカルサービス（開発用DBや管理ポート等）へ侵入されるのを防ぐため、必ず **`--add-host=host.docker.internal:127.0.0.1`**（Composeでは `extra_hosts: ["host.docker.internal:127.0.0.1"]`）を指定してホストへのルーティングを無効化してください。

### 1.3 バインドマウント時の4分類保護基準（何をAIに編集させ、何を保護するか）
コンテナへプロジェクトディレクトリをバインドマウント（`-v "$(pwd):/app"`）して `run_sandbox_command`（コンテナ内シェル）を使用する場合、**[`04_SANDBOX_PROTECTION_GUIDELINE.md`](./04_SANDBOX_PROTECTION_GUIDELINE.md)（AIエージェント編集範囲・サンドボックス保護ガイドライン）**に基づき、ファイルを以下の4分類に分けて保護し、環境変数に本番シークレットを渡さないことが鉄則です。

1. **① AIが作業するベース領域（`rw`：読み書き可能）**:
   * `src/`、`tests/`、`docs/`、`*.md` などのコードやドキュメント類。万一壊れても `git reset` 等で即座に復元できる成果物領域です。
2. **② 人間が管理する設定・管理領域（`:ro`：読み取り専用でオーバーレイ保護）**:
   * **Git管理領域**: `.git/`、`.gitignore`（Gitフックや履歴の改ざん防止）
   * **Docker・サンドボックス・Gemio拡張設定**: `Dockerfile`、`compose.yaml`、`.dockerignore`、`.devcontainer/`、`.gemio/`（安全の壁やカスタムツールの改ざん防止）
   * **パッケージ・ビルド定義**: `requirements.txt`、`pyproject.toml`（無断でのパッケージ変更や設定改ざん防止）
   * ※これらを `:ro` にしておいても、AIは中身を参照でき、また人間が承認プロンプトで `y` を押せばホスト側のGemioツール（`write_file` 等）経由で編集を手伝わせることも可能です。
3. **③ ホスト側と混ぜない仮想環境領域（匿名ボリュームで隔離）**:
   * `.venv/`（Node.jsなら `node_modules/`）。`-v /app/.venv`（Composeでは `- /app/.venv`）のように匿名ボリュームを被せることで、ホストOS（Windows/Mac等）の仮想環境とコンテナ（Linux）の環境が混ざって壊れるのを防ぎます。
4. **④ AIに見せない機密・個人領域（`/dev/null` マスク または配置しない）**:
   * **環境変数・秘密鍵**: コンテナ起動時に `-e` や `env_file` で本番シークレットを渡さないでください。また、ホスト側に `.env` が存在する場合は **`-v /dev/null:/app/.env:ro`** を指定し、コンテナ内から `.env` の中身を空（`/dev/null`）にマスクしてください。

---

## 2. 方法1. コマンド1発で素早く立ち上げる（即時検証用）

### 2.1 Python標準サンドボックスコンテナの起動
手元にあるPythonコードを検証するため、Docker公式イメージを使ってバックグラウンドで安全なコンテナを常駐させます。

```bash
# ネットワーク遮断・非root・ケーパビリティ剥奪・リソース制限を適用したコンテナの常駐起動
docker run -d \
  --name gemio-sandbox \
  --network none \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  -u 1000:1000 \
  -e HOME=/tmp \
  --memory=2g \
  --cpus=2.0 \
  --pids-limit=128 \
  -v "$(pwd):/app" \
  -w /app \
  python:3.12-slim \
  sleep infinity
```

#### 各オプションの意味と安全効果
* **`-d`**: バックグラウンド（デタッチ）実行。
* **`--name gemio-sandbox`**: Gemioから指定するためのコンテナ名。
* **`--network none`**: 外部通信を物理遮断。
* **`--cap-drop=ALL`**: すべてのLinuxカーネルケーパビリティ（root権限の一部機能）を剥奪。
* **`--security-opt=no-new-privileges:true`**: `setuid` 等による権限昇格を禁止。
* **`-u 1000:1000`**: **非特権ユーザー（非root）で実行**。コンテナ内での不正操作抑止に加え、Linux/WSL2環境でAIが生成したファイルがホスト側でroot所有になって編集不能になるパーミッション問題を防止。
* **`-e HOME=/tmp`**: 公式スリムイメージに `UID 1000` のホームディレクトリが存在しない場合でも、各種ツール（`pytest` や `pip` 等）がキャッシュ作成で権限エラーにならないよう書き込み可能な `/tmp` を指定。
* **`--memory=2g` / `--cpus=2.0` / `--pids-limit=128`**: DoS攻撃やFork爆弾によるホストフリーズを防止。
* **`-v "$(pwd):/app"`**: カレントプロジェクトをコンテナの `/app` に同期。
* **`-w /app`**: 作業ディレクトリを `/app` に設定。
* **`sleep infinity`**: コンテナを常駐させ、いつでも `docker exec` でコマンドを受け付けられる状態を維持。

### 2.2 さらに強固にするオプション（ルートFS読み取り専用化・設定ファイル保護・マスクマウント）
未知の外部リポジトリを検証する場合や、プロジェクト内の設定ファイル・`.git`・`.venv`・`.env` をOSレベルで厳格に保護する場合は、以下のオプションを追加します。

```bash
docker run -d \
  --name gemio-sandbox \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  -u 1000:1000 \
  -e HOME=/tmp \
  -e PYTHONDONTWRITEBYTECODE=1 \
  --memory=2g \
  --cpus=2.0 \
  --pids-limit=128 \
  -v "$(pwd):/app" \
  -v "$(pwd)/.git:/app/.git:ro" \
  -v "$(pwd)/.gitignore:/app/.gitignore:ro" \
  -v "$(pwd)/Dockerfile:/app/Dockerfile:ro" \
  -v "$(pwd)/compose.yaml:/app/compose.yaml:ro" \
  -v "$(pwd)/.dockerignore:/app/.dockerignore:ro" \
  -v "$(pwd)/requirements.txt:/app/requirements.txt:ro" \
  -v "$(pwd)/pyproject.toml:/app/pyproject.toml:ro" \
  -v /app/.venv \
  -v /dev/null:/app/.env:ro \
  -w /app \
  python:3.12-slim \
  sleep infinity
```

* **`--read-only`**: コンテナのシステム領域（`/usr`, `/bin`, `/etc` 等）をすべて読み取り専用にし、マルウェアの埋め込みやシステム改ざんを防止します。
* **`--tmpfs /tmp:rw,noexec,nosuid,size=64m`**: 一時ファイル領域（`/tmp`）をメモリ上に64MB上限で作成し、`noexec` によって `/tmp` に配置された不正バイナリの実行を禁止します。
* **`-v "$(pwd)/.git:/app/.git:ro"` 等の `:ro` マウント**: コンテナ内のシェルから `.git/` やDocker設定・ビルド定義ファイルが書き換えられるのをOSレベルで拒否します。
* **`-v /app/.venv`**: ホスト側の `.venv` をコンテナ専用の匿名ボリュームで隠し、環境汚染やバイナリ競合を防ぎます。
* **`-v /dev/null:/app/.env:ro`**: ホスト側に `.env` が存在しても、コンテナ内からは空ファイルとして見せかけ、シェル経由での機密情報読み取りを遮断します。

### 2.3 Gemioからの接続とマウント状態の自動診断 (`/sandbox`)
ホストOSのターミナルで以下を実行します：
```bash
# プロジェクトパスとバインドマウント元が一致していれば --container 省略でも自動検出されます
python -m gemio . --dev --container gemio-sandbox
```
Gemioがコンテナを自動検証し、起動時に **「🛡️ サンドボックス環境 & マウント状態診断」** パネルが表示されます。
* パネル内のテーブル（`ls -la` 相当の権限・所有者・サイズ一覧）で、`.env` が `crw-rw-rw-`（`/dev/null` マスク済 🟢）になっているか、`.git/` や設定ファイルが `ro`（読取専用 🟢 / ⚪）になっているか、`.venv/` が隔離（🟢）されているか、AIが編集可能な作業対象（`🔵 作業対象 (rw)`・シアン色分け表示）がどこか、未保護の機密ファイル（🔴 警告）がないかをひと目で確認できます。
* 対話中もいつでも **`/sandbox`**（または **`/status`**）コマンドで再検査が可能です。

---

## 3. 方法2. Dockerfileで専用サンドボックスを作成する（標準）

プロジェクト固有のテストツール（`pytest` 等）や依存ライブラリをあらかじめインストールした専用イメージを作成する標準的な手法です。

### 3.1 Python環境向け堅牢化Dockerfileと `.dockerignore`
プロジェクト直下に以下のファイルを用意します（本プロジェクトのルートに配置されているものと同一仕様です）。

#### `Dockerfile`
```dockerfile
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
```

#### `.dockerignore`（機密情報のイメージ混入防止）
`docker build` 時に万が一 `COPY . .` 等を実行した際、秘密鍵やクラウド認証情報、個人用メモがDockerイメージのレイヤ内に焼き込まれるのを防ぐため、Gemioの保護対象（`src/gemio/project/base.py` の `SECRET_FILE_PATTERNS` および `SECRET_DIR_NAMES`）に完全準拠した `.dockerignore` を配置します。
```gitignore
# Docker・プロジェクト設定
Dockerfile
.dockerignore
compose.yaml
.gitignore
.devcontainer

# Git・仮想環境・ビルドキャッシュ・個人作業領域
.git
.venv
venv
env
ENV
.pytest_cache
__pycache__
*.py[cod]
*.so
.mypy_cache
.ruff_cache
.tox
node_modules
target
vendor
*.egg-info
*.log
.DS_Store
Thumbs.db
.vscode
.idea
memo

# 機密ディレクトリ（SSH鍵・クラウドCLI認証・キーリング等 / SECRET_DIR_NAMES 準拠）
.ssh
.aws
.gcloud
.azure
.kube
.docker
.gnupg

# 環境変数・機密ファイル（イメージへの混入を厳禁とするファイル群 / SECRET_FILE_PATTERNS 準拠）
.env
.env.*
!.env.example
!.env.sample
!.env.template
!.env.dist
.envrc
.secrets
secrets.yaml
secrets.yml
secrets.json
*.pem
*.key
*.p12
*.pfx
*.ppk
*.keystore
*.jks
*.kdbx
id_rsa*
id_ed25519*
id_ecdsa*
id_dsa*
.npmrc
.yarnrc.yml
.pypirc
.netrc
.git-credentials
auth.json
master.key
credentials.toml
*credentials*.json
*service-account*.json
client_secret*.json
*.tfstate*
*.tfvars
*.tfvars.json
kubeconfig
```

### 3.2 イメージのビルドと起動
```bash
# 1. 専用イメージのビルド
docker build -t my-gemio-env:latest .

# 2. 安全オプションを付与して起動
docker run -d \
  --name gemio-sandbox \
  --network none \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --memory=2g \
  --cpus=2.0 \
  --pids-limit=128 \
  -v "$(pwd):/app" \
  -v "$(pwd)/.git:/app/.git:ro" \
  -v /app/.venv \
  my-gemio-env:latest
```

---

## 4. 方法3. Docker Composeで管理する（日常開発向け・最推奨）

コマンドが長大になるのを防ぎ、プロジェクトメンバー全員が [`04_SANDBOX_PROTECTION_GUIDELINE.md`](./04_SANDBOX_PROTECTION_GUIDELINE.md) の4分類基準に沿った同じ安全設定でコンテナを起動できるように `compose.yaml` で定義します。

### 4.1 `compose.yaml` の安全な定義例（本プロジェクト標準構成）
```yaml
services:
  sandbox:
    container_name: gemio-sandbox
    build:
      context: .
      dockerfile: Dockerfile
    # 外部ネットワークの完全遮断
    # ※やむを得ずブリッジ接続にする場合は extra_hosts: ["host.docker.internal:127.0.0.1"] を設定すること
    network_mode: none
    # 【注意】environment や env_file で本番APIキー・シークレットを絶対に渡さないこと
    environment:
      - PYTHONDONTWRITEBYTECODE=1
      - PYTHONUNBUFFERED=1
    # リソース制限（ホストリソースの枯渇・Fork爆弾防止）
    # Compose v2 標準設定および互換プロパティの両方を指定
    mem_limit: 2g
    cpus: 2.0
    pids_limit: 128
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
          pids: 128
    # 非特権ユーザー実行（非root）
    user: "1000:1000"
    # 権限の最小化
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    # 一時領域の制限（バイナリ実行禁止・サイズ上限）
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=64m
    # ボリュームマウント（manuals/04_SANDBOX_PROTECTION_GUIDELINE.md の4分類基準に準拠）
    volumes:
      # ① AIが作業するベース領域（src/, tests/, docs/ 等は読み書き可能）
      - .:/app
      # ② 人間が管理する領域（コンテナ内では「読み取り専用 :ro」にして改ざん防止）
      - ./.git:/app/.git:ro
      - ./.gitignore:/app/.gitignore:ro
      - ./Dockerfile:/app/Dockerfile:ro
      - ./compose.yaml:/app/compose.yaml:ro
      - ./.dockerignore:/app/.dockerignore:ro
      - ./requirements.txt:/app/requirements.txt:ro
      - ./pyproject.toml:/app/pyproject.toml:ro
      # （プロジェクト直下に .gemio/ を作成した場合は以下も有効化して :ro 保護）
      # - ./.gemio:/app/.gemio:ro
      # ③ ホスト側と混ぜない仮想環境領域（匿名ボリュームで隔離）
      - /app/.venv
      # ④ AIに見せない機密領域（ホスト側に .env がある場合はコメントを外す）
      # - /dev/null:/app/.env:ro
    working_dir: /app
    # 常駐設定
    command: sleep infinity
    restart: unless-stopped
```

### 4.2 起動・停止・Gemioからの接続
```bash
# 起動（バックグラウンド / Docker Compose v2 推奨）
docker compose up -d

# Gemioを起動して接続（--container 省略でもマウント元から自動検出されます）
python -m gemio . --dev --container gemio-sandbox

# 作業終了時の停止・削除
docker compose down
```

---

### 4.3 方法4. VS Code Dev Containers (`.devcontainer`) で管理する
VS Codeの **Dev Containers** 拡張機能を使って、エディタ環境ごとコンテナ内で開発する場合の推奨構成です。

#### `.devcontainer/devcontainer.json` の安全な定義例
```jsonc
{
  "name": "Gemio Safe Dev Container",
  "build": {
    "dockerfile": "../Dockerfile",
    "context": ".."
  },
  // 非特権ユーザーで実行
  "remoteUser": "sandbox",

  // 【注意】remoteEnv や containerEnv に GEMINI_API_KEY 等の機密キーは設定しないことを推奨！
  // （設定されているとAIにシェル経由で読まれるリスクがあるため、診断パネルで 🔴 危険 警告が表示されます）
  "remoteEnv": {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1"
  },

  // セキュリティ・リソース制限オプション
  "runArgs": [
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges:true",
    "--add-host=host.docker.internal:127.0.0.1",
    "--memory=2g",
    "--cpus=2.0",
    "--pids-limit=128"
  ],

  // 重要領域の読み取り専用保護・仮想環境の隔離・.envのマスクマウント
  "mounts": [
    "source=${localWorkspaceFolder}/.git,target=${containerWorkspaceFolder}/.git,type=bind,readonly",
    "source=${localWorkspaceFolder}/.gitignore,target=${containerWorkspaceFolder}/.gitignore,type=bind,readonly",
    "source=${localWorkspaceFolder}/Dockerfile,target=${containerWorkspaceFolder}/Dockerfile,type=bind,readonly",
    "source=${localWorkspaceFolder}/compose.yaml,target=${containerWorkspaceFolder}/compose.yaml,type=bind,readonly",
    "source=${localWorkspaceFolder}/.dockerignore,target=${containerWorkspaceFolder}/.dockerignore,type=bind,readonly",
    "source=${localWorkspaceFolder}/requirements.txt,target=${containerWorkspaceFolder}/requirements.txt,type=bind,readonly",
    "source=${localWorkspaceFolder}/pyproject.toml,target=${containerWorkspaceFolder}/pyproject.toml,type=bind,readonly",
    "target=${containerWorkspaceFolder}/.venv,type=volume,readonly"
    // ホスト側に .env がある場合は以下を追加
    // ,"source=/dev/null,target=${containerWorkspaceFolder}/.env,type=bind,readonly"
  ],

  "customizations": {
    "vscode": {
      "extensions": ["ms-python.python", "charliermarsh.ruff"]
    }
  }
}
```

#### Dev Container起動後の2つの接続スタイル
1. **ホストOS側のターミナルから接続する（APIキー入力不要・推奨）**:
   VS CodeでDev Containerを起動した状態で、**ホストOS側のターミナル**から以下を実行します：
   ```bash
   python -m gemio . --dev
   ```
   Gemioが `devcontainer.local_folder` ラベルから稼働中のDev Containerを自動検出し、ホスト側の環境変数 `GEMINI_API_KEY` を使って即座に接続します。
2. **Dev Container内の統合ターミナルで直接起動する（セキュア入力プロンプト）**:
   VS Code内の統合ターミナル（コンテナ内部）で以下を実行します：
   ```bash
   python -m gemio --dev
   ```
   起動時に「🔐 コンテナ内起動：APIキーの安全入力」パネルとOS別の環境変数確認・コピーコマンドが表示されます。ホストOSで別ターミナルを開いてコマンド（例: PowerShellなら `Set-Clipboard $env:GEMINI_API_KEY`）を実行し、プロンプト（`****` マスク表示）に貼り付けて `[Enter]` を押すことで、コンテナの環境変数や `/proc` に一切痕跡を残さず安全に起動できます。

---

## 5. 多言語環境への応用ポイント（Node.js / Go / Rust）

Gemioの開発者モードはPython専用ではありません。`run_sandbox_command` は任意のシェルコマンドを実行できるため、コンテナ内に目的の言語環境を揃えることで、多言語プロジェクトの開発・テスト・デバッグが可能です。

### 5.1 Node.js / TypeScript サンドボックス
Node.jsプロジェクトでは、ホスト側とコンテナ側で `node_modules` のOS依存バイナリが競合するのを防ぐため、匿名ボリュームマウントを活用し、`package.json` 等を `:ro` 保護するのがポイントです。

#### `Dockerfile`（Node.js用）
```dockerfile
FROM node:20-slim

# 非rootの組み込みユーザー "node" (UID 1000) を使用
WORKDIR /app

COPY package*.json ./
RUN npm install

USER node
CMD ["sleep", "infinity"]
```

#### `compose.yaml`（Node.js用）
```yaml
services:
  sandbox-node:
    container_name: gemio-sandbox-node
    build: .
    network_mode: none
    user: "1000:1000"
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: 2g
    cpus: 2.0
    pids_limit: 200
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
          pids: 200
    volumes:
      - .:/app
      - ./.git:/app/.git:ro
      - ./package.json:/app/package.json:ro
      - ./package-lock.json:/app/package-lock.json:ro
      # コンテナ内の node_modules をホスト側から隔離保護
      - /app/node_modules
    working_dir: /app
    command: sleep infinity
```

### 5.2 Go 言語サンドボックス
Goはビルドキャッシュ領域（`GOCACHE`）とモジュール領域（`GOPATH`）が必要となります。

```dockerfile
FROM golang:1.23-alpine

# ビルドに必要な最小限のgit等を導入
RUN apk add --no-cache git bash

# 非rootユーザーの作成
RUN adduser -D -u 1000 sandbox

WORKDIR /app
USER sandbox

# キャッシュとモジュールのパスを設定
ENV GOPATH=/home/sandbox/go
ENV GOCACHE=/home/sandbox/.cache/go-build

CMD ["sleep", "infinity"]
```

### 5.3 Rust サンドボックス
Rust（Cargo）はビルド成果物（`target` ディレクトリ）が肥大化しやすいため、Gemioの自動走査除外（`IGNORED_DIRS`）と相性が抜群です。

```dockerfile
FROM rust:1.82-slim

RUN useradd -u 1000 -m -s /bin/bash sandbox

WORKDIR /app
USER sandbox

# Cargoクレートキャッシュのパスを設定
ENV CARGO_HOME=/home/sandbox/.cargo

CMD ["sleep", "infinity"]
```

### 5.4 多言語開発におけるUID/GIDとパーミッション対策
コンテナ内で作成されたファイルがホスト側で `root` 所有になってしまい編集できなくなるトラブル（Permission Denied）を防ぐための鉄則です：

1. **Linux / WSL2 環境の場合**:
   ホスト側のUID/GID（通常 `1000:1000`）とコンテナ内のユーザーUID/GIDを一致させます。
   `docker run` 時に `-u $(id -u):$(id -g)` を指定するか、Dockerfileで `UID 1000` の非rootユーザーを定義してください。
2. **macOS (Docker Desktop) の場合**:
   VirtioFS / gRPC-FUSE により自動で所有者マッピングが行われるため特別な設定は不要ですが、非rootユーザー（`USER node` や `USER sandbox`）で動かすのが安全です。

---

## 6. Gemioでの実践ワークフローと安全機能の連携

### 6.1 ホストOS起動・コンテナ委譲（最推奨パターン）
* **APIキーの完全隔離**:
  `GEMINI_API_KEY` はホストOS側のメモリのみに保持され、ホストから `docker exec` を呼び出す際にも機密環境変数が自動スクラブされます（※ただし、`docker run -e` や `compose.yaml` の `env_file` 等でコンテナ自身に直接渡した環境変数はスクラブされないため、コンテナ起動設定には機密変数を渡さないでください）。
* **完全オフラインでも安心**:
  コンテナは `--network none` で完全にネットから切り離されているため、AIが外部へデータを送信することは不可能です。

### 6.2 トラストモード（`--trust`）の安全な活用
信頼できる自作コードで高速に「テスト駆動開発（TDD）」や「バグ自己修復」を行わせたい場合：

```bash
# 開発者モード ＋ トラストモードで起動
python -m gemio . --dev --container gemio-sandbox --trust
```

* **安全ガードレールは常時稼働**:
  トラストモードでも、Gemioのファイル操作ツールによる `.env` や秘密鍵の読み書き、`.git/` の破壊はブロックされます。さらに `compose.yaml` の4分類保護（設定ファイルの `:ro` マウント、`.venv` 隔離、`.env` マスク）を併用することで、シェルコマンド経由での設定改ざんや迂回アクセスも物理的に遮断できます。
* **サーキットブレーカー**:
  もし予期しない挙動があった場合は、承認要求で `a`（Abort）または `k`（Kill）を入力することで、一瞬でAIの実行ループや子プロセスを強制停止できます。

### 6.3 Web検索（`/search`）とオフライン検証の連携
1. ホストOSのターミナルで最新ライブラリの構文や使用例を検索：
   ```text
   > /search pytest-mock の最新の使い方
   ```
2. 検索結果が「作業前提資料」として自動保持されます。
3. 保持された資料をもとに、オフラインのサンドボックスコンテナ内で安全にテストコードを実装・実行させます。

---

## 7. 安全面のチェックリストとトラブルシューティング

### 🚀 起動前チェックリスト
- [ ] コンテナに `--privileged`（特権モード）が付いていないか？
- [ ] `/var/run/docker.sock` がマウントされていないか？
- [ ] `--network host` を使用していないか？（`--network none` を推奨。ブリッジ接続を使う場合は `--add-host=host.docker.internal:127.0.0.1` でホストへのアクセスを遮断しているか？）
- [ ] ホストのルートディレクトリ（`/` や `C:\`）をマウントしていないか？
- [ ] 非特権ユーザー（`-u 1000:1000` または `user: "1000:1000"` / `USER` 命令）で起動しているか？
- [ ] コンテナ起動時（`-e` や `env_file`）に本番APIキーやシークレットを渡していないか？
- [ ] [`04_SANDBOX_PROTECTION_GUIDELINE.md`](./04_SANDBOX_PROTECTION_GUIDELINE.md) に従い、`.git` やDocker設定・ビルド定義ファイル（`Dockerfile`, `compose.yaml`, `requirements.txt`, `pyproject.toml` 等）を `:ro`（読み取り専用）で保護し、`.venv` を隔離し、`.env` を `/dev/null` でマスクしているか？
- [ ] `.dockerignore` に機密ディレクトリ（`.ssh`, `.aws` 等）・秘密鍵・認証情報・`.env*`・個人フォルダ（`memo`）を記載し、イメージへの焼き込みを防いでいるか？
- [ ] コンテナが `sleep infinity` 等でバックグラウンド常駐しているか？

### 🔧 エラー別対処法

#### Q1. `[SECURITY_ALERT] 稼働中のサンドボックス用Dockerコンテナが見つかりません`
* **原因**: 指定した名前のコンテナが停止しているか、存在しません。
* **対処**: `docker ps` でコンテナが稼働しているか確認し、`docker start <コンテナ名>` または `docker run` / `docker compose up -d` で起動してください。

#### Q2. `[SECURITY_ALERT] 特権モード (--privileged) / ホストネットワーク共有モード (--network host) で実行されているため...`
* **原因**: Gemioのセーフティゲートが作動しました。
* **対処**: `--privileged` や `--network host` オプションを外してコンテナを起動し直してください。

#### Q3. `[SECURITY_ALERT] コンテナ内に '/var/run/docker.sock' が検出されました`
* **原因**: Dockerソケットがマウントされています。
* **対処**: `compose.yaml` や `docker run` の引数から `docker.sock` のマウント行を削除してください。

#### Q4. サンドボックス内で `pip install` や `npm install` が失敗する
* **原因**: `--network none` により外部ネットワーク通信が遮断されており、かつパッケージ領域が `root` 所有で保護されています。
* **対処**: 必要なパッケージはコンテナ起動時（Dockerfileのビルド時）にインストールしておくのが基本設計です。追加パッケージが必要な場合は、ホストOS側で `requirements.txt` に追記して再ビルド（`docker compose up -d --build`）してください。

#### Q5. 起動時や `/sandbox` 診断パネルで `🔴 コンテナ環境変数 (GEMINI_API_KEY, ...)` の危険アラートが表示される
* **原因**: `devcontainer.json` の `remoteEnv` / `containerEnv`、または `compose.yaml` の `environment` / `env_file`、`docker run -e` 等でコンテナ内部にAPIキーや機密トークンが渡されています。
* **対処**: テスト対象アプリ自体の動作に必須でない限り、コンテナ内のAIによる `env` や `/proc/*/environ` からのAPIキー読み取りを防ぐため、コンテナ設定ファイルからAPIキーの引き継ぎ設定を削除してコンテナを再作成（Rebuild Container）してください（Dev Container内起動時は環境変数を削除すると起動時のセキュア入力プロンプトが利用できます）。

---

## 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-03 | AI Agent | 運用中 | 初版作成。Gemioのセーフティゲート（SandboxSecurityError回避）、--network none推奨設計、3つのコンテナ起動方法（ワンライナー/Dockerfile/Compose）、多言語（Node.js/Go/Rust）対応ポイント、安全チェックリストを網羅。 |
| 2026-11-03 | AI Agent | 運用中 | コマンド1発（ワンライナー）およびDocker Compose設定に非特権ユーザー（`-u 1000:1000` / `user: "1000:1000"`）の指定を追加。root実行リスクとホスト側パーミッション競合の防止を強化。 |
| 2026-11-03 | AI Agent | 運用中 | セキュリティレビューに基づき、バインドマウント時の `.env` マスクマウント・`.git` 読み取り専用保護、コンテナ側環境変数の注意喚起、`.dockerignore` の機密パターン拡充、ブリッジ接続時の `host.docker.internal` 遮断、`--read-only` / `tmpfs` / `HOME=/tmp` 設定、およびComposeのリソース制限互換指定を追加。 |
| 2026-11-04 | AI Agent | 運用中 | VS Code Dev Containers (`.devcontainer/devcontainer.json`) による構築・運用セクションを追加し、コンテナ内起動時におけるAPIキー環境変数ブロックとセキュア入力プロンプト仕様を反映。 |
| 2026-11-04 | AI Agent | 運用中 | `gemio` パッケージ化（`src/gemio/` 構成への移行）に伴い、対象ファイルパスおよび起動コマンド表記（`python -m gemio`）を更新。 |
| 2026-11-04 | AI Agent | 運用中 | 起動時および `/sandbox` (`/status`) コマンドによるサンドボックス・マウント保護状態診断パネル（`ls -la` 権限・`/dev/null` マスク・`ro` 判定）の説明を追加。 |
| 2026-11-05 | AI Agent | 運用中 | `SANDBOX_PROTECTION_GUIDELINE.md` の4分類保護基準に合わせ、コンテナ名（`gemio-sandbox`）、`compose.yaml`（Docker設定・ビルド定義の `:ro` 保護、`/app/.venv` 匿名ボリューム隔離）、`Dockerfile`（`requirements.txt` のroot所有保護）、および `.dockerignore`（`SECRET_DIR_NAMES` / `SECRET_FILE_PATTERNS` 完全準拠）の設定値を最新仕様に同期。 |
| 2026-11-07 | AI Agent | 運用中 | `/sandbox` マウント状態診断テーブルにおける `🔵 作業対象 (rw)` の行色分け・ソート順整理を反映。 |
| 2026-11-09 | AI Agent | 運用中 | マニュアル類の `manuals/` フォルダ集約・連番化に伴い、`04_SANDBOX_PROTECTION_GUIDELINE.md` への参照パスを更新。 |
| 2026-11-10 | AI Agent | 運用中 | コンテナ内環境変数のAPIキー・機密トークン検査について、起動拒否から「両モード共通でのキー名検査（値非保持）と強い警告（`🔴 危険`）通知」へ統一された仕様を反映。 |
| 2026-11-14 | AI Agent | 運用中 | `.gemio/`（Skills・カスタムツール定義フォルダ）を利用する場合の `:ro`（読み取り専用）マウント保護推奨設定を追記。 |
