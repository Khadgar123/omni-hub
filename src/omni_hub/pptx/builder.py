"""PPTX outline + builder Protocol (v0.35)."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class Bullet:
    text: str
    level: int = 0                            # 0 = top-level bullet, 1+ = nested
    bold: bool = False
    italic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Slide:
    layout: str = "title-and-content"         # "title" | "title-and-content" | "two-content" | "section-header" | "blank"
    title: str = ""
    subtitle: str = ""
    bullets: list[Bullet] = field(default_factory=list)
    speaker_notes: str = ""
    image_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bullets"] = [b.to_dict() for b in self.bullets]
        return data


@dataclass(slots=True)
class DeckOutline:
    title: str
    author: str = ""
    audience: str = ""
    theme: str = "default"                    # "default" | "corporate" | "academic" | custom
    slides: list[Slide] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def slide_count(self) -> int:
        return len(self.slides)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["slides"] = [s.to_dict() for s in self.slides]
        return data


@dataclass(slots=True)
class PPTXResult:
    output_path: str
    slide_count: int
    builder_name: str
    bytes_written: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PPTXBuilder(Protocol):
    """Contract for every PPTX builder.

    Real implementations import ``pptx`` (python-pptx).  Main-repo
    stub raises ``NotImplementedError`` with a clear pointer.
    """

    name: str

    def available(self) -> bool: ...
    def render(self, outline: DeckOutline, output_path: Path) -> PPTXResult: ...


class StubPPTXBuilder:
    """Main-repo stub.  Always reports unavailable; clearer-failing
    than letting the import explode at call time."""

    name = "stub"
    harness_path = "agent-harness/integrations/pptx/"
    binary = "pptx-omni"                      # CLI shim name

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def render(self, outline: DeckOutline, output_path: Path) -> PPTXResult:
        if not self.available():
            raise NotImplementedError(
                f"PPTX rendering requires {self.harness_path} (python-pptx "
                f"wrapper exposing `{self.binary} build --outline-json - "
                f"--output <path>`).  Main repo is stdlib-only by design."
            )
        # When the broker IS installed, delegate via subprocess.
        import json
        process = subprocess.run(
            [self.binary, "build", "--outline-json", "-",
             "--output", str(output_path), "--theme", outline.theme],
            input=json.dumps(outline.to_dict()),
            capture_output=True, text=True, timeout=120,
        )
        if process.returncode != 0:
            raise RuntimeError(
                f"`{self.binary} build` failed (rc={process.returncode}): "
                f"{process.stderr[:500]}"
            )
        try:
            response = json.loads(process.stdout or "{}")
        except json.JSONDecodeError:
            response = {}
        return PPTXResult(
            output_path=str(output_path),
            slide_count=int(response.get("slide_count", outline.slide_count())),
            builder_name=str(response.get("builder_name", self.binary)),
            bytes_written=int(response.get("bytes_written", 0)),
            metadata=response.get("metadata") or {},
        )


__all__ = [
    "Bullet",
    "DeckOutline",
    "PPTXBuilder",
    "PPTXResult",
    "Slide",
    "StubPPTXBuilder",
]
