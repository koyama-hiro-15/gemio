# 📚 Gemio マニュアル・ガイドライン一覧

**作成日:** 2026年11月09日  
**最終更新日:** 2026年11月17日  
**ステータス:** 運用中  
**対象範囲:** `manuals/` 配下のエンドユーザー向け・コントリビューター向けマニュアル群（`01`〜`09`）  

### 【要約】
本ディレクトリには、Gemioの使い方を学ぶ**エンドユーザー向けマニュアル（`01_` 〜 `06_`）**と、内部アーキテクチャや拡張開発・テスト手順を解説する**コントリビューター向けリファレンス・ガイドライン（`07_` 〜 `09_`）**が読むべき順番に沿って整理されています。目的に応じて順にお読みください。

---

## 🗺️ おすすめの読み進め方（ロードマップ）

### 👤 エンドユーザー・利用者の方
1. **まずはGemioを動かして基本操作や全コマンドを知りたい方**
   👉 **[01_USER_MANUAL.md](./01_USER_MANUAL.md)** をお読みください。
2. **Dockerコンテナ内でコードのビルドやテスト（`pytest`, `npm test` 等）まで自動実行させたい方**
   👉 続けて **[02_DEVELOPER_MODE_MANUAL.md](./02_DEVELOPER_MODE_MANUAL.md)** ➔ **[03_DOCKER_CONTAINER_MANUAL.md](./03_DOCKER_CONTAINER_MANUAL.md)** の順にお読みください。
3. **自分のプロジェクトに合わせてDockerマウントや保護設定をカスタマイズしたい方**
   👉 **[04_SANDBOX_PROTECTION_GUIDELINE.md](./04_SANDBOX_PROTECTION_GUIDELINE.md)** で4分類の仕分け基準を確認してください。
4. **開発中に「こういう時はどうすればいい？」と迷った時**
   👉 **[05_DEVELOPMENT_QA.md](./05_DEVELOPMENT_QA.md)**（逆引きQ&A集）を参照してください。
5. **プロジェクト固有の規約（Skills）や独自Pythonツール（`.gemio/`）を追加したい方**
   👉 **[06_SKILLS_AND_TOOLS_MANUAL.md](./06_SKILLS_AND_TOOLS_MANUAL.md)** を参照してください。

### 🛠️ コントリビューター・ライブラリ組み込み開発者の方
1. **Gemioの全体アーキテクチャ・全モジュールの責務・内部仕様・公開APIを把握したい方**
   👉 **[07_ARCHITECTURE_REFERENCE.md](./07_ARCHITECTURE_REFERENCE.md)** をお読みください。
2. **新機能の追加・バグ修正・単体テスト作成・ドキュメント更新を行う方**
   👉 **[08_CONTRIBUTING_GUIDE.md](./08_CONTRIBUTING_GUIDE.md)** ➔ **[09_MANUAL_TEST_GUIDELINE.md](./09_MANUAL_TEST_GUIDELINE.md)** の順にお読みください。

---

## 📖 第1部：エンドユーザー向けマニュアル（`01` 〜 `06`）

| 順番 | マニュアルファイル | 対象読者 | 内容の要約 |
| :---: | :--- | :--- | :--- |
| **01** | **[01_USER_MANUAL.md](./01_USER_MANUAL.md)**<br>📘 ユーザーズマニュアル | **全員（必読）** | Gemioの概要、導入手順、基本操作（複数行入力・送信・クリア）、2つの動作モード、3段階の承認モード（通常／エディット `--edit`／トラスト `--trust`）、サーキットブレーカーと `ask_user`、全スラッシュコマンドの基本ガイド。 |
| **02** | **[02_DEVELOPER_MODE_MANUAL.md](./02_DEVELOPER_MODE_MANUAL.md)**<br>🛠️ 開発者モード詳細マニュアル | 開発者モード（`--dev`）利用者 | Docker / Dev Container サンドボックス内でシェルコマンド（`run_sandbox_command`）を実行する開発者モードの詳細仕様。2つの起動アーキテクチャ、APIキー保護機構、3つのセーフティゲート、`/sandbox` 診断パネルの見方を解説。 |
| **03** | **[03_DOCKER_CONTAINER_MANUAL.md](./03_DOCKER_CONTAINER_MANUAL.md)**<br>🐳 Docker サンドボックスコンテナ作成マニュアル | コンテナ環境を構築する方 | Gemioのセーフティゲートを満たす安全なDockerコンテナの構築手順。コマンド1発起動、`Dockerfile`、`compose.yaml`、VS Code Dev Containers（`.devcontainer`）のテンプレートと、多言語（Python / Node.js / Go / Rust）への応用ポイントを網羅。 |
| **04** | **[04_SANDBOX_PROTECTION_GUIDELINE.md](./04_SANDBOX_PROTECTION_GUIDELINE.md)**<br>🛡️ サンドボックス保護ガイドライン | サンドボックス設定を管理する方 | 「部屋の中（コード・テスト・文書）はAI、部屋の壁と鍵（Docker設定・Git・`.gemio`・依存定義・機密情報）は人間が管理」という原則に基づく、ファイルの4分類（① `rw` 作業領域、② `:ro` 保護領域、③ `.venv` 隔離、④ `/dev/null` 機密マスク）の判断基準。 |
| **05** | **[05_DEVELOPMENT_QA.md](./05_DEVELOPMENT_QA.md)**<br>💡 実践開発 Q&A | 日常の開発で活用する方 | 承認モードの使い分け、パッケージ追加、`.env` の扱い、PostgreSQL等のDBコンテナ連携（`internal: true`）、モノレポ構成、UID/GID問題、Git運用、コンテキスト管理（`/tokens`・`/clear`）など現場で迷いやすい疑問の解決ガイド。 |
| **06** | **[06_SKILLS_AND_TOOLS_MANUAL.md](./06_SKILLS_AND_TOOLS_MANUAL.md)**<br>🧩 Skills & カスタムツール拡張マニュアル | プロジェクトを拡張したい方 | `.gemio/skills/*/SKILL.md` による専門手順の段階的開示、`.gemio/tools/*.py` によるカスタムツール追加、実験的機能 `--dynamic-tools`（動的ツールセット）の使い方とセキュリティ保護仕様を解説。 |

---

## 🔧 第2部：コントリビューター向けリファレンス & ガイドライン（`07` 〜 `09`）

| 順番 | マニュアルファイル | 対象読者 | 内容の要約 |
| :---: | :--- | :--- | :--- |
| **07** | **[07_ARCHITECTURE_REFERENCE.md](./07_ARCHITECTURE_REFERENCE.md)**<br>🏗️ アーキテクチャ & 内部仕様リファレンス | **コントリビューター・組み込み開発者（必読）** | 3層レイヤー構造と単方向依存ルール、全モジュール責務マップ、`core` / `project` / `cli`（4層UI）の詳細内部仕様、CLI接続状態マトリクス、主要処理シーケンス、およびPythonライブラリとしての公開APIリファレンス。 |
| **08** | **[08_CONTRIBUTING_GUIDE.md](./08_CONTRIBUTING_GUIDE.md)**<br>🛠️ コントリビューション & 拡張開発ガイド | **コントリビューター** | 開発環境セットアップ、アーキテクチャ制約、機能拡張レシピ（コマンド・ツール・標準スキル・Core基盤API接続）、定数変更時の同期チェックリスト、全12単体テストモジュールの構成・作成ルール、ドキュメント運用規約。 |
| **09** | **[09_MANUAL_TEST_GUIDELINE.md](./09_MANUAL_TEST_GUIDELINE.md)**<br>📝 手動テストガイドライン | コントリビューター・品質検証担当者 | 自動テスト（`pytest`）ではカバーできない対話型CLI・3段階承認・`ask_user`・作業前提資料・Dockerサンドボックス・Skills連携について、`git worktree` を使った安全な隔離検証手順と手動テストケース一覧をまとめたガイドライン。 |

---

## 🔗 関連ドキュメント（内部設計書・開発履歴）

各機能の設計経緯や時系列の開発履歴については、[`docs/README.md`](../docs/README.md)（設計書インデックス）から参照してください。

* **[`docs/README.md`](../docs/README.md)**: 設計書・技術ドキュメント一覧インデックス（ステータス・レイヤー別逆引き）
* **[`docs/DOCUMENTATION_RULE.md`](../docs/DOCUMENTATION_RULE.md)**: 設計書・技術ドキュメント運用ルール（ADR型連番・ステータス規約）
* **[`docs/design/`](../docs/design/)**: 各機能の詳細設計書（`001_`〜`018_` 連番管理）
* **[`docs/tech_docs/`](../docs/tech_docs/)**: 技術調査資料・セキュリティ責任分界点マトリクス
* **[`docs/PROJECT_SUMMARY.md`](../docs/PROJECT_SUMMARY.md)**: プロジェクト改修作業の要約・開発履歴サマリー

---

## 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-09 | AI Agent | 運用中 | 初版作成（マニュアル類の `manuals/` フォルダ集約および連番化に伴い、目次・読書ロードマップを作成）。 |
| 2026-11-14 | AI Agent | 運用中 | `06_SKILLS_AND_TOOLS_MANUAL.md`、`07_ARCHITECTURE_REFERENCE.md`、`08_CONTRIBUTING_GUIDE.md` の新設および手動テストガイドラインの `09_MANUAL_TEST_GUIDELINE.md` への繰り下げに伴い、エンドユーザー編（`01`〜`06`）とコントリビューター編（`07`〜`09`）の2部構成へ全面更新。 |
| 2026-11-17 | AI Agent | 運用中 | `docs/README.md` 新設および `docs/design/` の3桁連番管理導入に伴い、関連ドキュメントのナビゲーションを更新。 |
| 2026-11-24 | AI Agent | 運用中 | Web検索支援機能の全面廃止（`#018`）に伴い、`09` の要約および設計書連番表記を更新。 |
