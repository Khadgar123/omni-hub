# Architecture v0.31+ — Application Plane Expansion

**写于 2026-05-28**。这是 v0.19 5-Plane 架构的 **Application Plane 扩展**。Application Plane 从 v0.19 的 2 个组件 (ReportOrchestrator + TaskRouter) 扩展到 9 个,覆盖用户列出的 8 个新功能。

底下 4 个 Plane (Control / Knowledge / Skill / Interface) **不动**;所有新增都在 Application Plane 之上,通过现有契约 (OperationSpec / Proposal[T] / Channel) 与下层交互。

---

## 1. 8 个新功能 → omni-hub 组件映射

| 用户需求 | 已有? | v0.31+ 新组件 | 主仓 stdlib 可做? |
|---|---|---|---|
| 1. 日/周/月报 | ✅ v0.26 | (已完成) | 是 |
| 2. 对话解决各领域难题 | 🟡 部分 | `app/chat.py` 编排器 + UserProfile + 19 skills + Judge | 是 |
| 3. 项目开发 | 🟡 基础 | `projects/` ProjectStore + ProjectLifecycle + claude/codex worker | 是 (主仓), 重 SDK 在 agent-harness |
| 4. PPT 制作 | ❌ | `pptx/` Protocol + agent-harness/integrations/pptx (python-pptx) | 协议是, 实现在 agent-harness |
| 5. 股市 / BTC 操作 | ❌ | `finance_ops/` FinanceAnalyst (只读) + agent-harness/integrations/finance (alpaca-py + ccxt, **下单经 Proposal**) | 分析是, 下单在 agent-harness |
| 6. 个性化聊天 | ❌ | `users/` UserProfileStore + 3-tier memory (core/recall/archival) | 是 |
| 7. 日程 + 任务管理 | ❌ | `scheduling/` CalendarStore (iCal stdlib) + PersonalTaskStore + TimeBlockPlanner | 是 |
| 8. 转发内容 → KB + 任务 | ❌ | `inbox/` ForwardedContentRouter | 是 |

**主仓 100% stdlib 约束保持**。重 SDK (python-pptx / alpaca-py / ccxt / google-calendar API) 全部进 `agent-harness/integrations/`,主仓库只暴露 Protocol。

---

## 2. 5-Plane 架构 (v0.31 更新图)

```
┌────────────────────────────────────────────────────────────────────┐
│ Application Plane (v0.19 → v0.31+ expanded to 9 apps)               │
│   reports/   · daily / weekly / monthly + narrative              ✅ │
│   routing/   · TaskRouter + history bias                         ✅ │
│   inbox/     · forwarded URL/PDF/calendar/task router            🆕 │
│   personalize/ · per-user style + memory                         🆕 │
│   chat/      · conversational orchestrator (router → skill → synth) 🆕│
│   projects/  · project lifecycle (plan → decompose → review)     🆕 │
│   pptx/      · deck generation orchestrator                      🆕 │
│   finance_ops/ · analysis only; orders go through Proposal[T]   🆕 │
│   scheduling/ · calendar + personal-task + time-block planner    🆕 │
├────────────────────────────────────────────────────────────────────┤
│ Interface Plane                  · CLI · MCP · Email · Feishu · Discord │
├────────────────────────────────────────────────────────────────────┤
│ Skill Plane          · 19 vertical skills + Judge + A/B + CrossSkill │
├────────────────────────────────────────────────────────────────────┤
│ Knowledge Plane      · 29 connectors + ClaimLedger + FTS5/Graph    │
├────────────────────────────────────────────────────────────────────┤
│ Control Plane        · Runner + TaskQueue + Proposal + Workflow    │
└────────────────────────────────────────────────────────────────────┘
```

每个 **app** 是 Skill + Knowledge + Control 的编排,自己**不直接调 LLM**。需要 LLM 生成的步骤 → `task_enqueue --lane claude` → 落 `Proposal[T]` 等人审。

---

## 3. 各 App SOTA 对标 (2026 Q2)

### App #1 — Reports (v0.26 已有)
| 方案 | 决策 |
|---|---|
| ReportOrchestrator (stdlib aggregation) + claude narrate enqueue | ✅ 已用 |
| Notion AI / Reflect | ❌ 不抄,我们 local-first |

### App #2 — Chat (v0.38)
- **2026 SOTA**: router → typed skill → synthesis 三段式;`agents-that-know-when-to-ask-for-help`
- LangGraph (production HITL), Claude Agent SDK (Anthropic-native), DSPy ReAct (compile router)
- omni-hub 已有: TaskRouter + 19 skills + ClaimLedger
- 新加: `app/chat.py` ConversationalOrchestrator — router → context-pack-build → enqueue (if generation needed) → assemble answer

### App #3 — Inbox (v0.33)
- **2026 SOTA**: 转发到秘密地址 → classify → typed handler (Tana 模式)
- 主仓 stdlib 已有: Email channel (imaplib),Channel Protocol
- 新加: `inbox/ForwardedContentRouter` — 检测 URL / PDF / .ics / task 语言 → 分发到 4 个 typed handler

### App #4 — Personalize (v0.31)
- **2026 SOTA**: Letta MemGPT 3-tier (core / recall / archival),Mem0 multi-tenant scoping,Anthropic memory_20250818
- omni-hub 已有: memory_tool (memory_20250818 surface, v0.17-J)
- 新加: `users/UserProfileStore` — 多用户 identity + per-user memory tier 路由 + persona block (Letta 模式)

### App #5 — Chat (depends on v0.31)
- 见 App #2,要求 UserProfile 存在后才能正常运行

### App #6 — Projects (v0.34)
- **2026 SOTA**: plan-decompose-fan-out-review (Devin, Cursor Composer, Aider architect mode);PR-level review,不是 keystroke-level
- omni-hub 已有: WorkflowKernel + TaskQueue + Proposal[T] + claude/codex worker lanes
- 新加: `projects/ProjectStore` (SQLite) + `projects/ProjectLifecycle` (planning → decomposition → review)
- 集成: `omni-hub project-plan` → claude lane enqueue → planner agent → 多个 sub-task → 每个 sub-task review

### App #7 — PPTX (v0.35)
- **2026 SOTA**: LLM emit Python script → python-pptx 渲染真实 .pptx (Anthropic 官方 pptx Skill 的模式)。**反模式**: LLM 直接生成 OOXML。
- 主仓 stdlib: 仅定义 PPTXBuilder Protocol
- agent-harness/integrations/pptx/ 用 python-pptx 实现 (~200 LOC shim)
- CLI: `pptx-build --outline file://outline.md --theme corporate --out deck.pptx`

### App #8 — Finance ops (v0.36)
- **2026 SOTA**: paper-trade + read-only analysis only for personal use;auto-execute 是 SEC/FINRA + hallucination 双重风险
- 主仓 stdlib: `finance_ops/FinanceAnalyst` 只做信号分析 (基于 finance / cn_finance / EDGAR / FRED 已有 connector)
- agent-harness/integrations/finance/: ccxt (crypto) + alpaca-py (US equities) wrapper;**所有下单 emit Proposal(kind=order_intent),人审通过后 broker CLI 执行**
- CLI: `finance-analyse --ticker NVDA`,`finance-watch --create --rule "price > 200"` 创建告警

### App #9 — Scheduling (v0.32)
- **2026 SOTA**: AI calendar layer on top of Google Calendar / Outlook;constraint-solver-based time-blocking (Motion/Reclaim);LLM produce intents, solver place events
- 主仓 stdlib: `scheduling/CalendarStore` (iCal/icalendar stdlib parser) + `scheduling/PersonalTaskStore` + `scheduling/TimeBlockPlanner` (deterministic priority+duration solver)
- agent-harness/integrations/calendar/: Google Calendar API + Outlook Graph API wrappers
- CLI: `cal-add --title "..." --start ... --duration 30m`, `cal-list --window today`, `task-plan --autoplace`

---

## 4. 关键设计决策

### 4.1 用户与多租户 (v0.31)

```python
@dataclass
class UserProfile:
    user_id: str                   # uuid; primary key
    handle: str                    # human-readable (e.g. "hzh")
    persona_block: str             # Letta-style core memory — agent can rewrite
    style_prefs: dict[str, Any]    # {"tone": "terse", "language": "zh-Hans", ...}
    created_at: str
    updated_at: str
```

每个 user 有自己的:
- `vault/users/<user_id>/wiki/` (个人 wiki, 可选, 默认主 wiki 共享)
- `.omni/preference/users/<user_id>/<domain>.jsonl` (per-user preference)
- 3-tier memory: `core` (~10KB, persona/identity), `recall` (last N sessions), `archival` (full history searchable)

**default user** = `hzh` (项目主人),无 user_id 时默认。新用户从 channel 来 → 自动 enroll → 进 PendingUsers,人审通过后转 active。

### 4.2 个人任务 vs Worker 任务 (v0.32)

omni-hub 的 `TaskQueue` 是 **Worker 任务** — 给 claude/codex/python lane 拉去执行的 unit-of-work,有 `lease_epoch` fencing + visibility timeout。

`PersonalTaskStore` 是 **用户任务** — "明天 9 点开会"、"周末买菜"、"5 月底前看完 ACE 论文" 这种,有 due/priority/category,可加到 Calendar。

两者**不混**。`PersonalTask` 可以触发 `Worker Task` (例如"5 月底前看完 ACE 论文" 触发一个 retrieve task),但反向不行。

### 4.3 Inbox 路由策略 (v0.33)

```
forwarded text/URL/PDF/.ics
   → ForwardedContentRouter.classify()
   ├── if URL → capture-url + wiki-propose-research
   ├── if PDF → vault/raw + extract + propose
   ├── if .ics → CalendarStore.import_event
   ├── if 含日期+任务动词 → PersonalTaskStore.add
   └── else → wiki-propose-research (default)
```

分类 v0.33 用启发式 (keyword + 模式匹配),v0.40+ swap LLM-as-Classifier。

### 4.4 PPT 协议 (v0.35)

```python
class PPTXBuilder(Protocol):
    name: str

    def render(
        self,
        outline: DeckOutline,   # 主仓 stdlib 数据类
        *,
        theme: str = "default",
        output_path: Path,
    ) -> PPTXResult: ...
```

`DeckOutline` 是主仓 stdlib dataclass (title, sections, bullets, images);`PPTXBuilder` 实现在 agent-harness;Application Plane 的 `app/pptx.py` 只编排 (LLM 生成 outline → builder 渲染 → 人审)。

### 4.5 Finance 写操作 = Proposal[T] (v0.36)

```python
class FinanceAnalyst:
    """主仓 stdlib — 只读分析"""
    def screen(self, criteria: ScreenCriteria) -> list[StockSignal]: ...
    def watch_create(self, rule: AlertRule) -> Alert: ...
    def portfolio_stats(self) -> PortfolioSnapshot: ...

class OrderIntent:
    """主仓 dataclass — 永远不直接下单"""
    instrument: str          # "NVDA" | "BTC-USD"
    side: Literal["buy", "sell"]
    qty: float
    type: Literal["market", "limit", "stop"]
    limit_price: float | None = None
    risk_check: RiskCheckResult
    # 必经 Proposal(kind="order_intent") 人审
```

下单流程: `finance-order-propose --ticker NVDA --side buy --qty 10 --type limit --limit 195` → emit Proposal → 人审 (含 risk_check 显示资金、仓位、止损) → 通过后由 `agent-harness/integrations/finance/cli` broker 执行 → 写回 fill record。

---

## 5. v0.31 → v0.40 路线图

| 版本 | 目标 | 工作量 | 这次? |
|---|---|---|---|
| **v0.31** | UserProfileStore + Letta 3-tier memory + per-user preference 路径 | 1 天 | ✅ |
| **v0.32** | CalendarStore (iCal stdlib) + PersonalTaskStore + TimeBlockPlanner | 1-2 天 | ✅ |
| **v0.33** | ForwardedContentRouter + 4 typed handlers + Channel 集成 | 1 天 | ✅ |
| **v0.34** | ProjectStore + ProjectLifecycle + project-plan/project-list CLI | 1 天 | ✅ |
| **v0.35** | PPTXBuilder Protocol + DeckOutline dataclass + agent-harness scaffold | 0.5 天 | ✅ |
| **v0.36** | FinanceAnalyst (只读) + OrderIntent + Proposal kind 扩展 | 1 天 | ✅ |
| **v0.37** | TaskRouter 接 UserProfile (style adapter) | 0.5 天 | (优先级低) |
| **v0.38** | ConversationalOrchestrator (chat app) | 1 天 | (优先级低) |
| **v0.39** | Anthropic Skills full v1.2 spec compliance + LangGraph-style HITL checkpoints | 2 天 | (后续) |
| **v0.40** | 端到端真实数据跑 1 周 + 修发现的问题 | 1 周 真实跑 | (后续) |

**本 session 目标: v0.31 - v0.36** (6 个 sub-version,可独立 commit)。

---

## 6. 工程不变量 (继承 v0.10-v0.30 + 新增)

1-8 不变 (见 v0.19 文档)
9. **每个新 app 不直接调 LLM** — 通过 OperationSpec → TaskQueue claude/codex lane → Proposal[T]
10. **写副作用 (calendar / order / 任意 file) 都经 Proposal** — 已有 wiki_update / lint_finding / generation;新增 order_intent / inbox_capture / project_plan
11. **每个新 store SQLite WAL** — `PRAGMA journal_mode=WAL; busy_timeout=30000`
12. **多用户 user_id 默认 = 项目主人** — 不用 Identity / OAuth,单机 local-first;后续若加远程访问再升级

---

## 7. 参考文献

- **Letta MemGPT 3-tier memory** (2024-2026, 现在是 mainline 模式)
- **Mem0 OS 多租户** (2026 Q1, 48k stars)
- **Anthropic memory_20250818 multi-tenant** (2026 Apr)
- **Tana forward-to-secret-address** (PKM 转发模式 SOTA)
- **Motion + Reclaim time-blocking** (constraint solver pattern)
- **alpaca-py + ccxt** (broker SDK 事实标准)
- **python-pptx + Anthropic pptx Skill** (LLM → Python script → pptx 模式)
- **Devin / Cursor / Aider architect mode** (plan-decompose-review)
- **LangGraph HITL checkpoints** (production agent orchestration)

---

_v0.31 开始,主仓 Application Plane 从 2 个组件长到 9 个。Skill Plane / Knowledge Plane / Control Plane 不动。_
