# Docker 技術資料・運用リファレンス

**作成日:** 2026年11月01日  
**最終更新日:** 2026年11月01日  
**ステータス:** 運用中  
**対象システム:** コンテナ環境 / テスト実行サンドボックス  

### 【要約】
本ドキュメントは、Dockerの基本概念やアーキテクチャから、基本操作・主要コマンドオプション、Dockerfileのベストプラクティス、Python Docker SDKの活用法、そしてAI生成コードやpytestを安全に実行するための堅牢化サンドボックス構築手順までを網羅した技術資料です。

---

## 目次
1. [Dockerの基本概念とアーキテクチャ](#1-dockerの基本概念とアーキテクチャ)
2. [Dockerの導入と基本操作](#2-dockerの導入と基本操作)
3. [主要コマンドとオプション詳細リファレンス](#3-主要コマンドとオプション詳細リファレンス)
4. [Dockerfileの書き方とベストプラクティス](#4-dockerfileの書き方とベストプラクティス)
5. [Python Docker SDK (`docker`) の使い方](#5-python-docker-sdk-docker-の使い方)
6. [AIコード実行用の隔離サンドボックス構築](#6-aiコード実行用の隔離サンドボックス構築)
7. [pytest実行用 堅牢化Dockerコンテナの構築と実行](#7-pytest実行用-堅牢化dockerコンテナの構築と実行)

---

## 1. Dockerの基本概念とアーキテクチャ

### 1.1 Dockerとは
Dockerは、アプリケーションをその依存関係（ライブラリや設定など）ごと一括でパッケージ化し、どの環境でも一貫して実行できるようにする**コンテナ型仮想化プラットフォーム**です。

#### 仮想マシン（VM）との仕組みの違い
* **仮想マシン（VM: ハイパーバイザ型）**:
  * ホストOSの上にハイパーバイザを介して**ゲストOS**を丸ごと起動します。
  * OSを丸ごと立ち上げるため、メモリ消費が大きく起動にも数十秒〜数分かかります。
* **Docker（コンテナ型）**:
  * ホストマシンの**OSカーネルをコンテナ間で共有**します。
  * ゲストOSを持たず、OS上の独立したプロセスとして動作するため、**軽量でメモリ消費が少なく、瞬時に起動**します。

### 1.2 Dockerが動く仕組み（アーキテクチャ）
Dockerは**クライアント・サーバー型（Client-Server）**の構成で動作します。

```text
[ Docker Client ]  ──(REST API)──>  [ Docker Host (dockerd) ]  <──>  [ Docker Registry ]
   (docker CLI)                        ├─ Images (イメージ)            (Docker Hub等)
                                       ├─ Containers (コンテナ)
                                       └─ Networks / Volumes
```

1. **Docker Client（CLI）**:
   * ユーザーが操作するコマンドラインツール（例: `docker run`, `docker build`）。
   * 内部ではDocker Daemonに対してREST API経由で命令を送信します。
2. **Docker Daemon (`dockerd` / Docker Host)**:
   * バックグラウンドで常駐し、イメージのビルド、コンテナの起動・停止、ネットワーク・ボリュームの管理などの実務を行います。
3. **Docker Registry**:
   * コンテナイメージを保存・共有する保管庫です。
   * デフォルトでは公式のクラウドサービス「Docker Hub」を参照します。

#### コンテナを隔離するLinuxの基盤技術
Dockerコンテナは、Linuxカーネルの以下の機能を利用して分離空間を作り出しています。
* **Namespace（名前空間）**: プロセスID、ネットワーク、マウントポイントなどを隔離し、コンテナごとに独立したOS空間があるように見せかける技術。
* **cgroups（コントロールグループ）**: コンテナごとにCPUやメモリ、I/Oなどのハードウェアリソースの上限を制限・監視する技術。
* **OverlayFS（Union File System）**: ファイルシステムのレイヤ（層）を重ね合わせて効率的にストレージを管理する仕組み。

### 1.3 Dockerの重要用語

| 用語 | 役割・説明 |
| :--- | :--- |
| **Dockerfile** | コンテナイメージの作成手順を記述したテキストファイル（「設計図」）。`FROM`, `RUN`, `COPY`, `CMD` などの命令で記述します。 |
| **Docker Image（イメージ）** | アプリケーションの実行に必要なファイル、ライブラリ、設定一式をまとめた読み取り専用のテンプレート（「金型」）。複数のレイヤが積み重なってできています。 |
| **Docker Container（コンテナ）** | Dockerイメージを元に実体化（インスタンス化）された実行環境。独立した軽量プロセスとして動作し、作成・起動・停止・破棄が容易です。 |
| **Docker Hub / Registry** | Dockerイメージをアップロード（`push`）したりダウンロード（`pull`）したりする共有レジストリ。GitHub PackagesやAWS ECRなどのプライベートレジストリも使われます。 |
| **Volume（ボリューム）** | コンテナ内のデータを永続化するためのストレージ領域。コンテナを削除してもデータが消えないようにホスト側で保持します。 |
| **Bind Mount（バインドマウント）** | ホスト側の任意のファイルやディレクトリを直接コンテナ内にマウントする仕組み。開発時にローカルコードをコンテナに即時反映させる際によく利用されます。 |
| **Bridge Network** | コンテナ同士が通信できるようにDockerが作成する仮想ネットワーク。 |
| **Docker Compose** | 複数のコンテナ（Webサーバー、DB、キャッシュなど）の構成を1つのYAMLファイル（`compose.yaml` / `docker-compose.yml`）に定義し、一括で起動・停止を管理するツール。 |

### 1.4 基本的なワークフロー（一連の流れ）
Dockerを利用する日常的な流れは次の4ステップです。

1. **定義（Write）**: `Dockerfile` にOSベースや環境構築手順を書く。
2. **ビルド（Build）**: `docker build` で Dockerfile から **Docker Image** を生成する。
3. **実行（Run）**: `docker run` でイメージを起動し、動く **Docker Container** にする。
4. **配布・共有（Share）**: `docker push` でイメージを **Docker Hub / Registry** へ登録し、本番環境や他の開発者が `docker pull` で取得する。

---

## 2. Dockerの導入と基本操作

### 2.1 Dockerの導入（インストール）

#### ① Windows / Mac の場合
GUIツールとコンテナ実行環境が一体になった **「Docker Desktop」** を利用するのが標準的です。

1. Docker公式サイトからインストーラーをダウンロードします。
   * **Windows**: 事前に「WSL 2（Windows Subsystem for Linux）」を有効化しておきます（推奨バックエンド）。
   * **Mac**: CPU（Apple Silicon / Intel チップ）に合わせたバイナリを選択します。
2. インストーラーを実行し、画面の指示に従ってインストールを完了させます。
3. Docker Desktopを起動します。

#### ② Linux（Ubuntuなど）の場合
サーバー用途などではGUI不要のため、軽量な **「Docker Engine」** をパッケージマネージャーから直接インストールします。

```bash
# Ubuntuの例: 依存パッケージの導入と公式リポジトリからのインストール
sudo apt-get update
sudo apt-get install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# sudoなしでdockerコマンドを実行できるように設定（オプション）
sudo usermod -aG docker $USER
```

#### ③ インストール確認
```bash
# バージョン確認
docker --version
docker compose version

# テストコンテナの実行
docker run --rm hello-world
```

### 2.2 基本的な使い方と必須コマンド

#### ① コンテナを起動する（`docker run`）
```bash
docker run -d -p 8080:80 --name my-web nginx
```
* `-d`: バックグラウンド（デタッチドモード）で実行。
* `-p 8080:80`: ホストの `8080` 番ポートをコンテナの `80` 番ポートに転送（`http://localhost:8080` でアクセス可能）。
* `--name my-web`: コンテナにわかりやすい名前を付与。
* `nginx`: 使用するイメージ名。ローカルになければDocker Hubから自動でダウンロード（`pull`）。

#### ② 状態確認・ログ・内部アクセス
```bash
# 起動中のコンテナ一覧を表示
docker ps

# 停止中も含めた全コンテナ一覧を表示
docker ps -a

# コンテナのログを出力（-f でリアルタイム監視）
docker logs -f my-web

# 起動中のコンテナのシェルに入る（デバッグ時によく使用）
docker exec -it my-web /bin/bash
```

#### ③ 停止と削除
```bash
# コンテナの停止
docker stop my-web

# コンテナの削除（停止済みである必要があります）
docker rm my-web

# 不要になったイメージの削除
docker rmi nginx

# 停止中の全コンテナ・未使用キャッシュ等を一括削除
docker system prune
```

### 2.3 自作アプリをコンテナ化する（Dockerfile例）
```dockerfile
# ベースイメージ
FROM node:20-alpine

# コンテナ内の作業ディレクトリ
WORKDIR /app

# 依存関係定義ファイルのコピーとインストール
COPY package*.json ./
RUN npm install

# アプリケーションコードのコピー
COPY . .

# 公開ポートの宣言
EXPOSE 3000

# 起動コマンド
CMD ["npm", "start"]
```

ビルドと実行:
```bash
# イメージのビルド
docker build -t my-node-app:1.0 .

# コンテナの起動
docker run -d -p 3000:3000 --name app my-node-app:1.0
```

### 2.4 複数コンテナをまとめて管理する（Docker Compose）
設定ファイル `compose.yaml`（または `docker-compose.yml`）を作成します。

```yaml
services:
  web:
    build: .
    ports:
      - "3000:3000"
    environment:
      - DATABASE_URL=postgres://user:password@db:5432/mydb
    depends_on:
      - db

  db:
    image: postgres:16-alpine
    restart: always
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: mydb
    volumes:
      - db-data:/var/lib/postgresql/data

volumes:
  db-data:
```

Compose操作コマンド:
```bash
# 全サービスをビルドしてバックグラウンドで一括起動
docker compose up -d

# 稼働状態の確認
docker compose ps

# 全サービスのログを閲覧
docker compose logs -f

# コンテナを停止してネットワークやコンテナを一括削除
docker compose down

# 永続ボリュームデータも含めて完全に初期化する場合
docker compose down -v
```

---

## 3. 主要コマンドとオプション詳細リファレンス

### 3.1 コンテナ作成・実行 (`docker run`)

```bash
docker run [オプション] イメージ名 [実行するコマンド]
```

| オプション | 正式名称 | 意味・役割 |
| :--- | :--- | :--- |
| **`-d`** | `--detach` | **バックグラウンド（デタッチドモード）で実行**。ターミナルを占有せずコンテナIDを出力。 |
| **`-p`** | `--publish` | **ポートフォワーディング**。`[ホスト側ポート]:[コンテナ側ポート]` で指定。 |
| **`--name`** | `--name` | **コンテナに名前を付ける**。省略時はランダム名が付与される。 |
| **`-v`** | `--volume` | **ストレージのマウント**。`[ホストパス]:[コンテナ内パス]` またはボリューム名を指定。 |
| **`-e`** | `--env` | **環境変数を設定**（例: `-e PORT=8080`）。 |
| **`--env-file`** | `--env-file` | **環境変数ファイルを読み込む**。`.env` ファイルの内容を一括適用。 |
| **`--rm`** | `--rm` | **コンテナ停止時に自動削除**。使い捨ての検証コンテナに便利。 |
| **`-i`** | `--interactive` | **コンテナの標準入力を開いたままにする**（キーボード入力を受付）。 |
| **`-t`** | `--tty` | **疑似ターミナル（TTY）を割り当てる**。通常 `-it` で対話シェル起動。 |
| **`-w`** | `--workdir` | **コンテナ内の作業ディレクトリを指定**。 |
| **`--network`** | `--network` | **接続するDockerネットワークを指定**。 |
| **`--restart`** | `--restart` | **再起動ポリシー**（`always`, `unless-stopped`, `on-failure`）。 |

### 3.2 コンテナの確認・操作・デバッグ
* **`docker ps`**: 稼働中コンテナ一覧（`-a`: 停止中含む全表示、`-q`: IDのみ）。
* **`docker exec -it <コンテナ名> /bin/bash`**: 稼働中コンテナ内でコマンド実行。
* **`docker logs -f --tail 100 <コンテナ名>`**: ログ確認（`-f`: 追跡、`--tail`: 行数指定、`-t`: タイムスタンプ付加）。
* **`docker stop <コンテナ名>`**: 安全に停止（`-t <秒>` で待機時間指定）。
* **`docker start <コンテナ名>` / `docker restart <コンテナ名>`**: 再開・再起動。
* **`docker rm <コンテナ名>`**: コンテナ削除（`-f`: 強制削除、`-v`: 匿名ボリューム同時削除）。

### 3.3 イメージの作成・管理
* **`docker build -t <名前:タグ> -f <Dockerfileパス> .`**: イメージ作成。
  * `--no-cache`: キャッシュを使わず完全再ビルド。
  * `--build-arg`: ビルド時引数を渡す。
* **`docker images`**: ローカルイメージ一覧（`-a`: 中間含む、`-q`: IDのみ）。
* **`docker rmi <イメージ名>`**: イメージ削除（`-f`: 強制削除）。
* **`docker pull <イメージ名>` / `docker push <イメージ名>`**: ダウンロード / アップロード。

### 3.4 クリーンアップ・状態調査
* **`docker system prune`**: 停止中コンテナ・未使用ネットワーク・ダングリングイメージを削除。
  * `-a`: コンテナから参照されていないすべてのイメージも削除。
  * `--volumes`: 未使用ボリュームも削除（永続データ消去に注意）。
  * `-f`: 確認プロンプトなしで強制実行。
* **`docker inspect <名前>`**: 内部メタデータをJSONで出力。
* **`docker stats`**: 全コンテナのCPU、メモリ、ネットワークI/Oをリアルタイム表示。

### 3.5 新形式のコマンド体系（Management Commands）

| 旧来の書き方 | 推奨される新形式（管理コマンド） | 役割 |
| :--- | :--- | :--- |
| `docker run` | `docker container run` | コンテナの作成と起動 |
| `docker ps` | `docker container ls` | コンテナ一覧 |
| `docker images` | `docker image ls` | イメージ一覧 |
| `docker rmi` | `docker image rm` | イメージの削除 |
| `docker volume ls` | `docker volume ls` | ボリューム一覧 |
| `docker network ls` | `docker network ls` | ネットワーク一覧 |

---

## 4. Dockerfileの書き方とベストプラクティス

### 4.1 基本形サンプル
```dockerfile
# syntax=docker/dockerfile:1
FROM node:20-alpine

WORKDIR /app

# 依存ライブラリのインストール（キャッシュ効率化）
COPY package*.json ./
RUN npm ci --only=production

COPY . .

# セキュリティ対策：非rootユーザーへ切り替え
USER node

EXPOSE 3000
CMD ["node", "server.js"]
```

### 4.2 主要な命令一覧

| 命令 | 役割 | 書式・例 |
| :--- | :--- | :--- |
| **`FROM`** | 基礎となるベースイメージを指定（必須・原則1行目）。 | `FROM python:3.12-slim` |
| **`WORKDIR`** | コンテナ内の作業ディレクトリを指定・作成。 | `WORKDIR /usr/src/app` |
| **`COPY`** | ホスト側のファイルやディレクトリをコンテナ内にコピー。 | `COPY . /app` |
| **`ADD`** | `COPY`の高機能版（tar展開やURL取得）。原則`COPY`推奨。 | `ADD archive.tar.gz /opt/` |
| **`RUN`** | ビルド時にコマンドを実行し、新レイヤを作成。 | `RUN apt-get update && apt-get install -y curl` |
| **`CMD`** | コンテナ起動時のデフォルトコマンド（上書き可能）。 | `CMD ["npm", "start"]` |
| **`ENTRYPOINT`** | コンテナの固定コマンド。引数をCMD等から受ける。 | `ENTRYPOINT ["python", "main.py"]` |
| **`ENV`** | コンテナ内の永続的な環境変数を設定。 | `ENV PORT=8080 NODE_ENV=production` |
| **`ARG`** | ビルド時のみ使用可能な変数を定義。 | `ARG VERSION=1.0.0` |
| **`EXPOSE`** | 公開ポート番号の宣言・ドキュメント。 | `EXPOSE 8080` |
| **`USER`** | 実行ユーザー名/UIDを指定（非root化）。 | `USER appuser` |
| **`VOLUME`** | マウントポイントの宣言。 | `VOLUME ["/data"]` |

### 4.3 `CMD` と `ENTRYPOINT`、Exec形式 vs Shell形式
* **`CMD` と `ENTRYPOINT` の使い分け**:
  * `CMD` は実行時に簡単に上書き可能（`docker run my-image /bin/bash` で上書き）。
  * `ENTRYPOINT` は固定実行ファイル。`ENTRYPOINT ["echo"]` と `CMD ["Hello"]` を併用すると、`CMD` は引数となる。
* **Exec形式（推奨） vs Shell形式**:
  * **Exec形式（推奨）**: `["実行ファイル", "引数1", "引数2"]`。JSON配列形式でPID 1として起動し、停止シグナル（SIGTERM）を正常処理できる。
  * **Shell形式**: `command arg1`。`/bin/sh -c` を介して実行されるためシグナル伝播で問題が生じやすい。

### 4.4 マルチステージビルド
ビルド用ツールを含む大きなイメージで成果物を作成し、最終実行環境には成果物のみをコピーすることでサイズを劇的に軽量化します。

```dockerfile
# --- ステージ1: ビルド環境 ---
FROM golang:1.22-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /bin/app

# --- ステージ2: 本番実行環境（極小イメージ） ---
FROM alpine:latest
WORKDIR /app
COPY --from=builder /bin/app /app/app
USER nobody
ENTRYPOINT ["/app/app"]
```

### 4.5 ベストプラクティス
1. **キャッシュ効率を意識した順序**: ソースコードより先に `requirements.txt` / `package.json` をコピーしてインストール。
2. **`RUN` 命令を1行にまとめてゴミを消す**: `--no-install-recommends` を付け、同一レイヤ内で `rm -rf /var/lib/apt/lists/*` を実行。
3. **`.dockerignore` を必ず配置**: `.git`, 一時ファイル, 秘密情報を除外。
4. **軽量なベースイメージを選ぶ**: `alpine` や `-slim`、Googleの `distroless` などを採用。
5. **root ユーザーで動かさない**: `USER` 命令で非特権ユーザーへ切り替え。
6. **先頭行に構文ディレクティブを付与**: `# syntax=docker/dockerfile:1` で最新BuildKitを有効化。

---

## 5. Python Docker SDK (`docker`) の使い方

### 5.1 インストールと初期化
```bash
pip install docker
```

```python
import docker

# デフォルト環境からクライアント初期化
client = docker.from_env()

# 疎通確認
print(client.ping())  # True
print(client.version()["Version"])

# ソケットを明示する場合:
# client = docker.DockerClient(base_url="unix://var/run/docker.sock")
```

### 5.2 コンテナ操作 (`client.containers`)
```python
# 1回限りのコマンド実行
output = client.containers.run("alpine", "echo Hello from Python!", remove=True)
print(output.decode("utf-8"))

# バックグラウンド実行と設定
container = client.containers.run(
    "nginx:alpine",
    detach=True,
    name="my-python-nginx",
    ports={"80/tcp": 8080},
    environment={"ENV_VAR": "1"},
    volumes={"/tmp/data": {"bind": "/app/data", "mode": "rw"}},
)

# 一覧取得、停止、再開、削除
for c in client.containers.list(all=True):
    print(c.name, c.status)

container = client.containers.get("my-python-nginx")
container.stop(timeout=10)
container.start()
container.remove(force=True)

# ログ取得（過去ログまたはリアルタイムストリーミング）
logs = container.logs(tail=50)
# for line in container.logs(stream=True, follow=True): print(line.decode("utf-8"))

# 稼働中コンテナ内でのコマンド実行 (exec_run)
exit_code, output = container.exec_run("ls -l /etc/nginx")
```

### 5.3 イメージ操作 (`client.images`)
```python
# イメージのプル
image = client.images.pull("redis", tag="alpine")

# Dockerfile からのビルド
image, build_logs = client.images.build(
    path=".",
    tag="my-app:latest",
    rm=True,
    buildargs={"PORT": "8080"}
)
for chunk in build_logs:
    if "stream" in chunk:
        print(chunk["stream"], end="")

# 一覧と削除
client.images.remove("my-app:latest", force=False)
```

### 5.4 ネットワーク・ボリューム操作とエラーハンドリング
```python
import docker
from docker.errors import DockerException, NotFound, APIError

try:
    client = docker.from_env()
    container = client.containers.get("non-existent-container")
except NotFound:
    print("指定されたコンテナが見つかりません。")
except APIError as e:
    print(f"Docker API エラー: {e.explanation}")
except DockerException:
    print("Docker デーモンに接続できません。")
```

---

## 6. AIコード実行用の隔離サンドボックス構築

### 6.1 サンドボックスで防ぐべき主な脅威
1. **破壊的コマンド / ファイル改ざん**: `rm -rf /` やシステムファイルの書き換え。
2. **リソース枯渇（DoS / Fork爆弾）**: 無限ループによるCPU 100%占有、メモリ浪費、プロセス大量生成。
3. **データ漏洩・SSRF**: 外部C2通信やクラウドメタデータ（`169.254.169.254`）へのアクセスによる認証情報窃取。
4. **コンテナエスケープ**: 悪意のある細工されたシステムコールによるホストOSへの脱獄。

### 6.2 サンドボックスの技術アプローチ比較

| 方式 | 主な技術 | 隔離レベル | 起動速度 | 特徴・向いている用途 |
| :--- | :--- | :--- | :--- | :--- |
| **① 堅牢化Docker** | 標準 Docker (`runc`) | 中（同一カーネル） | 数百ms | ローカル開発・検証。厳格なオプション設定が必須。 |
| **② アプリカーネル** | **gVisor (`runsc`)** | 高（ユーザー空間カーネル） | 数百ms | Dockerから差し替え可能。システムコールをインターセプトして防御。 |
| **③ MicroVM** | **Firecracker** / Kata | 最高（ハードウェア仮想化） | 100〜200ms | AWS Lambda等でも採用される完全なVM隔離。本番マルチテナント向け。 |
| **④ AI特化SaaS/OSS** | **E2B**, Modal | 最高（MicroVM内包） | 即時（API呼出） | AIエージェントに特化したCode Interpreter環境。運用コストを最小化したい場合。 |

### 6.3 実装例：Docker単体での堅牢化サンドボックス（Python SDK）
```python
import docker

client = docker.from_env()

def execute_untrusted_code(code: str, timeout_sec: int = 5) -> str:
    """AIが生成したPythonコードを堅牢化コンテナで実行する"""
    container = None
    try:
        cmd = ["python", "-c", code]
        
        container = client.containers.run(
            image="python:3.12-alpine",
            command=cmd,
            detach=True,
            
            # --- セキュリティと隔離の設定 ---
            network_mode="none",                # ネットワーク通信を完全遮断
            read_only=True,                     # ルートファイルシステムを読み取り専用化
            user="1000:1000",                   # 非特権ユーザーで実行
            cap_drop=["ALL"],                   # すべてのLinuxケーパビリティを剥奪
            security_opt=["no-new-privileges:true"], # 権限昇格を無効化
            
            # --- 一時書き込み領域 ---
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
            
            # --- リソース制限 (DoS・Fork爆弾対策) ---
            mem_limit="256m",                   # メモリ上限 256MB
            nano_cpus=1_000_000_000,            # CPU 1コア分まで
            pids_limit=50,                      # 生成可能プロセス数上限
        )

        res = container.wait(timeout=timeout_sec)
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8")
        
        return stdout if res["StatusCode"] == 0 else f"Error: {stderr}"

    except Exception as e:
        return f"Execution Failed or Timed out: {str(e)}"
    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass
```

### 6.4 gVisor (`runsc`) による多層防御
Google製のユーザー空間カーネル **gVisor (`runsc`)** を組み込むことで、システムコールをインターセプトしコンテナエスケープを強力に防ぎます。

```python
# gVisor導入済みの環境では runtime="runsc" を指定
container = client.containers.run(
    image="python:3.12-alpine",
    runtime="runsc",
    command=["python", "-c", code],
    network_mode="none",
    mem_limit="256m",
    # その他堅牢化オプション
)
```

### 6.5 AI特化サンドボックス（E2B）
Firecracker MicroVMを活用したOSS/SaaSであり、グラフ描画結果の取得やステートフルな実行を安全に行えます。

```python
from e2b_code_interpreter import Sandbox

with Sandbox() as sandbox:
    execution = sandbox.run_code("print('Hello from E2B!')")
    for log in execution.logs.stdout:
        print(log)
```

---

## 7. pytest実行用 堅牢化Dockerコンテナの構築と実行

### 7.1 プロジェクト構成
```text
pytest-sandbox/
├── Dockerfile
├── requirements.txt
└── tests/
    └── test_sample.py
```

#### `requirements.txt`
```text
pytest>=8.0.0
requests
numpy
```

#### `Dockerfile`（マルチステージ・非特権ユーザー環境）
```dockerfile
# syntax=docker/dockerfile:1

# --- Stage 1: ビルド環境 (パッケージインストール) ---
FROM python:3.12-slim AS builder

WORKDIR /install

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt

# --- Stage 2: 実行環境 (最小限・堅牢化) ---
FROM python:3.12-slim

# セキュリティ・安定性のためのPython環境変数
# - バイトコード（.pyc）の生成を無効化（read-only環境でのエラー防止）
# - ログ出力をバッファリングせず即時出力
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/sandbox/.local/bin:$PATH"

# 1. 最小限の非rootユーザーを作成 (UID/GID 10001)
RUN groupadd -g 10001 sandbox && \
    useradd -u 10001 -g sandbox -m -s /sbin/nologin sandbox

# 2. builderステージからインストール済みパッケージをコピー
COPY --from=builder /root/.local /home/sandbox/.local
RUN chown -R sandbox:sandbox /home/sandbox/.local

# 3. テスト実行用作業ディレクトリ
WORKDIR /workspace

# 4. 非特権ユーザーに切り替え
USER sandbox:sandbox

# 5. デフォルトコマンド（キャッシュ先をtmpfsに変更）
CMD ["pytest", "-o", "cache_dir=/tmp/.pytest_cache"]
```

### 7.2 イメージのビルド
```bash
docker build -t hardened-pytest:1.0 .
```

### 7.3 テストの安全な実行（CLI）
ホスト側の `tests` ディレクトリを**読み取り専用（`:ro`）**でマウントし、強力な拘束を適用して起動します。

```bash
docker run --rm \
  --network none \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --memory=512m \
  --cpus=1.0 \
  --pids-limit=50 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$(pwd)/tests:/workspace:ro" \
  hardened-pytest:1.0
```

#### 各オプションの堅牢化の意味
| オプション | 目的・防御内容 |
| :--- | :--- |
| **`--network none`** | 外部およびローカルネットワーク通信を完全遮断（C2通信、SSRF、データ持ち出し防止）。 |
| **`--read-only`** | コンテナ内のルートファイルシステムを書き込み禁止（マルウェア配置や改ざん防止）。 |
| **`-v ...:ro`** | ホスト側のテストコードを「読み取り専用」でマウント（テストコード改ざん防止）。 |
| **`--cap-drop=ALL`** | すべてのLinuxケーパビリティ（権限）を剥奪。 |
| **`--security-opt=no-new-privileges:true`** | `setuid` 等による権限昇格を無効化。 |
| **`--tmpfs /tmp:...`** | pytestのキャッシュや一時ファイル用に制限付きメモリ領域を提供（`noexec` で実行不可）。 |
| **`--memory=512m` / `--cpus=1.0` / `--pids-limit=50`** | DoS攻撃やFork爆弾を防止。 |

### 7.4 Python SDKからの自動実行実装
```python
import os
import docker

client = docker.from_env()

def run_isolated_pytest(tests_dir_path: str, timeout_sec: int = 30) -> dict:
    host_tests_path = os.path.abspath(tests_dir_path)
    container = None
    try:
        container = client.containers.run(
            image="hardened-pytest:1.0",
            command=["pytest", "-o", "cache_dir=/tmp/.pytest_cache", "-v"],
            detach=True,
            network_mode="none",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="512m",
            nano_cpus=1_000_000_000,
            pids_limit=50,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
            volumes={
                host_tests_path: {"bind": "/workspace", "mode": "ro"}
            }
        )

        result = container.wait(timeout=timeout_sec)
        output = container.logs().decode("utf-8")

        return {
            "exit_code": result["StatusCode"],
            "output": output,
            "success": result["StatusCode"] == 0
        }

    except Exception as e:
        return {"exit_code": -1, "output": str(e), "success": False}
    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass

if __name__ == "__main__":
    res = run_isolated_pytest("./tests")
    print(f"Success: {res['success']}")
    print(res["output"])
```

### 7.5 pytestを堅牢化環境で動かす際の重要Tips
1. **`.pytest_cache` の書き込みエラー対策**:
   * ルートファイルシステムが `:ro`（読み取り専用）のため、通常のキャッシュ作成でエラーが発生します。
   * Dockerfile内の環境変数 `PYTHONDONTWRITEBYTECODE=1` と、pytest引数 `-o cache_dir=/tmp/.pytest_cache` で書き込み先を `tmpfs` に向けることで解決します。
2. **外部APIへのモック（Mock）必須化**:
   * `--network none` を設定しているため外部通信は `ConnectionError` となります。テスト側では `pytest-mock` や `responses` を使用してモック化を前提とします。
