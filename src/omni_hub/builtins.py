from __future__ import annotations

from pathlib import Path

from .api_management import api_management_status
from .connectors.web import build_resource_from_body, fetch_url
from .content_store import ContentStore
from .memory import MemoryStore
from .models import OperationSpec, RiskLevel
from .proposals import ProposalStore, build_knowledge_proposal
from .queue import TaskQueue
from .registry import OperationRegistry
from .skill_intel import analyze_skill_set, recommend_skills
from .skills import SkillKind, SkillRegistry, SkillSpec, SkillStatus
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
            raise PermissionError("target path is outside the workspace") from exc

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
        # Flatten knowledge payload to top level so callers can read
        # output["entities"] / output["relations"] without descending into
        # payload (kept stable for the propose-note CLI consumers).
        output["entities"] = list(proposal.payload.get("entities", []))
        output["relations"] = list(proposal.payload.get("relations", []))
        output.update(stored_paths)
        return output

    return propose_knowledge


def make_digest_proposal(workspace: Path):
    workspace_root = workspace.resolve()

    def digest_proposal(spec: OperationSpec) -> dict[str, object]:
        proposal_ref = str(spec.payload["proposal"])
        proposal = ProposalStore(workspace_root).load(proposal_ref)
        result = MemoryStore(workspace_root).digest_proposal(proposal)
        return result.to_dict()

    return digest_proposal


def make_list_proposals(workspace: Path):
    workspace_root = workspace.resolve()

    def list_proposals(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        proposals = ProposalStore(workspace_root, create=False).list(
            state=payload.get("state"),
            kind=payload.get("kind"),
            limit=int(payload.get("limit", 50)),
        )
        return {
            "count": len(proposals),
            "proposals": [p.to_dict() for p in proposals],
        }

    return list_proposals


def make_approve_proposal(workspace: Path):
    workspace_root = workspace.resolve()

    def approve_proposal(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        proposal = ProposalStore(workspace_root).approve(
            str(payload["proposal_id"]),
            reason=str(payload.get("reason", "")),
            decided_by=str(payload.get("decided_by", "local-user")),
        )
        return proposal.to_dict()

    return approve_proposal


def make_reject_proposal(workspace: Path):
    workspace_root = workspace.resolve()

    def reject_proposal(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        proposal = ProposalStore(workspace_root).reject(
            str(payload["proposal_id"]),
            reason=str(payload.get("reason", "")),
            decided_by=str(payload.get("decided_by", "local-user")),
        )
        return proposal.to_dict()

    return reject_proposal


def make_enqueue_task(workspace: Path):
    workspace_root = workspace.resolve()

    def enqueue_task(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        queue = TaskQueue(workspace_root)
        task = queue.enqueue(
            lane=str(payload["lane"]),
            packet=dict(payload.get("packet", {})),
            domain_profile=str(payload.get("domain_profile", "")),
            idempotency_key=payload.get("idempotency_key"),
            available_at=payload.get("available_at"),
            max_attempts=int(payload.get("max_attempts", 3)),
        )
        return task.to_dict()

    return enqueue_task


def make_claim_task(workspace: Path):
    workspace_root = workspace.resolve()

    def claim_task(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        queue = TaskQueue(workspace_root)
        task = queue.claim(
            lane=str(payload["lane"]),
            claimed_by=payload.get("claimed_by"),
            visibility_timeout_sec=int(payload.get("visibility_timeout_sec", 600)),
        )
        return {"task": task.to_dict() if task is not None else None}

    return claim_task


def make_complete_task(workspace: Path):
    workspace_root = workspace.resolve()

    def complete_task(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        queue = TaskQueue(workspace_root)
        task = queue.complete(
            int(payload["task_id"]),
            output=payload.get("output"),
        )
        return task.to_dict()

    return complete_task


def make_fail_task(workspace: Path):
    workspace_root = workspace.resolve()

    def fail_task(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        queue = TaskQueue(workspace_root)
        task = queue.fail(
            int(payload["task_id"]),
            error=str(payload["error"]),
            backoff_base_sec=int(payload.get("backoff_base_sec", 60)),
            backoff_cap_sec=int(payload.get("backoff_cap_sec", 3600)),
        )
        return task.to_dict()

    return fail_task


def make_list_tasks(workspace: Path):
    workspace_root = workspace.resolve()

    def list_tasks(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        queue = TaskQueue(workspace_root, create=False)
        tasks = queue.list(
            state=payload.get("state"),
            lane=payload.get("lane"),
            limit=int(payload.get("limit", 50)),
        )
        return {
            "count": len(tasks),
            "counts_by_state": queue.counts_by_state(),
            "tasks": [t.to_dict() for t in tasks],
        }

    return list_tasks


def _make_build_report(workspace: Path, period: str):
    workspace_root = workspace.resolve()

    def build_report(spec: OperationSpec) -> dict[str, object]:
        from datetime import date as _date
        from pathlib import Path as _Path
        from . import reports as reports_mod

        payload = spec.payload
        anchor = _date.fromisoformat(str(payload["anchor"])) if payload.get("anchor") else None
        builder = {
            "daily": reports_mod.build_daily,
            "weekly": reports_mod.build_weekly,
            "monthly": reports_mod.build_monthly,
        }[period]
        body, ctx = builder(anchor=anchor, workspace=workspace_root)
        out_path = (
            _Path(str(payload["write_to"]))
            if payload.get("write_to")
            else reports_mod.default_output_path(workspace_root, ctx)
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body, encoding="utf-8")
        return {
            "period": period,
            "anchor": ctx.anchor_date.isoformat(),
            "output": str(out_path),
            "bytes": len(body.encode("utf-8")),
        }

    return build_report


def make_schedule_tick(workspace: Path):
    workspace_root = workspace.resolve()

    def schedule_tick(spec: OperationSpec) -> dict[str, object]:
        from datetime import date

        payload = spec.payload
        period = str(payload.get("period", "daily")).lower()
        anchor = str(payload.get("anchor") or date.today().isoformat())
        queue = TaskQueue(workspace_root)

        # Each plan entry enqueues a task whose packet drives a builtin
        # operation through the BuiltinAdapter when the worker drains it.
        plans: dict[str, list[dict[str, object]]] = {
            "daily": [
                {
                    "key": f"daily-redundancy-{anchor}",
                    "packet": {
                        "operation": "harness_redundancy_scan",
                        "kind": "scan_result",
                        "payload": {
                            "db_path": ".omni/memory.sqlite3",
                            "prefer_backend": "auto",
                        },
                    },
                },
                {
                    "key": f"daily-report-{anchor}",
                    "packet": {
                        "operation": "build_daily_report",
                        "kind": "report",
                        "payload": {"anchor": anchor},
                    },
                },
            ],
            "weekly": [
                {
                    "key": f"weekly-compile-engineering-{anchor}",
                    "packet": {
                        "operation": "harness_compile",
                        "kind": "report",
                        "payload": {
                            "domain": "engineering",
                            "from_version": "v0",
                            "backend": "manual",
                        },
                    },
                },
                {
                    "key": f"weekly-report-{anchor}",
                    "packet": {
                        "operation": "build_weekly_report",
                        "kind": "report",
                        "payload": {"anchor": anchor},
                    },
                },
            ],
            "monthly": [
                {
                    "key": f"monthly-report-{anchor}",
                    "packet": {
                        "operation": "build_monthly_report",
                        "kind": "report",
                        "payload": {"anchor": anchor},
                    },
                },
            ],
        }
        plan = plans.get(period)
        if plan is None:
            raise ValueError(
                f"unknown period {period!r}; expected daily|weekly|monthly"
            )

        enqueued: list[dict[str, object]] = []
        for item in plan:
            task = queue.enqueue(
                lane="python",
                packet=item["packet"],
                idempotency_key=str(item["key"]),
                domain_profile=period,
            )
            enqueued.append({
                "id": task.id,
                "idempotency_key": task.idempotency_key,
                "state": task.state,
            })

        return {"period": period, "anchor": anchor, "enqueued": enqueued}

    return schedule_tick


def make_search_memory(workspace: Path):
    workspace_root = workspace.resolve()

    def search_memory(spec: OperationSpec) -> dict[str, object]:
        query = str(spec.payload["query"])
        limit = int(spec.payload.get("limit", 10))
        results = MemoryStore(workspace_root, create=False).search(query, limit=limit)
        return {
            "query": query,
            "count": len(results),
            "results": [result.to_dict() for result in results],
        }

    return search_memory


def make_memory_stats(workspace: Path):
    workspace_root = workspace.resolve()

    def memory_stats(spec: OperationSpec) -> dict[str, int]:
        return MemoryStore(workspace_root, create=False).stats()

    return memory_stats


def make_register_skill(workspace: Path):
    workspace_root = workspace.resolve()

    def register_skill(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        skill = SkillSpec(
            skill_id=str(payload["skill_id"]),
            name=str(payload["name"]),
            kind=SkillKind(str(payload["kind"])),
            description=str(payload["description"]),
            version=str(payload.get("version", "0.1.0")),
            status=SkillStatus(str(payload.get("status", SkillStatus.DRAFT.value))),
            entrypoint=str(payload.get("entrypoint", "")),
            risk_level=RiskLevel.parse(payload.get("risk_level", "L0")),
            required_permissions=list(payload.get("required_permissions", [])),
            connectors=list(payload.get("connectors", [])),
            tags=list(payload.get("tags", [])),
            inputs=dict(payload.get("inputs", {})),
            outputs=dict(payload.get("outputs", {})),
            source_path=str(payload.get("source_path", "")),
        )
        write_card = bool(payload.get("write_card", True))
        return SkillRegistry(workspace_root).upsert(skill, write_card=write_card)

    return register_skill


def make_list_skills(workspace: Path):
    workspace_root = workspace.resolve()

    def list_skills(spec: OperationSpec) -> dict[str, object]:
        skills = SkillRegistry(workspace_root).list(
            kind=spec.payload.get("kind"),
            status=spec.payload.get("status"),
            tag=spec.payload.get("tag"),
        )
        return {
            "count": len(skills),
            "skills": [skill.to_dict() for skill in skills],
        }

    return list_skills


def make_get_skill(workspace: Path):
    workspace_root = workspace.resolve()

    def get_skill(spec: OperationSpec) -> dict[str, object]:
        skill = SkillRegistry(workspace_root).get(str(spec.payload["skill_id"]))
        return {"skill": skill.to_dict()}

    return get_skill


def make_disable_skill(workspace: Path):
    workspace_root = workspace.resolve()

    def disable_skill(spec: OperationSpec) -> dict[str, object]:
        skill = SkillRegistry(workspace_root).disable(str(spec.payload["skill_id"]))
        return {"skill": skill.to_dict()}

    return disable_skill


def make_recommend_skills(workspace: Path):
    workspace_root = workspace.resolve()

    def recommend(spec: OperationSpec) -> dict[str, object]:
        query = str(spec.payload.get("query", ""))
        limit = int(spec.payload.get("limit", 10))
        include_disabled = bool(spec.payload.get("include_disabled", False))
        max_risk_value = spec.payload.get("max_risk")
        max_risk = RiskLevel.parse(max_risk_value) if max_risk_value else None
        recommendations = recommend_skills(
            SkillRegistry(workspace_root).list(),
            query,
            limit=limit,
            max_risk=max_risk,
            include_disabled=include_disabled,
        )
        return {
            "query": query,
            "count": len(recommendations),
            "recommendations": [item.to_dict() for item in recommendations],
        }

    return recommend


def make_analyze_skills(workspace: Path):
    workspace_root = workspace.resolve()

    def analyze(spec: OperationSpec) -> dict[str, object]:
        registry = SkillRegistry(workspace_root)
        skill_ids = list(spec.payload.get("skill_ids", []))
        skills = [registry.get(str(skill_id)) for skill_id in skill_ids]
        return analyze_skill_set(skills).to_dict()

    return analyze


def make_api_management_status(workspace: Path):
    workspace_root = workspace.resolve()

    def status(spec: OperationSpec) -> dict[str, object]:
        timeout_seconds = float(spec.payload.get("timeout_seconds", 0.5))
        return api_management_status(workspace_root, timeout_seconds=timeout_seconds)

    return status


def make_harness_preference_add(workspace: Path):
    workspace_root = workspace.resolve()

    def harness_preference_add(spec: OperationSpec) -> dict[str, object]:
        from .harness.preference import PreferenceRecord, PreferenceStore

        payload = spec.payload
        text = str(payload.get("text", ""))
        decision = str(payload["decision"])
        store_root = workspace_root / str(payload.get("store_root", ".omni/preference"))
        record = PreferenceRecord(
            task_id=str(payload.get("task_id", "")),
            domain=str(payload["domain"]),
            prompt_version=str(payload.get("prompt_version", "v0")),
            candidate_text=text,
            decision=decision,
            accepted_spans=[text] if decision == "accepted" and text else [],
            rejected_spans=[text] if decision == "rejected" and text else [],
            reason=str(payload.get("reason", "")),
        )
        path = PreferenceStore(store_root).append(record)
        return {"record_id": record.record_id, "file": str(path)}

    return harness_preference_add


def make_harness_compile(workspace: Path):
    workspace_root = workspace.resolve()

    def harness_compile(spec: OperationSpec) -> dict[str, object]:
        from .harness import dspy_compile
        from .harness.preference import PreferenceStore

        payload = spec.payload
        store_root = workspace_root / str(payload.get("store_root", ".omni/preference"))
        output_root = workspace_root / str(payload.get("output_root", "prompts"))
        report = dspy_compile.compile(
            domain=str(payload["domain"]),
            from_version=str(payload.get("from_version", "v0")),
            output_root=output_root,
            preference_store=PreferenceStore(store_root),
            bootstrap_rounds=int(payload.get("bootstrap_rounds", 8)),
            max_positive=int(payload.get("max_positive", 12)),
            max_negative=int(payload.get("max_negative", 6)),
            backend=str(payload.get("backend", "auto")),
        )
        return report.to_dict()

    return harness_compile


def make_harness_redundancy_scan(workspace: Path):
    workspace_root = workspace.resolve()

    def harness_redundancy_scan(spec: OperationSpec) -> dict[str, object]:
        from .harness import redundancy

        payload = spec.payload
        db_path = workspace_root / str(payload.get("db_path", ".omni/memory.sqlite3"))
        report = redundancy.scan(
            db_path=db_path,
            prefer_backend=str(payload.get("prefer_backend", "auto")),
            freshness_days=int(payload.get("freshness_days", 365)),
            min_low_signal_ratio=float(payload.get("min_low_signal_ratio", 0.5)),
            max_documents=int(payload.get("max_documents", 5000)),
        )
        return report.to_dict()

    return harness_redundancy_scan


def build_default_registry(workspace: Path | str = ".") -> OperationRegistry:
    workspace_path = Path(workspace)
    registry = OperationRegistry()
    registry.register("summarize_text", summarize_text)
    registry.register("write_markdown", make_write_markdown(workspace_path))
    registry.register("capture_url", make_capture_url(workspace_path))
    registry.register("list_vault_notes", make_list_vault_notes(workspace_path))
    registry.register("read_vault_note", make_read_vault_note(workspace_path))
    registry.register("propose_knowledge", make_propose_knowledge(workspace_path))
    registry.register("digest_proposal", make_digest_proposal(workspace_path))
    registry.register("search_memory", make_search_memory(workspace_path))
    registry.register("memory_stats", make_memory_stats(workspace_path))
    registry.register("register_skill", make_register_skill(workspace_path))
    registry.register("list_skills", make_list_skills(workspace_path))
    registry.register("get_skill", make_get_skill(workspace_path))
    registry.register("disable_skill", make_disable_skill(workspace_path))
    registry.register("recommend_skills", make_recommend_skills(workspace_path))
    registry.register("analyze_skills", make_analyze_skills(workspace_path))
    registry.register("api_management_status", make_api_management_status(workspace_path))
    registry.register("harness_preference_add", make_harness_preference_add(workspace_path))
    registry.register("harness_compile", make_harness_compile(workspace_path))
    registry.register("harness_redundancy_scan", make_harness_redundancy_scan(workspace_path))
    registry.register("list_proposals", make_list_proposals(workspace_path))
    registry.register("approve_proposal", make_approve_proposal(workspace_path))
    registry.register("reject_proposal", make_reject_proposal(workspace_path))
    registry.register("enqueue_task", make_enqueue_task(workspace_path))
    registry.register("claim_task", make_claim_task(workspace_path))
    registry.register("complete_task", make_complete_task(workspace_path))
    registry.register("fail_task", make_fail_task(workspace_path))
    registry.register("list_tasks", make_list_tasks(workspace_path))
    registry.register("schedule_tick", make_schedule_tick(workspace_path))
    registry.register("build_daily_report", _make_build_report(workspace_path, "daily"))
    registry.register("build_weekly_report", _make_build_report(workspace_path, "weekly"))
    registry.register("build_monthly_report", _make_build_report(workspace_path, "monthly"))
    return registry
