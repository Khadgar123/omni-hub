from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omni_hub.audit import AuditLogger
from omni_hub.builtins import build_default_registry
from omni_hub.models import OperationSpec, OperationStatus, RiskLevel
from omni_hub.runner import OperationRunner
from omni_hub.skills import SkillKind, SkillRegistry, SkillSpec, SkillStatus


class SkillRegistryTests(unittest.TestCase):
    def test_registers_skill_and_writes_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = SkillRegistry(tmpdir)
            output = registry.upsert(
                SkillSpec(
                    skill_id="url-capture",
                    name="URL Capture",
                    kind=SkillKind.CONNECTOR,
                    description="Capture HTTP pages into the inbox.",
                    entrypoint="operation:capture_url",
                    risk_level=RiskLevel.LOCAL_WRITE,
                    connectors=["web"],
                    tags=["capture", "web"],
                )
            )

            self.assertTrue((Path(tmpdir) / "registry/skills.json").exists())
            self.assertTrue((Path(tmpdir) / output["skill_card_path"]).exists())
            self.assertEqual(registry.get("url-capture").name, "URL Capture")

    def test_lists_with_filters_and_disables_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = SkillRegistry(tmpdir)
            registry.upsert(
                SkillSpec(
                    skill_id="memory-search",
                    name="Memory Search",
                    kind=SkillKind.MEMORY,
                    description="Search canonical memory.",
                    status=SkillStatus.ACTIVE,
                    tags=["memory"],
                ),
                write_card=False,
            )
            registry.upsert(
                SkillSpec(
                    skill_id="n8n-bridge",
                    name="n8n Bridge",
                    kind=SkillKind.WORKFLOW,
                    description="Call n8n workflows.",
                    status=SkillStatus.DRAFT,
                    tags=["workflow"],
                ),
                write_card=False,
            )

            self.assertEqual(len(registry.list(kind="memory")), 1)
            self.assertEqual(len(registry.list(status="draft")), 1)
            self.assertEqual(len(registry.list(tag="workflow")), 1)

            disabled = registry.disable("memory-search")

            self.assertEqual(disabled.status, SkillStatus.DISABLED)
            self.assertEqual(registry.get("memory-search").status, SkillStatus.DISABLED)

    def test_rejects_invalid_skill_id(self) -> None:
        with self.assertRaises(ValueError):
            SkillSpec(
                skill_id="Bad ID",
                name="Bad",
                kind=SkillKind.UTILITY,
                description="Invalid ID",
            )

    def test_operations_register_and_read_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = OperationRunner(
                build_default_registry(tmpdir),
                audit=AuditLogger(Path(tmpdir) / "audit.jsonl"),
            )

            register_result = runner.run(
                OperationSpec(
                    name="register_skill",
                    action="register",
                    payload={
                        "skill_id": "project-linker",
                        "name": "Project Linker",
                        "kind": "project",
                        "description": "Link notes to project context.",
                        "entrypoint": "operation:propose_knowledge",
                        "risk_level": "L1",
                        "tags": ["project"],
                    },
                    risk_level=RiskLevel.LOCAL_WRITE,
                )
            )
            self.assertEqual(register_result.status, OperationStatus.SUCCEEDED)

            get_result = runner.run(
                OperationSpec(
                    name="get_skill",
                    action="read",
                    payload={"skill_id": "project-linker"},
                    risk_level=RiskLevel.READ_ONLY,
                )
            )

            self.assertEqual(get_result.status, OperationStatus.SUCCEEDED)
            self.assertEqual(get_result.output["skill"]["kind"], "project")


if __name__ == "__main__":
    unittest.main()
