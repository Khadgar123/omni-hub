# PPTX Builder Integration

**Status**: scaffolded (v0.35).  No python-pptx wrapper committed yet.

## Broker contract

The main-repo stub at `src/omni_hub/pptx/builder.py::StubPPTXBuilder`
expects a `pptx-omni` binary on `PATH` exposing:

```bash
pptx-omni build --outline-json - --output <path.pptx> [--theme <name>]
# stdin: DeckOutline dict (see src/omni_hub/pptx/builder.py)
# stdout JSON: {"slide_count": int, "builder_name": str, "bytes_written": int, "metadata": {...}}
# exit code 0 on success, non-zero with stderr on failure.

pptx-omni themes --json
# lists known themes: [{"name": "default"}, {"name": "corporate"}, ...]

pptx-omni status --json
# probe; outputs {"python_pptx_version": "1.0.x", "available": true}
```

## Implementation sketch

```python
# agent-harness/integrations/pptx/cli/pptx_omni.py
import argparse, json, sys
from pptx import Presentation
from pptx.util import Pt

def build(outline: dict, output_path: str, theme: str) -> dict:
    prs = Presentation()
    for slide_data in outline["slides"]:
        layout = prs.slide_layouts[1]   # title + content
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = slide_data["title"]
        if slide_data.get("bullets"):
            body = slide.placeholders[1].text_frame
            for bullet in slide_data["bullets"]:
                p = body.add_paragraph()
                p.text = bullet["text"]
                p.level = bullet.get("level", 0)
        if slide_data.get("speaker_notes"):
            slide.notes_slide.notes_text_frame.text = slide_data["speaker_notes"]
    prs.save(output_path)
    import os
    return {
        "slide_count": len(outline["slides"]),
        "builder_name": "pptx-omni/python-pptx",
        "bytes_written": os.path.getsize(output_path),
    }
```

About 200 LOC total once theme switching, image insertion, two-content
layouts, and academic-citation-block rendering are added.

## Why this is in agent-harness, not main repo

`python-pptx` brings in `lxml` + ~3MB of XML / typesetting code.  The
main omni-hub repo is dependency-free by design (see AGENTS.md rule
#1).  Pinning the SDK as an installable side-package under
`agent-harness/integrations/pptx/cli/` keeps that rule intact while
letting the main repo orchestrate via subprocess.

## TODO

1. `git submodule add https://github.com/scanny/python-pptx.git agent-harness/integrations/pptx/python-pptx`
   (or `pipx install python-pptx` if you prefer not to vendor).
2. Implement `agent-harness/integrations/pptx/cli/pptx_omni.py` per the
   contract above.
3. `pipx install -e ./cli` so `pptx-omni` lands on PATH.
4. `omni-hub pptx-build --outline-json file://demo.json --out demo.pptx`
   should work end-to-end.

## Decision log

* 2026-05-28: Decision = **wrap python-pptx via CLI shim**, do NOT
  generate raw OOXML from LLM.  Matches Anthropic official `pptx`
  Skill pattern; avoids token waste + brittle XML.
