# E2E Audit v0.41 (2026-05-28)

回答审核的 4 个问题:
1. SOTA / Karpathy 对齐度
2. E2E 完整性
3. 缺哪些 API + 费用
4. 实现了哪些功能 (诚实清单)

---

## 1. SOTA + Karpathy 对齐度

**结论**: 对齐,且**在某些载力点比 mainline 走得更远**。无 drift。

| Mainline 2026 Q2 真源 | 时间 | omni-hub 对应 | 状态 |
|---|---|---|---|
| Karpathy LLM Wiki gist (raw / wiki / schema + ingest/query/lint + index/log.md) | 2026-04-04 | `vault/raw + vault/evidence + vault/wiki` + `wiki-ingest/search/lint` + `index.md/log.md` | ✅ 1:1 + 多 evidence 中间层 |
| Anthropic Context Engineering (just-in-time + progressive disclosure + attention budget) | 2025-09-29 | `context-pack-build --tier minimal/standard/expanded` + retrieve cascade | ✅ 1:1 |
| Anthropic Memory Tool (`memory_20250818`) | 2025-08-18 | `omni_hub/memory_tool.py` 6 commands (view/create/str_replace/insert/delete/rename) | ✅ 1:1 |
| Anthropic Dreaming (async hippocampal-replay) | 2026-05-06 | `wiki-dream` skill | ✅ 名字 + 模式都一致 |
| Graphiti bitemporal (`t_valid_from/to`, `superseded_by`) | 2025-01 | `claims.jsonl` bitemporal | ✅ 我们走的更远 |
| AGENTS.md Linux Foundation 标准 | 2025-12-09 | `AGENTS.md + vault/wiki/AGENTS.md` | ✅ canonical |
| SKILL.md Agent Skills Open Standard | 2025-12-18 | 三层 (Foundation/Functional/Domain) SKILL.md + omni_hub frontmatter | ✅ 已超 spec |
| Anthropic "Demystifying evals" 三类 eval | 2026-01 | EvalCase.eval_class = capability/regression/calibration | ✅ HR #14 强制 |

**我们比 mainline 走得更远的地方** (Karpathy gist 本身没写,但我们加了):
1. **bitemporal `claims.jsonl`** — Anthropic Memory Tool 故意把 provenance 留给开发者,我们直接做了 Graphiti 模式
2. **Proposal[T] 写屏障** — Anthropic Memory Tool 默认让 agent 直写;我们强制 HITL
3. **5-Plane (Control/Knowledge/Skill/Interface/Application)** — 业界主流停在 2-plane "control vs data" (K8s 衍生);5-Plane 是我们的合成,无外部文档对照,但每个 plane 都映射到已发表的 primitive

**drift 风险**: Karpathy gist 是 minimal/undirected,decodethefuture / ScrapingArt / Astro-Han 等社区实现都更轻量 (没 evidence 层,没 claims 层,没 bitemporal)。omni-hub 是 production-grade 取舍,**README.md 需要写清楚每层为什么存在**。

---

## 2. E2E 完整性审计

### ✅ Fully E2E (零依赖,主仓 stdlib)

| 能力 | 验证命令 | 状态 |
|---|---|---|
| Wiki 写入闭环 | `retrieve --persist-evidence → wiki-ingest → propose-approve → wiki-apply-proposal` | ✅ v0.11+ |
| ClaimLedger CRUD | `claims-list / claims-show / claims-stats / wiki-supersede` | ✅ v0.13+ |
| Wiki 搜索 | `wiki-search` (FTS5 + substring fallback) | ✅ v0.16 |
| Knowledge Graph (本地) | `wiki-graph --node X` (Leiden communities) | ✅ v0.18-J |
| 17 retrieve sources 可用 | `retrieve-doctor` → ok=17 | ✅ arxiv/openalex/crossref/s2/europe_pmc/pubmed/wikipedia/wikidata/gdelt/internet_archive/jina_reader/hf_daily_papers/courtlistener/federal_register/bilibili/wayback/edgar |
| Projection registry + snapshots | `projection-list/rebuild/snapshots/rollback` | ✅ v0.18-H |
| WorkflowKernel | `workflow-list/show/signal/query/resume` | ✅ v0.18-F |
| 5 channel (CLI/MCP/Email/Feishu/Discord stubs) | `channel-list/health` | ✅ v0.19 (Feishu/Discord stub 报 off) |
| 19 domain SKILL.md + 17 foundation + 11 functional | `skill-list` → 56 entries | ✅ v0.38+ |
| TaskRouter + AppIntentRouter 两级 | `app-route-task`, `app-intent-route` | ✅ v0.40 |
| 报告 | `app-report-build --period daily/weekly/monthly` | ✅ v0.26 |
| 个性化 (UserProfile + 3-tier memory) | `user-list/enroll/approve/set-persona/memory-recall/memory-archival` | ✅ v0.31 |
| 日程 + 个人任务 | `cal-add/list, personal-task-add/list/done, schedule-plan` | ✅ v0.32 |
| 转发分类器 | `inbox-classify` (URL/PDF/.ics/task/wiki 五类) | 🟡 分类对,**dispatch 还没接** |
| Judge + AB + Cross-skill | `judge-evaluate, ab-test, meta-cross-skill-scan` | ✅ v0.23/v0.29/v0.28 |
| Eval Flywheel | `eval-list/show/run/promote` + 5 v0.1 seed pack | ✅ v0.41 |

### 🟡 半 E2E (主仓 stub,需要 agent-harness/integrations/ 落地真实输出)

| 能力 | 现状 | 解决路径 |
|---|---|---|
| PPTX 生成 | `pptx-build` 返回 `skipped: true` (没 pptx-omni broker) | 装 `agent-harness/integrations/pptx/` python-pptx shim |
| Feishu 推送 | Channel stub 报 off | 装 lark-oapi 进 agent-harness/integrations/feishu/ |
| Discord 推送 | Channel stub 报 off | 装 discord.py 进 agent-harness/integrations/discord/ |
| LLMJudge real | HeuristicJudge 兜底;LLMJudge fallback 到 heuristic | 配 `OMNI_CCLOAD_BASE` 或 `ANTHROPIC_API_KEY` |
| project-plan 真实拆任务 | 只 create Project 行 | v0.42 接 claude lane planner 任务 |
| finance-screen 真实筛 | 返回 `[]` | v0.42 SQL-style screen over cached evidence |
| inbox-route dispatch | 只 classify | v0.42 按 category 调 capture-url/calendar-add/task-add/wiki-propose |
| order-propose 真实下单 | emit `Proposal(order_intent)` 永不直接下 | 装 `agent-harness/integrations/finance/` broker shim |

### ❌ 真实下游需要 API key 才工作的 source (15 个 off)

| Source | 类型 | 费用 |
|---|---|---|
| `brave_search` | 通用搜索 | 2000 req/月 免费,后 $3/CPM |
| `fred` | 美联储经济数据 | 免费 (注册要 key) |
| `acled` | 全球冲突事件 | 学术免费;商业 $$$ |
| `congress_gov` | 美国国会 | 免费 (注册要 key) |
| `regulations_gov` | 美国法规 | 免费 (注册要 key) |
| `data_commons` | Google 公共数据 | 免费 (无 key 要求,可能是 quota 问题) |
| `tushare` | A 股 + 中国宏观 | 免费 (注册要 token) |
| `unsplash` / `pexels` | 摄影 | 免费 (注册要 key) |
| `crunchbase` | 企业情报 | $99/月 (basic) — $999/月 (enterprise) |
| `linkedin` | 招聘/人才 | 无公开 API;Proxycurl $49+/月 |
| `x_twitter` | Twitter | twitterapi.io broker ~$10/月 |
| `xiaohongshu` / `zhihu` / `weibo` | 中文社交 | broker 自托管 (免费) 或 paid $$$ |

---

## 3. 费用估算 (单用户)

### Tier 0 (零成本起步,推荐)

只用 **17 个 free 源** + ccLoad 本地网关 + 注册 5 个免费 key (Brave/FRED/Tushare/Federal/Congress)。

| 服务 | 月成本 |
|---|---|
| 17 个免费 retrieve 源 (arxiv/openalex/wikipedia/...) | $0 |
| Brave Search (2000 req 免费) | $0 (起步) |
| FRED / Tushare / Federal / Congress / Regulations API key | $0 |
| Claude API (LLMJudge 周跑 5 域,Sonnet) | $5-10 |
| ccLoad 本地网关 | $0 (本地 Docker) |
| **小计** | **$5-10/月** |

### Tier 1 (轻度商业用)

| 服务 | 月成本 |
|---|---|
| Tier 0 全部 | $5-10 |
| Brave Search ~10k req/月 ($3/CPM × 10) | $30 |
| Crunchbase basic | $99 |
| X/Twitter broker (twitterapi.io) | $10 |
| Claude API 加重 (生成 narrative + Judge LLM 跑) | $20-30 |
| **小计** | **$160-180/月** |

### Tier 2 (重度用 + 真实下单)

| 服务 | 月成本 |
|---|---|
| Tier 1 全部 | $160-180 |
| LinkedIn via Proxycurl | $49 |
| Polygon.io (实时美股) | $29-99 |
| Alpaca broker (paper trade 免费,实盘 commission) | $0+ |
| ccxt 加密 (开源,Coinbase/Binance API 免费;手续费另算) | $0 |
| Apify 反爬 (小红书/Bilibili 增强) | $39 |
| **小计** | **$280-380/月** |

### 一次性 broker / shim 工作量 (v0.42+,工程时间不是 API 钱)

| Broker | 工作量 |
|---|---|
| `pptx-omni` (python-pptx wrapper) | ~200 LOC + 1 day |
| Feishu (lark-oapi shim) | ~150 LOC + 1 day |
| Discord (discord.py shim) | ~200 LOC + 1 day |
| Finance broker (ccxt + alpaca-py) | ~300 LOC + 2 day |
| Xiaohongshu CLI (`jackwener/xiaohongshu-cli` PIN) | ~100 LOC subprocess wrap |

---

## 4. 实现了哪些功能 (用户最初 8 个需求逐条)

| 用户需求 | 实现状态 | 缺什么 |
|---|---|---|
| 1. 日报 / 周报 / 月报生成 | ✅ active `app-report-build` (有 `--narrate` 走 claude lane) | 真实 PreferenceStore 数据让 narrate 有东西可写 |
| 2. 对话解决各领域难题 | ✅ active TaskRouter + AppIntentRouter + 19 domain SKILL.md + context-pack-build | 真实 vault/wiki/domains/ 内容 (现在 0 页) |
| 3. 项目开发 | 🟡 stub `project-plan` 只 create Project 行 | v0.42 接 claude planner + Proposal(project_plan) |
| 4. PPT 制作 | 🟡 broker_required `pptx-build` 返回 skipped | 装 pptx-omni broker |
| 5. 股市 / BTC 操作 | 🟡 mixed: `finance-screen` stub (返回 []); `order-propose` ✅ active 走 Proposal(order_intent) | finance-screen 接 local cached EDGAR/Tushare;实盘 broker 进 agent-harness |
| 6. 个性化聊天 (per-user style + memory) | ✅ active UserProfileStore + Letta-style 3-tier (core/recall/archival) memory | 真实多用户场景才能验证 |
| 7. 日程 + 任务管理 | ✅ active `cal-add/list, personal-task-add/list/done, schedule-plan` (iCal-syncable) | 自然语言入口 (现在要明确 ISO 时间,v0.42 接 NLU) |
| 8. 转发 → 知识库 + 任务 | 🟡 partial `inbox-classify` 分类对 (URL/PDF/.ics/task/wiki),但**dispatch 没接** | v0.42 inbox-route 按 category 调下游 handler |

**净分**:
- 4 个 fully active (报告 / 对话 / 个性化 / 日程任务)
- 4 个 stub/partial (项目 / PPT / 金融筛 / inbox dispatch)
- 8/8 都有 contracts + CLI + tests

---

## 5. 跨项目数据

| 维度 | 数值 |
|---|---|
| **CLI subcommands** | 112 |
| **Skill registry** | 56 (52 active + 1 broker_required + 3 stub) |
| **Eval packs** | 5 (research/engineering/finance/meta/chat-relationships v0.1) |
| **Retrieve sources** | 40 (17 ok / 8 warn / 15 off-needs-key) |
| **Tests** | 707 green, 0 ResourceWarning |
| **Code size** | ~25k LOC stdlib only (no runtime deps) |
| **vault/ 物理占用** | 240KB (scaffolding) |
| **.omni/ 物理占用** | 4.8MB (主要是 retrieval cache) |
| **vault/wiki/domains/ 实际页数** | **0** (5-Plane 全到位,**真实内容还没灌**) |

---

## 6. 真正缺的是什么? (诚实)

### 不缺代码 (架构 OK):
- 5 Plane 都到位
- 56 skill 都 discoverable
- 707 测试都绿
- 0 资源泄漏
- E2E 测试 (forwarded paper → ingest → report) 跑通

### 缺真实数据 + broker:

1. **PreferenceStore 是空的** — 没真实使用就没 graduation 候选 → 没飞轮 → 没 v0.X+1 升级
2. **vault/wiki/domains/ 0 页** — context-pack-build 当前对所有 domain 都返回空;dogfood 跑一两周后才会变满
3. **5 broker 全是 stub** — pptx-omni / lark-oapi / discord.py / pptx broker / finance broker;装一个就 unlock 一类功能
4. **15 个 retrieve source off** — 注册 5-7 个免费 key 立刻 unlock 大部分 (FRED, Tushare, federal/congress/regulations.gov, Brave, ACLED)
5. **LLMJudge 没 wire** — `ANTHROPIC_API_KEY` 设了 + ccLoad 起来,就从 HeuristicJudge 升到 LLMJudge

---

## 7. 下一轮 v0.42 应该做什么 (按 ROI 排序)

1. **dogfood 1 周** — 用 `omni-hub retrieve --domain meta` + `wiki-ingest` + `propose-approve` + `wiki-apply` 灌 20 个 meta 域页;看真实 PreferenceStore 飞轮跑起来 (零成本,纯人工 1 周)
2. **注册 5 个免费 key** — FRED + Tushare + Federal + Congress + Brave (1 小时配 + $0/月)
3. **`pptx-omni` broker** — 解锁 PPT 生成 (1 天工程,~200 LOC,python-pptx)
4. **inbox dispatch** — `inbox-route` 从 classify-only → 按 category 调 handler (1 天,~150 LOC)
5. **`project-plan` 接 claude lane** — Proposal(kind=project_plan) emit (1 天,~150 LOC)
6. **`finance-screen` 真实查询** — SQL-style 过 local EDGAR cache (2 天,~300 LOC)
7. **LLMJudge wire ccLoad** — 1 小时配置 + 跑通 1 次 (用 Sonnet)

按这个顺序 v0.42 一周内 (按 1 人天/天投入) 可以把 4 个 stub 升到 active,把 vault/wiki/meta 灌满 ~20 页 (真实数据),把飞轮跑出第一份 PreferenceStore 数据。

---

## 8. 不变的工程约束

- **HR #1**: 主仓 stdlib only (新依赖进 `agent-harness/integrations/`)
- **HR #5**: 写 vault/wiki 必经 Proposal[T]
- **HR #11**: Eval pack v0.X 不可手编辑,只能 bump v0.X+1
- **HR #13**: Graduation 必经 Proposal,不允许自动 promotion

这 4 条决定 omni-hub 的"local-first 但企业级"定位,不会因为加 broker 或加飞轮被稀释。
