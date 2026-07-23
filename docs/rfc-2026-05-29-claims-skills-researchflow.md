# RFC: claims 单真源 · 19 域技能塌缩 · ResearchFlow 原生集成 (2026-05-29)

> 状态：**草案,待 review**。这是 [review-2026-05-29-architecture.md](review-2026-05-29-architecture.md) 的**可执行补集** ——
> 那份讲「为什么/SOTA 判断」,这份讲「怎么改/分几步/验收/回滚/文件级改动」。
> 立场:单人但生产级、优雅、鲁棒、**若非必要勿增实体**。每一步必须 `make test` 0 失败才进下一步。
>
> 三条工作流彼此**低耦合**,可独立推进、独立回滚:
> - **WS1** claims 单真源 + wiki 投影(杀三真源 drift)
> - **WS2** 19 域技能塌缩(N×M → N+M,消重叠)
> - **WS3** ResearchFlow 接成 research 域 Layer-4(经 Proposal 回写)
>
> 推荐顺序:WS1 → WS2 → WS3(WS1 把真源理清,WS2/WS3 都依赖「claims 是唯一知识真源」这个前提)。

---

## 0. 现状基线(代码级,均已核实)

| 事实 | 位置 | 对 RFC 的含义 |
|------|------|--------------|
| claims = JSONL 派生事实索引 | `.omni/claims.jsonl`;读写在 `knowledge_plane.py` `_load_claims_jsonl:1063` / `_append_claims` | WS1 要把它从「派生」升为「唯一真源」 |
| wiki 页面引用 claims | frontmatter `claim_ids: [...]`(`knowledge_plane.py:85`) | WS1 反转:wiki 由 claims **渲染**,不再各存一份真相 |
| wiki 写入唯一入口 | `apply_wiki_proposal:721`(校验 `state==APPROVED`) | WS1 在此之后追加「渲染投影」步骤 |
| evidence→claims 摄入 | `ingest_retrieval_evidence:841` | WS1/WS3 都复用这条,不另起炉灶 |
| bitemporal supersede | `supersede_claim:1091`(t_valid_to + superseded_by) | 已是 Graphiti 模式,WS1 保留 |
| 19 域 SKILL.md 自动生成 | `skill_stubs.py` 从 `domain_schemas.py::DOMAIN_SCHEMAS` 生成 | WS2 改的是**生成器模板**,不是 19 个文件 |
| 域 = 参数包(已存在) | `DOMAIN_SCHEMAS` + `DEFAULT_DOMAIN_CASCADES` + `domain-profiles.json` | WS2 的「域降级为 config」一半已就位 |
| 多域路由已有 | `task_router.py::route_multi`(v0.46) | WS2 编排层在此之上补,不重写 |
| RF 适配器只读 | `research_assets.py`:`discover_researchflow_assets` 只发现不回写 | WS3 在此之上加 claims 抽取 + Proposal |
| 写入硬约束 | AGENTS.md HR#5:agent 禁直写 vault/wiki,必经 Proposal | 三条 WS 全部不得违反 |

**已知小 bug(顺手记,不阻塞)**:`research_assets.py` `__all__` 列了未定义的 `research_assets` 符号。

---

## WS1 — claims 单真源 + wiki 投影

### 目标
消除「三真源 drift」:让 **claims.jsonl 成为知识的唯一真源**,`vault/wiki/*.md` 降级为**由 claims 渲染出的投影**(可随时 `wiki-render` 重建,不再是独立真相)。CLAUDE.md 自己点名的 drift 风险从根上消失。

### 不做什么(防过度工程)
- **不**引入图数据库(Kùzu 已死/FalkorDBLite dev-only/Mem0 已弃图存储)。claims + 一张 edge 关系仍用 JSONL/SQLite。
- **不**改 bitemporal 模型(`t_valid_from/to`+`superseded_by` 已是 SOTA)。
- **不**动 raw/evidence 两层(medallion 分层正确)。

### 分步(每步独立可验收 + 可回滚)

**S1.1 — 确立 claims schema 为权威,wiki frontmatter 改为「投影元数据」**
- 改:在 claim 记录里补 `rendered_into: [<wiki_path>...]`(反向链),`render_version`。
- 不变:claim 现有字段全保留。
- 验收:`claims-show <id>` 能列出它渲染进了哪些 wiki 页;旧 claims 读取不报错(向后兼容)。
- 回滚:字段是 additive,删字段即回滚。

**S1.2 — 新增 `wiki-render` op:从 claims 生成 wiki 页(纯函数,确定性)**
- 新增 `knowledge_plane.py::render_wiki_from_claims(domain, slug)`:取该页关联的 approved claims → 按模板渲染 markdown → 写 `vault/wiki/`。
- 注册为 builtin op(走 `OperationRunner`,符合 HR#1)。
- 关键:**渲染是确定性的**(同样 claims → 同样 markdown),这样 wiki 可丢弃重建。
- 验收:对现有 14 个 synthesis 页,`wiki-render` 产物与手写版语义等价(diff 只在格式);`make test` 0 失败。
- 回滚:新增函数 + 新 CLI 子命令,删除即回滚;不动 `apply_wiki_proposal`。

**S1.3 — `apply_wiki_proposal` 末尾接 `wiki-render`(闭合)**
- 改:approve→apply 落 claims 后,自动调 `render_wiki_from_claims` 重渲染受影响页。
- 验收:走一遍 `wiki-ingest → propose-approve → wiki-apply-proposal`,wiki 页是渲染产物且 claim 的 `rendered_into` 正确回填。
- 回滚:apply 里那一行调用注释掉即回滚到「手写 wiki」。

**S1.4 — 一致性校验 + 文档**
- 新增 `wiki-doctor` 检查项:`wiki 页存在但无对应 claims`(孤儿页) / `claim approved 但未渲染`(漏渲染)。
- 改 `CLAUDE.md` + `AGENTS.md` HR#5:明确「claims 是真源,wiki 是投影,wiki 可重建」。bump `WIKI_SCHEMA_VERSION`。
- 验收:`wiki-doctor` 0 孤儿 0 漏渲染;全测试过。

### WS1 验收总线
`claims.jsonl` 删一页对应的 wiki 文件后,`wiki-render` 能完整重建且 `wiki-search` 结果不变 → 证明 wiki 确为投影。

---

## WS2 — 19 域技能塌缩(N×M → N+M)

### 目标
把「19 个胖域技能(各自揉了 检索+wiki维护+下游任务)」重构为 **M≈10 个原子 foundation primitive × N=19 个薄域 config**。多域任务变成「一个共享检索 primitive 调 N 次传 N 份 config」,重叠从根上消失。

### 关键认知(来自 SOTA + 代码)
- 域技能 SKILL.md **已经是 `skill_stubs.py` 从 `DOMAIN_SCHEMAS` 自动生成的**——所以「塌缩」改的是**生成器模板 + 契约**,不是手改 19 个文件。
- 「域 = 参数包」**一半已就位**(cascade/schema/profile 都按域参数化)。缺的是:把 SKILL.md 模板从「三职责教程」改成「薄 stub:声明用哪些 primitive + 本域 config + 写入走 Proposal」。

### 分步

**S2.1 — 盘清并固化 M 个 foundation primitive 边界(纯文档,零代码风险)**
- 产出一张表:`retrieve / retrieve-grade-and-fuse / context-pack`(读)、`wiki-ingest / wiki-propose / wiki-apply / wiki-supersede / wiki-lint / wiki-render`(策展)、`judge-evaluate / ab-test`(评估)——确认每个都已是独立 op(大部分已是)。
- 验收:表中每个 primitive 都能 `omni-hub <op> --help` 单独调用。

**S2.2 — 改 `skill_stubs.py` 模板:域 SKILL.md 变薄 stub**
- 新模板内容:① 本域触发词(来自 DOMAIN_SCHEMAS)② 「检索调 `retrieve --domain <x>`;维护调 `wiki-ingest`;写入走 Proposal」③ 本域 config 指针(authoritative_sources / stale / rubric)。目标 ≤120 行(Anthropic 建议 ≤500,我们更激进因内容同质)。
- 重新 `omni-hub skill-stubs-sync` 生成 19 个新 SKILL.md。
- 验收:19 个 SKILL.md 重新生成;`skill-list` 数量不变;三真源(registry/SKILL.md/DOMAIN_SCHEMAS)`skill-sync` 0 drift;全测试过。
- 回滚:模板是单点,`git checkout skill_stubs.py && skill-stubs-sync` 即全量回滚。

**S2.3 — 多域编排器(orchestrator-worker)**
- 在 `task_router.route_multi` 之上加一个 application-plane 编排:多域任务 → 对每个域调一次共享 `retrieve`(传该域 config)→ 单次 synthesis → 单独 citation/lint pass(镜像 Anthropic lead→subagents→CitationAgent)。
- 显式委派契约:每个 worker 子任务带「目标+输出格式+源指引+边界」(防 Anthropic #1 失败模式:模糊任务→重复劳动)。
- 验收:一个跨 finance+ai_progress 的 query,跑出来两域各检索一次、合成一次、引用一次,无重复检索;`make test` 过。
- 回滚:新增编排 op,不改 route_multi 本身,删除即回滚到单域。

**S2.4 — 三级路由补 semantic 中间层(可选,低优先)**
- 现 heuristic 默认对 → 加 embedding semantic-router 处理歧义 query → LLM router 仅兜底。
- 验收:歧义 query 路由准确率提升(用现有 eval pack 量化);默认路径延迟不变。

### WS2 验收总线
多域 query 的检索调用次数 = 域数(不是技能数);19 个 SKILL.md 总行数显著下降且 0 drift。

---

## WS3 — ResearchFlow 接成 research 域 Layer-4

### 目标
让 ResearchFlow **既保持独立可跑的完整闭环**(单机 research pipeline),**又原生作为 research 域的深解析层**:它的 `main_analysis.json`(MinerU 抽的 sections/figures/tables/formulas/方法谱系)经**适配器抽成 candidate claims → Proposal → 人审 → 回写母仓 claims/wiki**。不重写 RF,只接缝。

### 现状缝隙(`research_assets.py` 已核实)
- ✅ 已有:`discover_researchflow_assets` 只读发现 RF 的 obsidian-vault。
- ❌ 缺:claims 抽取、Proposal 提交、域路由打标、bitemporal 包装。

### 分步

**S3.1 — 补全论文元数据层(Layer 1-3,不碰 PDF)**
- 按 review doc §1.3:S2 连接器补 references/affiliation;新增 DBLP 连接器(录用判定);hf_hub 与论文记录**按 arxiv id 关联**(现在各查各的);github 补 `/community/profile` 代码完整度分。
- 验收:给定 arxiv id,能拿到 作者+affiliation+references+录用状态+code/dataset/checkpoint 链接 + 完整度分。
- 回滚:每个连接器改动独立,逐个可回退。

**S3.2 — `research_assets.py` 加 claims 抽取适配器**
- 新增 `researchflow_analysis_to_claims(analysis_json) -> list[CandidateClaim]`:
  - `analysis_truth.decisive_evidence[]` → 结论类 claim
  - `method.changed_slots / baseline_methods` → 方法类 claim
  - `experiments.main_results[]`(benchmark/metric/delta) → 结果类 claim
- 每条带 evidence anchor(回指 RF 的 section/figure)。
- 修顺手的 `__all__` 未定义符号 bug。
- 验收:喂一个真实 `main_analysis.json`,产出结构正确的 candidate claims(单测,离线 fixture)。
- 回滚:纯新增函数。

**S3.3 — candidate claims → Proposal → 回写(闭合,严守 HR#5)**
- 新增 op `wiki-ingest-researchflow`:discover → 抽 claims → `Proposal(kind=wiki_update)` → 人审 → `wiki-apply-proposal`(复用 WS1 的渲染)。
- RF 的 obsidian-vault 退化为 RF 单机模式的本地缓存;**母仓真源仍是经 Proposal 落地的 claims**。
- 验收:跑一遍,RF 一篇分析 → pending proposal → approve → claims.jsonl 新增 + research 域 wiki 渲染;**全程无直写 vault/wiki**(HR#5 不破)。
- 回滚:新增 op,删除即回滚到「只读发现」。

**S3.4 — submodule 登记规范化**
- 按 AGENTS.md HR#4:RF 在 `agent-harness/manifest.json` 登记 `decision=upstream-direct`(你对 RipeMangoBox/ResearchFlow 有权限,直接 pin gitlink,不绕个人 fork)。
- 验收:`make harness-status` 显示 RF 为 upstream-direct;`git submodule` 状态干净。

### WS3 验收总线
ResearchFlow 单独 `cd` 进去能独立跑通;同时母仓 `wiki-ingest-researchflow` 能把它的分析经 Proposal 变成 research 域 claims。

---

## 风险与回滚总则

1. **每步一验收**:任何一步 `make test` 非 0 失败,立即停,不进下一步。
2. **WS 间隔离**:三条 WS 不共享文件改动(WS1 动 knowledge_plane;WS2 动 skill_stubs/task_router;WS3 动 research_assets/connectors),互不阻塞,可单独 revert。
3. **HR 红线**:全程不得违反 AGENTS.md 4+ 条工程硬约束(尤其 HR#5 写入门、HR#1 op 注册、HR#3 单测)。
4. **并发 session**:本分支有并发改动(prompts/researchflow/vault),每次提交只 `git add` 本 WS 明确改的文件,绝不 `git add -A`。
5. **GEPA 闭环**(review doc §5 的开口)归入 WS2 之后的独立小步:compiled prompt → `Proposal(kind=skill_update)` → 人审 → 写 SKILL.md(不自动 apply,符合 HR#13 反 reward-hacking)。

## 落地顺序建议(单人可执行)
```
WS1.1 → WS1.2 → WS1.3 → WS1.4   (claims 单真源,~最高 ROI,先做)
WS2.1 → WS2.2 → WS2.3            (技能塌缩 + 编排;WS2.4 可缓)
WS3.1 → WS3.2 → WS3.3 → WS3.4   (RF 集成;依赖 WS1 的渲染闭环)
之后:GEPA auto-relay 小步 + 量化时序存储(见 review doc §1.4)
```
