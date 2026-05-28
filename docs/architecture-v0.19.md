# Architecture v0.19+ — 5-Plane 重构

**写于 2026-05-28**。这份文档把 v0.10-v0.18 已经长出来的零件用 **5-Plane 模型** 重新组织,并把用户在 v0.19 提出的 8 层架构需求映射成 omni-hub 的实际工程结构。

---

## 1. 核心判断

v0.10-v0.18 我们已经建好了 **Control Plane + Knowledge Plane** 的地基(60% 完工)。v0.19+ 要做的是把 **Skill Plane / Interface Plane / Application Plane** 这 3 个新 Plane 围绕同一个 Knowledge Plane 长出来——**不要为了新功能改 Knowledge Plane 的契约**。

这是 8 层 → 5 个 Plane 的映射:

| 用户的 8 层 | omni-hub Plane | 是否已有 |
|---|---|---|
| 数据底层存储层 | Knowledge Plane / Storage Layer | ✅ v0.10-v0.18 |
| 数据检索获取层 | Knowledge Plane / Acquisition Layer | ✅ v0.10-v0.18 |
| 领域垂直技能设计层 | Skill Plane / Design Layer | 🟡 部分 (DSPy 5-comp 有) |
| 领域垂直技能层 | Skill Plane / Registry (15 个) | 🟡 1/15 (research-wiki) |
| 技能迭代进化层 | Skill Plane / Evolution Layer | 🟡 GEPA + Preference 已通 |
| 知识库内检索层 | Knowledge Plane / Internal Retrieval | ✅ FTS5 + Graph + Context-Pack |
| 用户交互层 | Interface Plane | 🟡 CLI + MCP, 飞书/Discord/Email 缺 |
| 功能层 | Application Plane | ❌ 日报/对话/路由器全缺 |

---

## 2. 5-Plane 架构图

```
┌────────────────────────────────────────────────────────────────────┐
│ Application Plane                                                  │
│   reports/  · daily / weekly / monthly · trend / decisions          │
│   chat/     · 对话任务路由器 → 选 skill → 编排 → 答复               │
│   tasks/    · 长任务管道 (paper analysis, idea gen, life planning)  │
├────────────────────────────────────────────────────────────────────┤
│ Interface Plane                                                    │
│   CLI · MCP · Email (stdlib imaplib/smtplib)                       │
│   Feishu · Discord    ← Adapter 模式,真实 SDK 进 agent-harness/    │
│   每个 channel 实现统一 Channel Protocol                            │
├────────────────────────────────────────────────────────────────────┤
│ Skill Plane                                                        │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ Skill Design Layer                                            │ │
│   │   DSPy Signature / Module / Metric / Optimizer / CompiledSkill│ │
│   │   Anthropic Skills SKILL.md frontmatter (v1.2)               │ │
│   ├──────────────────────────────────────────────────────────────┤ │
│   │ Skill Registry (15 个垂直技能)                                │ │
│   │   meta · ai-progress · engineering · research                 │ │
│   │   fitness-wellness · cooking · photography · fashion          │ │
│   │   chat-relationships · travel · marketing · enterprise        │ │
│   │   finance · cn-policy · us-policy · international-relations   │ │
│   ├──────────────────────────────────────────────────────────────┤ │
│   │ Skill Evolution Layer                                         │ │
│   │   PreferenceStore (Argilla schema) → harness-compile-skill    │ │
│   │   GEPA optimizer · Judge LLM · A/B test framework             │ │
│   │   Cross-skill knowledge transfer (meta skill 学习其他技能)    │ │
│   └──────────────────────────────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────────────┤
│ Knowledge Plane (v0.10-v0.18,稳定不改)                              │
│   ┌──────────────────────────────────────────────────────────────┐ │
│   │ Acquisition Layer                                             │ │
│   │   retrieve cascade · 18 connectors · domain-source-map        │ │
│   │   (arxiv / openalex / crossref / s2 / github / rss / hn ...)  │ │
│   ├──────────────────────────────────────────────────────────────┤ │
│   │ Storage Layer (Source of Truth)                               │ │
│   │   vault/raw/ · vault/evidence/ · vault/wiki/                  │ │
│   │   .omni/claims.jsonl (bitemporal, append-only)                │ │
│   │   .omni/event_log.jsonl (audit)                               │ │
│   │   .omni/projections.sqlite3 (snapshot pointers + cursors)     │ │
│   ├──────────────────────────────────────────────────────────────┤ │
│   │ Internal Retrieval Layer                                      │ │
│   │   FTS5 (wiki + claims) · GraphRAG (entities + communities)    │ │
│   │   Context-Pack (progressive disclosure: minimal/standard/expanded) │
│   │   Cohere Rerank v4 / Voyage rerank-2.5 (key-gated)            │ │
│   └──────────────────────────────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────────────┤
│ Control Plane (v0.7-v0.18,稳定不改)                                 │
│   OperationRunner · TaskQueue · ProposalStore · AuditLogger        │
│   Policy engine (RiskLevel + budget + violations)                  │
│   WorkflowKernel (Signal + Query + suspend/resume)                 │
│   ProjectionRegistry (Iceberg-style snapshot pointer swap)         │
└────────────────────────────────────────────────────────────────────┘
```

### 横切关注点 (cross-cutting,不属于任何单一 Plane)

| Concern | 实现位置 | 状态 |
|---|---|---|
| **Audit** | `.omni/event_log.jsonl` + `trace_id` 贯穿 | ✅ v0.10-v0.18 |
| **Policy** | `policy.py::PolicyDecision` (allow / require_approval / require_sandbox / deny) | ✅ v0.18-D |
| **Trace** | `OperationSpec.trace_id` UUID4 auto-inject | ✅ v0.18-C |
| **Eval** | PreferenceStore + harness-compile-skill + Judge LLM | 🟡 部分 |
| **OTel** | OpenTelemetry collector | ❌ 推迟到 P2 |
| **Identity** | 单用户 → 后续 RBAC | ❌ 推迟 |

---

## 3. 完成度评估

| Plane | 已建 | 待建 (v0.19+) | 完成度 |
|---|---|---|---|
| Control | OperationRunner + TaskQueue + Proposal + Policy + Audit + WorkflowKernel + ProjectionRegistry | OPA daemon, OTel collector | **85%** |
| Knowledge - Storage | ClaimLedger + vault layout + projections.sqlite3 + event_log | LanceDB embedded vector | **80%** |
| Knowledge - Acquisition | 18 connectors + cascade | 中文 connector (Bilibili/小红书/Zhihu) + 财经 (Polygon/Tushare) + 政策 (gov.cn) | **65%** |
| Knowledge - Internal Retrieval | FTS5 + GraphRAG + Context-Pack + 2 reranker | (need API key 才能跑 reranker) | **80%** |
| Skill - Design | DSPy 5-comp + Anthropic Skills 部分规范 | Anthropic Skills v1.2 完整规范 | **70%** |
| Skill - Registry | research-wiki (1 个) | 14 个新技能 stub + 完整化 | **7%** |
| Skill - Evolution | GEPA + Preference (单技能) | Judge LLM + A/B test + cross-skill transfer | **40%** |
| Interface | CLI + MCP | Email (stdlib) + Feishu + Discord adapter | **30%** |
| Application | (无) | Reports orchestrator + Chat router + Long-running tasks | **0%** |

**总完成度 ~55%**。

---

## 4. 各领域 SOTA 对标 (2026 Q2)

### Storage Layer
| 方案 | 类型 | 用还是不用 | 理由 |
|---|---|---|---|
| **SQLite WAL + JSONL append-only** | 已用 | ✅ 用 | 单用户 < 100k claims 完全够,trigger 见下 |
| Apache Iceberg | 表格式 | ❌ 不用 | 单机过度设计;数据量 > 10GB 时再考虑 |
| Delta Lake / Hudi | 同上 | ❌ 同上 | |
| DuckDB | embedded analytical DB | 🟡 待 trigger | ClaimLedger > 100k 行时迁 DuckDB + Parquet |
| LanceDB | embedded vector | 🟡 待 trigger | FTS5 无法满足"语义相似"时引入 |
| pgvector / Pinecone / Weaviate | 远程向量库 | ❌ 不用 | 单用户 local-first 不需要远程依赖 |

### Acquisition Layer
| 方案 | 已用? | 备注 |
|---|---|---|
| arxiv / openalex / crossref / s2 / github / rss / hn / reddit | ✅ 18 个 connector | v0.10-v0.11 |
| **Firecrawl** (web crawler) | 🟡 待加 | 优于自写 BeautifulSoup,有商业 license |
| **Exa.ai** (semantic web search) | 🟡 待加 | 商业 API key-gated |
| **Tavily** (LLM-optimized search) | 🟡 待加 | 商业 API key-gated |
| **Bilibili / 小红书 / Zhihu / 微博** | ❌ 待加 | 健身/穿搭/旅游/做饭/摄影 强相关 (v0.20) |
| **Polygon.io / Alpha Vantage / Tushare** | ❌ 待加 | finance domain (v0.22) |
| **gov.cn / 国务院 / federalregister / whitehouse** | ❌ 待加 | policy domain (v0.21) |
| **Crunchbase / PitchBook / LinkedIn** | ❌ 待加 | enterprise domain (v0.22) |
| **PubMed / 营养学论文** | ❌ 待加 | fitness-wellness (v0.20) |

### Internal Retrieval
| 方案 | 已用? | 备注 |
|---|---|---|
| **SQLite FTS5** | ✅ v0.16 | wiki + claims 全文检索 |
| **GraphRAG-style** (Leiden communities) | ✅ v0.18-J | local + global 双模式 |
| **Cohere Rerank v4** | ✅ v0.17-K (key-gated) | 需 COHERE_API_KEY |
| **Voyage rerank-2.5** | ✅ v0.17-K (key-gated) | 需 VOYAGE_API_KEY |
| **ColBERTv2** late interaction | ❌ 待 trigger | 当 FTS5 + reranker 效果不够时加 |
| **Mem0 OS bitemporal query** | 🟡 部分实现 | wiki-search 已支持 t_valid_to filter |

### Skill Design Layer
| 方案 | 已用? | 备注 |
|---|---|---|
| **DSPy 3 / GEPA** | ✅ v0.18-K | 5-comp 拆分 |
| **Anthropic Skills** (`SKILL.md` frontmatter) | 🟡 部分 | 缺 v1.2 字段 (allowed_tools / sub_skills / metric) |
| **Letta Agent Definitions** | 🟡 参考 | memory blocks + tool blocks 思路已借鉴 |
| **OpenAI Custom GPT JSON** | ❌ 不用 | 商业封闭格式 |

### Skill Evolution Layer
| 方案 | 已用? | 备注 |
|---|---|---|
| **PreferenceStore** (Argilla 兼容) | ✅ v0.15 | 单技能闭环已通 |
| **GEPA optimizer** | ✅ v0.18-K | 已 wire |
| **LLM-as-Judge** (Constitutional AI 思路) | ❌ 待建 v0.23 | 自动评 candidate |
| **A/B test framework** | ❌ 待建 v0.29 | 同查询两 prompt 版本对比 |
| **Cross-skill transfer** | ❌ 待建 v0.28 | meta skill 学习其他技能模式 |

### Interface Plane
| 方案 | 已用? | 备注 |
|---|---|---|
| **CLI** (argparse) | ✅ v0.1+ | 主入口 |
| **MCP server** (官方 SDK) | ✅ v0.14 | 16 个 wiki/claims/memory 工具 |
| **Email** (`imaplib` + `smtplib` stdlib) | ❌ v0.25 | 100% stdlib,可进 main repo |
| **Feishu** (lark-oapi) | ❌ v0.24 | 重依赖,进 `agent-harness/integrations/feishu/` |
| **Discord** (discord.py) | ❌ v0.25 | 重依赖,进 `agent-harness/integrations/discord/` |
| **Slack Bolt** | ❌ 不做 | 没需求 |
| **Microsoft Teams Bot** | ❌ 不做 | 没需求 |

### Application Plane
| 方案 | 参考? | 备注 |
|---|---|---|
| **Devin** (Cognition) | 借鉴 | autonomous SWE agent 模式 |
| **Cursor Composer + Background Agent** | 借鉴 | 后台任务模式 |
| **Manus** (中国) | 借鉴 | 多技能编排 + 长任务 |
| **OpenAI Tasks** (2026-03) | 借鉴 | 定时执行 + 报告生成 |
| **Anthropic Computer Use** | ❌ 不抄 | omni-hub 不做 GUI 自动化 |

---

## 5. 15 个垂直技能详细设计

每个技能 = `vault/wiki/domains/<slug>/` 域目录 + `.agents/skills/<slug>-wiki/SKILL.md` skill 文件 + N 个 retrieval connector + 1 个 evaluation metric。

| Skill ID | 来源 | 关键 Connector | Metric | 状态 |
|---|---|---|---|---|
| **meta** | omni-hub 自身代码 + commit log + 用户反馈 | git log + filesystem walker | "建议是否被采纳" (PreferenceRecord) | NEW |
| **ai-progress** | arxiv (cs.AI/cs.LG/cs.CL) + OpenAlex + GitHub + HF Daily Papers + Anthropic/OpenAI blog | 已建 6 个 | LongMemEval citation 覆盖率 | ✅ schema 已有 |
| **engineering** | GitHub + StackOverflow + 框架官方 docs | github + 新 SO | "推荐代码片段编译通过" | ✅ schema 已有 |
| **research** | arxiv + OpenAlex + Crossref + S2 | 已建 4 个 (ResearchFlow 接) | LongMemEval + 引用完整性 | ✅ 完整 |
| **fitness-wellness** | PubMed + 营养学论文 + Bilibili 健身博主 | PubMed + Bilibili connector | "建议是否带 RCT 引用" | NEW |
| **cooking** | 下厨房 + 小红书 + Bilibili 美食 + Allrecipes (英文) | 小红书 + Bilibili + 新 | "用户复刻成功率" | NEW |
| **photography** | 500px + 莱卡论坛 + B 站摄影区 + DPReview | Unsplash + Pexels (已建) | "构图建议被采纳率" | ✅ schema 已有 |
| **fashion** | 小红书 + Vogue + 时尚博主 + Vestiaire | 小红书 connector | "穿搭被复用次数" | ✅ schema 已有 |
| **chat-relationships** | 用户本地聊天记录 + 关系映射 | 本地 IMAP / 微信导出 (local-only) | "回复被采纳率" | ✅ schema 已有 |
| **travel** | 携程 + 小红书 + 马蜂窝 + TripAdvisor | 小红书 + 新 | "行程是否被预订" | NEW |
| **marketing** | 案例库 + 微博热搜 + 抖音趋势 + 营销博主 | 新 | "方案被采用率" | NEW |
| **enterprise** | Crunchbase + LinkedIn + 财报 PDF + 招股书 | 新 | "分析准确性 (3 个月后回测)" | NEW |
| **finance** | EDGAR + FRED + Polygon + Tushare + 雪球 | EDGAR + FRED (已建) | "推荐操作的 PnL" | ✅ schema 已有 |
| **cn-policy** | gov.cn + 国务院 + 各部委文件 + 中央财办 | 新 gov.cn | "政策影响判断准确率" | NEW (split from policy) |
| **us-policy** | whitehouse + federalregister + Congress + SCOTUS | federal_register, regulations, congress_gov (已建) | 同上 | ✅ (rename from policy) |
| **international-relations** | ACLED + GDELT + Foreign Affairs + 路透 + 新华社 | ACLED + GDELT (已建) | 跨技能,链 cn/us-policy | ✅ schema 已有 |

### 设计原则

1. **每个技能初始 = 空 schema + 5-10 个 connector + 1 个 SKILL.md stub**
2. **第一次填充** 通过 `omni-hub retrieve --domain <skill>`
3. **持续迭代** 通过 PreferenceStore 反馈 → 1 个月后 `harness-compile-skill` 自动改 SKILL.md
4. **跨技能引用** (例如 cn-us-relations) 通过 `wiki-graph` 查邻居技能的 claim
5. **隐私敏感技能** (chat / fitness 个人数据) 默认 local-only,不出网络

---

## 6. 实施路线图 v0.19 → v0.30

不一口气干完。每个版本 1 个 Plane / Sub-Plane,可独立 commit + push + 测试。

| 版本 | 目标 | 工作量 | Plane |
|---|---|---|---|
| **v0.19** | 5-Plane 架构文档 + 6 个新 domain (meta/fitness/cooking/travel/marketing/enterprise) + policy split + Interface Plane base + Application Plane base | 1-2 天 | 5-Plane 重组 |
| **v0.20** | 中文消费域 connector 批: Bilibili + 小红书 + Zhihu + 微博 (放 agent-harness/) | 2-3 天 | Knowledge - Acquisition |
| **v0.21** | 政策域 connector 批: gov.cn + 国务院 RSS + 各部委 + Congress + Federal Register 补全 | 2-3 天 | Knowledge - Acquisition |
| **v0.22** | 金融+企业域 connector: Polygon + Tushare + 雪球 + Crunchbase + LinkedIn + 财报 PDF parser | 2-3 天 | Knowledge - Acquisition |
| **v0.23** | LLM-as-Judge + Eval 框架 (跨 15 技能) | 3-4 天 | Skill - Evolution |
| **v0.24** | Feishu adapter (agent-harness/integrations/feishu/) + omni-hub Channel 注册 | 3-4 天 | Interface |
| **v0.25** | Discord adapter + Email (IMAP/SMTP stdlib) channel | 2-3 天 | Interface |
| **v0.26** | Application Plane: 日报生成器 (跨 15 技能汇总) + 周报 + 月报 | 2-3 天 | Application |
| **v0.27** | Application Plane: 对话任务路由器 (chat skill 集成,根据 query 选 skill) | 3-4 天 | Application |
| **v0.28** | Cross-skill knowledge transfer + meta skill (学习其他技能模式) | 4-5 天 | Skill - Evolution |
| **v0.29** | A/B test framework + skill versioning + 渐进发布 | 3-4 天 | Skill - Evolution |
| **v0.30** | 端到端真实数据跑 1 周 + 修发现的问题 | 1 周 真实 dogfood | (调优) |

**到 v0.30 接近真正可用的 v1.0**。版本号严格保持 v0.xx,不超过 v1.0,除非端到端跑 1 个月稳定。

---

## 7. 工程不变量 (硬约束,不可破)

继承 v0.10-v0.18 + 新增:

1. **主仓库 100% Python stdlib** — 新 connector / channel adapter 用了 lark-oapi / discord.py / requests 等,统统进 `agent-harness/integrations/<name>/` 作为 pinned forks
2. **Proposal[T] 是唯一写入路径** — 所有 skill 的 corpus 更新都经 Proposal 人审
3. **ClaimLedger 是唯一 SoT** — 所有新 projection 从 ClaimLedger 重建
4. **每个新组件带 schema_version** — 不留无版本契约
5. **每次写带 trace_id** — Interface Plane 的入站消息 → Application Plane 的出站结果全链路追踪
6. **Skill 不允许直接调 LLM** — 必须通过 `OperationSpec` 走 `runner.execute()`,审计 + 政策 + 预算都走一遍
7. **Channel 实现 Channel Protocol** — 必有 `listen()` + `reply()` + `health_check()`
8. **Application Plane 不直读 vault/wiki/** — 必经 Skill Registry,这样 skill metadata 才能影响输出

---

## 8. v0.19 具体交付物 (本次 PR)

1. **`docs/architecture-v0.19.md`** (本文档)
2. **`src/omni_hub/domain_schemas.py`** bump 到 v0.19,新增 6 个 domain + policy split:
   - `meta` (自迭代)
   - `fitness_wellness` (健身养生)
   - `cooking` (做饭)
   - `travel` (旅游)
   - `marketing` (营销宣传)
   - `enterprise` (企业分析)
   - `us_policy` (从 `policy` 重命名)
   - `cn_policy` (中国政策, 新)
3. **`src/omni_hub/channels/`** Interface Plane base:
   - `__init__.py`
   - `base.py` — Channel Protocol + InboundMessage / OutboundMessage
   - `cli_channel.py` — wrap CLI 入口为 Channel
   - `mcp_channel.py` — wrap MCP server 为 Channel
   - `email_channel.py` — IMAP poll + SMTP send,纯 stdlib
   - `stub_channels.py` — Feishu / Discord stub,指向 agent-harness/
4. **`src/omni_hub/app/`** Application Plane base:
   - `__init__.py`
   - `report_orchestrator.py` — 跨 skill 日报/周报/月报
   - `task_router.py` — 对话任务路由
5. **`vault/wiki/domains/<slug>/_schema.md`** 自动生成 18 个 (12 老 + 6 新)
6. **`.agents/skills/<slug>-wiki/SKILL.md`** 自动生成 18 个 stub
7. **`tests/test_v019_p1.py`** 测试新组件
8. **CLI 子命令**: `channel-list` / `channel-health` / `app-report-daily` / `app-route-task`

---

## 9. 参考文献 (SOTA 来源)

- **Karpathy LLM Wiki + Context Engineering** (Sequoia Ascent, 2026-05-19; Anthropic 加入 2026-05)
- **Anthropic Skills** (agentskills.io v1.2, 2026 Q2)
- **Anthropic Managed Agents Memory** (`memory_20250818`)
- **Anthropic Dreaming** (2026-05-06, offline consolidation)
- **Letta MemFS pivot** (2026-03-16, git-backed memory)
- **Mem0 OS open source** (2026-Q1, LoCoMo 92.5 / LongMemEval 94.4)
- **Graphiti** (bitemporal validity, supersedes/superseded_by)
- **GraphRAG** (Microsoft Research, local + global with Leiden communities)
- **DSPy 3 + GEPA** (Stanford NLP, 2026-Q1)
- **Apache Iceberg** (table format, atomic pointer swap)
- **Outbox / Inbox pattern** (Chris Richardson, microservices.io)
- **Temporal** (Signal + Query + suspend/resume on WorkflowRun)
- **Pulumi / Terraform plan-apply** (Command.preview + ProjectionDiff)
- **12-Factor Agents** (humanlayer)
- **Cognition Devin / Cursor / Manus** (autonomous task agents)
- **MCP 2026-07-28 RC** (no memory primitive at protocol layer)

---

_本文档是 v0.19 重构的设计真源。修改 = 重新审视 5-Plane 边界,不要直接改这里的内容,改完同步 bump 文档版本。_
