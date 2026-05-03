from __future__ import annotations

from pathlib import Path

from .connectors.web import build_resource_from_body, fetch_url
from .content_store import ContentStore
from .models import OperationSpec
from .proposals import ProposalStore, build_knowledge_proposal
from .registry import OperationRegistry
from .vault import VaultReader


def summarize_text(spec: OperationSpec) -> dict[str, str | int]:
    text = str(spec.payload.get("text", "")).strip()
    max_chars = int(spec.payload.get("max_chars", 800))
    summary = text[:max_chars].strip()
    if len(text) > max_chars:
        summary += "..."
    return {
        "summary": summary,
        "input_chars": len(text),
        "summary_chars": len(summary),
    }


def make_write_markdown(workspace: Path):
    workspace_root = workspace.resolve()

    def write_markdown(spec: OperationSpec) -> dict[str, str | int]:
        relative_path = str(spec.payload["path"])
        title = str(spec.payload.get("title", "")).strip()
        body = str(spec.payload.get("body", "")).strip()

        target = (workspace_root / relative_path).resolve()
        try:
            target.relative_to(workspace_root)
        except ValueError as exc:
            raise PermissionError("target path is outside the workspace")

        target.parent.mkdir(parents=True, exist_ok=True)
        content = f"# {title}\n\n{body}\n" if title else f"{body}\n"
        target.write_text(content, encoding="utf-8")

        return {
            "path": str(target.relative_to(workspace_root)),
            "bytes": target.stat().st_size,
        }

    return write_markdown


def make_capture_url(workspace: Path):
    workspace_root = workspace.resolve()

    def capture_url(spec: OperationSpec) -> dict[str, str]:
        url = str(spec.payload["url"]).strip()
        if not url:
            raise ValueError("url is required")

        fetch_enabled = bool(spec.payload.get("fetch", True))
        timeout_seconds = int(spec.payload.get("timeout_seconds", 20))
        max_bytes = int(spec.payload.get("max_bytes", 2_000_000))
        note = str(spec.payload.get("note", ""))

        if "html" in spec.payload:
            resource = build_resource_from_body(
                url,
                str(spec.payload["html"]),
                content_type="text/html",
            )
        elif "text" in spec.payload:
            resource = build_resource_from_body(
                url,
                str(spec.payload["text"]),
                content_type="text/plain",
            )
        elif fetch_enabled:
            resource = fetch_url(
                url,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
        else:
            resource = build_resource_from_body(
                url,
                "",
                content_type="text/plain",
            )

        stored = ContentStore(workspace_root).store(resource, note=note)
        return stored.to_dict()

    return capture_url


def make_list_vault_notes(workspace: Path):
    workspace_root = workspace.resolve()

    def list_vault_notes(spec: OperationSpec) -> dict[str, object]:
        limit = int(spec.payload.get("limit", 100))
        vault_dir = str(spec.payload.get("vault_dir", "vault"))
        notes = VaultReader(workspace_root, vault_dir=vault_dir).list_notes(limit=limit)
        return {
            "count": len(notes),
            "notes": [note.to_dict() for note in notes],
        }

    return list_vault_notes


def make_read_vault_note(workspace: Path):
    workspace_root = workspace.resolve()

    def read_vault_note(spec: OperationSpec) -> dict[str, object]:
        note_path = str(spec.payload["path"])
        document = VaultReader(workspace_root).read_note(note_path)
        data = document.to_dict()
        max_body_chars = int(spec.payload.get("max_body_chars", 4000))
        data["body"] = document.body[:max_body_chars]
        data["body_chars"] = len(document.body)
        return data

    return read_vault_note


def make_propose_knowledge(workspace: Path):
    workspace_root = workspace.resolve()

    def propose_knowledge(spec: OperationSpec) -> dict[str, object]:
        note_path = str(spec.payload["path"])
        document = VaultReader(workspace_root).read_note(note_path)
        proposal = build_knowledge_proposal(document)
        stored_paths = ProposalStore(workspace_root).store(proposal)
        output = proposal.to_dict()
        output.update(stored_paths)
        return output

    return propose_knowledge


def build_default_registry(workspace: Path | str = ".") -> OperationRegistry:
    workspace_path = Path(workspace)
    registry = OperationRegistry()
    registry.register("summarize_text", summarize_text)
    registry.register("write_markdown", make_write_markdown(workspace_path))
    registry.register("capture_url", make_capture_url(workspace_path))
    registry.register("list_vault_notes", make_list_vault_notes(workspace_path))
    registry.register("read_vault_note", make_read_vault_note(workspace_path))
    registry.register("propose_knowledge", make_propose_knowledge(workspace_path))
    return registry
