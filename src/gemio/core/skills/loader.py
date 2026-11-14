"""
Skills（専門手順・ワークフロー定義）ローダーモジュール。

ローカル環境情報の露出と冗長な探索処理を排除するため、読み込み元を以下の固定2箇所のみに限定します：
1. パッケージ標準固定ディレクトリ: `src/gemio/core/skills/builtin/<skill_name>/SKILL.md`
2. プロジェクト直下固定ディレクトリ: `<project_root>/.gemio/skills/<skill_name>/SKILL.md`

フロントマター（name, description, recommended_toolsets）のみを先行スキャンし、
Markdown本文は有効化時にのみ遅延ロード（Progressive Disclosure）します。
また、物理ファイルパスや生のOS例外メッセージは外部へ一切公開しません。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "builtin"
SKILL_FILENAME = "SKILL.md"
MAX_SKILL_FILE_BYTES = 256 * 1024  # 256 KB 上限


def _strip_quotes(value: str) -> str:
    val = value.strip()
    if len(val) >= 2 and (
        (val.startswith('"') and val.endswith('"'))
        or (val.startswith("'") and val.endswith("'"))
    ):
        return val[1:-1].strip()
    return val


def parse_skill_markdown(
    raw_text: str,
    fallback_name: str = "",
) -> tuple[dict[str, Any], str]:
    """
    SKILL.md のテキストから軽量 YAML フロントマター（`---` で囲まれた先頭ブロック）と
    Markdown 本文を分離・解析します（外部ライブラリ非依存）。
    """
    normalized = raw_text.replace("\r\n", "\n")
    meta: dict[str, Any] = {
        "name": fallback_name.strip(),
        "description": "",
        "recommended_toolsets": [],
    }
    body = normalized.strip()

    if normalized.startswith("---\n"):
        end_idx = normalized.find("\n---\n", 4)
        if end_idx == -1 and normalized.endswith("\n---"):
            end_idx = len(normalized) - 4
        if end_idx != -1:
            fm_block = normalized[4:end_idx]
            after_sep = end_idx + 5
            body = normalized[after_sep:].strip()

            current_list_key: str | None = None
            for raw_line in fm_block.splitlines():
                stripped = raw_line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                if stripped.startswith("- ") and current_list_key == "recommended_toolsets":
                    item = _strip_quotes(stripped[2:])
                    if item:
                        meta["recommended_toolsets"].append(item)
                    continue

                current_list_key = None
                if ":" not in stripped:
                    continue

                key, val = stripped.split(":", 1)
                key = key.strip().lower()
                val = val.strip()

                if key == "name":
                    parsed_name = _strip_quotes(val)
                    if parsed_name:
                        meta["name"] = parsed_name
                elif key == "description":
                    meta["description"] = _strip_quotes(val)
                elif key in ("recommended_toolsets", "toolsets"):
                    if not val:
                        current_list_key = "recommended_toolsets"
                    elif val.startswith("[") and val.endswith("]"):
                        inner = val[1:-1].strip()
                        if inner:
                            items = [
                                _strip_quotes(part)
                                for part in inner.split(",")
                                if _strip_quotes(part)
                            ]
                            meta["recommended_toolsets"].extend(items)
                    else:
                        items = [
                            _strip_quotes(part)
                            for part in val.split(",")
                            if _strip_quotes(part)
                        ]
                        meta["recommended_toolsets"].extend(items)

    if not meta["description"]:
        for line in body.splitlines():
            clean_line = line.strip().lstrip("#").strip()
            if clean_line:
                meta["description"] = clean_line[:160]
                break
    if not meta["description"]:
        meta["description"] = f"{meta['name']} スキル"

    return meta, body


@dataclass
class SkillSpec:
    """
    1つのスキルの論理メタデータと本文遅延ローダーを保持するデータクラス。
    ローカル環境情報の露出防止のため、物理ファイルパス属性は保持しません。
    """

    name: str
    description: str
    recommended_toolsets: list[str] = field(default_factory=list)
    _body_loader: Callable[[], str] = field(
        default=lambda: "",
        repr=False,
        compare=False,
    )

    def load_body(self) -> str:
        """スキルの詳細手順（Markdown本文）をロードして返します。"""
        return self._body_loader()


class SkillLoader:
    """
    パッケージ内の標準固定ディレクトリ（`builtin/`）およびプロジェクト直下の固定ディレクトリ
    （`<project_root>/.gemio/skills/`）の2箇所のみからスキル定義を読み込むローダークラス。
    """

    @staticmethod
    def _scan_single_fixed_root(
        skills_root: Path,
        boundary_root: Path,
        on_warning: Callable[[str], None] | None = None,
    ) -> list[SkillSpec]:
        if (
            not skills_root.exists()
            or skills_root.is_symlink()
            or not skills_root.is_dir()
        ):
            return []

        try:
            resolved_skills_root = skills_root.resolve()
            resolved_boundary = boundary_root.resolve()
            if not resolved_skills_root.is_relative_to(resolved_boundary):
                return []
            subdirs = sorted(resolved_skills_root.iterdir(), key=lambda p: p.name)
        except Exception:
            return []

        found: list[SkillSpec] = []

        for subdir in subdirs:
            folder_name = subdir.name
            if folder_name.startswith((".", "_")):
                continue
            if subdir.is_symlink() or not subdir.is_dir():
                continue

            skill_md = subdir / SKILL_FILENAME
            if not skill_md.exists() or skill_md.is_symlink() or not skill_md.is_file():
                continue

            try:
                resolved_md = skill_md.resolve()
                if resolved_md.parent.parent != resolved_skills_root:
                    continue
                if resolved_md.stat().st_size > MAX_SKILL_FILE_BYTES:
                    if on_warning:
                        on_warning(
                            f"スキル '{folder_name}' はサイズ上限を超えているためスキップしました。"
                        )
                    continue

                raw_text = resolved_md.read_text(encoding="utf-8")
                meta, _ = parse_skill_markdown(raw_text, fallback_name=folder_name)
                skill_name = str(meta["name"]).strip() or folder_name

                def _make_lazy_loader(target_file: Path, logical_name: str) -> Callable[[], str]:
                    def _load() -> str:
                        try:
                            if (
                                not target_file.exists()
                                or target_file.is_symlink()
                                or target_file.stat().st_size > MAX_SKILL_FILE_BYTES
                            ):
                                raise RuntimeError("invalid skill file")
                            text = target_file.read_text(encoding="utf-8")
                            _, parsed_body = parse_skill_markdown(
                                text, fallback_name=logical_name
                            )
                            return parsed_body
                        except Exception as exc:
                            # 物理パスを含まない論理エラーメッセージのみを送出
                            raise RuntimeError(
                                f"スキル '{logical_name}' の本文を読み込めませんでした。"
                            ) from None

                    return _load

                found.append(
                    SkillSpec(
                        name=skill_name,
                        description=str(meta["description"]),
                        recommended_toolsets=list(meta["recommended_toolsets"]),
                        _body_loader=_make_lazy_loader(resolved_md, skill_name),
                    )
                )
            except Exception:
                if on_warning:
                    on_warning(
                        f"スキル '{folder_name}' の読み込みに失敗したためスキップしました。"
                    )
                continue

        return found

    @classmethod
    def load_all(
        cls,
        project_root: str | Path | None = None,
        on_warning: Callable[[str], None] | None = None,
    ) -> list[SkillSpec]:
        """
        固定2箇所（パッケージ内 `builtin/` とプロジェクト直下 `.gemio/skills/`）から
        SkillSpec を読み込みます。同名スキルが存在する場合はプロジェクト側を優先します。
        """
        merged: dict[str, SkillSpec] = {}

        # 1. パッケージ内の標準固定ディレクトリ
        for spec in cls._scan_single_fixed_root(
            BUILTIN_SKILLS_DIR,
            BUILTIN_SKILLS_DIR,
            on_warning=on_warning,
        ):
            merged[spec.name] = spec

        # 2. プロジェクト直下の固定ディレクトリ (<project_root>/.gemio/skills/)
        if project_root is not None:
            try:
                resolved_proj = Path(project_root).resolve()
                proj_skills_dir = resolved_proj / ".gemio" / "skills"
                for spec in cls._scan_single_fixed_root(
                    proj_skills_dir,
                    resolved_proj,
                    on_warning=on_warning,
                ):
                    merged[spec.name] = spec
            except Exception:
                pass

        return list(merged.values())
