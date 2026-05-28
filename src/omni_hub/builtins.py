from __future__ import annotations

from pathlib import Path

from .api_management import api_management_status
from .connectors.web import build_resource_from_body, fetch_url
from .content_store import ContentStore
from .memory import MemoryStore
from .models import OperationSpec, RiskLevel
from .optimizer import (
    DatasetSplit,
    EvalGate,
    OptimizationRun,
    OptimizerStore,
    SkillVersion,
)
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


def make_optimizer_register_skill_version(workspace: Path):
    workspace_root = workspace.resolve()

    def register_skill_version(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        version = SkillVersion(
            skill_id=str(payload["skill_id"]),
            version=str(payload["version"]),
            domain=str(payload.get("domain", "engineering")),
            prompt_path=str(payload.get("prompt_path", "")),
            module_path=str(payload.get("module_path", "")),
            optimizer=str(payload.get("optimizer", "manual")),
            source_run_id=str(payload.get("source_run_id", "")),
            status=str(payload.get("status", "candidate")),
            notes=str(payload.get("notes", "")),
        )
        return OptimizerStore(workspace_root).register_skill_version(version).to_dict()

    return register_skill_version


def make_optimizer_list_skill_versions(workspace: Path):
    workspace_root = workspace.resolve()

    def list_skill_versions(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        versions = OptimizerStore(workspace_root, create=False).list_skill_versions(
            skill_id=payload.get("skill_id"),
            limit=int(payload.get("limit", 50)),
        )
        return {
            "count": len(versions),
            "versions": [v.to_dict() for v in versions],
        }

    return list_skill_versions


def make_optimizer_record_run(workspace: Path):
    workspace_root = workspace.resolve()

    def record_run(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        run = OptimizationRun(
            skill_id=str(payload["skill_id"]),
            optimizer=str(payload["optimizer"]),
            from_version=str(payload["from_version"]),
            to_version=str(payload["to_version"]),
            dataset_split=DatasetSplit(
                train_count=int(payload.get("train_count", 0)),
                dev_count=int(payload.get("dev_count", 0)),
                holdout_count=int(payload.get("holdout_count", 0)),
            ),
            eval_gate=EvalGate(
                metric_thresholds={
                    str(k): float(v)
                    for k, v in dict(payload.get("metric_thresholds", {})).items()
                },
                min_holdout_count=int(payload.get("min_holdout_count", 0)),
            ),
            holdout_metrics={
                str(k): float(v)
                for k, v in dict(payload.get("holdout_metrics", {})).items()
            },
            pareto_candidates=int(payload.get("pareto_candidates", 0)),
            notes=str(payload.get("notes", "")),
        )
        return OptimizerStore(workspace_root).record_run(run).to_dict()

    return record_run


def make_optimizer_list_runs(workspace: Path):
    workspace_root = workspace.resolve()

    def list_runs(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        runs = OptimizerStore(workspace_root, create=False).list_runs(
            skill_id=payload.get("skill_id"),
            limit=int(payload.get("limit", 50)),
        )
        return {
            "count": len(runs),
            "runs": [r.to_dict() for r in runs],
        }

    return list_runs


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


def make_task_stats(workspace: Path):
    workspace_root = workspace.resolve()

    def task_stats(spec: OperationSpec) -> dict[str, object]:
        queue = TaskQueue(workspace_root, create=False)
        return queue.stats()

    return task_stats


def make_memory_tool(workspace: Path):
    workspace_root = workspace.resolve()

    def memory_tool(spec: OperationSpec) -> dict[str, object]:
        from .memory_tool import dispatch

        payload = dict(spec.payload)
        command = str(payload.pop("command"))
        return dispatch(workspace_root, command, arguments=payload).to_dict()

    return memory_tool


def make_memory_remember_core(workspace: Path):
    workspace_root = workspace.resolve()

    def memory_remember_core(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        return MemoryStore(workspace_root).remember_core(
            str(payload["key"]),
            str(payload["value"]),
            confidence=float(payload.get("confidence", 1.0)),
        )

    return memory_remember_core


def make_memory_forget_core(workspace: Path):
    workspace_root = workspace.resolve()

    def memory_forget_core(spec: OperationSpec) -> dict[str, object]:
        deleted = MemoryStore(workspace_root).forget_core(str(spec.payload["key"]))
        return {"key": spec.payload["key"], "deleted": bool(deleted)}

    return memory_forget_core


def make_memory_recall(workspace: Path):
    workspace_root = workspace.resolve()

    def memory_recall(spec: OperationSpec) -> dict[str, object]:
        store = MemoryStore(workspace_root, create=False)
        tier = str(spec.payload.get("tier", "recall")).lower()
        query = str(spec.payload.get("query", ""))
        limit = int(spec.payload.get("limit", 20))

        if tier == "core":
            return {"tier": "core", "results": store.list_core()}
        if tier == "recall":
            if query:
                return {"tier": "recall", "results": store.recall_search(query, limit=limit)}
            return {"tier": "recall", "results": store.list_recall(limit=limit)}
        if tier == "archival":
            return {
                "tier": "archival",
                "results": [r.to_dict() for r in store.archival_search(query, limit=limit)] if query else [],
            }
        raise ValueError(f"unknown tier: {tier!r}; expected core|recall|archival")

    return memory_recall


def make_memory_promote_recall(workspace: Path):
    workspace_root = workspace.resolve()

    def memory_promote_recall(spec: OperationSpec) -> dict[str, object]:
        payload = spec.payload
        return MemoryStore(workspace_root).promote_to_recall(
            str(payload["content"]),
            source_kind=str(payload.get("source_kind", "preference")),
            source_id=str(payload.get("source_id", "")),
            score=float(payload.get("score", 0.0)),
        )

    return memory_promote_recall


def make_retrieve_cascade(workspace: Path):
    workspace_root = workspace.resolve()

    def retrieve_cascade(spec: OperationSpec) -> dict[str, object]:
        from .retrieval import (
            Cascade,
            EvidenceStore,
            HeuristicGrader,
            TTLCache,
            builtin_sources,
        )

        payload = spec.payload
        cache: TTLCache | None = None
        if bool(payload.get("use_cache", False)):
            cache = TTLCache(workspace_root)

        cascade = Cascade(builtin_sources(), cache=cache)

        fusion = str(payload.get("fusion", "concat"))
        if fusion not in ("rrf", "concat"):
            fusion = "concat"

        grader = None
        grader_name = str(payload.get("grader", "")).strip().lower()
        if grader_name == "heuristic":
            grader = HeuristicGrader()
        # `llm` grader is intentionally not wireable from CLI yet — the
        # callable needs a model client the operator pins themselves.

        result = cascade.retrieve(
            str(payload["query"]),
            domain=str(payload.get("domain", "default")),
            per_source_limit=int(payload.get("per_source_limit", 5)),
            total_limit=int(payload.get("total_limit", 20)),
            sources=list(payload["sources"]) if payload.get("sources") else None,
            fusion=fusion,                # type: ignore[arg-type]
            grader=grader,
        )

        # v0.17-K: optional cross-encoder rerank tail (Cohere v4 / Voyage v2.5).
        # Applied AFTER fusion + grader.  Key-gated; falls through silently
        # when the relevant API key isn't configured.
        reranker_name = str(payload.get("reranker", "")).strip().lower()
        rerank_meta: dict[str, object] = {}
        if reranker_name and reranker_name != "none" and result.records:
            try:
                from .retrieval.rerankers import build_reranker
                reranker = build_reranker(reranker_name)
                if reranker is not None:
                    reranked = reranker.rerank(
                        str(payload["query"]),
                        list(result.records),
                        top_n=int(payload.get("total_limit", 20)),
                    )
                    if reranked:
                        result.records = reranked
                        rerank_meta = {"reranker": reranker.name, "ok": True, "count": len(reranked)}
                    else:
                        rerank_meta = {"reranker": reranker_name, "ok": False, "reason": "empty response"}
            except Exception as exc:                            # noqa: BLE001
                rerank_meta = {"reranker": reranker_name, "ok": False, "reason": str(exc)}

        result_dict = result.to_dict()
        if rerank_meta:
            result_dict["rerank"] = rerank_meta

        if bool(payload.get("persist_evidence", False)):
            evidence_store = EvidenceStore(workspace_root)
            extra = {"operation": "retrieve_cascade"}
            run_id = str(payload.get("run_id", "")).strip()
            artifact = evidence_store.write(
                result_dict,
                run_id=run_id or None,
                extra_manifest=extra,
            )
            result_dict["evidence"] = artifact.to_dict()

        return result_dict

    return retrieve_cascade


def make_retrieve_doctor(workspace: Path):
    workspace_root = workspace.resolve()

    def retrieve_doctor(spec: OperationSpec) -> dict[str, object]:
        from .retrieval import builtin_sources
        from .retrieval.health import probe_all, summarise

        sources = list(builtin_sources().values())
        reports = probe_all(sources)
        return {
            "rows": [r.to_dict() for r in reports],
            "summary": summarise(reports),
        }

    return retrieve_doctor


def make_fetch_url_reader(workspace: Path):
    workspace_root = workspace.resolve()

    def fetch_url_reader(spec: OperationSpec) -> dict[str, object]:
        from .connectors.web import fetch_url
        from .retrieval.base import RetrievalError
        from .retrieval.jina_reader import JinaReaderFetcher

        payload = spec.payload
        url = str(payload["url"])
        use_reader = bool(payload.get("use_reader", True))

        reader_result: dict[str, object] | None = None
        if use_reader:
            try:
                record = JinaReaderFetcher().fetch(url)
                reader_result = record.to_dict()
            except RetrievalError as exc:
                reader_result = {"error": str(exc)}

        # urllib fallback / parallel capture — gives us the raw HTML even
        # when Jina worked, so the caller has both views.
        urllib_result: dict[str, object]
        raw_html_for_fallback = ""
        try:
            resource = fetch_url(url)
            urllib_result = {
                "title": resource.title,
                "text": resource.text[:4000],
                "content_type": resource.content_type,
                "source_kind": resource.source_kind,
                "metadata": dict(resource.metadata),
            }
            if "html" in resource.content_type.lower():
                raw_html_for_fallback = resource.body
        except Exception as exc:                            # noqa: BLE001
            urllib_result = {"error": f"{type(exc).__name__}: {exc}"}

        # trafilatura fallback — opt-in; runs when reader empty/failed AND
        # urllib retrieved HTML.  Silent no-op if trafilatura binary missing.
        trafilatura_result: dict[str, object] | None = None
        if bool(payload.get("use_trafilatura", False)) and raw_html_for_fallback:
            from .connectors.trafilatura_bridge import extract_with_metadata

            payload_dict, status = extract_with_metadata(
                raw_html_for_fallback, url,
            )
            trafilatura_result = {
                "status": status,
                "title": payload_dict.get("title", ""),
                "text": str(payload_dict.get("text", ""))[:4000],
                "author": payload_dict.get("author", ""),
                "date": payload_dict.get("date", ""),
            }

        out: dict[str, object] = {
            "url": url,
            "reader": reader_result,
            "urllib": urllib_result,
        }
        if trafilatura_result is not None:
            out["trafilatura"] = trafilatura_result
        return out

    return fetch_url_reader


def make_research_kb_status(workspace: Path):
    workspace_root = workspace.resolve()

    def research_kb_status(spec: OperationSpec) -> dict[str, object]:
        from .research_assets import status

        return status(workspace_root)

    return research_kb_status


def make_research_kb_search(workspace: Path):
    workspace_root = workspace.resolve()

    def research_kb_search(spec: OperationSpec) -> dict[str, object]:
        from .research_assets import search

        payload = spec.payload
        results = search(
            str(payload["query"]),
            workspace=workspace_root,
            source_id=str(payload.get("source", "all")),
            limit=int(payload.get("limit", 10)),
        )
        return {
            "count": len(results),
            "results": [result.to_dict() for result in results],
        }

    return research_kb_search


def make_research_kb_read(workspace: Path):
    workspace_root = workspace.resolve()

    def research_kb_read(spec: OperationSpec) -> dict[str, object]:
        from .research_assets import read_analysis

        payload = spec.payload
        return read_analysis(
            str(payload["path"]),
            workspace=workspace_root,
            source_id=str(payload["source"]),
            max_chars=int(payload.get("max_chars", 4000)),
        )

    return research_kb_read


def make_researchflow_skill_inventory(workspace: Path):
    workspace_root = workspace.resolve()

    def researchflow_skill_inventory(spec: OperationSpec) -> dict[str, object]:
        from .research_assets import list_researchflow_skills

        skills = list_researchflow_skills(workspace_root)
        return {
            "count": len(skills),
            "skills": [skill.to_dict() for skill in skills],
        }

    return researchflow_skill_inventory


def make_wiki_init(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_init(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import init_layout

        return init_layout(workspace_root)

    return wiki_init


def make_wiki_status(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_status(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import status

        return status(workspace_root)

    return wiki_status


def make_wiki_search(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_search(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import search_wiki

        payload = spec.payload
        query = str(payload["query"])
        results = search_wiki(
            query,
            workspace=workspace_root,
            limit=int(payload.get("limit", 10)),
            include_closed=bool(payload.get("include_closed", False)),
            backend=str(payload.get("backend", "auto")),
        )
        return {
            "query": query,
            "count": len(results),
            "results": [result.to_dict() for result in results],
        }

    return wiki_search


def make_wiki_propose_research(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_propose_research(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import propose_research_wiki_update

        payload = spec.payload
        result = propose_research_wiki_update(
            workspace_root,
            source_id=str(payload["source"]),
            analysis_path=str(payload["path"]),
            target_domain=str(payload.get("domain", "research")),
        )
        proposal = result.get("proposal")
        if hasattr(proposal, "to_dict"):
            result["proposal"] = proposal.to_dict()
        return result

    return wiki_propose_research


def make_wiki_apply_proposal(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_apply_proposal(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import apply_wiki_proposal, preview_apply_wiki_proposal

        if spec.dry_run:
            diff = preview_apply_wiki_proposal(
                workspace_root,
                str(spec.payload["proposal"]),
                trace_id=spec.trace_id,
            )
            return diff.to_dict()

        return apply_wiki_proposal(
            workspace_root,
            str(spec.payload["proposal"]),
            trace_id=spec.trace_id,
        )

    return wiki_apply_proposal


def make_wiki_ingest(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_ingest(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import ingest_retrieval_evidence

        payload = spec.payload
        domain = str(payload.get("domain", "")).strip() or None
        result = ingest_retrieval_evidence(
            workspace_root,
            run_id=str(payload["run_id"]),
            domain=domain,
            title=str(payload.get("title", "")),
            max_records=int(payload.get("max_records", 20)),
        )
        proposal = result.get("proposal")
        if hasattr(proposal, "to_dict"):
            result["proposal"] = proposal.to_dict()
        return result

    return wiki_ingest


def make_wiki_dream(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_dream(spec: OperationSpec) -> dict[str, object]:
        from .wiki_dream import run_dream

        payload = spec.payload
        rules_raw = payload.get("rules")
        rules = list(rules_raw) if rules_raw else None
        report = run_dream(
            workspace_root,
            since_days=int(payload.get("since_days", 7)),
            rules=rules,
            persist_proposals=bool(payload.get("persist", False)),
            update_state=bool(payload.get("update_state", True)),
        )
        return report.to_dict()

    return wiki_dream


def make_wiki_lint(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_lint(spec: OperationSpec) -> dict[str, object]:
        from .wiki_lint import lint_wiki

        payload = spec.payload
        domain = str(payload.get("domain", "")).strip() or None
        rules_raw = payload.get("rules")
        rules = list(rules_raw) if rules_raw else None
        report = lint_wiki(
            workspace_root,
            domain=domain,
            rules=rules,
            stale_after_days=int(payload.get("stale_after_days", 30)),
            persist_proposals=bool(payload.get("persist", False)),
        )
        return report.to_dict()

    return wiki_lint


def make_wiki_doctor(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_doctor(spec: OperationSpec) -> dict[str, object]:
        from .wiki_doctor import run_doctor

        return run_doctor(workspace_root).to_dict()

    return wiki_doctor


def make_wiki_supersede(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_supersede(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import preview_supersede_claim, supersede_claim

        payload = spec.payload
        if spec.dry_run:
            diff = preview_supersede_claim(
                workspace_root,
                new_claim_id=str(payload["new_claim_id"]),
                old_claim_id=str(payload["old_claim_id"]),
                trace_id=spec.trace_id,
            )
            return diff.to_dict()
        return supersede_claim(
            workspace_root,
            new_claim_id=str(payload["new_claim_id"]),
            old_claim_id=str(payload["old_claim_id"]),
            reason=str(payload.get("reason", "")),
            expected_version=int(payload["expected_version"]) if "expected_version" in payload else None,
        )

    return wiki_supersede


def make_wiki_conflict_resolve(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_conflict_resolve(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import resolve_conflict

        payload = spec.payload
        return resolve_conflict(
            workspace_root,
            proposal_id=str(payload["proposal_id"]),
            decision=str(payload["decision"]),
            new_claim_id=str(payload.get("new_claim_id", "")),
            old_claim_id=str(payload.get("old_claim_id", "")),
            reason=str(payload.get("reason", "")),
            expected_version=int(payload["expected_version"]) if "expected_version" in payload else None,
            trace_id=spec.trace_id,
        )

    return wiki_conflict_resolve


def make_claims_list(workspace: Path):
    workspace_root = workspace.resolve()

    def claims_list(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import list_claims

        payload = spec.payload
        state = (str(payload.get("state", "")).strip() or None)
        domain = (str(payload.get("domain", "")).strip() or None)
        claims = list_claims(
            workspace_root,
            state=state,
            domain=domain,
            include_closed=bool(payload.get("include_closed", False)),
            limit=int(payload.get("limit", 50)),
        )
        return {"count": len(claims), "claims": claims}

    return claims_list


def make_claims_show(workspace: Path):
    workspace_root = workspace.resolve()

    def claims_show(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import show_claim

        return show_claim(workspace_root, claim_id=str(spec.payload["claim_id"]))

    return claims_show


def make_claims_stats(workspace: Path):
    workspace_root = workspace.resolve()

    def claims_stats_op(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import claims_stats

        return claims_stats(workspace_root)

    return claims_stats_op


def make_wiki_reindex(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_reindex(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import reindex_wiki

        return reindex_wiki(workspace_root)

    return wiki_reindex


def make_wiki_graph_query(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_graph_query(spec: OperationSpec) -> dict[str, object]:
        from .wiki_graph import query_community, query_neighbours

        payload = spec.payload
        if payload.get("community"):
            return query_community(workspace_root, community_id=str(payload["community"]))
        if payload.get("node"):
            return query_neighbours(
                workspace_root,
                node_id=str(payload["node"]),
                limit=int(payload.get("limit", 20)),
            )
        raise ValueError("wiki_graph_query requires 'node' or 'community' in payload")

    return wiki_graph_query


def make_projection_list(workspace: Path):
    workspace_root = workspace.resolve()

    def projection_list(spec: OperationSpec) -> dict[str, object]:
        from .projection import build_default_projection_registry

        registry = build_default_projection_registry(workspace_root)
        return registry.overview()

    return projection_list


def make_projection_rebuild(workspace: Path):
    workspace_root = workspace.resolve()

    def projection_rebuild(spec: OperationSpec) -> dict[str, object]:
        from .projection import build_default_projection_registry

        registry = build_default_projection_registry(workspace_root)
        snapshot = registry.rebuild(str(spec.payload["target"]))
        return snapshot.to_dict()

    return projection_rebuild


def make_projection_snapshots(workspace: Path):
    workspace_root = workspace.resolve()

    def projection_snapshots(spec: OperationSpec) -> dict[str, object]:
        from .projection import build_default_projection_registry

        registry = build_default_projection_registry(workspace_root)
        snaps = registry.list_snapshots(
            str(spec.payload["target"]),
            limit=int(spec.payload.get("limit", 20)),
        )
        return {"count": len(snaps), "snapshots": [s.to_dict() for s in snaps]}

    return projection_snapshots


def make_projection_rollback(workspace: Path):
    workspace_root = workspace.resolve()

    def projection_rollback(spec: OperationSpec) -> dict[str, object]:
        from .projection import build_default_projection_registry

        registry = build_default_projection_registry(workspace_root)
        snap = registry.rollback(
            str(spec.payload["target"]),
            str(spec.payload["snapshot"]),
        )
        return snap.to_dict()

    return projection_rollback


def make_workflow_list(workspace: Path):
    workspace_root = workspace.resolve()

    def workflow_list(spec: OperationSpec) -> dict[str, object]:
        from .workflow import WorkflowStore

        store = WorkflowStore(workspace_root)
        runs = store.list_workflows(
            state=spec.payload.get("state"),
            limit=int(spec.payload.get("limit", 50)),
        )
        return {
            "count": len(runs),
            "workflows": [r.to_dict() for r in runs],
        }

    return workflow_list


def make_workflow_show(workspace: Path):
    workspace_root = workspace.resolve()

    def workflow_show(spec: OperationSpec) -> dict[str, object]:
        from .workflow import WorkflowStore

        store = WorkflowStore(workspace_root)
        run_id = str(spec.payload["run_id"])
        wf = store.get_workflow(run_id)
        steps = store.list_steps(run_id)
        return {
            "workflow": wf.to_dict(),
            "steps": [s.to_dict() for s in steps],
        }

    return workflow_show


def make_workflow_signal(workspace: Path):
    workspace_root = workspace.resolve()

    def workflow_signal(spec: OperationSpec) -> dict[str, object]:
        from .workflow import WorkflowStore

        payload = spec.payload
        store = WorkflowStore(workspace_root)
        signal_id = store.send_signal(
            str(payload["run_id"]),
            str(payload["name"]),
            dict(payload.get("payload", {})),
        )
        return {"signal_id": signal_id, "trace_id": spec.trace_id}

    return workflow_signal


def make_workflow_query(workspace: Path):
    workspace_root = workspace.resolve()

    def workflow_query(spec: OperationSpec) -> dict[str, object]:
        from .workflow import WorkflowStore

        payload = spec.payload
        store = WorkflowStore(workspace_root)
        return store.serve_query(
            str(payload["run_id"]),
            str(payload.get("name", "state")),
        )

    return workflow_query


def make_workflow_resume(workspace: Path):
    workspace_root = workspace.resolve()

    def workflow_resume(spec: OperationSpec) -> dict[str, object]:
        from .workflow import WorkflowStore

        store = WorkflowStore(workspace_root)
        wf = store.resume(str(spec.payload["run_id"]))
        return wf.to_dict()

    return workflow_resume


def make_wiki_log_append(workspace: Path):
    workspace_root = workspace.resolve()

    def wiki_log_append(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import append_log

        payload = spec.payload
        return append_log(
            workspace_root,
            op=str(payload["op"]),
            summary=str(payload["summary"]),
            source=str(payload.get("source", "")),
        )

    return wiki_log_append


def make_context_pack_build(workspace: Path):
    workspace_root = workspace.resolve()

    def context_pack_build(spec: OperationSpec) -> dict[str, object]:
        from .knowledge_plane import build_context_pack

        payload = spec.payload
        pack = build_context_pack(
            workspace_root,
            query=str(payload["query"]),
            domain=str(payload.get("domain", "research")),
            wiki_limit=int(payload.get("wiki_limit", 6)),
            research_limit=int(payload.get("research_limit", 6)),
            persist=bool(payload.get("persist", False)),
            tier=str(payload.get("tier", "standard")),
            include_closed=bool(payload.get("include_closed", False)),
        )
        return pack.to_dict()

    return context_pack_build


def make_event_log_dump(workspace: Path):
    workspace_root = workspace.resolve()

    def event_log_dump(spec: OperationSpec) -> dict[str, object]:
        from .event_log import EventLog

        log = EventLog(workspace_root)
        task_id = int(spec.payload.get("task_id", 0))
        events = [event.to_dict() for event in log.replay(task_id)]
        result: dict[str, object] = {
            "task_id": task_id,
            "count": len(events),
            "events": events,
        }
        if spec.payload.get("verify"):
            ok, errors = log.verify_chain(task_id)
            result["chain_ok"] = ok
            result["chain_errors"] = errors
        return result

    return event_log_dump


def make_event_log_list(workspace: Path):
    workspace_root = workspace.resolve()

    def event_log_list(spec: OperationSpec) -> dict[str, object]:
        from .event_log import EventLog

        log = EventLog(workspace_root)
        ids = log.list_tasks()
        return {"task_ids": ids, "count": len(ids)}

    return event_log_list


def make_skill_sync(workspace: Path):
    workspace_root = workspace.resolve()

    def skill_sync(spec: OperationSpec) -> dict[str, object]:
        from .skill_sync import sync_skills

        return sync_skills(
            workspace_root,
            apply=bool(spec.payload.get("apply", False)),
        )

    return skill_sync


def _weekly_compile_skill_tasks(anchor: str) -> list[dict[str, object]]:
    """Enumerate one ``harness_compile_skill`` task per registered domain.

    Returns task descriptors the schedule plan splices in.  When a new
    domain lands in `domain_schemas.DOMAIN_SCHEMAS`, it's picked up here
    automatically on the next weekly tick.
    """

    from .domain_schemas import DOMAIN_SCHEMAS

    out: list[dict[str, object]] = []
    for slug in sorted(DOMAIN_SCHEMAS.keys()):
        out.append(
            {
                "key": f"weekly-compile-skill-{slug}-{anchor}",
                "packet": {
                    "operation": "harness_compile_skill",
                    "kind": "report",
                    "payload": {
                        "domain": slug,
                        "backend": "manual",
                    },
                },
            }
        )
    return out


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
                    # Karpathy wiki-lint daily — six rules, persisted as
                    # Proposal(kind=lint_finding) for human review.
                    "key": f"daily-wiki-lint-{anchor}",
                    "packet": {
                        "operation": "wiki_lint",
                        "kind": "scan_result",
                        "payload": {"persist": True},
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
                # v0.17-A: weekly offline consolidation (Anthropic Dreaming parity)
                {
                    "key": f"weekly-wiki-dream-{anchor}",
                    "packet": {
                        "operation": "wiki_dream",
                        "kind": "scan_result",
                        "payload": {"since_days": 7, "persist": True},
                    },
                },
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
                # v0.17-I: weekly SKILL.md compile for every registered domain.
                # Generated programmatically below so adding a new domain in
                # domain_schemas.py automatically enrolls it.
                *_weekly_compile_skill_tasks(anchor),
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
                # v0.17-I: monthly cold-start dream — re-scan everything,
                # not just the last 7 days.  Catches multi-month patterns
                # that weekly windows miss.
                {
                    "key": f"monthly-wiki-dream-full-{anchor}",
                    "packet": {
                        "operation": "wiki_dream",
                        "kind": "scan_result",
                        "payload": {"since_days": 0, "persist": True},
                    },
                },
                # Monthly doctor — proactive health surfacing.
                {
                    "key": f"monthly-wiki-doctor-{anchor}",
                    "packet": {
                        "operation": "wiki_doctor",
                        "kind": "scan_result",
                        "payload": {},
                    },
                },
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
        optimizer_store = OptimizerStore(workspace_root)
        run = optimizer_store.record_run(OptimizationRun(
            skill_id=report.domain,
            optimizer=report.backend,
            from_version=report.from_version,
            to_version=report.to_version,
            dataset_split=DatasetSplit(
                train_count=report.positive_used + report.negative_used,
                dev_count=0,
                holdout_count=0,
            ),
            eval_gate=EvalGate(),
            pareto_candidates=0,
            notes=report.notes,
        ))
        prompt_path = Path(report.output_dir) / "system_prompt.md"
        optimizer_store.register_skill_version(SkillVersion(
            skill_id=report.domain,
            version=report.to_version,
            domain=report.domain,
            prompt_path=str(prompt_path),
            optimizer=report.backend,
            source_run_id=run.run_id,
            status="candidate",
            notes=report.notes,
        ))
        out = report.to_dict()
        out["optimizer_run_id"] = run.run_id
        out["gate_decision"] = run.gate_decision
        return out

    return harness_compile


def make_harness_compile_skill(workspace: Path):
    workspace_root = workspace.resolve()

    def harness_compile_skill(spec: OperationSpec) -> dict[str, object]:
        from .harness import dspy_compile
        from .harness.preference import PreferenceStore

        payload = spec.payload
        store_root = workspace_root / str(payload.get("store_root", ".omni/preference"))
        output_root = workspace_root / str(payload.get("output_root", ".agents/skills"))
        report = dspy_compile.compile_skill_md(
            domain=str(payload["domain"]),
            skill_id=str(payload.get("skill_id", "")),
            description=str(payload.get("description", "")),
            output_root=output_root,
            preference_store=PreferenceStore(store_root),
            max_positive=int(payload.get("max_positive", 10)),
            max_negative=int(payload.get("max_negative", 4)),
            backend=str(payload.get("backend", "manual")),
        )
        return report.to_dict()

    return harness_compile_skill


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


def make_argilla_export_proposals(workspace: Path):
    workspace_root = workspace.resolve()

    def argilla_export_proposals(spec: OperationSpec) -> dict[str, object]:
        from datetime import UTC, datetime, timedelta

        from ._storage import safe_workspace_path
        from .harness.argilla_bridge import export_proposals

        payload = spec.payload
        output_path = safe_workspace_path(
            workspace_root,
            str(payload.get("output", ".omni/argilla/proposals.jsonl")),
        )
        proposals = ProposalStore(workspace_root, create=False).list(
            state=payload.get("state"),
            kind=payload.get("kind"),
            limit=int(payload.get("limit", 100)),
        )
        since_days = int(payload.get("since_days", 0) or 0)
        if since_days > 0:
            threshold = datetime.now(UTC) - timedelta(days=since_days)
            kept = []
            for p in proposals:
                try:
                    ts = datetime.fromisoformat(str(p.created_at).replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts >= threshold:
                    kept.append(p)
            proposals = kept
        result = export_proposals(
            proposals,
            output_path,
            dataset=str(payload.get("dataset", "omni_proposal_review_v1")),
            domain=str(payload.get("domain", "general")),
            skill_id=str(payload.get("skill_id", "")),
            skill_version=str(payload.get("skill_version", "v0")),
        )
        result["file"] = str(output_path.relative_to(workspace_root))
        result["since_days"] = since_days
        return result

    return argilla_export_proposals


def make_argilla_sync_feedback(workspace: Path):
    workspace_root = workspace.resolve()

    def argilla_sync_feedback(spec: OperationSpec) -> dict[str, object]:
        from ._storage import safe_workspace_path
        from .harness.argilla_bridge import sync_feedback_file
        from .harness.preference import PreferenceStore

        payload = spec.payload
        input_path = safe_workspace_path(workspace_root, str(payload["input"]))
        preference_root = safe_workspace_path(
            workspace_root,
            str(payload.get("preference_root", ".omni/preference")),
        )
        return sync_feedback_file(
            input_path,
            proposal_store=ProposalStore(workspace_root),
            preference_store=PreferenceStore(preference_root),
            default_domain=str(payload.get("domain", "general")),
        )

    return argilla_sync_feedback


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
    registry.register("harness_compile_skill", make_harness_compile_skill(workspace_path))
    registry.register("harness_redundancy_scan", make_harness_redundancy_scan(workspace_path))
    registry.register("argilla_export_proposals", make_argilla_export_proposals(workspace_path))
    registry.register("argilla_sync_feedback", make_argilla_sync_feedback(workspace_path))
    registry.register("list_proposals", make_list_proposals(workspace_path))
    registry.register("approve_proposal", make_approve_proposal(workspace_path))
    registry.register("reject_proposal", make_reject_proposal(workspace_path))
    registry.register("enqueue_task", make_enqueue_task(workspace_path))
    registry.register("claim_task", make_claim_task(workspace_path))
    registry.register("complete_task", make_complete_task(workspace_path))
    registry.register("fail_task", make_fail_task(workspace_path))
    registry.register("list_tasks", make_list_tasks(workspace_path))
    registry.register(
        "optimizer_register_skill_version",
        make_optimizer_register_skill_version(workspace_path),
    )
    registry.register(
        "optimizer_list_skill_versions",
        make_optimizer_list_skill_versions(workspace_path),
    )
    registry.register("optimizer_record_run", make_optimizer_record_run(workspace_path))
    registry.register("optimizer_list_runs", make_optimizer_list_runs(workspace_path))
    registry.register("schedule_tick", make_schedule_tick(workspace_path))
    registry.register("task_stats", make_task_stats(workspace_path))
    registry.register("skill_sync", make_skill_sync(workspace_path))
    registry.register("event_log_dump", make_event_log_dump(workspace_path))
    registry.register("event_log_list", make_event_log_list(workspace_path))
    registry.register("retrieve_cascade", make_retrieve_cascade(workspace_path))
    registry.register("retrieve_doctor", make_retrieve_doctor(workspace_path))
    registry.register("fetch_url_reader", make_fetch_url_reader(workspace_path))
    registry.register("research_kb_status", make_research_kb_status(workspace_path))
    registry.register("research_kb_search", make_research_kb_search(workspace_path))
    registry.register("research_kb_read", make_research_kb_read(workspace_path))
    registry.register(
        "researchflow_skill_inventory",
        make_researchflow_skill_inventory(workspace_path),
    )
    registry.register("wiki_init", make_wiki_init(workspace_path))
    registry.register("wiki_status", make_wiki_status(workspace_path))
    registry.register("wiki_search", make_wiki_search(workspace_path))
    registry.register("wiki_propose_research", make_wiki_propose_research(workspace_path))
    registry.register("wiki_apply_proposal", make_wiki_apply_proposal(workspace_path))
    registry.register("wiki_ingest", make_wiki_ingest(workspace_path))
    registry.register("wiki_reindex", make_wiki_reindex(workspace_path))
    registry.register("wiki_doctor", make_wiki_doctor(workspace_path))
    registry.register("wiki_dream", make_wiki_dream(workspace_path))
    registry.register("wiki_lint", make_wiki_lint(workspace_path))
    registry.register("wiki_supersede", make_wiki_supersede(workspace_path))
    registry.register("wiki_conflict_resolve", make_wiki_conflict_resolve(workspace_path))
    registry.register("claims_list", make_claims_list(workspace_path))
    registry.register("claims_show", make_claims_show(workspace_path))
    registry.register("claims_stats", make_claims_stats(workspace_path))
    registry.register("wiki_graph_query", make_wiki_graph_query(workspace_path))
    registry.register("projection_list", make_projection_list(workspace_path))
    registry.register("projection_rebuild", make_projection_rebuild(workspace_path))
    registry.register("projection_snapshots", make_projection_snapshots(workspace_path))
    registry.register("projection_rollback", make_projection_rollback(workspace_path))
    registry.register("workflow_list", make_workflow_list(workspace_path))
    registry.register("workflow_show", make_workflow_show(workspace_path))
    registry.register("workflow_signal", make_workflow_signal(workspace_path))
    registry.register("workflow_query", make_workflow_query(workspace_path))
    registry.register("workflow_resume", make_workflow_resume(workspace_path))
    registry.register("wiki_log_append", make_wiki_log_append(workspace_path))
    registry.register("context_pack_build", make_context_pack_build(workspace_path))
    registry.register("memory_tool", make_memory_tool(workspace_path))
    registry.register("memory_remember_core", make_memory_remember_core(workspace_path))
    registry.register("memory_forget_core", make_memory_forget_core(workspace_path))
    registry.register("memory_recall", make_memory_recall(workspace_path))
    registry.register("memory_promote_recall", make_memory_promote_recall(workspace_path))
    registry.register("build_daily_report", _make_build_report(workspace_path, "daily"))
    registry.register("build_weekly_report", _make_build_report(workspace_path, "weekly"))
    registry.register("build_monthly_report", _make_build_report(workspace_path, "monthly"))
    # v0.19 Interface + Application Plane operations.
    registry.register("channel_list", make_channel_list(workspace_path))
    registry.register("channel_health", make_channel_health(workspace_path))
    registry.register("app_report_build", make_app_report_build(workspace_path))
    registry.register("app_route_task", make_app_route_task(workspace_path))
    registry.register("skill_stubs_sync", make_skill_stubs_sync(workspace_path))
    # v0.23 Judge LLM framework.
    registry.register("judge_evaluate", make_judge_evaluate(workspace_path))
    # v0.28 Cross-skill transfer (meta).
    registry.register("meta_cross_skill_scan", make_meta_cross_skill_scan(workspace_path))
    # v0.29 A/B test framework.
    registry.register("ab_test_run", make_ab_test_run(workspace_path))
    registry.register("ab_test_list", make_ab_test_list(workspace_path))
    registry.register("ab_test_show", make_ab_test_show(workspace_path))
    registry.register("ab_test_stats", make_ab_test_stats(workspace_path))
    # v0.38 Functional layer operations.
    registry.register("inbox_classify", make_inbox_classify(workspace_path))
    registry.register("project_plan", make_project_plan(workspace_path))
    registry.register("pptx_build", make_pptx_build(workspace_path))
    registry.register("calendar_add", make_calendar_add(workspace_path))
    registry.register("schedule_plan", make_schedule_plan(workspace_path))
    registry.register("task_add", make_task_add(workspace_path))
    registry.register("finance_screen", make_finance_screen(workspace_path))
    registry.register("order_propose", make_order_propose(workspace_path))
    # v0.39 — read paths + missing CRUD so CLI commands map 1:1.
    registry.register("user_list", make_user_list(workspace_path))
    registry.register("user_enroll", make_user_enroll(workspace_path))
    registry.register("user_approve", make_user_approve(workspace_path))
    registry.register("user_set_persona", make_user_set_persona(workspace_path))
    registry.register("user_memory_recall", make_user_memory_recall(workspace_path))
    registry.register("user_memory_archival", make_user_memory_archival(workspace_path))
    registry.register("calendar_list", make_calendar_list(workspace_path))
    registry.register("task_list_personal", make_task_list_personal(workspace_path))
    registry.register("task_done", make_task_done(workspace_path))
    registry.register("project_list", make_project_list(workspace_path))
    registry.register("project_show", make_project_show(workspace_path))
    registry.register("finance_watch_create", make_finance_watch_create(workspace_path))
    registry.register("finance_watch_list", make_finance_watch_list(workspace_path))
    registry.register("finance_portfolio_stats", make_finance_portfolio_stats(workspace_path))
    # v0.40 — explicit 2-level AppIntentRouter (intent → domain → tools).
    registry.register("app_intent_route", make_app_intent_route(workspace_path))
    # v0.41 — eval flywheel
    registry.register("eval_list", make_eval_list(workspace_path))
    registry.register("eval_show", make_eval_show(workspace_path))
    registry.register("eval_run", make_eval_run(workspace_path))
    registry.register("eval_promote", make_eval_promote(workspace_path))
    return registry


# ---------------------------------------------------------------------------
# v0.41 — Eval flywheel operations
# ---------------------------------------------------------------------------


def make_eval_list(workspace: Path):
    workspace_root = workspace.resolve()

    def eval_list(spec: OperationSpec) -> dict:
        from .evals import EvalStore
        store = EvalStore(workspace_root)
        packs = store.list_packs()
        return {
            "count": len(packs),
            "packs": [p.to_dict() for p in packs],
        }

    return eval_list


def make_eval_show(workspace: Path):
    workspace_root = workspace.resolve()

    def eval_show(spec: OperationSpec) -> dict:
        from .evals.store import EvalStore, HoldoutAccessDenied
        domain = str(spec.payload.get("domain", "")).strip()
        version = str(spec.payload.get("version", "v0.1"))
        if not domain:
            raise ValueError("domain is required")
        store = EvalStore(workspace_root)
        pack = store.get_pack(domain, version)
        if pack is None:
            return {"domain": domain, "version": version, "found": False}
        include_holdout = bool(spec.payload.get("include_holdout", False))
        try:
            cases = store.list_cases(pack, include_holdout=include_holdout)
        except HoldoutAccessDenied as exc:
            return {
                "found": True,
                "pack": pack.to_dict(),
                "case_count": 0,
                "cases": [],
                "holdout_denied": str(exc),
            }
        return {
            "found": True,
            "pack": pack.to_dict(),
            "case_count": len(cases),
            "cases": [c.to_dict() for c in cases],
            "include_holdout": include_holdout,
        }

    return eval_show


def make_eval_run(workspace: Path):
    workspace_root = workspace.resolve()

    def eval_run(spec: OperationSpec) -> dict:
        from .evals import EvalRunner, EvalStore
        from .evals.store import HoldoutAccessDenied
        domain = str(spec.payload.get("domain", "")).strip()
        version = str(spec.payload.get("version", "v0.1"))
        if not domain:
            raise ValueError("domain is required")
        store = EvalStore(workspace_root)
        pack = store.get_pack(domain, version)
        if pack is None:
            raise ValueError(f"no pack at {domain}/{version}")
        runner_ = EvalRunner(
            workspace=workspace_root,
            judge=str(spec.payload.get("judge", "heuristic")),
        )
        try:
            run = runner_.run(
                pack,
                skill_version=str(spec.payload.get("skill_version", "")),
                trace_id=str(spec.trace_id or ""),
                include_holdout=bool(spec.payload.get("include_holdout", False)),
                use_echo_only=bool(spec.payload.get("echo_only", False)),
            )
        except HoldoutAccessDenied as exc:
            return {
                "pack_id": pack.pack_id,
                "holdout_denied": str(exc),
                "run_id": "",
                "case_count": 0,
                "pass_rate": 0.0,
            }
        # Trim per_case_results from CLI payload — full detail lands in
        # .omni/eval_runs.sqlite3 (use ``omni-hub eval-run-show`` v0.42+).
        adapter_counts: dict[str, int] = {}
        for r in run.per_case_results:
            adapter_counts[r.adapter_used or "unknown"] = (
                adapter_counts.get(r.adapter_used or "unknown", 0) + 1
            )
        return {
            "run_id": run.run_id,
            "pack_id": run.pack_id,
            "judge_name": run.judge_name,
            "composite_score": run.composite_score,
            "pass_rate": run.pass_rate,
            "pass_rate_by_class": run.pass_rate_by_class,
            "case_count": len(run.per_case_results),
            "adapter_counts": adapter_counts,
        }

    return eval_run


def make_eval_promote(workspace: Path):
    workspace_root = workspace.resolve()

    def eval_promote(spec: OperationSpec) -> dict:
        from .evals import propose_pack_upgrade
        domain = str(spec.payload.get("domain", "")).strip()
        new_version = str(spec.payload.get("new_version", "v0.2"))
        if not domain:
            raise ValueError("domain is required")
        candidate = propose_pack_upgrade(workspace_root, domain, new_version)
        return candidate.to_dict()

    return eval_promote


def make_app_intent_route(workspace: Path):
    def app_intent_route(spec: OperationSpec) -> dict:
        from .app import AppIntentRouter
        from .channels.base import InboundMessage

        body = str(spec.payload.get("query") or spec.payload.get("body") or "").strip()
        subject = str(spec.payload.get("subject", "")).strip()
        sender = str(spec.payload.get("sender", "cli-user"))
        channel = str(spec.payload.get("channel", "cli"))
        if not body and not subject:
            raise ValueError("query (or body) is required")
        inbound = InboundMessage.new(
            channel=channel, sender=sender, body=body, subject=subject,
        )
        router = AppIntentRouter()
        decision = router.route(inbound)
        return {
            "inbound": inbound.to_dict(),
            "decision": decision.to_dict(),
        }

    return app_intent_route


# ---------------------------------------------------------------------------
# v0.39 — read paths + CRUD wrappers (so CLI subcommands map 1:1)
# ---------------------------------------------------------------------------


def make_user_list(workspace: Path):
    workspace_root = workspace.resolve()

    def user_list(spec: OperationSpec) -> dict:
        from .users import UserProfileStore
        store = UserProfileStore(workspace_root)
        users = store.list()
        return {"count": len(users), "users": [u.to_dict() for u in users]}

    return user_list


def make_user_enroll(workspace: Path):
    workspace_root = workspace.resolve()

    def user_enroll(spec: OperationSpec) -> dict:
        from .users import UserProfileStore
        handle = str(spec.payload.get("handle", "")).strip()
        if not handle:
            raise ValueError("handle is required")
        store = UserProfileStore(workspace_root)
        return store.enroll(handle=handle).to_dict()

    return user_enroll


def make_user_approve(workspace: Path):
    workspace_root = workspace.resolve()

    def user_approve(spec: OperationSpec) -> dict:
        from .users import UserProfileStore
        user_id = str(spec.payload.get("user_id", "")).strip()
        if not user_id:
            raise ValueError("user_id is required")
        store = UserProfileStore(workspace_root)
        return store.approve(user_id).to_dict()

    return user_approve


def make_user_set_persona(workspace: Path):
    workspace_root = workspace.resolve()

    def user_set_persona(spec: OperationSpec) -> dict:
        from .users import UserProfileStore
        user_id = str(spec.payload.get("user_id", "")).strip()
        block = str(spec.payload.get("block", ""))
        if not user_id:
            raise ValueError("user_id is required")
        return UserProfileStore(workspace_root).set_persona_block(user_id, block).to_dict()

    return user_set_persona


def make_user_memory_recall(workspace: Path):
    workspace_root = workspace.resolve()

    def user_memory_recall(spec: OperationSpec) -> dict:
        from .users import PerUserMemoryStore
        user_id = str(spec.payload.get("user_id", "")).strip()
        if not user_id:
            raise ValueError("user_id is required")
        entries = PerUserMemoryStore(workspace_root).list_recall(
            user_id, limit=int(spec.payload.get("limit", 50)),
        )
        return {"count": len(entries), "entries": [e.to_dict() for e in entries]}

    return user_memory_recall


def make_user_memory_archival(workspace: Path):
    workspace_root = workspace.resolve()

    def user_memory_archival(spec: OperationSpec) -> dict:
        from .users import PerUserMemoryStore
        user_id = str(spec.payload.get("user_id", "")).strip()
        query = str(spec.payload.get("query", "")).strip()
        if not user_id or not query:
            raise ValueError("user_id and query are required")
        hits = PerUserMemoryStore(workspace_root).search_archival(
            user_id, query, limit=int(spec.payload.get("limit", 20)),
        )
        return {"count": len(hits), "hits": [h.to_dict() for h in hits]}

    return user_memory_archival


def make_calendar_list(workspace: Path):
    workspace_root = workspace.resolve()

    def calendar_list(spec: OperationSpec) -> dict:
        from datetime import UTC, datetime, timedelta
        from .scheduling import CalendarStore
        user_id = str(spec.payload.get("user_id", "")).strip()
        if not user_id:
            raise ValueError("user_id is required")
        days_ahead = int(spec.payload.get("days_ahead", 7))
        now = datetime.now(UTC)
        events = CalendarStore(workspace_root).list_events(
            user_id, window_start=now, window_end=now + timedelta(days=days_ahead),
        )
        return {"count": len(events), "events": [e.to_dict() for e in events]}

    return calendar_list


def make_task_list_personal(workspace: Path):
    workspace_root = workspace.resolve()

    def task_list_personal(spec: OperationSpec) -> dict:
        from .scheduling import PersonalTaskStore, TaskStatus
        user_id = str(spec.payload.get("user_id", "")).strip()
        if not user_id:
            raise ValueError("user_id is required")
        status_raw = spec.payload.get("status")
        status = None
        if status_raw:
            try:
                status = TaskStatus(str(status_raw))
            except ValueError:
                status = None
        tasks = PersonalTaskStore(workspace_root).list(
            user_id=user_id, status=status,
            limit=int(spec.payload.get("limit", 50)),
        )
        return {"count": len(tasks), "tasks": [t.to_dict() for t in tasks]}

    return task_list_personal


def make_task_done(workspace: Path):
    workspace_root = workspace.resolve()

    def task_done(spec: OperationSpec) -> dict:
        from .scheduling import PersonalTaskStore, TaskStatus
        task_id = str(spec.payload.get("task_id", "")).strip()
        if not task_id:
            raise ValueError("task_id is required")
        return PersonalTaskStore(workspace_root).update_status(
            task_id, TaskStatus.DONE,
        ).to_dict()

    return task_done


def make_project_list(workspace: Path):
    workspace_root = workspace.resolve()

    def project_list(spec: OperationSpec) -> dict:
        from .projects import ProjectStatus, ProjectStore
        status_raw = spec.payload.get("status")
        status = None
        if status_raw:
            try:
                status = ProjectStatus(str(status_raw))
            except ValueError:
                status = None
        projects = ProjectStore(workspace_root).list(
            user_id=spec.payload.get("user_id") or None,
            status=status,
            limit=int(spec.payload.get("limit", 50)),
        )
        return {"count": len(projects), "projects": [p.to_dict() for p in projects]}

    return project_list


def make_project_show(workspace: Path):
    workspace_root = workspace.resolve()

    def project_show(spec: OperationSpec) -> dict:
        from .projects import ProjectStore
        project_id = str(spec.payload.get("project_id", "")).strip()
        if not project_id:
            raise ValueError("project_id is required")
        store = ProjectStore(workspace_root)
        project = store.get(project_id)
        if project is None:
            return {"project_id": project_id, "found": False}
        subtasks = store.list_subtasks(project_id)
        return {
            "found": True, "project": project.to_dict(),
            "subtask_count": len(subtasks),
            "subtasks": [s.to_dict() for s in subtasks],
        }

    return project_show


def make_finance_watch_create(workspace: Path):
    workspace_root = workspace.resolve()

    def finance_watch_create(spec: OperationSpec) -> dict:
        import secrets
        from .finance_ops import AlertRule, FinanceAnalyst
        user_id = str(spec.payload.get("user_id", "")).strip()
        instrument = str(spec.payload.get("instrument", "")).strip()
        expression = str(spec.payload.get("expression", "")).strip()
        if not all((user_id, instrument, expression)):
            raise ValueError("user_id, instrument, expression all required")
        rule = AlertRule(
            rule_id=f"al_{secrets.token_hex(6)}",
            user_id=user_id, instrument=instrument,
            expression=expression,
            channel=str(spec.payload.get("channel", "email")),
        )
        FinanceAnalyst(workspace_root).watch_create(rule)
        return rule.to_dict()

    return finance_watch_create


def make_finance_watch_list(workspace: Path):
    workspace_root = workspace.resolve()

    def finance_watch_list(spec: OperationSpec) -> dict:
        from .finance_ops import FinanceAnalyst
        rules = FinanceAnalyst(workspace_root).list_alerts(
            user_id=spec.payload.get("user_id"),
        )
        return {"count": len(rules), "rules": [r.to_dict() for r in rules]}

    return finance_watch_list


def make_finance_portfolio_stats(workspace: Path):
    workspace_root = workspace.resolve()

    def finance_portfolio_stats(spec: OperationSpec) -> dict:
        from .finance_ops import FinanceAnalyst
        user_id = str(spec.payload.get("user_id", "")).strip()
        if not user_id:
            raise ValueError("user_id is required")
        return FinanceAnalyst(workspace_root).portfolio_stats(user_id).to_dict()

    return finance_portfolio_stats


# ---------------------------------------------------------------------------
# v0.38 — Functional layer operations (the missing 8)
# ---------------------------------------------------------------------------


def make_inbox_classify(workspace: Path):
    def inbox_classify(spec: OperationSpec) -> dict:
        from .channels.base import InboundMessage
        from .inbox import ForwardedContentRouter

        body = str(spec.payload.get("body", "")).strip()
        subject = str(spec.payload.get("subject", ""))
        sender = str(spec.payload.get("sender", "cli-user"))
        channel = str(spec.payload.get("channel", "cli"))
        if not body and not subject:
            raise ValueError("body or subject is required")
        msg = InboundMessage.new(
            channel=channel, sender=sender, body=body, subject=subject,
        )
        decision = ForwardedContentRouter().classify(
            msg,
            default_user_id=str(spec.payload.get("user_id", "")),
            default_domain=str(spec.payload.get("default_domain", "")),
        )
        return decision.to_dict()

    return inbox_classify


def make_project_plan(workspace: Path):
    workspace_root = workspace.resolve()

    def project_plan(spec: OperationSpec) -> dict:
        from .projects import ProjectStore

        user_id = str(spec.payload.get("user_id", "")).strip()
        title = str(spec.payload.get("title", "")).strip()
        description = str(spec.payload.get("description", ""))
        if not user_id:
            raise ValueError("user_id is required")
        if not title:
            raise ValueError("title is required")
        store = ProjectStore(workspace_root)
        project = store.create(
            user_id=user_id, title=title, description=description,
        )
        return project.to_dict()

    return project_plan


def make_pptx_build(workspace: Path):
    workspace_root = workspace.resolve()

    def pptx_build(spec: OperationSpec) -> dict:
        from .pptx import DeckOutline, Slide, Bullet, StubPPTXBuilder

        outline_raw = spec.payload.get("outline") or {}
        if not isinstance(outline_raw, dict) or not outline_raw.get("title"):
            raise ValueError("outline (with at least title) is required")
        outline = DeckOutline(
            title=str(outline_raw["title"]),
            author=str(outline_raw.get("author", "")),
            audience=str(outline_raw.get("audience", "")),
            theme=str(outline_raw.get("theme", "default")),
            slides=[
                Slide(
                    layout=str(s.get("layout", "title-and-content")),
                    title=str(s.get("title", "")),
                    subtitle=str(s.get("subtitle", "")),
                    bullets=[
                        Bullet(
                            text=str(b.get("text", "")),
                            level=int(b.get("level", 0)),
                            bold=bool(b.get("bold", False)),
                            italic=bool(b.get("italic", False)),
                        )
                        for b in (s.get("bullets") or [])
                    ],
                    speaker_notes=str(s.get("speaker_notes", "")),
                    image_paths=list(s.get("image_paths") or []),
                )
                for s in (outline_raw.get("slides") or [])
            ],
        )
        output_rel = str(spec.payload.get("output_path", "vault/decks/out.pptx"))
        output_path = workspace_root / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        builder = StubPPTXBuilder()
        if not builder.available():
            return {
                "skipped": True,
                "reason": "pptx-omni broker not on PATH; install "
                          "agent-harness/integrations/pptx/ first",
                "outline_slide_count": outline.slide_count(),
            }
        result = builder.render(outline, output_path)
        return result.to_dict()

    return pptx_build


def make_calendar_add(workspace: Path):
    workspace_root = workspace.resolve()

    def calendar_add(spec: OperationSpec) -> dict:
        from datetime import datetime
        from .scheduling import CalendarStore

        user_id = str(spec.payload.get("user_id", "")).strip()
        summary = str(spec.payload.get("summary", "")).strip()
        start_raw = str(spec.payload.get("start", "")).strip()
        end_raw = str(spec.payload.get("end", "")).strip()
        if not all((user_id, summary, start_raw, end_raw)):
            raise ValueError("user_id, summary, start, end are all required")
        start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        store = CalendarStore(workspace_root)
        event = store.add_event(
            user_id=user_id, summary=summary, start=start, end=end,
            description=str(spec.payload.get("description", "")),
            location=str(spec.payload.get("location", "")),
            categories=list(spec.payload.get("categories") or []),
        )
        return event.to_dict()

    return calendar_add


def make_schedule_plan(workspace: Path):
    workspace_root = workspace.resolve()

    def schedule_plan(spec: OperationSpec) -> dict:
        from datetime import UTC, datetime, timedelta
        from .scheduling import (
            CalendarStore, PersonalTaskStore, TimeBlockPlanner, TaskStatus,
        )

        user_id = str(spec.payload.get("user_id", "")).strip()
        if not user_id:
            raise ValueError("user_id is required")
        days_ahead = int(spec.payload.get("days_ahead", 7))
        window_start = datetime.now(UTC)
        window_end = window_start + timedelta(days=max(1, days_ahead))

        cal = CalendarStore(workspace_root)
        events = cal.list_events(
            user_id, window_start=window_start, window_end=window_end,
        )
        tasks_store = PersonalTaskStore(workspace_root)
        open_tasks = tasks_store.list(user_id=user_id, status=TaskStatus.OPEN)
        planner = TimeBlockPlanner()
        blocks = planner.plan(
            open_tasks, events,
            window_start=window_start, window_end=window_end,
        )
        return {
            "user_id": user_id,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "task_count": len(open_tasks),
            "event_count": len(events),
            "blocks": [b.to_dict() for b in blocks],
        }

    return schedule_plan


def make_task_add(workspace: Path):
    workspace_root = workspace.resolve()

    def task_add(spec: OperationSpec) -> dict:
        from .scheduling import PersonalTaskStore, TaskCategory

        user_id = str(spec.payload.get("user_id", "")).strip()
        title = str(spec.payload.get("title", "")).strip()
        if not user_id:
            raise ValueError("user_id is required")
        if not title:
            raise ValueError("title is required")
        category_raw = str(spec.payload.get("category", "other"))
        try:
            category = TaskCategory(category_raw)
        except ValueError:
            category = TaskCategory.OTHER
        store = PersonalTaskStore(workspace_root)
        task = store.add(
            user_id=user_id, title=title,
            description=str(spec.payload.get("description", "")),
            category=category,
            priority=int(spec.payload.get("priority", 3)),
            estimated_minutes=int(spec.payload.get("estimated_minutes", 30)),
            due_at=str(spec.payload.get("due_at", "")),
        )
        return task.to_dict()

    return task_add


def make_finance_screen(workspace: Path):
    workspace_root = workspace.resolve()

    def finance_screen(spec: OperationSpec) -> dict:
        from .finance_ops import FinanceAnalyst, ScreenCriteria

        criteria = ScreenCriteria(
            domain=str(spec.payload.get("domain", "finance")),
            tickers=list(spec.payload.get("tickers") or []),
            sector=str(spec.payload.get("sector", "")),
            market=str(spec.payload.get("market", "")),
        )
        analyst = FinanceAnalyst(workspace_root)
        signals = analyst.screen(criteria)
        return {
            "criteria": criteria.to_dict(),
            "count": len(signals),
            "signals": [s.to_dict() for s in signals],
        }

    return finance_screen


def make_order_propose(workspace: Path):
    workspace_root = workspace.resolve()

    def order_propose(spec: OperationSpec) -> dict:
        from .finance_ops import (
            OrderIntent, OrderSide, OrderType, risk_check,
        )
        from .proposals import PENDING, Proposal, ProposalStore

        user_id = str(spec.payload.get("user_id", "")).strip()
        instrument = str(spec.payload.get("instrument", "")).strip()
        side_raw = str(spec.payload.get("side", "")).lower()
        qty = float(spec.payload.get("qty", 0))
        order_type_raw = str(spec.payload.get("order_type", "market")).lower()
        if not all((user_id, instrument, side_raw)) or qty <= 0:
            raise ValueError("user_id, instrument, side, qty (>0) are required")
        intent = OrderIntent.new(
            user_id=user_id, instrument=instrument,
            side=OrderSide(side_raw), qty=qty,
            order_type=OrderType(order_type_raw),
            limit_price=spec.payload.get("limit_price"),
            stop_price=spec.payload.get("stop_price"),
            rationale=str(spec.payload.get("rationale", "")),
        )
        risk = risk_check(
            intent,
            portfolio_value_usd=float(spec.payload.get("portfolio_value_usd", 0.0)),
            estimated_price=spec.payload.get("estimated_price"),
        )
        intent.risk_check = risk

        # Land the intent + risk audit in the Proposal store as
        # kind=order_intent — never executes from main repo.
        store = ProposalStore(workspace_root)
        prop = Proposal(
            kind="order_intent",
            state=PENDING,
            title=(
                f"OrderIntent {intent.side.value.upper()} "
                f"{intent.qty} {intent.instrument}"
            ),
            summary=(
                f"{intent.type.value} {intent.side.value} {intent.qty}@"
                f"{intent.limit_price or intent.stop_price or 'MARKET'} — "
                f"risk_passes={risk.passes}, "
                f"position_pct={risk.position_pct_of_portfolio:.1%}"
            ),
            payload=intent.to_dict(),
            source_path=f"finance_ops/order/{intent.intent_id}",
            suggested_action="execute_via_broker_after_approve",
            confidence=0.5 if risk.passes else 0.0,
        )
        store.store(prop)
        return {
            "intent_id": intent.intent_id,
            "proposal_id": prop.proposal_id,
            "risk_passes": risk.passes,
            "warnings": risk.warnings,
            "hard_blocks": risk.hard_blocks,
            "intent": intent.to_dict(),
        }

    return order_propose


# ---------------------------------------------------------------------------
# v0.19 — Interface Plane + Application Plane + skill-stub operations
# ---------------------------------------------------------------------------


def _default_channel_registry():
    """Lazy-build a ChannelRegistry seeded with the stdlib channels we ship.

    Imports inline so a builtins-import does not pull channels into every
    cold start.
    """

    from .channels import (
        CLIChannel,
        DiscordChannel,
        EmailChannel,
        FeishuChannel,
        MCPChannel,
        ChannelRegistry,
    )

    registry = ChannelRegistry()
    registry.register(CLIChannel())
    registry.register(MCPChannel())
    registry.register(EmailChannel())             # Email auto-detects env config
    registry.register(FeishuChannel())
    registry.register(DiscordChannel())
    return registry


def make_channel_list(workspace: Path):
    def channel_list(spec: OperationSpec) -> dict:
        registry = _default_channel_registry()
        return {
            "names": registry.names(),
            "health": [h.to_dict() for h in registry.health()],
        }

    return channel_list


def make_channel_health(workspace: Path):
    def channel_health(spec: OperationSpec) -> dict:
        name = str(spec.payload.get("name", "")).strip()
        registry = _default_channel_registry()
        if not name:
            return {"names": registry.names(), "error": "name is required"}
        channel = registry.get(name)
        return channel.health_check().to_dict()

    return channel_health


def make_app_report_build(workspace: Path):
    workspace_root = workspace.resolve()

    def app_report_build(spec: OperationSpec) -> dict:
        from .app import ReportOrchestrator, ReportPeriod

        period_raw = str(spec.payload.get("period", "daily")).lower()
        try:
            period = ReportPeriod(period_raw)
        except ValueError as exc:
            raise ValueError(
                f"unknown period {period_raw!r}; expected daily|weekly|monthly"
            ) from exc
        orchestrator = ReportOrchestrator(workspace_root)
        narrate = bool(spec.payload.get("narrate", False))
        if narrate:
            summary, narrative_req = orchestrator.build_with_narrative(
                period,
                target_audience=str(spec.payload.get("audience", "self")),
                additional_notes=str(spec.payload.get("notes", "")),
                trace_id=str(spec.trace_id or ""),
            )
        else:
            summary = orchestrator.build(period)
            narrative_req = None

        persist = bool(spec.payload.get("persist", False))
        persisted_path: str | None = None
        if persist:
            reports_dir = workspace_root / "vault" / "40_Reports" / "app"
            reports_dir.mkdir(parents=True, exist_ok=True)
            from datetime import UTC, datetime
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            target = reports_dir / f"{period.value}-{stamp}.md"
            target.write_text(summary.markdown, encoding="utf-8")
            persisted_path = str(target.relative_to(workspace_root))

        narrative_task_id = ""
        if narrate and narrative_req is not None:
            # v0.26 — enqueue a claude-lane task carrying the markdown
            # summary; the worker writes Proposal(kind=generation) which
            # the human approves to land the narrative.
            from .queue import TaskQueue

            queue = TaskQueue(workspace_root)
            task = queue.enqueue(
                lane="claude",
                packet=narrative_req.to_packet(),
                idempotency_key=f"report-narrate-{period.value}-{summary.window_end}",
            )
            narrative_task_id = task.task_id
            summary.narrative_task_id = narrative_task_id

        out = summary.to_dict()
        if persisted_path:
            out["persisted_path"] = persisted_path
        if narrative_task_id:
            out["narrative_task_id"] = narrative_task_id
        return out

    return app_report_build


def make_app_route_task(workspace: Path):
    def app_route_task(spec: OperationSpec) -> dict:
        from .app import TaskRouter
        from .channels.base import InboundMessage

        body = str(spec.payload.get("query") or spec.payload.get("body") or "").strip()
        subject = str(spec.payload.get("subject", "")).strip()
        sender = str(spec.payload.get("sender", "cli-user"))
        channel = str(spec.payload.get("channel", "cli"))
        if not body and not subject:
            raise ValueError("query (or body) is required")
        inbound = InboundMessage.new(
            channel=channel, sender=sender, body=body, subject=subject,
        )
        router = TaskRouter()
        decision = router.route(inbound)
        return {
            "inbound": inbound.to_dict(),
            "decision": decision.to_dict(),
            "reply_template": router.reply_template(inbound, decision).to_dict(),
        }

    return app_route_task


def make_skill_stubs_sync(workspace: Path):
    workspace_root = workspace.resolve()

    def skill_stubs_sync(spec: OperationSpec) -> dict:
        from .skill_stubs import (
            regenerate_all, regenerate_foundation, regenerate_functional,
        )

        skills_root = str(spec.payload.get("skills_root", ".agents/skills"))
        layers_raw = spec.payload.get("layers") or "all"
        layers = (
            {"foundation", "functional", "domain"}
            if layers_raw == "all"
            else set(layers_raw if isinstance(layers_raw, list) else [layers_raw])
        )

        actions: list = []
        if "foundation" in layers:
            actions.extend(regenerate_foundation(skills_root, workspace=workspace_root))
        if "functional" in layers:
            actions.extend(regenerate_functional(skills_root, workspace=workspace_root))
        if "domain" in layers:
            actions.extend(regenerate_all(skills_root, workspace=workspace_root))

        summary: dict[str, int] = {}
        for action in actions:
            summary[action.action] = summary.get(action.action, 0) + 1
        return {
            "skills_root": skills_root,
            "layers": sorted(layers),
            "total": len(actions),
            "summary": summary,
            "actions": [
                {"skill_id": a.skill_id, "action": a.action}
                for a in actions
            ],
        }

    return skill_stubs_sync


def make_judge_evaluate(workspace: Path):
    """v0.23 — invoke a Judge against a candidate answer.

    Payload contract:
        domain:     domain slug (required)
        candidate:  candidate answer text (required)
        reference:  optional reference / ground-truth context
        judge:      "heuristic" (default) | "llm"
        rubric:     optional dict[str, float] dimension weights
    """

    workspace_root = workspace.resolve()

    def judge_evaluate(spec: OperationSpec) -> dict:
        from .judge import HeuristicJudge, JudgeRequest, LLMJudge

        domain = str(spec.payload.get("domain", "")).strip()
        candidate = str(spec.payload.get("candidate", "")).strip()
        if not domain:
            raise ValueError("domain is required")
        if not candidate:
            raise ValueError("candidate is required")
        reference_raw = spec.payload.get("reference") or ""
        if isinstance(reference_raw, str) and reference_raw.startswith("file://"):
            ref_path = Path(reference_raw[len("file://"):])
            if not ref_path.is_absolute():
                ref_path = workspace_root / ref_path
            reference = ref_path.read_text(encoding="utf-8")
        else:
            reference = str(reference_raw)

        rubric = spec.payload.get("rubric") or {}
        if not isinstance(rubric, dict):
            rubric = {}
        rubric = {str(k): float(v) for k, v in rubric.items()}

        request = JudgeRequest(
            domain=domain,
            candidate=candidate,
            reference=reference,
            rubric=rubric,
            trace_id=str(spec.trace_id or ""),
            metadata={"workspace": str(workspace_root)},
        )

        judge_name = str(spec.payload.get("judge", "heuristic")).lower()
        if judge_name == "llm":
            judge = LLMJudge()
        else:
            judge = HeuristicJudge()

        verdict = judge.evaluate(request)
        return verdict.to_dict()

    return judge_evaluate


# ---------------------------------------------------------------------------
# v0.28 — Meta cross-skill knowledge transfer
# ---------------------------------------------------------------------------


def make_meta_cross_skill_scan(workspace: Path):
    workspace_root = workspace.resolve()

    def meta_cross_skill_scan(spec: OperationSpec) -> dict:
        from .meta import CrossSkillTransfer

        threshold = float(spec.payload.get("signal_threshold", 0.4))
        min_domains = int(spec.payload.get("min_strong_domains", 3))
        min_accepted = int(spec.payload.get("min_accepted_in_strong", 2))
        max_findings = int(spec.payload.get("max_findings", 50))
        transfer = CrossSkillTransfer(
            workspace=workspace_root,
            signal_threshold=threshold,
            min_strong_domains=min_domains,
            min_accepted_in_strong=min_accepted,
        )
        findings = transfer.find_transfers()[:max_findings]
        return {
            "signal_threshold": threshold,
            "min_strong_domains": min_domains,
            "total_findings": len(findings),
            "findings": [f.to_dict() for f in findings],
        }

    return meta_cross_skill_scan


# ---------------------------------------------------------------------------
# v0.29 — A/B test framework
# ---------------------------------------------------------------------------


def make_ab_test_run(workspace: Path):
    workspace_root = workspace.resolve()

    def ab_test_run(spec: OperationSpec) -> dict:
        from .ab import ABTestRunner, ABTestStore, Variant

        domain = str(spec.payload.get("domain", "")).strip()
        if not domain:
            raise ValueError("domain is required")
        candidate_a = str(spec.payload.get("candidate_a", "")).strip()
        candidate_b = str(spec.payload.get("candidate_b", "")).strip()
        if not candidate_a or not candidate_b:
            raise ValueError("candidate_a and candidate_b are required")
        a = Variant(
            label=str(spec.payload.get("label_a", "A")),
            candidate=candidate_a,
            notes=str(spec.payload.get("notes_a", "")),
        )
        b = Variant(
            label=str(spec.payload.get("label_b", "B")),
            candidate=candidate_b,
            notes=str(spec.payload.get("notes_b", "")),
        )
        reference = str(spec.payload.get("reference", ""))
        rubric = spec.payload.get("rubric") or {}
        if not isinstance(rubric, dict):
            rubric = {}
        rubric = {str(k): float(v) for k, v in rubric.items()}
        judge = str(spec.payload.get("judge", "heuristic")).lower()

        store = ABTestStore(workspace_root)
        run_id = store.new_run_id()
        runner = ABTestRunner(judge_name=judge)
        verdict = runner.run(
            run_id=run_id, domain=domain, a=a, b=b,
            reference=reference, rubric=rubric,
            trace_id=str(spec.trace_id or ""),
        )
        store.record(verdict)
        return verdict.to_dict()

    return ab_test_run


def make_ab_test_list(workspace: Path):
    workspace_root = workspace.resolve()

    def ab_test_list(spec: OperationSpec) -> dict:
        from .ab import ABTestStore

        domain = spec.payload.get("domain")
        limit = int(spec.payload.get("limit", 50))
        store = ABTestStore(workspace_root)
        rows = store.list(
            domain=str(domain) if domain else None,
            limit=limit,
        )
        return {"count": len(rows), "runs": rows}

    return ab_test_list


def make_ab_test_show(workspace: Path):
    workspace_root = workspace.resolve()

    def ab_test_show(spec: OperationSpec) -> dict:
        from .ab import ABTestStore

        run_id = str(spec.payload.get("run_id", "")).strip()
        if not run_id:
            raise ValueError("run_id is required")
        store = ABTestStore(workspace_root)
        verdict = store.get(run_id)
        if verdict is None:
            return {"run_id": run_id, "found": False}
        return {"found": True, **verdict.to_dict()}

    return ab_test_show


def make_ab_test_stats(workspace: Path):
    workspace_root = workspace.resolve()

    def ab_test_stats(spec: OperationSpec) -> dict:
        from .ab import ABTestStore

        domain = spec.payload.get("domain")
        store = ABTestStore(workspace_root)
        return store.win_rate(domain=str(domain) if domain else None)

    return ab_test_stats
