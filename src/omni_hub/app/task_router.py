"""Task Router — conversational entry-point to the Skill Plane (v0.19).

Routes an :class:`omni_hub.channels.InboundMessage` to the most relevant
domain skill by keyword/heuristic match (no LLM call — v0.19 keeps the
main repo stdlib-only).

The router does NOT generate the answer.  It returns a
:class:`RoutingDecision` containing:

* the selected ``skill_id`` (one of the 19 registered skill domains),
* a confidence score,
* runner-up domains for human override,
* a recommended ``OperationSpec`` the caller should run
  (``context_pack_build`` for read-only queries,
  ``task_enqueue --lane claude`` for generation),
* an optional :class:`OutboundMessage` template that the caller can fill
  in after the generation step.

Downstream: the caller (CLI / channel pump) executes the OperationSpec,
attaches the artifact, and dispatches the OutboundMessage via the channel
that delivered the InboundMessage.

For v0.19 the keyword map is hand-curated; v0.23 will swap in an
LLM-as-Judge classifier that updates the map via PreferenceStore.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..channels.base import InboundMessage, OutboundMessage


# ---------------------------------------------------------------------------
# Domain keyword map — keys match domain_schemas.DOMAIN_SCHEMAS keys.
# Each list is ordered by specificity (rarer terms first; common ones last
# act as fallbacks).  English + 中文 keywords are intentionally mixed because
# the channel pump receives both.
# ---------------------------------------------------------------------------


_KEYWORDS: dict[str, list[str]] = {
    "research": [
        "paper", "arxiv", "openalex", "doi", "citation", "venue",
        "论文", "投稿", "iclr", "neurips", "icml", "acl",
    ],
    "ai_progress": [
        "claude", "gpt-5", "gemini", "llama", "anthropic", "openai",
        "rag", "dspy", "gepa", "agent skill", "memory tool",
        "ai 进展", "大模型",
    ],
    "engineering": [
        "bug", "stack trace", "compile error", "type error", "test failure",
        "framework", "library", "ide", "lsp", "ci pipeline",
        "代码", "编译", "调试", "重构",
    ],
    "meta": [
        "omni-hub", "this repo", "本仓库", "control plane", "knowledge plane",
        "skill plane", "interface plane", "application plane",
        "skill compile", "preference store", "wiki-lint",
        "迭代系统", "改 omni",
    ],
    "fitness_wellness": [
        "workout", "training", "rep", "set", "macro", "calorie", "rct",
        "supplement", "sleep", "recovery",
        "健身", "增肌", "减脂", "蛋白质", "营养", "睡眠", "康复",
    ],
    "cooking": [
        "recipe", "knead", "braise", "ferment", "sous-vide",
        "做饭", "菜谱", "食谱", "烘焙", "发酵", "调味",
        "做什么菜", "今晚做什么", "做啥", "炒菜",
        "红烧肉", "麻婆豆腐", "宫保鸡丁", "鱼香肉丝",
    ],
    "photography": [
        "iso", "aperture", "shutter speed", "lens", "raw file", "lightroom",
        "exposure", "composition", "光圈", "快门", "构图", "胶片",
    ],
    "fashion": [
        "outfit", "ootd", "season", "ss26", "fw25", "tailoring",
        "穿搭", "搭配", "时装", "穿衣",
    ],
    "chat_relationships": [
        "relationship", "conversation", "social", "boundary",
        "聊天", "回复", "对话", "关系",
    ],
    "travel": [
        "itinerary", "visa", "flight", "hotel", "ryokan", "passport",
        "旅游", "行程", "签证", "航班", "酒店",
    ],
    "marketing": [
        "campaign", "ctr", "ltv", "cac", "growth hack", "viral",
        "营销", "推广", "投放", "增长", "转化",
    ],
    "enterprise": [
        "due diligence", "company report", "competitor analysis",
        "funding round", "headcount",
        "公司分析", "企业分析", "尽调", "竞品", "融资",
    ],
    "finance": [
        "stock", "ticker", "earnings", "10-k", "fred series", "interest rate",
        "options", "futures",
        "股票", "美股", "a股", "财报", "利率",
    ],
    "us_policy": [
        "federal register", "scotus", "congress", "bill", "regulation",
        "us policy", "美政策", "美国政策",
    ],
    "cn_policy": [
        "国务院", "央行", "证监会", "网信办", "五年规划", "中央财办",
        "国发", "中政策", "中国政策",
    ],
    "international_relations": [
        "geopolitics", "treaty", "sanction", "summit", "alliance",
        "国际关系", "中美", "外交", "贸易战", "制裁",
    ],
    "agent_systems": [
        "letta", "graphiti", "mem0", "swe-agent", "openhands", "promptfoo",
        "agent 框架", "agent system",
    ],
    "social_en": [
        "tweet", "twitter", "reddit", "hn", "hacker news",
    ],
    "social_zh": [
        "微博", "小红书", "知乎", "公众号", "weixin",
    ],
}


# v0.37: per-domain INTENT phrases — higher-weight than entity keywords.
# When a query mentions "OpenAI" (entity in ai_progress) AND "组织架构" (intent
# phrase in enterprise), the intent wins.  Heuristic until v0.40 LLM
# classifier; calibrated by the 2026-05-28 review's failure case.
_INTENT_PHRASES: dict[str, list[str]] = {
    "research": [
        "论文综述", "调研一下", "compare papers", "literature review",
        "research gap", "评审意见",
    ],
    "engineering": [
        "stack trace", "refactor this", "ci 挂了", "测试为什么失败",
        "为什么 test 挂了", "代码评审",
    ],
    "ai_progress": [
        "模型对比", "release note", "model card", "model evaluation",
    ],
    "meta": [
        "omni-hub 接下来", "重构 omni", "应该 build 还是 use",
        "下一步 v0", "skill 注册",
    ],
    "fitness_wellness": [
        "训练计划", "饮食方案", "RCT meta-analysis", "睡眠改善",
    ],
    "cooking": [
        "今晚做什么", "替换食材", "怎么做", "怎么炒",
    ],
    "photography": [
        "构图建议", "曝光建议", "镜头选择", "raw 后期",
    ],
    "fashion": [
        "搭配方案", "穿什么", "outfit ideas",
    ],
    "chat_relationships": [
        "怎么回复", "如何应对", "感情建议", "boundary advice",
    ],
    "travel": [
        "行程规划", "签证流程", "几天合适", "best time to visit",
    ],
    "marketing": [
        "投放策略", "增长方案", "转化优化", "营销 case study",
    ],
    "enterprise": [
        # The v0.37 review's failure-case phrases get top billing.
        "组织架构", "公司分析", "值得加入",
        "护城河", "due diligence", "团队组成",
        "投融资", "招聘趋势", "人事变动",
        "企业分析", "竞品分析", "公司情况",
    ],
    "finance": [
        "估值",  "目标价", "进场点位", "出场点位",
        "回撤", "财报解读", "投资建议",
    ],
    "us_policy": [
        "scotus 判决", "bill 通过", "executive order",
        "联邦法规", "美国政策影响",
    ],
    "cn_policy": [
        "政策解读", "部委文件", "五年规划", "央行政策",
        "监管动向",
    ],
    "international_relations": [
        "地缘政治", "外交动向", "中美关系", "制裁影响",
        "局势分析",
    ],
    "agent_systems": [
        "build vs use", "fork vs pin", "agent 框架对比",
    ],
    "social_en": [],
    "social_zh": [],
}


# Compile keyword patterns once at import time for cheap matching.
_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    domain: [re.compile(rf"(?i){re.escape(kw)}") for kw in kws]
    for domain, kws in _KEYWORDS.items()
}


# Intent patterns are weighted ``INTENT_WEIGHT`` × per-hit (vs 1× for keywords).
_INTENT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    domain: [re.compile(rf"(?i){re.escape(kw)}") for kw in kws]
    for domain, kws in _INTENT_PHRASES.items()
}


INTENT_WEIGHT = 3.0
KEYWORD_WEIGHT = 1.0


@dataclass(slots=True)
class RoutingDecision:
    """The router's verdict for one InboundMessage."""

    inbound_trace_id: str
    selected_skill_id: str
    confidence: float                         # 0..1 normalised by match count
    matched_keywords: list[str] = field(default_factory=list)
    runners_up: list[tuple[str, float]] = field(default_factory=list)
    recommended_operation: str = ""           # e.g. "context_pack_build" / "task_enqueue"
    recommended_payload: dict[str, Any] = field(default_factory=dict)
    note: str = ""                            # human-readable explanation
    history_bias_applied: bool = False        # v0.27 — set when prior-turn skill broke a tie

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ConversationTurn:
    """One prior router decision, supplied for v0.27 history-aware routing."""

    trace_id: str
    selected_skill_id: str
    timestamp: str = ""                       # ISO 8601; ordering only
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskRouter:
    """Heuristic InboundMessage → skill_id router.

    Stdlib-only.  Future versions will swap the keyword map for an
    LLM-as-Judge classifier, but the Protocol stays stable.

    v0.27 adds optional ``conversation_history``: when two skills tie on
    keyword score, recent prior turns nudge the decision toward the
    skill that already owns the conversation context.
    """

    DEFAULT_SKILL_ID = "research"             # fall-through default
    HISTORY_BIAS_WINDOW = 5                   # only consider the last N turns

    def __init__(self, *, default_skill_id: str | None = None) -> None:
        self.default_skill_id = default_skill_id or self.DEFAULT_SKILL_ID

    def route(
        self,
        inbound: InboundMessage,
        *,
        conversation_history: list["ConversationTurn"] | None = None,
    ) -> RoutingDecision:
        haystack = " ".join([inbound.body, inbound.subject]).strip()
        if not haystack:
            return RoutingDecision(
                inbound_trace_id=inbound.trace_id,
                selected_skill_id=self.default_skill_id,
                confidence=0.0,
                note="empty body — fell through to default skill",
                recommended_operation="context_pack_build",
                recommended_payload={
                    "query": inbound.subject or "(empty)",
                    "domain": self.default_skill_id,
                    "tier": "standard",
                },
            )

        # v0.37 — Weighted score per domain.  Each keyword hit contributes
        # KEYWORD_WEIGHT (1.0), each intent-phrase hit contributes
        # INTENT_WEIGHT (3.0).  Intent phrases are how we distinguish
        # "OpenAI 最新组织架构" (enterprise intent: 组织架构 × 3.0) from
        # "Claude 4.7 怎么样" (ai_progress entity: Claude × 1.0).
        scores: dict[str, tuple[float, list[str]]] = {}
        for domain, patterns in _PATTERNS.items():
            hits: list[str] = []
            score = 0.0
            for pattern in patterns:
                match = pattern.search(haystack)
                if match:
                    hits.append(match.group(0))
                    score += KEYWORD_WEIGHT
            for pattern in _INTENT_PATTERNS.get(domain, []):
                match = pattern.search(haystack)
                if match:
                    hits.append(match.group(0))
                    score += INTENT_WEIGHT
            if hits:
                scores[domain] = (score, hits)

        if not scores:
            return RoutingDecision(
                inbound_trace_id=inbound.trace_id,
                selected_skill_id=self.default_skill_id,
                confidence=0.0,
                note="no keyword match — fell through to default skill",
                recommended_operation="context_pack_build",
                recommended_payload={
                    "query": haystack[:200],
                    "domain": self.default_skill_id,
                    "tier": "standard",
                },
            )

        ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
        top_domain, (top_hits, top_words) = ranked[0]

        # v0.27 — break ties with conversation history.  If the runner-up
        # matches the top hit-count AND was the skill from a recent
        # prior turn, promote it.
        history_bias_applied = False
        if (
            conversation_history
            and len(ranked) > 1
            and ranked[1][1][0] == top_hits
        ):
            recent_skills = [
                t.selected_skill_id
                for t in conversation_history[-self.HISTORY_BIAS_WINDOW:]
            ]
            for candidate_domain, (hits, words) in ranked:
                if hits != top_hits:
                    break
                if candidate_domain in recent_skills:
                    top_domain = candidate_domain
                    top_hits = hits
                    top_words = words
                    history_bias_applied = True
                    break

        total_hits = sum(c for c, _ in scores.values())
        confidence = top_hits / max(total_hits, 1)
        runners_up = [
            (domain, hits / max(total_hits, 1))
            for domain, (hits, _) in ranked[1:5]
            if domain != top_domain
        ]

        recommended_op, payload, note = self._recommend(top_domain, haystack)
        if history_bias_applied:
            note = f"{note} [history-bias: matched recent skill]"

        return RoutingDecision(
            inbound_trace_id=inbound.trace_id,
            selected_skill_id=top_domain,
            confidence=round(confidence, 3),
            matched_keywords=top_words,
            runners_up=runners_up,
            recommended_operation=recommended_op,
            recommended_payload=payload,
            note=note,
            history_bias_applied=history_bias_applied,
        )

    def reply_template(
        self,
        inbound: InboundMessage,
        decision: RoutingDecision,
    ) -> OutboundMessage:
        """Build an OutboundMessage acknowledging the routing decision.

        Used by channel-pump scripts to send an immediate ack before the
        actual generation runs through claude/codex.  The body intentionally
        carries the trace_id so users can quote it back.
        """

        lines = [
            f"已收到 (trace `{inbound.trace_id}`)",
            "",
            f"路由到 `{decision.selected_skill_id}` 技能 "
            f"(confidence {decision.confidence:.2f},"
            f" 匹配关键词: {', '.join(decision.matched_keywords) or '(无)'})",
        ]
        if decision.runners_up:
            lines.append("候选: " + ", ".join(
                f"`{d}` ({c:.2f})" for d, c in decision.runners_up
            ))
        lines.extend([
            "",
            f"下一步: `{decision.recommended_operation}`",
        ])
        return OutboundMessage.in_reply_to_msg(inbound, "\n".join(lines))

    # ---- internals ----------------------------------------------

    def _recommend(
        self, domain: str, query: str,
    ) -> tuple[str, dict[str, Any], str]:
        """Pick a sensible default operation for the routed domain.

        Most domains start with a context_pack_build (read-only); the
        engineering / meta / enterprise domains often need write actions
        and so are recommended to go through the claude/codex worker lane.
        """

        if domain in {"engineering", "meta", "enterprise"}:
            return (
                "task_enqueue",
                {
                    "lane": "claude",
                    "task_type": domain,
                    "domain_profile": domain,
                    "goal": query[:200],
                },
                f"{domain} 涉及代码 / 长任务,建议走 claude/codex 异步 worker + Proposal[T]",
            )
        return (
            "context_pack_build",
            {
                "query": query[:200],
                "domain": domain,
                "tier": "standard",
            },
            f"{domain} 是知识查询,先 build 一个 context pack",
        )


__all__ = ["ConversationTurn", "RoutingDecision", "TaskRouter"]
