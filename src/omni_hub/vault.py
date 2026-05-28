from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .markdown import MarkdownDocument, parse_markdown


@dataclass(slots=True)
class VaultNoteSummary:
    path: str
    title: str
    metadata: dict[str, object]
    headings: list[str]
    tags: list[str]
    wiki_links: list[str]
    markdown_link_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class VaultReader:
    def __init__(self, workspace: Path | str = ".", vault_dir: str = "vault") -> None:
        self.workspace = Path(workspace).resolve()
        self.vault_root = self._safe_path(vault_dir)

    def list_notes(self, *, limit: int = 100) -> list[VaultNoteSummary]:
        notes = []
        for path in sorted(self.vault_root.rglob("*.md")):
            if self._is_hidden_or_runtime_path(path):
                continue
            document = parse_markdown(path, self.workspace)
            notes.append(self._summary(document))
            if len(notes) >= limit:
                break
        return notes

    def read_note(self, relative_path: str) -> MarkdownDocument:
        path = self._safe_path(relative_path)
        if path.suffix.lower() != ".md":
            raise ValueError("vault note must be a Markdown file")
        if not path.exists():
            raise FileNotFoundError(f"vault note does not exist: {relative_path}")
        return parse_markdown(path, self.workspace)

    def _summary(self, document: MarkdownDocument) -> VaultNoteSummary:
        return VaultNoteSummary(
            path=document.path,
            title=document.title,
            metadata=document.metadata,
            headings=document.headings,
            tags=document.tags,
            wiki_links=document.wiki_links,
            markdown_link_count=len(document.markdown_links),
        )

    def _safe_path(self, relative_path: str) -> Path:
        from ._storage import safe_workspace_path
        return safe_workspace_path(self.workspace, relative_path)

    def _is_hidden_or_runtime_path(self, path: Path) -> bool:
        parts = set(path.relative_to(self.workspace).parts)
        return ".obsidian" in parts or ".trash" in parts
