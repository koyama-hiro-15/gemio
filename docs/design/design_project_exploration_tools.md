# プロジェクト構造・ファイル探索ツール（`get_structure` / `find_files` / `search_text`）機能設計書

**作成日:** 2026年11月06日  
**最終更新日:** 2026年11月06日  
**ステータス:** 実装完了  
**対象システム:** `src/gemio/project/base.py`, `src/gemio/core/prompts.py`, `tests/test_project_base.py`  

### 【要約】
中〜大規模なプロジェクトや大量のアセット・ログを含むディレクトリにおいて、ファイル一覧や全文検索の結果がコンテキストウィンドウを圧迫するリスクを防ぐとともに、単純な件数打ち切りによって「後半のフォルダやファイルが認識されず放置される問題」を構造的に解消するため、`get_structure` の階層・範囲指定拡張、`find_files`（ファイル名検索）の新規追加、および `search_text` の範囲指定・未表示フォルダ内訳サマリ機能を定義します。

---

## 1. 背景と解決すべき課題

1. **巨大ディレクトリによるコンテキスト圧迫とI/O負荷**:
   従来の `get_structure` および `search_text` は `self.project_path.rglob("*")` で全階層を一括走査し、件数上限なしで全件を返却していた。そのため、`IGNORED_DIRS`（`node_modules`, `.venv` 等）以外のデータフォルダ（`data/`, `assets/`, `logs/`, `dist/` 等）に数千個のファイルが存在する場合、1回のツール呼び出しでコンテキストが溢れるリスクがあった。
2. **単純な件数打ち切りによる「後半フォルダ・ファイルの見落とし（放置）」**:
   標準の深さ優先・辞書順（`rglob`）で先頭N件のみを切り出すと、アルファベット順で前半にあるフォルダ（例: `assets/`）だけで上限に達してしまい、後半にある重要なフォルダ（例: `src/`, `tests/`）の存在自体をAIが認知できないまま作業を終えてしまう危険がある。
3. **ファイル名・拡張子でピンポイントに探す手段の不足**:
   コード内の文字列を検索する `search_text`（`grep` 相当）は存在するが、ファイル名やグロブパターンで探す `find_files`（`find` / `glob` 相当）が存在しなかったため、特定のファイルを探す際にも `get_structure` で全体を取得する必要があった。

---

## 2. 設計方針（コンテキスト保護と見落とし防止の両立）

1. **幅優先探索（浅い階層優先）と階層上限（`max_depth`）の導入 (`get_structure`)**:
   * 浅い階層（深さ1 ➔ 深さ2 ➔ ...）から順番にリスト化することで、トップレベルや浅い階層のフォルダが打ち切りによって消えることを完全に防ぐ。
   * `max_depth`（デフォルト: `3`）に達したフォルダに配下のファイル・サブフォルダが存在する場合は、単に省略するのではなく **`(※配下に未表示 N 件 -> target_dir='...' で展開可)`** と付記し、その下に何件あるかを可視化する。
2. **除外ディレクトリの事前枝刈り（Pruning）による高速化**:
   * `rglob("*")` で全ファイルを走査してからフィルタするのではなく、ディレクトリ走査時に `IGNORED_DIRS` および `SECRET_DIR_NAMES` をその場で枝刈りし、巨大ディレクトリ内部への無駄なディスクI/Oを発生させない。
3. **上限超過時の「未表示フォルダ内訳サマリ」の自動付与 (`get_structure`, `find_files`, `search_text`)**:
   * 最大件数上限を超えた場合、「全何件中何件を表示したか」だけでなく、**「表示しきれなかった残りの項目がどのフォルダに何件残っているか」の内訳リスト**を末尾に付与する。
   * これにより、AIは「まだ `tests/` に15件、`docs/` に8件の未確認対象が残っている」と正確に把握でき、`target_dir` 引数を使って段階的に漏れなく回収できる。
4. **通常時（上限以内）の後方互換性の維持**:
   * 深さ・件数ともに上限以内に収まる小〜中規模プロジェクト（既存の単体テスト環境を含む）では、従来と全く同じ形式のリスト（`list[str]` / `list[dict]`）を返却し、既存コードやテストとの互換性を維持する。

---

## 3. モジュール別詳細設計

### 3.1 定数定義 (`src/gemio/project/base.py`)

```python
DEFAULT_STRUCTURE_MAX_DEPTH = 3   # get_structure のデフォルト最大階層深さ
MAX_STRUCTURE_ENTRIES = 200       # get_structure の1回あたりの最大表示件数
MAX_FIND_RESULTS = 200            # find_files の1回あたりの最大表示件数
MAX_SEARCH_RESULTS = 100          # search_text の1回あたりの最大表示マッチ件数
```

---

### 3.2 `get_structure` の改修仕様 (`src/gemio/project/base.py`)

#### (1) メソッドシグネチャ
```python
    def get_structure(
        self,
        target_dir: str = ".",
        max_depth: int = DEFAULT_STRUCTURE_MAX_DEPTH,
        include_dirs: bool = True,
    ) -> list[str] | str:
        """
        プロジェクト内の指定ディレクトリ配下のファイルおよびフォルダ構成を取得します。
        （.git や node_modules, .venv 等の除外領域や機密ファイルは自動除外されます）

        Args:
            target_dir: 調査対象のディレクトリパス（プロジェクトルートからの相対パス、デフォルトは "."）。
            max_depth: 展開する階層の深さ上限（デフォルトは 3）。さらに深い階層があるフォルダには未表示件数が付記されます。
            include_dirs: ディレクトリ自体も一覧に含めるかどうか（デフォルトは True）。

        Returns:
            プロジェクトルートからの相対パス文字列のリスト（未展開フォルダや上限超過時は案内情報付き）、
            またはエラーメッセージ文字列。
        """
```

#### (2) 処理ロジック
1. **パス検証**:
   * `base_dir = self._verify_and_get_path(target_dir, is_write=False)` を実行。
   * 存在しない場合は `"[ERROR] ディレクトリが存在しません: {target_dir}"`、ディレクトリでない場合は `"[ERROR] 指定されたパスはディレクトリではありません: {target_dir}"` を返す。
   * `effective_depth = max(1, int(max_depth))` とする。
2. **幅優先（浅い階層順）での走査と深さ制御**:
   * `base_dir` 配下の有効なエントリ（`_is_ignored_dir`・`_is_secret_file`・プロジェクト外シンボリックリンクを除いたもの）を走査する。
   * `base_dir` からの相対深さ `depth = len( path.relative_to(base_dir).parts )` を基準とする。
   * **並び順**: `(depth, str(rel_to_project))` の順（すなわち浅い階層から順、同じ深さでは辞書順）で並べることで、トップレベルおよび上位階層の構造が必ず先頭に含まれるようにする。
   * **`depth == effective_depth` のディレクトリ**:
     そのディレクトリの直下（さらに深い階層 `depth > effective_depth`）に有効なファイルやフォルダが `hidden_count` 件（`hidden_count > 0`）存在する場合：
     * `include_dirs=True` の場合は、そのディレクトリの表示文字列を
       `f"{rel_path_str} (※配下に未表示 {hidden_count} 件 -> target_dir='{rel_path_str}' で展開可)"`
       として記録する。
     * `include_dirs=False` の場合も、ファイルが見落とされないよう同様の未展開通知エントリを含める。
3. **件数上限（`MAX_STRUCTURE_ENTRIES`）超過時の処理**:
   * 表示対象リストが `MAX_STRUCTURE_ENTRIES`（200件）を超えた場合、先頭200件を切り出す。
   * 切り捨てられた残りのエントリについて、`base_dir` 直下のサブディレクトリ単位で未表示件数を集計し、末尾に以下の1要素を追加する：
     ```text
     [省略通知] 表示上限（200件）に達したため、残り {omitted_count} 件を省略しました。未表示項目が多いフォルダ: {folder_summary} -> 詳細を確認するには get_structure(target_dir='フォルダパス') を指定してください。
     ```

---

### 3.3 `find_files`（新規ツール）の仕様 (`src/gemio/project/base.py`)

#### (1) メソッドシグネチャ
```python
    def find_files(
        self,
        pattern: str = "*",
        target_dir: str = ".",
    ) -> list[str] | str:
        """
        プロジェクト内の指定ディレクトリ配下から、ファイル名またはパスパターン（ワイルドカード可）に
        一致するファイルを検索します。（除外ディレクトリや機密ファイルはスキップされます）

        Args:
            pattern: 検索するファイル名またはグロブパターン（例: "*.py", "*test*.py", "docs/*.md"）。
            target_dir: 検索開始ディレクトリ（プロジェクトルートからの相対パス、デフォルトは "."）。

        Returns:
            一致したファイルの相対パス文字列のリスト（上限超過時は末尾に未表示フォルダ内訳を付与）、
            またはエラーメッセージ文字列。
        """
```

#### (2) 処理ロジック
1. **パスおよびセキュリティ検証**:
   * `base_dir = self._verify_and_get_path(target_dir, is_write=False)` で対象ディレクトリを検証。
   * `os.walk` 等で走査する際、`IGNORED_DIRS` および `SECRET_DIR_NAMES` に該当するディレクトリは `dirs[:]` から即座に除外（枝刈り）する。
   * ファイルごとに `_is_secret_file` およびシンボリックリンク解決後のパス検証（プロジェクト内かつ非機密ファイルであること）を行い、機密ファイルが一覧に漏洩しないことを保証する。
2. **パターンマッチング**:
   * `clean_pattern = pattern.strip() or "*"`
   * パターンに `/`（または `\`）が含まれている場合は、プロジェクトルートからの相対パス（および `base_dir` からの相対パス）に対して `fnmatch.fnmatchcase(path_str.lower(), clean_pattern.lower())` で判定。
   * パターンにパス区切りが含まれない場合（例: `"*.py"`, `"agent"`）は、ファイル名 `file_name.lower()` に対して `fnmatch` 判定（ワイルドカード記号 `*`, `?`, `[` が含まれない場合は部分一致 `clean_pattern.lower() in file_name.lower()` も許容してAIの検索ミスを防ぐ）。
3. **件数上限（`MAX_FIND_RESULTS = 200`）と未表示フォルダ内訳サマリ**:
   * マッチしたファイル数が `MAX_FIND_RESULTS` 以下の場合は、相対パス文字列のリストをそのまま返す。
   * 超過した場合は、先頭200件のリストの末尾に、未表示ファイルがどのフォルダに何件残っているかを集計したサマリ文字列を追加して返す：
     ```text
     [省略通知] 全 {total} 件中、先頭 200 件を表示しました。未表示の {omitted} 件が残っているフォルダ: {folder_summary} -> 漏れなく確認するには find_files(pattern='{pattern}', target_dir='フォルダパス') を指定してください。
     ```
4. **`Project.agent_tools()` への登録**:
   * `Project.agent_tools()` のリストに `self.find_files` を追加する。

---

### 3.4 `search_text` の改修仕様 (`src/gemio/project/base.py`)

#### (1) メソッドシグネチャ
```python
    def search_text(
        self,
        pattern: str,
        target_dir: str = ".",
    ) -> list[dict] | str:
        """
        プロジェクト内のファイルを対象に、指定された正規表現パターンに一致する行を検索します。
        （機密ファイルや .git, node_modules 等の除外ディレクトリはスキップされます）

        Args:
            pattern: 検索する正規表現パターン（Pythonのreモジュール準拠）。
            target_dir: 検索対象ディレクトリ（プロジェクトルートからの相対パス、デフォルトは "."）。

        Returns:
            マッチした箇所の情報（file_path, line_number, line_str）の辞書リスト
            （上限超過時は末尾に未表示フォルダ内訳の notice 辞書を含む）、またはエラーメッセージ文字列。
        """
```

#### (2) 処理ロジック
1. **対象ディレクトリ検証と枝刈り走査**:
   * `base_dir = self._verify_and_get_path(target_dir, is_write=False)` を検証。
   * `IGNORED_DIRS` / `SECRET_DIR_NAMES` を枝刈りしながら対象ファイルを走査し、機密ファイルやプロジェクト外シンボリックリンクをスキップする。
2. **件数上限（`MAX_SEARCH_RESULTS = 100`）と未表示フォルダ内訳サマリ**:
   * マッチ件数が `MAX_SEARCH_RESULTS`（100件）以下の場合は、従来通り `[{"file_path": ..., "line_number": ..., "line_str": ...}, ...]` を返す。
   * 100件を超えた場合は、先頭100件の結果リストの末尾に、残りのマッチ箇所が存在するフォルダと件数の内訳を格納した以下の辞書要素を追加する：
     ```python
     {
         "notice": (
             f"[省略通知] 全 {total_matches} 件中、先頭 {MAX_SEARCH_RESULTS} 件を表示しました。"
             f"未表示の {omitted_count} 件が残っているフォルダ: {folder_summary} "
             f"-> 漏れなく確認するには search_text(pattern='{pattern}', target_dir='フォルダパス') を指定してください。"
         )
     }
     ```

---

### 3.5 システムインストラクションの更新 (`src/gemio/core/prompts.py`)

`DEFAULT_SYSTEM_INSTRUCTION` の【コード参照に関するルール】に以下の指針を追加します。

```text
- プロジェクトのファイル構成やコードを探索する際は、`get_structure`、`find_files`、`search_text` を適切に使い分けること。
- これらのツールの実行結果に「未表示件数」や「[省略通知]」が含まれている場合、作業に関連する可能性のあるフォルダやファイルを放置せず、`target_dir` 引数を指定して段階的に掘り下げ、漏れなく確認すること。
```

---

## 4. テスト計画 (`tests/test_project_base.py`)

1. **既存テストの後方互換性確認**:
   - 既存の `test_create_directory_and_structure_ignores_bloat_dirs` および `test_security_secret_files_blocked_and_env_templates_allowed` がそのまま合格すること。
2. **`get_structure` の深さ制限・幅優先・`target_dir` 展開テスト**:
   - `max_depth` を超える深い階層がある場合、上位フォルダはすべて表示され、深さ上限のフォルダに `(※配下に未表示 N 件 -> target_dir='...' で展開可)` が表示されること。
   - `get_structure(target_dir="...")` を指定することで、そのサブディレクトリ配下を掘り下げて取得できること。
   - エントリ数が `MAX_STRUCTURE_ENTRIES` を超えた場合、先頭分＋未表示フォルダ内訳の `[省略通知]` が返ること。
3. **`find_files` の動作およびセキュリティ検証テスト**:
   - ワイルドカード（`"*.py"`）や部分文字列、サブディレクトリ指定（`target_dir`）で目的のファイルが検索できること。
   - `.env` 等の機密ファイルや `node_modules` 等の除外ディレクトリ内のファイルが結果に含まれないこと。
   - 上限（`MAX_FIND_RESULTS`）超過時に未表示フォルダの内訳サマリが付与されること。
4. **`search_text` の `target_dir` 絞り込みと上限・内訳サマリ検証テスト**:
   - `target_dir` 指定時に該当フォルダ配下のみが検索されること。
   - マッチ件数が `MAX_SEARCH_RESULTS` を超えた場合、先頭100件＋未表示フォルダ内訳を含む `notice` 要素が付与されること。
5. **`target_dir` に対するパストラバーサル・機密領域保護テスト**:
   - `get_structure(target_dir="../")`、`find_files(target_dir=".git")`、`search_text("x", target_dir=".ssh")` などが `[ERROR]`（`SECURITY_ALERT`）として安全にブロックされること。

---

## 5. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-06 | AI Agent | 実装完了 | 初版作成および実装完了。`get_structure` の幅優先・深さ制限・`target_dir` 対応、`find_files` の新規追加、`search_text` の上限・未表示フォルダ内訳サマリ仕様を定義・実装。 |
