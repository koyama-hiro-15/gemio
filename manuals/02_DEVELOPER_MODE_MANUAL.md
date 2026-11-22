# 🛠️ Gemio 開発者モード（Developer Mode）詳細マニュアル

**作成日:** 2026年11月02日  
**最終更新日:** 2026年11月23日  
**ステータス:** 運用中  
**対象システム:** Gemio 開発者モード (`src/gemio/project/developer.py`, `src/gemio/project/base.py`, `src/gemio/core/prompts.py`, `src/gemio/cli/main.py`, `src/gemio/cli/ui/`)  

### 【要約】
本ドキュメントは、Gemioの「開発者モード（`--dev`）」を使用して、隔離されたDev Container / Dockerサンドボックス内で安全にシェルコマンド実行・ビルド・テスト検証・デバッグを行うための詳細マニュアルです。2つの実行アーキテクチャ（ホストOS起動委譲／Dev Container内直接起動）、両モード共通でのAPIキー・機密環境変数のキー名検査（値は読み取らず強い警告で通知）とDev Container内起動時のセキュア入力機構、堅牢な `.devcontainer/devcontainer.json` の構築手順、セーフティゲートと多言語対応ガードレールの仕様、および3段階の承認モード（通常承認モード／エディットモード `--edit`／トラストモード `--trust`）を活用した実践ワークフローを解説します。

---

## 目次
1. [開発者モードの概要とリスクベースの設計思想](#1-開発者モードの概要とリスクベースの設計思想)
2. [2つの起動アーキテクチャと実践手順](#2-2つの起動アーキテクチャと実践手順)
   - [2.1 パターンA：ホストOS起動・コンテナ委譲（Host-Orchestrated モード・最推奨）](#21-パターンaホストos起動コンテナ委譲host-orchestrated-モード最推奨)
   - [2.2 パターンB：Dev Container内起動（In-Container モード）](#22-パターンbdev-container内起動in-container-モード)
3. [堅牢なサンドボックス環境（Docker / Dev Container）の構築ガイド](#3-堅牢なサンドボックス環境docker--dev-containerの構築ガイド)
   - [3.1 ネットワーク完全遮断（`--network none`）による最高強度のサンドボックス構築](#31-ネットワーク完全遮断--network-noneによる最高強度のサンドボックス構築)
   - [3.2 推奨 `.devcontainer/devcontainer.json` テンプレート](#32-推奨-devcontainerdevcontainerjson-テンプレート)
   - [3.3 ファイルI/Oパフォーマンスを最大化するベストプラクティス](#33-ファイルioパフォーマンスを最大化するベストプラクティス)
4. [開発者モードの多層セキュリティ機構（セーフティゲート詳細）](#4-開発者モードの多層セキュリティ機構セーフティゲート詳細)
   - [4.1 Gate 1：コンテナ隔離と危険構成の強制検査 (`SandboxSecurityError`)](#41-gate-1コンテナ隔離と危険構成の強制検査-sandboxsecurityerror)
   - [4.2 Gate 2：子プロセス環境変数の自動スクラブ（多言語・クラウド対応）](#42-gate-2子プロセス環境変数の自動スクラブ多言語クラウド対応)
   - [4.3 Gate 3：機密ファイル・管理領域のハードブロックと多言語キャッシュ除外](#43-gate-3機密ファイル管理領域のハードブロックと多言語キャッシュ除外)
5. [`run_sandbox_command` ツールの動作仕様と制御](#5-run_sandbox_command-ツールの動作仕様と制御)
   - [5.1 タイムアウト制御とハングアップ防止](#51-タイムアウト制御とハングアップ防止)
   - [5.2 長大ログ出力の自動トランケート（コンテキスト保護）](#52-長大ログ出力の自動トランケートコンテキスト保護)
   - [5.3 承認アクション（4択）・コマンド全文＆意図表示とサーキットブレーカー・プロセスキル](#53-承認アクション4択コマンド全文意図表示とサーキットブレーカープロセスキル)
6. [実践ワークフロー：通常承認モードとトラストモードの使い分け](#6-実践ワークフロー通常承認モードとトラストモードの使い分け)
   - [6.1 レシピ1：未知・外部のプロジェクトを扱う場合（通常承認モード）](#61-レシピ1未知外部のプロジェクトを扱う場合通常承認モード)
   - [6.2 レシピ2：信頼できる自作プロジェクトを高速開発する場合（トラストモード `--dev --trust`）](#62-レシピ2信頼できる自作プロジェクトを高速開発する場合トラストモード---dev---trust)
   - [6.3 レシピ3：Web検索 (`/search`) とオフラインサンドボックス検証の連携](#63-レシピ3web検索-search-とオフラインサンドボックス検証の連携)
7. [トラブルシューティング（エラーメッセージ別対処法）](#7-トラブルシューティングエラーメッセージ別対処法)
8. [更新履歴](#8-更新履歴)

---

## 1. 開発者モードの概要とリスクベースの設計思想

Gemioの**開発者モード（`--dev`）**は、ソフトウェア開発における「コード作成 ➔ ビルド・型チェック・Linter実行 ➔ テスト実行 ➔ エラー解析 ➔ 自己修復」のサイクルを、Pythonに限らずあらゆる言語（TypeScript/Node.js, Go, Rust, Java, Ruby, PHP等）で回せるようにするための専用モードです。

### 基本モードとの違い
| 項目 | 基本モード（デフォルト） | 開発者モード（`--dev` 指定時） |
| :--- | :--- | :--- |
| **使用クラス** | `Project` (`src/gemio/project/base.py`) | `DeveloperProject` (`src/gemio/project/developer.py`) |
| **Docker / コンテナ** | 不要（ホストOS上で直接動作） | **必須**（安全なコンテナが確認できない場合は起動拒否） |
| **シェルコマンド実行** | **不可**（外部プロセス起動なし） | **可能**（`run_sandbox_command` ツールが追加される） |
| **主な用途** | 文書作成、ファイル整理、コード閲覧、軽量なPythonスクリプト作成 | 多言語開発、ビルド・Linter・単体/結合テスト実行、自律デバッグ |

### リスク管理の3段階モード（通常承認モード / エディットモード / トラストモード）
* **🟢 通常承認モード（デフォルト）**:
  外部からクローンしたばかりの未知のリポジトリなど、悪意あるコードやプロンプトインジェクションが含まれる可能性がある場合に使用します。すべての変更操作とコマンド実行に**人間の目（承認プロンプト）**が入ります。
* **🟡 エディットモード（`--edit` / `/edit`）**:
  ファイルの作成・編集・リネーム・コピーは自動承認してコーディングのテンポを上げつつ、**ファイルの削除（`delete_path`）とサンドボックス内コマンド実行（`run_sandbox_command`）だけは都度確認**を行う中間モードです。また、削除やコマンド実行を拒否した後にAIが無断でファイル書き換えを行おうとした場合もサーキットブレーカーで確実に遮断されます。
* **🔴 トラストモード（`--trust` / `/trust`）**:
  開発者自身が作成したプロジェクトや、安全性が確認済みのプロジェクトで使用します。**Dockerコンテナによる物理隔離**と**うっかりミス防止のガードレール**の上で、承認確認なしにAIに自由かつ高速にテスト・修正サイクルを回させます。

---

## 2. 2つの起動アーキテクチャと実践手順

`DeveloperProject` は、起動時に実行環境を厳格に判別し、以下の2つのアーキテクチャに対応します。

```text
【パターンA: ホストOS起動・コンテナ委譲 (Host-Orchestrated) ★最推奨】
  ┌─ ホストOS (Windows / macOS / Linux) ──┐      ┌─ Docker コンテナ (--network none 等) ┐
  │  Gemio 本体 (--dev)                   │      │  ・必要な言語ツール・ライブラリ導入済 │
  │  (APIキーはホスト側メモリのみに保持)  ├───(docker exec)───> /bin/sh                  │
  └───────────────────────────────────────┘      └──────────────────────────────────────┘

【パターンB: Dev Container内起動 (In-Container)】
  ┌─ Dev Container (隔離されたLinuxコンテナ) ─────────────────────────────────┐
  │  ・環境変数(os.environ / /proc/1/environ)のAPIキー有無を検査・強い警告通知│
  │  ・環境変数未設定時は起動時プロンプト(****マスク)でAPIキーを直接メモリ保持│
  │  ・prctl(PR_SET_DUMPABLE, 0) で子プロセスからのメモリ読み取りを遮断       │
  │  Gemio 本体 (--dev) ──────(クリーンな subprocess)──────> /bin/bash        │
  └───────────────────────────────────────────────────────────────────────────┘
```

---

### 2.1 パターンA：ホストOS起動・コンテナ委譲（Host-Orchestrated モード）
Gemio本体はホストOSのターミナルで起動し、シェルコマンド（`run_sandbox_command`）の実行だけを稼働中のDockerコンテナ（または起動中のDev Container）内へ委譲する方式です。

#### 特徴・メリット
* **ホスト側のシステム環境変数（`GEMINI_API_KEY`）でそのまま起動可能**:
  Gemio本体がホストOS側にあるため、ホストOSに設定した環境変数をそのまま使ってコピペなしで起動できます。コンテナ内部にはAPIキーが一切渡りません。
* **完全オフラインコンテナ（`--network none`）との組み合わせが可能**:
  Gemini APIとの通信や `/search`（Web検索）はホストOS側で行い、コマンド実行はプロセス間通信の `docker exec` で行うため、**コンテナのインターネット接続を完全遮断（`--network none`）した状態でも100%動作します**。
* **コンテナ側にGemioの依存ライブラリが不要**: Node.js専用やRust専用のコンテナでもそのまま利用できます。

#### 起動手順
1. 対象プロジェクトのDockerコンテナ（またはVS CodeのDev Container）を起動しておきます。
2. ホストOSのターミナルで以下を実行します：
   ```bash
   # コンテナ名を明示指定して起動する場合
   python -m gemio /path/to/project --dev --container my-sandbox-container

   # バインドマウント情報や devcontainer.local_folder ラベルからコンテナを自動検出する場合
   python -m gemio /path/to/project --dev
   ```

---

### 2.2 パターンB：Dev Container内起動（In-Container モード）
VS Codeの「**Dev Containers: Reopen in Container**」や `@devcontainers/cli` でコンテナ内部のターミナルに入り、コンテナの中で直接Gemioを起動する方式です。

#### コンテナ内起動におけるAPIキー保護の仕組み（3重の防御）
Linuxコンテナ内で環境変数（`remoteEnv` や `docker run -e`、`.bashrc` 等）にAPIキーを設定すると、`/proc/$PPID/environ` や `/proc/1/environ` に起動時の文字列が残り続け、AIがシェルツール経由で読み取れてしまうリスクがあります。
そのため、Gemioのコンテナ内起動モードでは以下の**3重の保護機構**を採用しています。

1. **起動時のセキュア入力プロンプト（`****` マスク表示＆OS別コマンド案内）**:
   コンテナの環境変数にAPIキーを設定せずに起動すると、ホストOS側のシステム環境変数からAPIキーを取り出すための**OS別コマンド案内パネル**が表示されます。入力（ペースト）した文字はすべて `****` でマスク表示され、再確認なしでそのままスムーズに起動します。環境変数やシェル履歴、ディスクには一切記録されません。
2. **コンテナ環境変数へのAPIキー・機密トークン露出の自動検知と強い警告通知**:
   `os.environ` または `/proc/1/environ` に各種APIキー・トークン（`GEMINI_API_KEY`, `OPENAI_API_KEY`, `*_TOKEN`, `*_SECRET` 等）が設定されている場合、開発作業を妨げずに起動しつつ、直後の `/sandbox` 診断パネルで**「🔴 危険（`danger`）」の強い警告**として設定されている変数名（※値は読み取らずキー名のみ）を通知します（ホストOS起動モードでも同様にコンテナ内環境変数を検査します）。
3. **プロセスメモリダンプのOSレベル遮断（`prctl(PR_SET_DUMPABLE, 0)`）**:
   Linuxカーネルの `PR_SET_DUMPABLE=0` を自動適用し、AIが実行する子プロセスから親プロセス（Gemio自身）のメモリ（`/proc/$PPID/mem` や `ptrace`）を覗き見る行為もOSレベルで遮断します。

#### 起動手順（ステップ・バイ・ステップ）
1. **VS CodeでDev Containerを開く**:
   VS Codeのコマンドパレット（`Ctrl+Shift+P` / `Cmd+Shift+P`）から **`Dev Containers: Reopen in Container`** を実行します。
2. **コンテナ内の統合ターミナルでGemioを起動する**:
   ```bash
   python -m gemio --dev
   ```
3. **ホストOSの別ターミナルからAPIキーをコピー＆ペーストする**:
   画面に表示される案内に従い、ホストOS（手元のPC）で別のターミナルを開いて以下のいずれかのコマンドを実行し、APIキーをコピーします。
   * **Windows (PowerShell)**:
     * クリップボードへ直接コピー（画面非表示・推奨）: `Set-Clipboard $env:GEMINI_API_KEY`
     * 画面に表示してコピー: `$env:GEMINI_API_KEY`
   * **Windows (コマンドプロンプト)**:
     * クリップボードへ直接コピー: `echo | set /p="%GEMINI_API_KEY%" | clip`
     * 画面に表示してコピー: `echo %GEMINI_API_KEY%`
   * **macOS / Linux / WSL**:
     * クリップボードへ直接コピー (macOS): `printf "%s" "$GEMINI_API_KEY" | pbcopy`
     * 画面に表示してコピー: `echo $GEMINI_API_KEY`
4. **プロンプトに貼り付けて `[Enter]` を押す**:
   `GEMINI_API_KEY を入力してください > ****` に貼り付けて Enter を押すと、そのまま対話セッションが開始されます（コピーに使ったホスト側の別ターミナルは閉じて構いません）。
5. ウェルカムバナーの「動作モード」に **`開発者モード (Dev Container内実行)`** と表示されれば準備完了です。

---

## 3. 堅牢なサンドボックス環境（Docker / Dev Container）の構築ガイド

### 3.1 ネットワーク完全遮断（`--network none`）による最高強度のサンドボックス構築（パターンA向け）
あらかじめ開発に必要なライブラリやツール（`pytest`, `ruff`, `npm`, `cargo`, `go` 等）をインストールしたDockerイメージを用意し、**`--network none`（外部ネットワーク完全遮断）** で起動してホストOSから接続する方法です。

```bash
# 1. バックグラウンドでネットワーク遮断・非特権・機密領域保護付きのサンドボックスコンテナを起動
docker run -d \
  --name gemio-sandbox \
  --network none \
  --cap-drop=ALL \
  --security-opt=no-new-privileges:true \
  --memory=4g \
  --cpus=2.0 \
  --pids-limit=256 \
  -u 1000:1000 \
  -v "$(pwd):/workspaces/app" \
  -v /dev/null:/workspaces/app/.env:ro \
  -v "$(pwd)/.git:/workspaces/app/.git:ro" \
  -v /workspaces/app/.venv \
  -w /workspaces/app \
  my-dev-image:latest \
  sleep infinity

# 2. ホストOSから開発者モード（＋信頼できるプロジェクトなら --trust）で接続
python -m gemio . --dev --container gemio-sandbox --trust
```

* `--network none` により、コンテナ内から外部インターネットやホストLANへの情報送信は物理的に100%不可能になります（コンテナ内のループバック `localhost` は有効なため、ローカルWebサーバーのテスト等はそのまま実行可能です）。

---

### 3.2 推奨 `.devcontainer/devcontainer.json` テンプレート（VS Code Dev Container向け）

VS CodeのDev Containerを使用する場合は、プロジェクト直下に `.devcontainer/devcontainer.json` を作成します。

> **⚠️ 重要（APIキーの取り扱い）**:
> `devcontainer.json` の `remoteEnv` や `containerEnv` には、原則として **`GEMINI_API_KEY` や `GOOGLE_API_KEY` 等の機密キーを記載しないでください**。
> コンテナ環境変数にAPIキーを設定すると、AIが実行するシェルやテストコードから環境変数（`env` や `/proc/*/environ`）経由で読み取られるリスクがあるため、起動時の診断パネルで `🔴 危険` の強い警告が表示されます（環境変数に設定しなければ、コンテナ内起動時は安全なマスク入力プロンプトが利用できます）。

#### 基本テンプレート（Python開発環境の例）
```jsonc
{
  "name": "Gemio Safe Dev Container",
  "image": "mcr.microsoft.com/devcontainers/python:1-3.12-bullseye",

  // 1. 非特権ユーザーで実行（root実行リスクとホスト側ファイル権限汚染を防止）
  "remoteUser": "vscode",

  // 2. 注意: remoteEnv や containerEnv に GEMINI_API_KEY は絶対に設定しないこと！
  "remoteEnv": {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1"
  },

  // 3. セキュリティ強化・リソース制限オプション
  "runArgs": [
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges:true",
    // コンテナ内からホストOS上のローカルサービスへの侵入を遮断
    "--add-host=host.docker.internal:127.0.0.1",
    // DoS・Fork爆弾によるホストOSフリーズを防止
    "--memory=4g",
    "--cpus=2.0",
    "--pids-limit=256"
  ],

  // 4. 任意: ホスト側に .env がある場合のマスクマウントや .git の読み取り専用保護
  // "mounts": [
  //   "source=${localWorkspaceFolder}/.git,target=${containerWorkspaceFolder}/.git,type=bind,readonly",
  //   "source=/dev/null,target=${containerWorkspaceFolder}/.env,type=bind,readonly"
  // ],

  // 5. コンテナ作成時の依存パッケージ自動インストール
  "postCreateCommand": "pip install --upgrade pip && pip install -e . pytest",

  // 6. VS Code 固有の拡張機能・設定
  "customizations": {
    "vscode": {
      "extensions": [
        "ms-python.python",
        "charliermarsh.ruff"
      ]
    }
  }
}
```

#### Dev Containerの2通りの使い方
この `.devcontainer/devcontainer.json` でコンテナを起動した後は、好みに応じてどちらの起動パターンでも利用できます：
1. **VS Codeの統合ターミナル（コンテナ内）でそのまま使う場合（パターンB）**:
   VS Code内のターミナルで `python -m gemio --dev` を実行し、起動時のプロンプトにAPIキーを貼り付けて開始します。
2. **ホストOS側のターミナルから自動接続して使う場合（パターンA）**:
   Dev Containerを起動した状態で、ホストOS側のターミナルから `python -m gemio . --dev` を実行します。Gemioが `devcontainer.local_folder` ラベルを自動認識してそのDev Containerへ接続するため、APIキー入力なしで即座に利用できます。

---

### 3.3 ファイルI/Oパフォーマンスを最大化するベストプラクティス
* **Windows (WSL2) の場合**: `C:\Users\...` ではなく、WSL2内部のLinuxファイルシステム（`~/projects/...`）上にプロジェクトを配置してください。
* **macOS の場合**: Docker Desktopの設定で **VirtioFS** を有効にしてください。

---

## 4. 開発者モードの多層セキュリティ機構（セーフティゲート詳細）

### 4.1 Gate 1：コンテナ隔離と危険構成の強制検査 (`SandboxSecurityError`)
`DeveloperProject` は初期化時に以下の検査を実行し、違反があれば即座に `SandboxSecurityError` で起動を拒否します。

1. **ホストOS誤判定の完全防止（実体ファイル検証）**:
   * `_is_running_in_container()` は環境変数（`DEVCONTAINER` 等）に依存せず、`sys.platform == "linux"` かつ `/.dockerenv` または `/run/.containerenv` が実在する場合のみコンテナ内と判定します。Windows / macOS や生身のLinuxホスト上でローカルシェルが直接実行されることは絶対にありません。
2. **`/var/run/docker.sock` の遮断**:
   * コンテナへの `docker.sock` マウントを検知した場合は起動を拒否します。
3. **特権モード（`--privileged`）およびホストネットワーク共有（`--network host`）の遮断**:
   * 特権コンテナおよびホストOSのネットワーク空間を共有する `--network host` コンテナへの接続を拒否します。
4. **ホストルートマウントの遮断**:
   * ホストの `/` や `C:\` が直接マウントされているコンテナを拒否します。
5. **コンテナ内起動時のプロセスメモリ保護**:
   * コンテナ内起動（`in_container`）時、`prctl(PR_SET_DUMPABLE, 0)` により、子プロセスからの親プロセスメモリ読み取り（`/proc/$PPID/mem` 等）をカーネルレベルで禁止します（※コンテナ環境変数に各種APIキーが設定されている場合は、起動拒否ではなく後述の `/sandbox` 診断にて両モード共通で `🔴 危険` の強い警告として通知します）。

---

### 4.2 Gate 2：子プロセス環境変数の自動スクラブ（多言語・クラウド対応）
子プロセスへ渡す環境変数から、以下の機密変数が自動的に除去（スクラブ）されます。

* **完全一致で除去される変数**:
  `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AZURE_CLIENT_SECRET`, `GITHUB_TOKEN`, `GH_TOKEN`, `GITLAB_TOKEN`, `NPM_TOKEN`, `NODE_AUTH_TOKEN`, `PYPI_TOKEN`, `CARGO_REGISTRY_TOKEN`, `GEM_HOST_API_KEY`, `COMPOSER_AUTH`
* **サフィックス（末尾一致）で自動除去される変数**:
  `*_API_KEY`, `*_SECRET`, `*_SECRET_KEY`, `*_TOKEN`, `*_PASSWORD`, `*_PRIVATE_KEY`

---

### 4.3 Gate 3：機密ファイル・管理領域のハードブロックと多言語キャッシュ除外
通常承認モード・トラストモードを問わず、AIのうっかりミスを防ぐため以下のガードレールが常時機能します。

* **機密ファイルの読み書き・検索禁止**:
  * `.env`, `.env.*`（※テンプレート用の `.env.example`, `.env.sample`, `.env.template`, `.env.dist` は許可）
  * 秘密鍵・証明書・キーストア（`*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.ppk`, `*.keystore`, `*.jks`, `*.kdbx`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`）
  * パッケージマネージャ・フレームワーク・クラウド認証情報（`.npmrc`, `.pypirc`, `.netrc`, `auth.json`, `master.key`, `*credentials*.json`, `*service-account*.json`, `client_secret*.json`, `*.tfstate*`, `kubeconfig`）
  * ※上記ファイルを含むディレクトリの丸ごと削除・移動もブロックされます。
* **既存 `.devcontainer/`、`.gemio/`、`.git/`、および `*.egg-info/` の保護**:
  * 既存の `.devcontainer/` の変更・削除・ファイル追加、`.gemio/`（Skills・カスタムツール定義）配下への作成・変更・削除、`.git/` 内部への直接ファイル操作、およびパッケージ生成メタデータ領域（`*.egg-info/`）への直接変更・削除は禁止されています。
* **多言語ビルドキャッシュ・依存ディレクトリの自動走査除外 (`IGNORED_DIRS` / `IGNORED_DIR_PATTERNS`)**:
  * `get_structure`, `find_files`, `search_text` の実行時、`.git`, `node_modules`, `.venv`, `venv`, `target`, `.gradle`, `vendor`, `.next`, `.nuxt`, `.terraform`, `__pycache__`, `*.egg-info` 等を自動的にスキップし、高速な動作を維持します。

---

### 4.4 サンドボックス環境 & マウント保護状態の自動診断（`/sandbox` / `/status`）
開発者モード（`--dev`）の**起動時**、および対話中の **`/sandbox`（または `/status`, `/container`）コマンド実行時**に、コンテナの内部状態とマウント保護状態を自動検査し、一覧パネルで可視化します。

* **コンテナ基本設定の表示**: 接続アーキテクチャ、実行ユーザー（`uid`/`gid`、root実行警告の有無）、ネットワーク隔離状態（`none` / `bridge` 等）、**コンテナ環境変数内の機密キー検出有無（値は読み取らずキー名のみ表示：🟢 機密キー検出なし / 🔴 機密キー検出あり）**、RootFS（`--read-only`）・メモリ・CPU・PID上限。
* **ワークスペース内ファイル・フォルダの `ls -la` ＆ マウント診断（カテゴリ順ソート＆行色分け表示）**:
  * **🟢 安全（緑）**: `.env` 等の機密ファイルが `/dev/null`（キャラクタデバイス `1:3`）でマスクされている場合や、`.git/`・`.gemio/` 等が `ro`（読み取り専用）またはコンテナ専用ボリュームで隔離されている場合。
  * **🔴 危険・警告（赤）**: `.env`, `.envrc`, 秘密鍵（`.ssh/`, `*.pem`, `*.key`, `id_rsa*` 等）、クラウド・パッケージ認証情報（`.aws/`, `.gcloud/`, `.npmrc`, `master.key`, `*.tfstate*`, `*.tfvars` 等）がマスクされずコンテナ内から平文で読み取れる場合。
  * **🟡 注意・推奨案内（黄）**: `.git/`, `.devcontainer/`, `.gemio/`, `.vscode/`, `.venv/`, `node_modules/` 等がホストOSと `rw`（書き込み可能）で共有マウントされている場合。
  * **⚪ 読取専用 `ro`（グレー `dim`）**: 個別マウント等で読み取り専用（`ro`）に保護された通常設定ファイル（`Dockerfile`, `compose.yaml`, `pyproject.toml` 等）。
  * **🔵 作業対象 `rw`（水色 `cyan` 行ハイライト）**: コンテナ内から読み書き可能なAIの作業対象ファイル・フォルダ（`src/`, `tests/`, `docs/` 等）。`⚪ 読取専用 (ro)` の下段に分離してまとめられ、行全体がシアンで色分け表示されます。

---

## 5. `run_sandbox_command` ツールの動作仕様と制御

### 5.1 タイムアウト制御とハングアップ防止
* デフォルト `60秒`（最大 `300秒`）。超過時は子プロセスを自動 `kill()` し、部分出力とともにタイムアウトエラーをAIに返します。

### 5.2 長大ログ出力の自動トランケート（コンテキスト保護）
* 標準出力・標準エラー出力が **200行 または 10,000文字** を超えた場合、先頭と末尾を残して中央部分を省略して返します。

### 5.3 承認アクション（4択）・コマンド全文＆意図表示とサーキットブレーカー・プロセスキル
* **承認要求パネルの表示内容**:
  * **コマンドの意図説明（`description`）**: AIがコマンドを実行する目的・意図の短い説明（100字以内。最大150文字で切り詰め、未指定時は `(説明なし)` と表示）が `説明: ...` として表示されます。
  * **実行コマンド全文表示**: 複数行のPython検証スクリプト等であっても途中で省略せず、**実行コマンド全体（全文）**がそのまま表示されます。
* **承認アクション（4択）**:
  通常承認モードおよびエディットモードでは以下の4択から選択できます：
  * **`y` (許可)** / **`n` (拒否)** / **`a` (作業強制中止 / Abort)** / **`k` (プロセス即時終了 / Kill)**
* `n`（拒否）の直後にAIが相談なく連続して変更やコマンド実行を試みた場合は、**サーキットブレーカー**が作動して作業ループを強制停止します。

---

## 6. 実践ワークフロー：通常承認モードとトラストモードの使い分け

### 6.1 レシピ1：未知・外部のプロジェクトを扱う場合（通常承認モード）
外部から取得したリポジトリの中身を調査・動作確認する場合は、`--trust` を付けずに起動します。
```bash
python -m gemio ./external-repo --dev --container sandbox
```
ファイル変更やコマンド実行のたびに内容を確認し、不審な操作があれば `n` または `a` で即座に停止できます。

---

### 6.2 レシピ2：信頼できる自作プロジェクトを高速開発する場合（トラストモード `--dev --trust`）
自分で作成しているプロジェクトで、「テストが全部通るまで確認なしで自律的に実装・デバッグしてほしい」という場合の推奨フローです。

1. **作業用Gitブランチを作成**:
   ```bash
   git checkout -b feature/new-module
   ```
2. **開発者モード ＋ トラストモードで起動**:
   ```bash
   python -m gemio . --dev --container sandbox --trust
   ```
   *(対話中に `/trust on` で有効化、`/trust off` で通常承認モードへ戻すことも可能です)*
3. **ゴールと検証コマンドを指示**:
   ```text
   新機能を実装し、単体テストを追加して pytest（または npm test / cargo test）がすべてパスするまで修正してください。
   ```
4. **完了後に `git diff` で差分を確認してコミット**します。

---

### 6.3 レシピ3：Web検索（`+WEB` / `/search`）とオフラインサンドボックス検証の連携
1. 通常プロンプトでそのまま実装・調査を指示すると、ホストOS側のAIが必要に応じて `web_search` ツールで最新仕様を調査します（送信前にプロンプト全文を確認できます。不要な場合はヘッダーの `+WEB` クリックまたは `/search off` で `-WEB` に切替可能）。また、事前に `/search <内容>` ➔ `[+ 資料に追加]` で前提資料として保持しておくこともできます。
2. 取得した最新仕様をもとに、オフライン（`--network none`）のサンドボックス内で安全にコード実装・テストを実行させます。

---

## 7. トラブルシューティング（エラーメッセージ別対処法）

* **`[SECURITY_ALERT] 稼働中のサンドボックス用Dockerコンテナが見つかりません`**:
  Dockerコンテナを起動するか、`--container <コンテナ名>` を指定してください。
* **`[SECURITY_ALERT] コンテナ内に '/var/run/docker.sock' が検出されました`**:
  コンテナから `/var/run/docker.sock` のマウントを除外してください。
* **`[SECURITY_ALERT] 特権モード (--privileged) / ホストネットワーク共有モード (--network host) で実行されているため...`**:
  `--privileged` や `--network host` を外してコンテナを起動し直してください。
* **診断パネル（`/sandbox`）で `🔴 コンテナ環境変数 (GEMINI_API_KEY, ...)` の危険アラートが表示される**:
  コンテナ内の環境変数（または `/proc/1/environ`）にAPIキーや機密トークンが設定されています。テスト対象アプリ自体の動作に必須でない限り、AIからシェル経由で読み取られるリスクを防ぐため、`compose.yaml` の `environment` / `env_file`、`devcontainer.json` の `remoteEnv` / `containerEnv`、またはシェルの環境変数から削除してコンテナを再起動してください（Dev Container内起動時は環境変数を削除すると起動時のセキュア入力プロンプトが利用できます）。

---

## 8. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-02 | AI Agent | 運用中 | 初版作成。開発者モードの2つの起動アーキテクチャ、3つのセーフティゲート、実践レシピを網羅。 |
| 2026-11-03 | AI Agent | 運用中 | 「トラストモード（`--trust` / `/trust`）」への刷新、`--network none` 推奨構成と `--network host` 遮断、`_is_running_in_container` の厳格化、および多言語開発環境向けの保護・除外仕様を反映。 |
| 2026-11-04 | AI Agent | 運用中 | Dev Container内起動（In-Container）モードにおけるAPIキー環境変数設定の禁止と起動時マスク入力（OS別コマンド案内付き）・`PR_SET_DUMPABLE=0` メモリ保護の仕様を反映し、Dev Container構築・運用手順を拡充。 |
| 2026-11-04 | AI Agent | 運用中 | `gemio` パッケージ化（`src/gemio/` 構成への移行）に伴い、ファイルパス表記および起動コマンド表記（`python -m gemio`）を更新。 |
| 2026-11-04 | AI Agent | 運用中 | 起動時および `/sandbox` (`/status`, `/container`) コマンドによるサンドボックス・マウント保護状態診断（`inspect_sandbox_status`）の仕様と標準オーバーレイマウント設定を反映。 |
| 2026-11-06 | AI Agent | 運用中 | 3段階の承認モードとしてエディットモード（`--edit` / `/edit`：ファイル作成・編集は自動承認、削除とコマンド実行は都度確認、拒否後の変更操作は全遮断）を追加・反映。 |
| 2026-11-07 | AI Agent | 運用中 | `/sandbox` 診断テーブルにおいて、`🔵 作業対象 (rw)` と `⚪ 読取専用 (ro)` の表示ブロック分離（ソート順整理）および `作業対象 (rw)` 行全体のシアン色分け仕様を反映。 |
| 2026-11-08 | AI Agent | 運用中 | パッケージ生成メタデータ領域（`*.egg-info`）の自動走査除外（`IGNORED_DIR_PATTERNS`）および書き込み・削除保護ガードレールの仕様を反映。 |
| 2026-11-10 | AI Agent | 運用中 | コンテナセキュリティの3段階境界基準統一に伴い、コンテナ環境変数内の各種APIキー・トークンを `in_container` / `host_docker` 両モードで検査（値は読み取らずキー名のみ検出）し、起動拒否ではなく強い警告（`🔴 危険`）として診断パネルに表示する仕様へ更新。 |
| 2026-11-11 | AI Agent | 運用中 | `run_sandbox_command` の承認パネルにおいて、コマンドの意図説明（`description`）と実行コマンド全体（省略なし）を表示する仕様を反映。 |
| 2026-11-14 | AI Agent | 運用中 | `.gemio/`（Skills・カスタムツール管理領域）の書き込み保護ガードレールおよび `/sandbox` マウント診断対象への追加を反映。 |
| 2026-11-23 | AI Agent | 運用中 | AIによるWeb検索ツール連携（`+WEB` / `-WEB`）および `/search` の任意資料化仕様に合わせてレシピ3の記述を更新。 |
