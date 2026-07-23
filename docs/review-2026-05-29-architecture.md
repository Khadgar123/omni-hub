# omni-hub 架构审查 (2026-05-29)：存储 → Wiki → 技能闭环

> 方法：业界 SOTA 检索(8 路并行)+ 代码级测绘(storage / connectors / skills / ResearchFlow)。
> 立场：单人但生产级、优雅、鲁棒、**若非必要勿增实体**。
> 一句话总览:**你的文档知识平面的分层、blackboard+Proposal 写门、原子技能拆分,经 SOTA 验证多数是对的**;真正的 gap 集中在 6 处 —— 论文深抽取、量化时序存储、降级/质量解耦、多域编排、GEPA 回流、若干 stub。

---

## 0. 结论矩阵(TL;DR)

| # | 你的问题 | 现状(代码级) | SOTA 判断 | 动作 |
|---|---------|-------------|-----------|------|
| 1.1 | 是否按来源/类别单独存储?最简分几类? | 按 **domain** 组织,**完全不按 source 区分**;3 层 + claims 索引 + 3 个 sqlite | ✅ 正确(medallion bronze/silver/gold + 派生事实索引)。勿增实体 | 文档侧**不要**再分;**唯一**该新增的实体 = 量化时序存储 |
| 1.2 | API 格式 / 是否取全 / 调取依据 / 会不会少源 | 共享 `RetrievalRecord`;v0.46 已存全量 metadata;静态 cascade + 并行 + RRF | ⚠️ 论文取的是**子集**;有 under-sourcing(静默 fallback + 无 min-K 保证) | 补全字段 + 覆盖度断言 + tier 接入执行 |
| 1.3 | 论文能否全取 / 参考 ResearchFlow? | 连接器 lossy;ResearchFlow 有 MinerU 深解析但**脱节** | API-metadata-first + 按需 PDF 深解析(Docling/MinerU)分层 | 把 ResearchFlow 接成 research 域 Layer-4 |
| 1.4 | K线/订单级/秒级 量化存储 | **无**专用时序存储 | 需要专用列存时序库(物理特性正交) | 新增 ArcticDB / DuckDB+Parquet / QuestDB |
| 2 | API 管理鲁棒性 + 降级vs原始存储 | 静态优先级;source_policy tier 未接执行 | priority ≠ quality;要 provenance + 学习型质量分 | resilient_call() + 质量分自校正 |
| 3 | raw→wiki 整合过滤该用什么机制?是 skill 吗? | batch + Proposal 人审(wiki-ingest) | ✅ 比自治写更安全;补 4-op reconcile | 机制属 **foundation op**,不是 per-domain skill |
| 4 | 技能既检索又维护又下游?如何原子化? | domain skill = 声明式契约(揉了三职责) | 分离 memory(维护)/actor(执行);原子+组合 | 三职责显式拆分 |
| 5 | 应用层×领域层如何配合调度?GEPA 怎么进? | 单域 top-1 路由;GEPA 闭环**开口** | plan-and-execute + 三级路由;一个 DSPy program 端到端 | 补编排器 + 闭合回流 |
| 6 | ResearchFlow 映射哪些环节?如何重构? | 独立 obsidian-vault,read-only 被母仓发现 | 它是 research 子领域的完整闭环样板 | 既独立闭环、又经 Proposal 原生回写母仓 |
| 7 | 冗余/死代码 / 如何初始化 / 还缺哪些配置 | 多个 stub;prompts 产物不回流;tier 未用 | — | 见 §7 清单 |

---

## 1. 数据存储与获取层

### 1.1 存储分层:最简类别 + 「若非必要勿增实体」

**现状(代码级,均已核实):**

| 层 | 格式 | 落盘位置 | 写入者 | medallion 对应 |
|----|------|---------|--------|---------------|
| raw | Markdown + YAML frontmatter(+ `<!-- omni:metadata -->` JSON 块) | `vault/raw/<domain>/<run_id>/*.md` | `_render_raw_capture` (`knowledge_plane.py:1746`) | **Bronze**(不可变源) |
| evidence | JSON(每记录一文件) | `vault/evidence/<domain>/<run>__<idx>__<hash>.json` | `_write_evidence_files` (`knowledge_plane.py:1685`) | **Silver**(规范化) |
| wiki | Markdown + frontmatter | `vault/wiki/<type>/<slug>.md` | `apply_wiki_proposal` (`knowledge_plane.py:711`) | **Gold**(被消费层) |
| claims | JSONL(append + 原子重写) | `.omni/claims.jsonl` | `_append_claims` (`knowledge_plane.py:1952`) | **派生事实索引**(非第 4 层) |
| proposals / fts / queue | SQLite | `.omni/*.sqlite3` | 各 store | 控制/索引 |
| preference | JSONL/域 | `.omni/preference/<domain>.jsonl` | `_record_wiki_preference` (`knowledge_plane.py:789`) | 飞轮信号 |

**关键事实:存储完全不按 source 区分,只按 domain 组织。** `_write_evidence_files(workspace, domain, run_id, records)` 的签名里**只有 domain**,arxiv / openalex / RSS 全落进同一个 `vault/evidence/<domain>/`,source 只是记录里的一个字段(`knowledge_plane.py:1685-1743`)。

**SOTA 判断(验证后):这是对的,且正处在最简端。**
- 这正是 Databricks medallion(bronze/silver/gold)被泛化后的标准三层;claims.jsonl 是 Gold 之上的**派生 bitemporal 事实索引**,不是第 4 个「存字节」的层 —— 概念上保持这一区分本身就是 Occam's razor 的正确用法。
- Karpathy LLM-Wiki 的原话:raw 是 *immutable source of truth*,wiki 是 *LLM owns this layer*,而且**刻意选 Markdown+git**(open / diffable / future-proof)。你的实现比原 gist 更严谨(多了 Proposal 门 + bitemporal supersede)。
- **不要**为不同 source 建不同 store/schema:domain 才是正确的结构轴,source 作为字段保留即可。**不要**引入 Delta / Iceberg / DuckLake —— 那些解决的是「并发写者 ACID + Parquet 上时间旅行」,你单人没有这个问题。

**唯一该新增的「实体」:量化时序存储**(见 §1.4)。判据:文档知识平面回答的是「关于 X 什么是真的 / 找语义相近的文本」,量化平面回答的是「给我 AAPL 09:30:00.000–09:30:01.000 的每一笔成交并对齐当时盘口」。两者**数据物理特性正交**,没有任何一种索引/布局能同时服务好 —— 把 tick 塞进文档存储才是真正的工程错误。所以最简类别 = **(a) 文档/知识平面(现有)+ (b) 时序/量化平面(新增)**,到此为止,不要再细分。

> **结论 1.1**:文档侧分层已是 SOTA 最简形态,保持。新增且**仅**新增一个专用列存时序库;其余「按来源单独优化」一律不做。

---

### 1.2 API 数据格式、覆盖度、调取依据、是否会少源

**(a) 格式与覆盖度。** 所有连接器映射到共享 `RetrievalRecord`(`retrieval/base.py:36-67`:`source/title/url/snippet/score/metadata/canonical_id/cite_id`)。v0.46 已经把连接器的 API 原生 metadata 逐字落盘到 evidence 与 raw(`knowledge_plane.py:1729-1734`),这点已修好 —— 「API 结构丢失」的旧问题不再存在于**存储**层。

**但「取数」层仍是 API 能力的子集**,论文尤其明显(字段级审计):

| 连接器 | 取到 | 丢掉的关键字段 |
|--------|------|---------------|
| arxiv | title, summary(截 500), authors[**仅 5**], categories, pdf_url | 完整作者、affiliation、references、figures/formulas/tables |
| openalex | authors_detailed[50] 带 **ORCID+ROR+机构**、abstract(倒排重建)、topics、cited_by、OA pdf | references 边、figures、邮箱、funding、accepted vs published |
| semantic_scholar | tldr、citation/influential 计数、reference **仅 IDs**、external_ids、OA pdf | affiliation、完整 references(无标题/上下文)、SPECTER embedding(故意跳过) |
| openreview | **on-demand** `forum_thread()` → reviews(rating/confidence)、decision、**accepted** | 评审正文、rebuttal、camera-ready |
| pubmed | title、authors[8]、journal、pubdate | **abstract 根本没取**(只调了 esummary) |
| github / hf_hub | repo: releases/assets(checkpoint)、license、pushed_at;model: downloads/likes | 与论文记录**未关联**(各查各的) |

→ **figures / formulas / tables / 完整 references / 邮箱 / affiliation(除 OpenAlex)在整个连接器层为零**。

**(b) 调取依据。** 完全是**静态的 per-domain 映射** `DEFAULT_DOMAIN_CASCADES`(`cascade.py:47-171`):每个 domain 一个有序 source 列表,`retrieve()` 取 `cascade_for(domain)` → `ThreadPoolExecutor` 并行 fan-out → RRF 融合(k=60,`cascade.py:351`)。没有 LLM 参与选源。

**(c) under-sourcing 风险:确实存在,两个机制:**
1. **静默回落 default**:domain key 不在 cascade 里 → 静默用 `default`(`cascade.py:223`)。这正是 `agent_systems` 在 v0.46 前的真实 bug(注释 `cascade.py:124-129` 自认 20/23 域漂移)。
2. **静默部分成功**:某源超时/429/auth 失败只记进 `errors`,不中止(`cascade.py:305-331`)。**没有「成功源 < K 就告警/重试」的保证** —— 一个本该 5 源的查询可能只剩 1 源返回,而调用方拿到的结果看起来「正常」。
3. **tier 未接入执行**:`source_policy.py` 的 `PolicyEntry(tier, fail_soft)`(0 免费/1 配额/2 付费)和文档里写的「tier0→1→2 fallback、无 `--allow-paid` 跳过 tier2」**并没有被 cascade 调用** —— `resolve_policy()` 没有任何 retrieval 路径在用(`source_policy.py:262-272` 自述 composer 已删,只剩 grading 用)。即 fail-soft 实际发生在连接器层(env 未配 → 返回空),而非 tier 策略层。

> **结论 1.2**:存储已存全;**取数仍 lossy**(尤其论文);选源是静态映射,有静默少源风险。动作:① 论文字段补全见 §1.3;② cascade 增加**覆盖度断言**(`sources_succeeded < ceil(K*0.5)` → 记 warning/降 confidence);③ 把 tier 元数据真正接入执行,或删掉这层未用的 advisory 以消除双源真相。

---

### 1.3 论文获取:ResearchFlow 能否全取 + 更优方案

**两个关键事实:**
1. 母仓连接器对论文是 **lossy**(见上表)。
2. **ResearchFlow 恰恰是母仓缺的那个深解析器**:它用 **MinerU** 把 PDF → Markdown + figures/tables 元数据,7 阶段(parse → chunk → 逐块锚点抽取 → 合并验证 → 图表富化 → 7 个分节写手 → vault 导出),能拿到 **sections / figures(带 caption)/ tables / formulas / 方法谱系**(`agent-harness/researchflow/scripts/run_local_paper_analysis.py`)。但它独立存于 `obsidian-vault/`,**不写 claims.jsonl、不写 vault/wiki**,且**重复**母仓的 arxiv/S2/openreview 检索;母仓只能经 `research_assets.py:58-75` **只读发现**它。

**SOTA 推荐架构 —— API-metadata-first,PDF 深解析按需(这是 paper-qa 等生产实现的范式):**

```
输入: arxiv id / DOI / title
 └─ Layer 0 身份解析: → S2 paperId / OpenAlex id / DOI
 └─ Layer 1 元数据(不碰 PDF,覆盖 ~90%)
      • Semantic Scholar Graph API  ← 一站式:作者+ID、abstract、TLDR、
            references/citations(含上下文)、SPECTER2 向量、venue、OA pdf  [主]
      • OpenAlex   ← 权威 affiliation(ROR)+ 机构消歧 + concepts
      • Crossref   ← DOI/出版方 ground truth
      • arxiv API  ← 版本历史 + PDF URL
 └─ Layer 2 录用状态
      • OpenReview ← 决定 + 评分 + meta-review(唯一有评审内幕)
      • DBLP       ← 「被哪个会/刊收录」(只索引 peer-reviewed)
      • 否则 venue+DOI → 视为 preprint/未知
 └─ Layer 3 开源链路
      • HF Hub(按 arxiv id)← models/checkpoints/datasets  [主,PwC 已 2025-07 关停]
      • GitHub API ← /community/profile health% + tests/CI/requirements/release → 代码完整度评分
      • abstract/首页正则 ← 抓 github.com 链接
 └─ Layer 4 选择性 PDF 深解析(仅当需要正文/图/表/公式)
      • 无 GPU → Docling(CPU 快,MIT)
      • 有 GPU → MinerU 2.5(开源最佳 tables TEDS 93.42 / formulas CDM 97.45)
      • 脏扫描/多栏 → olmOCR;只要参考文献 → GROBID-TEI;难公式 → 前沿 VLM 兜底
      • 解析结果按 paper id 缓存,解析一次复用
```

**对你的落地映射:**
- Layer 1–3 = 母仓连接器需要**补全字段**(S2 增 references/affiliation/embedding;新增 DBLP 连接器;hf_hub 与论文记录**按 arxiv id 关联**而非各查各的;github.py 已有 release/asset,补 `/community/profile` 代码完整度分)。
- **Layer 4 = ResearchFlow**。它就是「按需 PDF 深解析」这一层的现成实现,不要重写,**接进来**:research domain 的深解析交给 ResearchFlow,产出经 Proposal 回写母仓 claims/wiki(详见 §6)。
- 「中稿/开源」正是你担心易遗漏的:录用走 OpenReview+DBLP,checkpoint/dataset 走 HF Hub(按 arxiv id),代码完整度走 GitHub `/community/profile` + tests/CI/requirements/release 资产的加权分。

> **结论 1.3**:论文「全取」的正解不是更猛地解析 PDF,而是**先用权威 API 拿干净元数据,PDF 只在需要正文/图/表/公式时深解析**;ResearchFlow 是你已有的 Layer-4,要做的是**接通**而非重写。

---

### 1.4 量化 / K线 / 订单级 / 秒级 时序存储

**现状:母仓没有任何专用时序存储**;finance 域走的是 edgar/fred/tushare 连接器(filings/宏观,非行情 bar/tick)。

**SOTA(单人、近专业级、避开 kdb+ 许可墙):**

| 方案 | 定位 | 单人适配 |
|------|------|---------|
| **ArcticDB**(Man Group) | serverless DataFrame DB,列存写 S3/本地 LMDB,内建 symbol 版本(bitemporal) | **默认首选**:`pip install`、零运维、Man Group PB 级生产、vectorbt 原生集成 |
| **DuckDB + Parquet(+Polars)** | 「文件即湖仓」,DuckLake 给 ACID+时间旅行 | 最便携、最 future-proof,可与 ArcticDB 共用同一 object store |
| **QuestDB** | 真服务器,SQL,亚毫秒读,原生 ASOF JOIN + OHLCV 物化视图 | 需要实时看板/低延迟服务时选 |
| kdb+/DolphinDB/ClickHouse/Timescale/Influx | — | 许可墙/社区版限核/弱 as-of/达不到 tick 规模,单人都不推荐为首选 |

**布局标准(与引擎无关):** 列存(Parquet/Arrow);**按 `symbol + date` 分区**(`symbol=AAPL/date=2026-05-29/…`);分区内按时间排序;Snappy/zstd 压缩;热数据本地 NVMe、冷历史对象存储两级。**L2/L3 全盘口**别存重建快照,存**增量消息流**(L2 价位档增量 / L3 订单级 add-modify-cancel)+ 周期性快照,回测时从最近快照重放。

**回测:** 列存 + mmap + 向量化(Polars/Arrow/DuckDB)对行存是数量级优势;as-of join 是热路径。框架配对:**vectorbt**(向量化研究,原生 ArcticDB)+ **NautilusTrader**(事件驱动、纳秒级、原生 L2/L3 回测,自带 ParquetDataCatalog)。

**与知识平面的关系:联邦,不内嵌。** 量化库通过一个薄的 retrieval connector / query API 暴露;知识平面可以把量化结果**作为 claim 引用/摘要**(如「NVDA 已实现波动率在 2026-05-20 跳升[来源:量化库查询]」),但原始 tick/OHLCV 永远留在自己的引擎里。这样文档库保持小而文本化,量化库专注扫描/回测吞吐 —— **这正是正交、非冗余的「必要实体」,不是过度设计**。

> **结论 1.4**:新增**一个**专用列存时序库(ArcticDB 默认 / DuckDB+Parquet 备选 / QuestDB 需服务器时),partition by symbol+date,L2/L3 存增量流,vectorbt+NautilusTrader 回测,以 connector 形式联邦进知识平面。

---

## 2. API 管理层:鲁棒性 + 「降级 vs 原始」的存储与排序

### 2.1 鲁棒性(对 LLM provider 路由 + 数据源抓取两处都适用)

七个经典模式 → Python 成熟库:
- **retry(指数退避+jitter)**:`tenacity`(事实标准)
- **circuit breaker**:`pybreaker`(Nygard 式)
- **bulkhead**:stdlib `asyncio.Semaphore` / 线程池隔离
- **timeout**:每个外呼必设(你 cascade 已有 15s 墙钟,`cascade.py:305`)
- **rate-limit**:per-provider token bucket
- **fallback / hedged**:hedged 仅用于**幂等读**(数据源 GET),**付费 LLM 生成不要 hedge**(翻倍成本)

落地:**不要手搓**这些原语;用 `tenacity + pybreaker + Semaphore`(保守)或 `pyresilience`(单装饰器,年轻),并**收口到一个 `resilient_call()` helper**,策略在一处声明,不散落到每个连接器。

LLM 网关:你 `CLAUDE.md` 已把网关工作收到 `api-management/metapi`+`ccLoad`。**把 LiteLLM 当规格**(自托管 OSS,数据自控),它有而手搓路由必须学的 5 点:① **有序 fallback 组 + 每级 retry 预算**;② **cooldown 健康剔除**(失败 provider 暂时移出池、自动恢复 = 路由层的熔断);③ **latency-based 选路**(近窗平均延迟);④ **per-key/domain 预算硬上限**(fail-closed);⑤ **缓存,最好语义缓存**(抄 Portkey)。Cloudflare AI Gateway **不做 failover**,此处错配。

### 2.2 「降级的也不一定差,优先级高的也不一定好」—— 这是本次最高杠杆的点

把**两件事拆开**:
- **priority**(你先试谁 / 路由顺序)
- **quality / confidence**(结果实际有多好)

现状你 cascade 用的是**静态 priority**(`DEFAULT_DOMAIN_CASCADES`),没有任何 measured quality。SOTA(truth-discovery / multi-source reconciliation)的核心是:**source 可靠度与「真值」联合迭代估计,不预先钉死优先级**。三步落地,全部复用你已有的 ClaimLedger + Proposal:

**① 每条 evidence/claim 盖 provenance(扩展 bitemporal ledger):**
```json
{ "source_id": "...", "fetched_at": "...",
  "served_via": "primary | fallback | cache | hedge",
  "http_status": 200, "latency_ms": 320 }
```
字段命名对齐 **W3C PROV**(entity/activity/agent)语义即可,**不要上 OpenLineage/Marquez** 这种 fleet 级机器 —— 单人一个 JSON provenance 块足够。这样「这条答案用了降级源」变成**可查询**的。

**② 维护滚动的 per-source 质量分**(取代静态优先级被信任):成功率 + 新鲜度(上次成功抓取的 recency)+ **多源一致性**(与其他源在重叠 claim 上的吻合度),合成一个标量供 cascade 参考。关键:**让 measured score 覆盖静态顺序** —— 一个静态高优先但已 stale/开始分歧的源被自动降权,一个一贯准确的「fallback」被升权。这天然适合做成 `meta-cross-skill-scan` 式的周期 job,产出调整 source 权重的 `Proposal`。

**③ 冲突按质量加权裁决,不是 first-wins:**每源投票按学习到的可靠度加权(Bayesian/迭代 truth-discovery 风格),并**防「抄袭式一致」**(两个总互相 echo 的源不算独立佐证 —— SmartMTD 的洞见);低置信冲突走你已有的 `wiki-conflict-resolve → Proposal(supersede)`。

> **结论 2**:鲁棒性收口到 `resilient_call()` + 把 LiteLLM 当规格;**「降级 vs 原始」的正解是 provenance(served_via)+ 学习型质量分 + 按质量裁决冲突**,让 cascade 自校正 —— 这一步能让你的检索层从「静态优先级」进化成「自纠错」,是差异化最大的投入。

---

## 3. raw→wiki 整合过滤机制(Karpathy 路线)

**Karpathy 框架(直接智识父本)确认:** raw(你的 §1 raw/evidence)是 *immutable source of truth*,**wiki 才是被下游消费的知识层**(`raw/ 是源码,LLM 是编译器,wiki/ 是可执行产物,lint 是测试,query 是运行时`)。所以「1、2 是原始数据,wiki 层才该被消费」—— **你的理解完全正确**。

**机制该是什么?** 看成熟记忆系统(Graphiti/Mem0/Zep/HippoRAG)实际做法,清一色是 **LLM 抽取 + reconcile 决策,增量逐条,不是规则引擎**。其中 **Mem0 的两阶段 Extract→Update** 最值得抄:Update 阶段把每条新事实与已存近似项比对,LLM 在 **ADD / UPDATE / DELETE / NOOP** 里选一个。Zep 在 LongMemEval 上以 63.8% vs Mem0 49.0% 领先,差距归因于**存事实有效期窗口而非时间戳快照**(= 你已有的 bitemporal)。

**它该是 skill 吗?——不是 per-domain skill,是 foundation operation。** 这是回答你 §4 困惑的关键区分:
- **整合/过滤机制本身 = 共享基础设施**(你的 `wiki-ingest` foundation skill + `wiki-apply-proposal`),它对所有域同构:retrieval evidence → 抽 candidate claims → `Proposal(kind=wiki_update)` → 人审 → 落地 + 写 PreferenceRecord + FTS 重建。
- **domain skill 只提供「镜头」**:该域的 schema(`domain_schemas.py`)、authoritative sources、lint 阈值、judge 权重 —— 即「这个域里什么算一条好 claim」。
- 你当前的 batch + 人审 Proposal 门**比 Mem0/Graphiti 的自治写更安全**,对「你真正信任的个人正典」是正确取舍。

**唯一要补的:把 ingest 从「append」升级成「reconcile」** —— 抽取步对每条 candidate claim 显式产出 **ADD / UPDATE / SUPERSEDE / NOOP** 决定(你已有 `wiki-supersede`,只差让 ingest 主动给决定而非只追加)。这是「日志」与「知识库」的分水岭。

**图层要不要上?** 不要上 MS-GraphRAG(全局重算、376× token 开销)。学术共识:**GraphRAG 只在真多跳推理时才回本**,简单事实检索 vanilla RAG 持平或更好。**你的 FTS5 + Markdown `[[wikilink]]` 本身就是轻量知识图谱**(wiki 交叉引用 = 边,LLM 查询时遍历 = 正是 Karpathy 的「读 index → 钻进链接页」)—— 拿到 ~80% 多跳收益、~1% token 成本、零新基建。**只**在某个域确实多跳失败时,**局部**引 nano-graphrag(1100 行可读可 fork)或借 Graphiti 的 bitemporal 模型。

> **结论 3**:机制 = **foundation 级 batch + Proposal 人审**(已有,正确且更安全);补一个 **4-op reconcile** 决策;domain skill 只当「镜头」不当「机制」;**不上重图引擎**,FTS5+wikilink 已是合适的轻量图。

---

## 4. 领域垂直技能:职责拆分 + 原子化 + 多域编排

**现状(核实):** 19 个 domain `*-wiki` skill 是从 `DOMAIN_SCHEMAS` 自动渲染的**声明式 SKILL.md 契约**,5 段式(Retrieve / Apply / Guardrails / Eval / Write Policy),**指向 CLI 操作而非自带实现**。所以技能确实在一份契约里**同时声明了 检索 + wiki 维护 + 下游 Apply** 三职责 —— 这就是你说的「揉在一起」。Foundation skill(原语)与 Functional skill(编排器)已分得很干净。

**SOTA 判断:你的「blackboard + memory-vs-actor」其实已经是对的,只差把三职责显式分离 + 补多域编排。**

**正确的三层心智模型(2025-26 共识 = 原子 + 可组合,反对胖技能):**

```
                      ┌─────────────────────────────────────────┐
   actor 平面(执行)   │  Application 编排器 (lead agent)          │
                      │  app-report-build / chat-route /          │
                      │  entity-timeline-build / order-propose…   │ ← 只 READ 知识库
                      └───────────────┬───────────────────────────┘
                                      │ 调度/组合
   memory 平面(维护)  ┌──────────────┴───────────────────────────┐
                      │  19 个原子 domain skill (知识镜头)         │
                      │  - 提供 schema / 源 / lint / judge 权重    │
                      │  - 经 Proposal 写,**永不直写 vault/wiki** │
                      └───────────────┬───────────────────────────┘
                                      │ 复用
                      ┌──────────────┴───────────────────────────┐
   foundation 原语     │ retrieve / context-pack / wiki-ingest /   │
                      │ wiki-apply / claims-show / judge / ab-test│
                      └───────────────────────────────────────────┘
        共享 blackboard: vault/wiki + claims.jsonl + Proposal 队列
```

**关键不变量(最重要的一条架构纪律,你 CLAUDE.md 已写「Agent 不允许直写 vault/wiki」):actor 永不直写知识库,所有写经 `Proposal[T]` + 人审。** 这与 LangGraph 的 Store-vs-Checkpointer、Anthropic 的 gather-then-synthesize 完全一致。**不需要引 LangGraph/AutoGen/CrewAI** —— 对单人 stdlib-first 项目它们是重依赖去复制你已有的结构;借它们的**词汇**(Store vs actor)和**纪律**(给每个 subagent 明确目标/输出格式/工具边界)即可。

**原子化怎么落:**
- 把 domain skill 的 5 段式**收敛为 2 段**:`Retrieve`(产 context-pack)+ `Maintain`(产 `Proposal(wiki_update)`)。**删掉 domain skill 里的「Apply/下游」段** —— 下游产品流上移到 application 编排器。这样 domain skill 回归「知识镜头」单一职责,重叠度自然下降(research/ai_progress/agent_systems 的差异只在 schema+源+阈值,不在执行逻辑)。
- 多域任务交给**编排器**,domain skill 不需要知道彼此。

**多域编排(当前缺失,`task_router.py:402` 只取 top-1):** 用 **plan-and-execute**:编排器产出有序计划(列明每个子问题归哪个 `*-wiki`)→ 并行 fan-out 各域 context-pack → **一次性 synthesize**(gather-then-synthesize 拆分)→ 产 Proposal。按复杂度伸缩(Anthropic:简单事实 1 agent/3-10 tool calls,广度研究才 10+ subagent;多 agent ~15× token,值才用)。路由用**三级 cascade**:`task_router` 启发式(免费/确定,主)→ `semantic-router`(低置信兜底,~100ms)→ LLM(最后 tiebreaker),**不要把 LLM 放在每个 query 的热路径**。`entity-timeline-build` 是你已有的广度型范例。

> **结论 4**:domain skill 从 5 段收敛为「检索 + 维护」两段、去掉下游段;下游上移到 application 编排器;补 plan-and-execute 多域编排 + 三级路由;保持原子+可组合,**不引重框架**。

---

## 5. 应用层技能 × 领域知识技能:调度配合 + GEPA/harness 闭环

**完整「知识 → 生产力」闭环:**

```
用户 query
  │
  ▼ 三级路由(heuristic→semantic→LLM)          ← 决定「谁来做」
  ▼ Application 编排器 plan-and-execute
  │    ├─ 单域 → 1 个 domain skill 取 context-pack
  │    └─ 多域 → 并行取各域 context-pack → 合成
  ▼ 读知识库(wiki/claims,只读)                ← memory 平面
  ▼ 生成产品(report/timeline/answer/order…)     ← actor 平面
  ▼ 产出 = Proposal(generation) → 人审            ← 写门
  ▼ 人审 accept/reject/edit → PreferenceRecord    ← 飞轮信号
  └────────────────────────── 回到 GEPA(下)
```

**「什么时候调谁、用什么方案」** 由编排器据路由置信度 + app-intent(`task_router.py:265` 的 schedule/report/pptx/finance_op…)+ 任务复杂度决定;domain skill 只被动提供镜头,application skill 主动编排。

**GEPA / harness 进入整条链路(当前闭环开口:`prompts/<domain>/v1/system_prompt.md` 编译出来后不自动回流 SKILL.md):**

成熟端到端工作流(全部映射到你已有组件):
1. **整条 cascade 建成一个 DSPy program**,每阶段(retrieve→extract→synthesize)是一个 module,用**一个端到端 metric** 打分(你 v0.45 把 LLMJudge 从 0.27→0.66 的那个 composite)。优化整程 metric 才叫「端到端」而非逐 prompt。
2. **首选 GEPA 而非 MIPROv2**:① 你能产**富文本反馈**(citation-missing / schema 违规 / claim 无支撑 / judge 理由)= GEPA 的超能力;② GEPA 样本高效,契合你「真实闭环才 10%」、短期没有 200+ 标注样本。GEPA 逐 module round-robin 变异、整程 metric 评分,正好让复合管线可解。
3. **训练集来自你自己的飞轮**:`claims.jsonl` + Proposal 通过/拒绝 + `ab_tests.sqlite3` 胜率**已是带标签的偏好信号**。approved Proposal → gold example,rejected → 负反馈文本。
4. **编译产物以 JSON 存进 git**(`program.save(save_program=False)`,可 diff、可在 PR review),`harness-compile`/`harness-compile-skill` 作为**部署步**把 JSON 拉进 `system_prompt.md` / `.agents/skills/<X>-wiki/`。**这一步就是闭合当前开口。**
5. **每次晋升都过 `ab-test` + Judge 门**:候选 vs 现役跑 `ab-test --judge llm`,要求决定性胜出,再经 Proposal 落地。**永不自动部署。**

心智模型:**GEPA 产 JSON 产物 → ab-test/Judge 把关 → harness-compile 部署 → 飞轮(claims+Proposal+胜率)生成下一轮训练集**。

> **结论 5**:配合靠「编排器主动、domain 被动」+ 三级路由 + plan-and-execute;GEPA 靠「一个 DSPy program / 端到端 metric / 飞轮训练集 / JSON 产物 / harness-compile 部署 / ab-test 门」**闭合当前开口**。

---

## 6. ResearchFlow 拆解、映射与重构

**它对应母仓哪些环节(7 阶段 → 5 Plane):**

| ResearchFlow 阶段 | 母仓 Plane | 母仓现状 |
|------------------|-----------|---------|
| Stage0 MinerU PDF 解析 | Retrieval(深解析) | **母仓缺** —— 这是 §1.3 的 Layer 4 |
| Stage1-3 chunk→锚点→合并验证 | Knowledge(抽取) | 母仓有 wiki-ingest,但无 PDF 级锚点抽取 |
| Stage4-6 图表富化 / 分节报告 / vault 导出 | Application(产品) | 对应 app-report-build,但各写各的 |
| Stage7 index 重建 | Control / 索引 | 对应 FTS5,但独立 index.jsonl |
| `.claude/skills/*`(collect/download/analyze/query/idea/review) | Skill | 与母仓 19 域**平行另一套** |

**集成现状:脱节。** 独立 `obsidian-vault/`;**不写** `.omni/claims.jsonl`、**不写** `vault/wiki/`;不 import 母仓模块;母仓只经 `research_assets.py:58-75` 只读发现;**重复**母仓 arxiv/S2/openreview 检索且无去重。即 —— 两套并行知识系统。

**重构(你是作者,可直接动):让 ResearchFlow 既是独立完美闭环、又原生作 submodule 回写母仓。** 定义一条**清晰契约边界**:

1. **它是 research 域的 Layer-4 深解析 + 分析引擎**(对外唯一职责)。母仓 finance/eng/ai_progress 共用同一深解析能力时,也走它。
2. **产出 candidate claims,经母仓 Proposal 回写**:ResearchFlow 的 `main_analysis.json`(method/experiments/formulas/figures + 录用 + 开源)→ 转成母仓 bitemporal claim 候选 → `Proposal(kind=wiki_update, domain=research)` → 人审 → 落 `vault/wiki/` + `claims.jsonl`。这是它从「孤岛」变「原生 submodule」的关键一根线。
3. **去重检索**:二选一 —— (a) 母仓连接器(S2/OpenAlex/arxiv)作为 ResearchFlow 的**统一取数前端**,ResearchFlow 只做下载+解析+分析;或 (b) ResearchFlow 的 collector 升级成母仓 connector。推荐 (a),消除双份 source 逻辑。
4. **存储桥接**:`obsidian-vault/paperPDFs` 与 `analysis/` 映射到母仓 research 域的 `vault/raw` / `vault/evidence`(或软链 + 一个 adapter),让「解析一次复用」缓存对母仓也生效。
5. **由母仓 TaskQueue 调度**:批量分析作为 `claude` lane 任务入队,而非独立脚本,纳入统一的 Proposal/审计/lease 机制。
6. **保持可独立运行**:契约是单向依赖(ResearchFlow 不 import 母仓;母仓通过 adapter 调它的 CLI + 读它的产物),所以它仍是一个能脱离母仓独立用的研究闭环 —— 这正满足你「既独立、又原生 submodule」的双要求。它也是未来其它垂直域 fork 的样板(它已有 `domain-fork` skill)。

> **结论 6**:ResearchFlow = research 子领域的完整闭环样板,但目前脱节。重构成**单向契约的 Layer-4 引擎**:统一取数 → 深解析 → 产 candidate claims → 经母仓 Proposal 回写 wiki/claims → 由 TaskQueue 调度。一根「Proposal 回写」线就把孤岛接成原生 submodule。

---

## 7. 冗余 / 死代码审计 + 数据初始化 + 配置清单

### 7.1 冗余 / 复杂 / 死代码(按确定性排序)

| 项 | 位置 | 性质 | 建议 |
|----|------|------|------|
| **prompts 编译产物不回流** | `prompts/<domain>/v1/*` → SKILL.md | 闭环开口,产物实际未被消费 = 半死 | §5 闭合;否则编译白跑 |
| **source_policy tier 未接执行** | `source_policy.py:48-259` | advisory,`resolve_policy` 无 retrieval 调用方 | 要么接进 cascade(§2.2),要么删 POLICIES 消除双源真相 |
| **ResearchFlow 重复检索** | `researchflow` collectors vs 母仓 connectors | 双份 source 逻辑 | §6 去重 |
| **stub functional skills** | finance-screen / project-plan / inbox-route / pptx-build | 契约在、返回 placeholder | 要么排期实现,要么从 registry 摘掉别让路由器推荐 |
| **cascade 静默少源** | `cascade.py:223, 305-331` | 鲁棒性缺口 | 加覆盖度断言 + 降 confidence |
| **pubmed 不取 abstract** | `biomedical.py`(esummary only) | 取数 bug | 补 efetch 取 abstract |
| **hf_hub/github 与论文记录不关联** | 各查各的 | 信息割裂 | 按 arxiv id 关联(§1.3) |

> 注:`source_policy.py:262-272` 已自述删掉了漂移的 per-domain composer —— 这是好的清理方向,但**残留的 tier 元数据仍未接执行**,属同一类未完成清理。

### 7.2 数据初始化(直接点亮全部功能)

git status 显示 `vault/evidence/*` 与 `vault/raw/*` 的 8 域已有 seed 落地(v0.44 的 8-domain seed 生效了),但 `vault/wiki` / `claims.jsonl` / PreferenceStore 仍空。点亮顺序(每域一遍,或脚本批跑):

```bash
# 1. 抓取 + 持久化 evidence(seed 已做一部分;按域补)
omni-hub retrieve --query "<域代表性查询>" --domain <X> --persist-evidence

# 2. evidence → Proposal(wiki_update)
omni-hub wiki-ingest --run-id <run> --domain <X>

# 3. 人审 → 落地(写 claims + PreferenceRecord + FTS 重建)
omni-hub propose-list --kind wiki_update --state pending
omni-hub propose-approve --id <pid>
omni-hub wiki-apply-proposal --proposal <pid>

# 4. lint 跑通(daily 会自动)
omni-hub wiki-lint --persist

# 5. 飞轮编译(攒够 PreferenceRecord 后)
omni-hub harness-compile-skill --domain <X>

# 6. 校验
omni-hub wiki-status / claims-stats / context-pack-build --tier standard
```

要让 claims/wiki 非空、飞轮起转,**核心动作是把已 seed 的 evidence 走完 ingest→approve→apply** —— 这是当前从「scaffolding 95%」迈向「闭环」的那一步。

### 7.3 还需完成的配置(API keys / env)

| 用途 | env / key | 影响域 |
|------|-----------|--------|
| Semantic Scholar 提速(1 RPS) | `S2_API_KEY` | research / ai_progress 论文 |
| OpenAlex polite pool(2025-02 起需 key) | `OPENALEX_MAILTO` / key | research 全域 |
| 美国政策 | `DATA_GOV_API_KEY`(已落,见 commit bd95cba) | us_policy |
| A 股行情 | `TUSHARE_TOKEN` | finance |
| 网页搜索 | `BRAVE_API_KEY` / `TAVILY_API_KEY` / `EXA_API_KEY` | 多域 head |
| 冲突事件 | `ACLED_KEY` | international_relations |
| RSSHub(中国部委源) | `OMNI_RSSHUB_BASE` | cn_policy |
| 社媒(tier2,可选) | reddit OAuth / `X_API`(TwitterAPI.io)/ 各 broker | social_* / cooking / travel |
| LLM 网关 | metapi / ccLoad / `ANTHROPIC_API_KEY` | judge / 生成 / GEPA |
| (新增)量化 | object store 路径 / ArcticDB lib | finance 量化 |

---

## 8. 分阶段重构路线图(单人可执行顺序)

1. **闭合数据闭环(最高优先,无新代码)**:把已 seed 的 8 域 evidence 走完 `ingest→approve→apply`,点亮 claims/wiki/PreferenceStore(§7.2)。
2. **取数补全 + 覆盖度断言**:论文连接器补 references/affiliation;新增 DBLP;hf_hub/github 按 arxiv id 关联;cascade 加 min-K 覆盖度断言(§1.2-1.3)。
3. **provenance + 质量分**:claim 盖 `served_via` + per-source quality score + 按质量裁冲突(§2.2)。
4. **domain skill 收敛 + 多域编排**:5 段→2 段;下游上移到 application 编排器;补 plan-and-execute + 三级路由(§4)。
5. **ResearchFlow 接通**:Proposal 回写 + 统一取数 + TaskQueue 调度(§6)。
6. **量化时序库**:ArcticDB/DuckDB+Parquet,connector 联邦(§1.4)。
7. **GEPA 闭环**:一个 DSPy program + 端到端 metric + JSON 产物 + harness-compile 部署 + ab-test 门(§5)。
8. **清理**:接入或删除 source_policy tier;实现或摘掉 stub skills;修 pubmed abstract(§7.1)。

**贯穿主线:每一点都指向「在扁平文件上加语义(confidence / supersede 决定 / source-tier / provenance)」,而不是「加系统(图数据库 / 表格式 / lineage 服务)」—— 这正是 2025-26 单人生产级的成熟取向,也是你架构已经站对的一端。**
