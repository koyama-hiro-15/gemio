"""
作業前提資料（Reference Materials）管理モジュール。

ローカルファイルパスやOS環境情報を一切保持せず、SHA-256ハッシュ値を用いて
重複登録の防止および同一元データの更新を行います。
AI側からはWeb検索結果（WEB_SEARCH）のみが登録され、検索結果以外の資料追加や削除はユーザー操作専用とします。
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
from urllib.parse import unquote

from gemio.core.models import get_now_time_text


class MaterialKind(str, Enum):
    """作業前提資料の種別を表す列挙型。"""

    WEB_SEARCH = "web_search"
    USER_DOCUMENT = "user_document"


def compute_sha256(text: str) -> str:
    """与えられた文字列の SHA-256 ハッシュ値（16進文字列）を計算して返します。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ReferenceMaterial:
    """
    セッション内で保持される1件の作業前提資料データクラス。
    ローカル環境情報漏洩防止のため、物理ファイルパスやホスト側属性は一切保持しません。
    """

    material_id: str
    kind: MaterialKind
    title: str
    content: str
    content_hash: str
    source_hash: str | None = None
    sources: list[dict[str, str]] = field(default_factory=list)
    created_at: str = ""
    char_count: int = 0


class MaterialStore:
    """
    セッション内の作業前提資料を蓄積・管理し、プロンプト先頭注入用テキストを生成するストア。
    """

    def __init__(self) -> None:
        self._materials: list[ReferenceMaterial] = []
        self._seq: int = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"m{self._seq}"

    def __len__(self) -> int:
        return len(self._materials)

    def __bool__(self) -> bool:
        return bool(self._materials)

    @property
    def total_char_count(self) -> int:
        """保持している全作業前提資料の合計文字数を返します。"""
        return sum(m.char_count for m in self._materials)

    def list_all(self) -> list[ReferenceMaterial]:
        """保持している全資料のリスト（コピー）を返します。"""
        return list(self._materials)

    def get(self, identifier: str | int) -> ReferenceMaterial | None:
        """
        1始まりの表示連番（int または数字文字列）あるいは material_id（例: 'm1'）で
        特定の作業前提資料を取得します。該当しない場合は None を返します。
        """
        if isinstance(identifier, int):
            idx = identifier - 1
            return self._materials[idx] if 0 <= idx < len(self._materials) else None

        cleaned = identifier.strip()
        if cleaned.isdigit():
            idx = int(cleaned) - 1
            return self._materials[idx] if 0 <= idx < len(self._materials) else None

        lowered = cleaned.lower()
        for mat in self._materials:
            if mat.material_id.lower() == lowered:
                return mat
        return None

    @staticmethod
    def extract_source_display(title: str, url: str) -> str:
        """
        Web参照元の表示用文字列（タイトル + 判別可能なドメイン）を生成します。
        長大な暗号化リダイレクトURL（vertexaisearch等）はプロンプトから除外します。
        """
        clean_title = (title or "").strip()
        domain = ""
        if url:
            try:
                decoded_url = unquote(url)
                match = re.search(
                    r"https?://(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
                    decoded_url,
                )
                if match:
                    extracted = match.group(1)
                    if "vertexaisearch" not in extracted and "google" not in extracted:
                        domain = extracted
            except Exception:
                pass

        if clean_title and domain and domain not in clean_title:
            return f"{clean_title} ({domain})"
        if clean_title:
            return clean_title
        if domain:
            return domain
        return "Web参照元"

    def add_search_result(
        self,
        query: str,
        response_text: str,
        sources: list[dict[str, str]] | None = None,
        created_at: str = "",
    ) -> tuple[ReferenceMaterial, bool]:
        """
        Web検索結果を作業前提資料として追加します。
        同一本文ハッシュ（content_hash）の資料が既に存在する場合は重複追加せず (既存資料, False) を返します。
        """
        clean_content = response_text.strip()
        c_hash = compute_sha256(clean_content)
        for existing in self._materials:
            if existing.content_hash == c_hash:
                return existing, False

        mat = ReferenceMaterial(
            material_id=self._next_id(),
            kind=MaterialKind.WEB_SEARCH,
            title=query.strip(),
            content=clean_content,
            content_hash=c_hash,
            source_hash=None,
            sources=list(sources or []),
            created_at=created_at or get_now_time_text(),
            char_count=len(clean_content),
        )
        self._materials.append(mat)
        return mat, True

    def add_user_document(
        self,
        title: str,
        content: str,
        source_hash: str | None = None,
        created_at: str = "",
    ) -> tuple[ReferenceMaterial, str]:
        """
        ユーザーが指定したテキスト資料（ファイル読み込み内容やメモ）を追加・更新します（ユーザー操作専用）。

        Returns:
            (対象資料, ステータス文字列: "added" | "updated" | "duplicate")
        """
        clean_title = title.strip() or "ユーザー参照資料"
        clean_content = content.strip()
        c_hash = compute_sha256(clean_content)

        for existing in self._materials:
            if existing.content_hash == c_hash:
                return existing, "duplicate"

        if source_hash:
            for existing in self._materials:
                if (
                    existing.kind == MaterialKind.USER_DOCUMENT
                    and existing.source_hash == source_hash
                ):
                    existing.title = clean_title
                    existing.content = clean_content
                    existing.content_hash = c_hash
                    existing.created_at = created_at or get_now_time_text()
                    existing.char_count = len(clean_content)
                    return existing, "updated"

        mat = ReferenceMaterial(
            material_id=self._next_id(),
            kind=MaterialKind.USER_DOCUMENT,
            title=clean_title,
            content=clean_content,
            content_hash=c_hash,
            source_hash=source_hash,
            sources=[],
            created_at=created_at or get_now_time_text(),
            char_count=len(clean_content),
        )
        self._materials.append(mat)
        return mat, "added"

    def remove(self, identifier: str | int) -> ReferenceMaterial | None:
        """
        1始まりの表示連番（int または数字文字列）あるいは material_id（例: 'm1'）を指定して、
        不要な作業前提資料を1件削除します（ユーザー操作専用）。
        """
        if isinstance(identifier, int):
            idx = identifier - 1
            if 0 <= idx < len(self._materials):
                return self._materials.pop(idx)
            return None

        cleaned = identifier.strip()
        if cleaned.isdigit():
            idx = int(cleaned) - 1
            if 0 <= idx < len(self._materials):
                return self._materials.pop(idx)
            return None

        lowered = cleaned.lower()
        for i, mat in enumerate(self._materials):
            if mat.material_id.lower() == lowered:
                return self._materials.pop(i)
        return None

    def clear(self, kind: MaterialKind | None = None) -> int:
        """
        保持している作業前提資料をクリアし、破棄した件数を返します。
        kind が指定された場合は該当種別の資料のみを破棄します。
        """
        if kind is None:
            count = len(self._materials)
            self._materials.clear()
            return count

        before = len(self._materials)
        self._materials = [m for m in self._materials if m.kind != kind]
        return before - len(self._materials)

    def format_for_display(self) -> str:
        """
        ユーザーが保持中の作業前提資料を再表示・コピー確認するための Markdown 文字列を生成します。
        """
        if not self._materials:
            return ""

        blocks: list[str] = []
        for idx, mat in enumerate(self._materials, 1):
            kind_label = "Web検索" if mat.kind == MaterialKind.WEB_SEARCH else "ユーザー資料"
            header = f"### [{idx}] ({mat.material_id}) [{kind_label}] {mat.title}"
            if mat.created_at:
                header += f" ({mat.created_at} / {mat.char_count:,} 文字)"
            lines = [header]
            if mat.kind == MaterialKind.WEB_SEARCH and mat.sources:
                src_list = [
                    f"- {self.extract_source_display(s.get('title', ''), s.get('url', ''))}"
                    for s in mat.sources
                ]
                lines.append("\n**参照元:**\n" + "\n".join(src_list))
            lines.append(f"\n{mat.content}")
            blocks.append("\n".join(lines))

        return "\n\n---\n\n".join(blocks)

    def format_for_prompt(self) -> str:
        """
        蓄積された全資料をプロンプト先頭インジェクション用テキストとして整形します。
        ローカルファイルパスやリダイレクトURLは一切出力しません。
        """
        if not self._materials:
            return ""

        lines = [
            "【作業前提資料】",
            "※ 以下はWeb検索による外部最新情報、またはユーザーが明示的に提供した客観的な参照資料です。",
            "現在のローカル環境や確定仕様ではなく、外部ドキュメント・提案仕様・ドラフト・特定バージョン/環境等の前提条件が含まれる場合があります。",
            "手元の現行環境（安定版）や既存コードと差異がある場合でも、自己矛盾やエラーと誤認せず、",
            "「資料の前提」と「手元の実態」の時間軸やスコープの違いとして客観的に整理して扱ってください。",
            "",
        ]

        for idx, mat in enumerate(self._materials, 1):
            lines.append("---")
            header = f"[参照資料 {idx}]"
            if mat.created_at:
                header += f" (取得・追加時刻: {mat.created_at})"
            lines.append(header)

            if mat.kind == MaterialKind.WEB_SEARCH:
                lines.append(f"■ 検索クエリ: {mat.title}")
                if mat.sources:
                    lines.append("■ 参照元情報:")
                    for s in mat.sources:
                        display = self.extract_source_display(
                            s.get("title", ""), s.get("url", "")
                        )
                        lines.append(f"  - {display}")
            else:
                lines.append(f"■ 資料タイトル: {mat.title}")

            lines.append("■ 資料内容:")
            lines.append(mat.content)
            lines.append("")

        lines.append("--------------------------------------------------")
        return "\n".join(lines)
