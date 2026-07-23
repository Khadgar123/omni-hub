# Discord 全量文本优先、媒体并行补齐的可审计总结设计

日期：2026-07-21

状态：待用户书面复核

选定方案：方案 1——严格消息并集 + 100% 文本覆盖账本 + 媒体独立并行层

## 1. 背景与证据基线

当前 Discord Collector v2 已经保存了大规模原始消息、消息证据、回复关系和媒体资产，但“采集很多”不等于“已经形成完整、可核验的博主总结”。本设计解决的是从冻结证据到可阅读总结之间缺失的派生处理层，不重新定义既有采集证据，也不把不可访问或未解析内容伪装成完整。

本设计绑定以下已核验输入：

- 正式四分片历史基线：831,915 个唯一 root message ID。
- 最新闭合边界明确列出的 message ID：11,119 个；其中与基线重叠 609 个，严格新增 10,510 个。
- 严格消息并集：`831,915 + 11,119 - 609 = 842,425` 个唯一 message ID。
- 最新闭合 raw response 共 23,978 行，但摘要语料只能采用 head-catchup 明确列出的 11,119 个 ID；边界页中未列入闭合集合的行不得旁路进入摘要。
- 609 个重叠 ID 必须保留两份快照；忽略签名 URL 查询参数刷新后，17 个仍有真实快照变化。闭合快照用于当前态摘要，历史快照作为审计证据保留。
- 最新闭合 namespace：`closure/full-pinned-cdd1ad0-s01retry-20260721T0009Z`。
- 正式计划：`plans/full-pinned-20260719T170645Z/plan.json`，SHA-256 `fdc4c3bb1770454091494a6b9bc1a584ad510d80f0d90642d50abda4f930d731`。
- merge request SHA-256：`90432fe6923654c69ab1fbb5c868b63218722e606d1ba048c90ddb7d2b146e6d`。
- merge audit SHA-256：`a7ece2756b02de77fad640fe22c71e68dfd766ba033b6a78814906899924f35e`。
- closure audit SHA-256：`0ec713817d8c6a9cd217ea7db0749541a868c7eecc4e88f9fe24db1f944b2807`。
- census SHA-256：`a4d8600b2580d7779d1e1c8944f1ab476d4157414170a14e2f38ee42af6cc8bf`。
- head catchup SHA-256：`823d9fd46573faa690eef1214f55fe3dd5e6b79d0ad8af1aaa3a16d771a68d1b`。
- `T_close` 来源：`preflight/discord-scope-20260719T1629Z.json`，SHA-256 `800d24f71fbf64bef234ae31f3d2e337c77ca9ff6228fc968aca44b53ceb44f1`。
- 共同闭合上界 `H = 2026-07-21T00:57:18.979Z`。
- census threads 121、head targets 234，历史缺失 thread 为 0，missing/unexpected/invalid/unverified target ID 均为 0。
- 123 个 private archived thread 返回 403；它们不在 bot 的 authorized readable scope 内，内容及潜在媒体未知。

该 closure audit 的验证错误为 0，但顶层状态仍为 `incomplete`，原因是四个媒体分片未满足完成门且存在 private archived 403。本文的 842,425 只表示当前目标集合的 authorized readable message corpus，不表示整个 Discord 服务器全部可见历史。

媒体现状必须与消息现状分开陈述：

- 基线媒体记录 146,017 个；已捕获 binary 142,704 个；failed 2,910 个；reference-only 403 个。
- 已捕获 binary 去重后 110,707 个文件，约 30.06 GiB，包括图片 109,190、视频 1,357、音频 97、application 54、text 9。
- 2,910 个 failed 中：HTTP 400/404/415 为 2,438 个，unsafe 为 335 个，download transient 为 71 个，resolution unresolved 为 62 个，hard mismatch 为 4 个。
- 403 个 reference-only 是无 bytes/SHA/blob 的 YouTube player 引用，不能计为二进制媒体。
- 10,510 个新增消息尚未进入正式 message-evidence/media pipeline；只读重建发现 10,525 个节点、2 个 `referenced_message_unknown` partial、1,849 个待处理可下载媒体 occurrence（附件 1,704、embed 145）和 2,424 个引用 occurrence。

上述数字是本设计的验收基线，不是“已经完整”的声明。

## 2. 目标与非目标

### 2.1 目标

1. 为 842,425 个唯一 message ID 建立唯一、可恢复、可验证的 primary coverage assignment；任何消息不得遗漏，也不得被两个 primary batch 重复计数。
2. 不依赖关键词预过滤，直接阅读所有 primary message；明显重复只在归纳层合并，原始证据和逐消息 disposition 永久保留。
3. 先交付完整文本覆盖的频道、thread、博主/来源、日/周/区间总结；媒体下载和媒体语义解析并行推进，随后增量升级受影响结论。
4. 每一条归纳结论都能追溯到 message ID、频道/thread、Discord delivery identity、时间戳、回复关系以及相关媒体 occurrence 的当前状态。
5. 将 `text_primary_coverage`、`reply_context_coverage`、`media_occurrence_coverage`、`media_semantic_coverage`、`private_scope` 五类状态独立披露，禁止用单一“完成率”掩盖差异。
6. 支持崩溃恢复、模型失败重试、独立复核和 no-clobber 发布；任何失败批次都必须显式保留，不能静默跳过。

### 2.2 非目标

- 本设计不把聊天内容直接转换为可执行交易订单，不发送 Discord 消息，不实现 Gateway。
- 本设计不计算博主胜率、收益率或排名，不续用旧的选择性回测结果。交易事件化、K 线补齐和无未来函数回测必须另写设计。
- 本设计不绕过 Discord 权限，不把 123 个 private archived 403 thread 声称为已读取。
- 本设计不放宽媒体 SSRF、MIME、大小、candidate、YouTube 或 HTTP 400/404/415 语义。
- 本设计不要求等待所有媒体失败都解决后才发布文本总结，但任何与未解析媒体相关的结论必须标记为待媒体补强。
- 本设计不把 webhook delivery username 直接等同于真实博主身份。

## 3. 方案选择

采用分阶段证据管线：

1. 先冻结严格消息并集和覆盖账本。
2. 对全部 842,425 条消息执行无关键词过滤的 primary map。
3. 按频道/thread、来源身份和时间窗口做分层 reduce。
4. 独立做 coverage/citation/identity/conflict review 后发布文本总结。
5. 媒体捕获与 OCR/图表/视频语义处理并行；每次新增媒体证据只重开受影响的 summary claim，不重新定义文本覆盖。

未选方案：

- “全部多媒体处理完再总结”：会让 30 GiB 以上存量和失败媒体长期阻塞已经可读的文本，且无法给用户及时可用结果。
- “只总结优先频道”：速度快，但无法证明全量覆盖，和用户要求的完整性冲突。
- “关键词先筛选再让模型看”：容易漏掉改单、撤单、上下文、图片说明和聊天纠错，不适合作为 primary coverage。
- “删除旧记录重新抓”：Discord REST 限速、权限和已失效媒体不会因删除而改善，还会破坏审计链和快照差异证据。

## 4. 架构与仓库约束

实现遵守主仓库 5-Plane 和工程硬约束：

- 新的确定性基础逻辑放在主仓库模块，例如 `src/omni_hub/discord_summary.py`；仅负责语料重建、账本、验证、派生输入/输出和发布，不直接调用 LLM。
- 新 CLI 子命令扩展现有 `src/omni_hub/cli/discord.py`，继续由其导出 `register(subparsers)` 与 `COMMANDS`，不得向根入口追加分支。
- 所有落盘写操作先注册到 `src/omni_hub/builtins.py`，再通过 `OperationRunner.run(OperationSpec(...))` 执行，保留 policy 与 audit。
- 模型 map/reduce/review 任务必须进入 `TaskQueue`，由专用 tool-free worker profile 消费；Application Plane 和 CLI 不直接调用模型。现有 `claude`/`codex` 默认 profile 仍有只读工具/文件能力，不得直接用于不可信 Discord 正文。
- 原始 Discord evidence、正式 closure 和 media ledger 只读；派生产物写入新的 no-clobber summary namespace。
- 不引入“Temporal-grade”或“Iceberg-grade”表述；本实现只是可审计的 lightweight local pipeline。

逻辑数据流：

```text
冻结基线 + 明确 closure message IDs
  -> 确定性 corpus reconstruction
  -> coverage ledger + content-addressed batch inputs
  -> TaskQueue map（全部 primary messages）
  -> target/time/identity reducers
  -> citation + identity + conflict review
  -> no-clobber text summary release
  -> media enrichment events
  -> 仅重开受影响 claims 并发布新版本
```

## 5. 冻结语料与快照规则

### 5.1 Canonical corpus

`discord-summary-plan` 必须在零网络条件下：

1. 验证绑定的 merge audit、closure audit、census 和 head-catchup 文件 SHA。
2. 从基线 evidence 读取 831,915 个唯一 message ID。
3. 只从 head-catchup 的显式 ID 集合读取 11,119 个闭合消息，不扫描 raw 边界页扩大范围。
4. 生成恰好 842,425 个唯一 canonical message ID；若 missing、extra、duplicate 或无法定位原始 evidence 任一非零，立即 fail-closed。
5. 对 609 个重叠 ID 保存 baseline 与 closure 两个 source snapshot 引用；当前摘要读取 closure snapshot，差异审计保留 baseline snapshot。

Canonical corpus 只固定“需要阅读哪些消息”，不覆盖原始 JSON，不改消息正文，不删除旧快照。

### 5.2 Primary 与 secondary context

- 每个 canonical message ID 恰好属于一个 primary batch，primary assignment 是覆盖率唯一分母。
- 为理解回复链，batch 可以附带 parent/referenced message 作为 secondary context；secondary context 必须带 `context_only=true`，不能再次增加 completed message 数。
- thread、forum post、nested reply 必须保存 parent target 和 reply edge；无法解析的引用作为显式 unresolved evidence，不丢弃当前消息。
- 编辑消息以当前 closure snapshot 为摘要主输入，同时保留 edited timestamp 和历史 snapshot provenance。

## 6. 批次与全量阅读策略

### 6.1 确定性分批

Primary batch 按以下顺序稳定生成：

1. target family；
2. channel/thread ID；
3. message timestamp；
4. Discord snowflake/message ID。

每个 batch 最多 500 个 primary messages，并同时受模型适配器固定的完整请求预算约束，尽量不跨 target；超长 target 按时间连续切片。完整请求预算必须计算最终序列化的 system prompt、output schema、primary、secondary context 和 JSON overhead，并绑定具体 model revision 的 context limit、保留 output limit 与 safety margin。842,425 条消息的理论下限是 1,685 批，实际批数由 target 边界和请求预算决定，不能把 1,685 硬编码为验收值。消息数上限、预算算法/版本、context/output limits、排序版本都必须写入 corpus manifest，resume 时不得漂移。

单条 message record 超过预算时，不能因为“已经独立成批”就标为已处理。系统要按 canonical UTF-8 byte offsets 做确定性 segment，保存每段 hash；全部 segments 成功且聚合验证后，原 message ID 才能产生唯一最终 disposition。若适配器无法无损分段，则该消息保持 blocked。provider 返回 truncation、非完整 finish reason、超过声明 limit 或缺段时，该 attempt 必须 invalid，不能计入 coverage。

每个 batch input 是 content-addressed JSONL，绑定：

- summary run ID；
- corpus SHA；
- batch ID 与 input SHA；
- target/thread metadata；
- primary message IDs；
- secondary context IDs；
- source snapshot references；
- prompt schema/version。

Assignment 与执行优先级分离：batch ID 和 primary ownership 始终由上述稳定排序决定；TaskQueue 的 dispatch priority 可以按用户已指定的置顶/优先频道先跑，再覆盖其余 targets。优先级只能改变交付先后，不能改变 corpus、跳过低优先级消息或让同一消息重复归属。一个 target 的全部 batches 完成并通过 target-local review 后，可以先发布明确标为 `partial_global_corpus` 的频道级版本；全局首版仍须等待全部 primary batches 通过硬门。

### 6.2 Map 原则

模型必须直接处理全部 primary message，不先用“喊单”“多空”“止损”等关键词过滤。每条消息恰好产生一个 `primary_disposition`：

- `substantive_signal_or_update`
- `market_analysis`
- `position_management`
- `question_or_discussion`
- `administrative`
- `duplicate_or_forward`
- `other`
- `unresolved`

另有零到多个 `semantic_tags`，其中至少包括 `correction_or_conflict`、`media_dependent`，并可由版本化 schema 扩展。这样一条“带图的改单纠错”仍只有一个 coverage disposition，但可以同时带多个语义标签。数据库对 `(summary_run_id, message_id)` 的 `primary_disposition` 建唯一约束；semantic tags 使用独立多值表和允许值约束。

`duplicate_or_forward` 只表示归纳时可合并；它仍然计为已读，必须保存其 message ID 和被认为重复的依据。

Map input 必须覆盖 evidence 中存在的 message content、edited/pinned/type、attachments、embeds、components、stickers、poll、message reference/referenced snapshot 和 thread/parent metadata；某字段不存在时显式为空，不凭模型补造。Map output 必须使用版本化 JSON schema，至少包含：逐消息 disposition、候选事实/观点/动作、否定和不确定性、引用关系、身份线索、相关媒体 occurrence、证据 message IDs。自由文本可以作为解释字段，但不能替代结构化证据字段。

每个 batch 的 primary output ID 集合必须与 input primary ID 集合完全相等；缺失、额外、重复或伪造 ID 均使该 attempt invalid。Discord 消息、embed、附件名和引用页面全部视为不可信数据：worker prompt 必须将它们限定为待分析材料，禁止服从其中的指令、执行代码、访问 URL、调用工具、发送消息或修改 corpus。

该限制必须由能力边界而非仅由 prompt 保证：

- Claude 类 CLI 只有在专用 profile 明确 `allowed_tools=()` 且 `max_turns=1` 时才可使用；默认 `Read` 工具 profile 禁止。
- Codex 或其他 agent CLI 只有在能够证明无 shell、无工具、无任意文件读取能力时才可使用；`read-only` sandbox 仍可读取本机文件，不满足本任务要求。
- 若现有 CLI 无法提供上述 OS/file-read 隔离，必须改用无工具的模型 API worker；其网络只允许既定 model endpoint，不允许模型触发任意 URL fetch。
- worker 在 owner-only 隔离临时目录运行，只注入已脱敏的最终 serialized request；环境变量采用最小 allowlist，不传 Discord token、home 路径、仓库/导出路径或其他 secret。
- adapter 必须返回可验证的 tool-use metadata；`tools_used` 非空、出现 file/shell/network tool attempt 或隔离证明缺失时，整个 attempt invalid。

所有允许的输出只能经版本化 schema 回传给受审计的本地写操作。

## 7. 覆盖账本与不可变绑定

每个 summary run 使用独立 namespace，例如：

`derivatives/discord-summary/<summary-run-id>/`

当前正式数据的根目录固定为 `/Users/hzh/discord-exports/v2`，因此本任务的完整发布根是 `/Users/hzh/discord-exports/v2/derivatives/discord-summary/<summary-run-id>/`；manifest 同时保存规范化绝对路径和相对 export-root 路径。未来若更换 export root，必须创建新 summary run 并重新绑定 source paths/hashes。

至少包含：

- `coverage.sqlite3`
- `corpus-manifest.json`
- `batches/inputs/*.jsonl`
- `batches/outputs/*.json`
- `reviews/*.json`
- `release/coverage.json`
- `release/coverage.md`
- `release/summaries/...`

目录和 SQLite 文件必须 owner-only。SQLite 至少包含以下逻辑实体：

| 实体 | 作用 |
| --- | --- |
| `corpus_message` | canonical message ID、target、时间、primary snapshot |
| `source_snapshot` | baseline/closure provenance、content hash、edited state |
| `primary_assignment` | 一个消息到唯一 batch 的映射 |
| `batch_run` | input/output hash、worker、模型、prompt、状态、attempt |
| `message_disposition` | 每条消息唯一 primary disposition |
| `message_semantic_tag` | 每条消息零到多个版本化语义标签 |
| `evidence_card` | 可归纳事实/观点/动作及 citations |
| `media_occurrence` | occurrence 的 acquisition disposition、semantic status 与原始 asset provenance |
| `reduction` | channel/thread/identity/time-window 的派生结果 |
| `review` | schema、citation、coverage、identity、conflict 审查结果 |

账本顶层必须绑定：代码 commit SHA、正式计划/merge request/merge audit/census/head/closure/T_close source 的精确规范化路径与 SHA、corpus SHA、模型供应商/model revision、采样参数与 seed（若支持）、prompt version、output schema version、生成时间和 OperationRunner audit ID。路径与 SHA 必须成对验证，不能只接受同类型的另一个文件。

先对 canonical attempt spec 计算 SHA-256。Attempt spec 至少包括 task kind（map/reduce/review）、corpus/reduction input SHA、batch/claim-set ID、provider、model revision、sampling config/seed、adapter version、prompt version、output schema version、serialized request SHA 和 reviewer independence profile。TaskQueue idempotency key 必须由该完整 `attempt_spec_sha256` 派生；map、reduce、review 都使用同一规则。

只有 `attempt_spec_sha256` 完全相同的成功结果才能幂等复用；input、provider/model、sampling、adapter、prompt、schema 或 task kind 任一改变都生成新 attempt，不能被旧 TaskQueue key 去重，也不能覆盖旧输出。每个 attempt 还保存 raw response SHA、validated output SHA、finish reason 和 validator version。

Batch input 只包含完成文本理解所需的正文和结构化 metadata。带凭据或签名查询参数的 URL 必须在进入派生 input 前替换为不含 secret/query 的安全 locator、occurrence ID 和已知 SHA/disposition；原始 URL 只留在既有只读 evidence。summary namespace 只用于本地私有分析，不允许自动对外发布；若未来要发送或公开，必须另走 `EXTERNAL_SEND`/`EXTERNAL_PUBLISH` 审批。

实施计划必须固定获准使用的 tool-free worker profile、模型和数据边界。不得把同一批私有消息静默转交给另一个供应商；provider/model 变化会生成新 attempt 并在账本中披露。任务日志、CLI stdout 和失败诊断只记录 IDs、hashes、计数和脱敏错误，不回显批量消息正文。

## 8. 身份与博主归属

当前语料中 webhook delivery 占绝大多数。Discord author envelope 只是投递身份，不天然等于内容原作者。因此使用两层身份：

1. `discord_delivery_identity`：Discord author ID、username/global name、bot/webhook/application 字段及其出现区间。
2. `claimed_source_identity`：内容声称来自的博主/交易员/来源；必须有证据和置信度。

禁止仅凭同名 username 合并身份。`claimed_source_identity` 可由以下证据支持：

- 用户确认的频道到博主映射；
- 频道/thread 固定归属与持续一致的 webhook 名称；
- embed/forward metadata 中稳定的来源字段；
- 消息正文中的明确署名；
- 人工复核映射。

每个映射保存 evidence IDs、有效时间范围、confidence 和冲突项。置信度不足时，输出以频道/投递身份为主，不强行命名博主。

## 9. 分层归纳与输出合同

Reduce 采用从小到大的证据层级：

1. batch map cards；
2. channel/thread 时间片；
3. claimed source/博主时间线；
4. 日、周、用户指定区间；
5. 跨来源共识、分歧和纠错。

Canonical 时间窗口统一使用 UTC 半开区间 `[start, end)`：日界为 00:00 UTC，周从周一 00:00 UTC 开始。Asia/Singapore 或其他本地时区只作为标注清楚的展示视图，不能改变 canonical membership；时区数据库版本、窗口起止毫秒和 reducer version 必须写入 reduction provenance。

文本首版至少发布：

- 全局 coverage 报告；
- 每个 channel/thread 的时间线与主题摘要；
- 有足够身份证据的博主/来源摘要；
- 每日、每周和指定区间的信号/观点/纠错整理；
- 跨来源共识与相互冲突；
- low-confidence、unresolved、media-dependent 和 access-blocked 清单。

每条可验证结论必须引用：message ID、`snapshot_ref`、snapshot content hash、channel/thread、author envelope、UTC 时间戳、必要的 reply chain，以及相关媒体 occurrence 的 disposition。609 个 overlap ID 的 citation 不得只写 message ID；必须明确引用 baseline 或 closure snapshot。发布层不得包含 bot token、Authorization、带签名查询参数的媒体 URL、原始内部 logical key。

Citation validator 先验证存在性、snapshot hash、target 和时间，再由独立 evidence-support reviewer 检查 claim 是否真的被证据支持，包括主体、否定/肯定、时态、数值、标的和不确定性；“有一个格式正确的引用”不能替代 entailment。Reviewer 是独立 TaskQueue task/attempt，默认使用不同 review prompt，且只看 claim、绑定 snapshot ref/hash 的同一已脱敏 evidence projection 和结构化上下文，不直接读取原始含签名 URL 的 snapshot，也不读取 map/reducer 的自由推理过程；若可用，实施计划优先选不同 model revision，并将差异写入 provenance。Reviewer 同样必须运行在 §6.2 的 tool-free 隔离 profile。

本阶段不得输出“胜率最高博主”“已验证收益”等回测结论；只能整理原始观点与动作，并注明证据完整度。

## 10. 媒体独立层

媒体获取与媒体语义是两个正交状态，不能共用一个 `pending`。每个媒体 occurrence 必须且只能有一个 `acquisition_disposition`：

- `captured_binary`：有 bytes、SHA-256、blob path 和已验证 MIME；
- `reference_only`：只有外部播放器/页面引用，无 binary；
- `terminal_failed`：已审计且按当前政策不可自动恢复；
- `retryable_pending`：有界重试或明确补偿仍待执行；
- `unprocessed_pending`：尚未进入正式 acquisition pipeline；
- `invalid`：ledger/source 证据不合法，阻止完成。

另有且仅有一个 `semantic_status`：`not_applicable`、`pending`、`succeeded`、`failed` 或 `invalid`。Captured binary 可以同时是 `semantic_status=pending`；reference-only、terminal failed 在没有合法 bytes 时通常为 `not_applicable`。原始 attempt history、terminal reason 和 failure detail 永久保留；派生状态不能改写旧失败。

现有 2,910 个 `failed` 不能整批映射为 `terminal_failed`。必须依据当前 record、最新 attempt、retry budget、HTTP/MIME/安全分类逐项派生；其中 transient 或仍有合法补偿路径的记录进入 `retryable_pending`，只有确定终态才进入 `terminal_failed`。

SHA 去重只用于复用下载、OCR、图像理解、音视频转写结果；不能把多个 occurrence 合成一条而丢失消息、频道、时间和位置关系。

媒体语义处理单独记录：

- 图片：OCR 文本、图表/标注语义、模型与版本、置信度；
- 视频：容器/时长、抽帧策略、音轨转写、关键帧摘要；
- 音频：转写、语言、时间片；
- reference-only/failed：保留不可验证说明，不推断其内容。

若媒体结果支持、否定或修正已发布文本 claim，系统生成受影响 claim 列表和新版本 summary；旧版本及其证据绑定保留。媒体未完成不会阻止 `text_primary_complete`，但会阻止 `media_semantic_complete`。

## 11. 状态模型与验收门

### 11.1 独立状态

发布报告必须分别给出：

- `text_primary_coverage`：canonical messages 的 primary map 完成度；
- `reply_context_coverage`：引用/回复目标已解析、未知、权限阻塞和终态缺失的数量；
- `media_occurrence_coverage`：每个 occurrence 是否已有合法 acquisition disposition；
- `media_semantic_coverage`：eligible captured binary 的 semantic status 分布；
- `private_scope`：authorized readable、private archived 403、其他权限阻塞的数量。

禁止把五项加权成单一“总体完成率”。

### 11.2 `text_primary_complete` 与上下文硬门

只有以下条件全部满足才能声明 primary 文本阅读完成：

- corpus unique IDs 恰为 842,425；
- missing/extra/duplicate primary assignment 均为 0；
- primary completed message 数恰为 842,425；
- 每条消息恰有一个合法 primary disposition；
- failed/in-progress/invalid batch 为 0；
- output schema invalid 为 0；
- orphan citation、引用 corpus 外 ID、citation snapshot/hash/target mismatch 均为 0；
- evidence-support review 对主体、否定、时态、数值、标的和不确定性的 invalid/unsupported 为 0；
- secondary context 未被计入 primary completed；
- 每个 summary claim 至少有一个有效 citation；
- identity conflict、low-confidence 和 unresolved 均已列出而非静默消失；
- 独立 reviewer 通过 coverage、citation、identity 和 conflict 四类检查。

`text_primary_complete=true` 只证明 842,425 个 authorized primary messages 均已读取和分类，不证明所有被回复消息都可见。只有 unresolved/invalid/access-blocked reply references 均为 0 时，`reply_context_complete=true`；否则首版可以发布，但标题和 coverage 报告必须明确写 `reply_context_complete=false` 及精确 blocker 数量。当前只读 delta 重建中的 2 个 `referenced_message_unknown` 在正式证据修复前就是此类 blocker，不能用 `unresolved` disposition 把它们变成“上下文完整”。

### 11.3 媒体与权限门

- `media_occurrence_complete` 只表示所有 occurrence 已有合法 acquisition disposition 且 invalid 为 0，不表示 binary 全部成功。
- `media_semantic_complete` 要求 authorized、eligible occurrence 中 `retryable_pending`/`unprocessed_pending`/`invalid` acquisition 均为 0，且所有 captured binary 的 `semantic_status` 已到 `succeeded` 或 `failed`，semantic pending/invalid 为 0；它只表示语义处理已终结，仍必须单列 failed，不能声称所有媒体都已理解。reference-only 与 terminal_failed 作为不可提供 binary 的已披露边界单独计数。
- 2,910 个基线 failed、403 个 reference-only、1,849 个新增待下载 occurrence 必须逐类披露；数字随正式 delta pipeline 更新，但不能被重分类为成功来清零。
- 123 个 private archived 403 永远阻止“服务器全部历史完整”的声明；只有 Discord 权限变更或用户提供合法手工导出后才能补齐。

## 12. 错误、重试与崩溃恢复

- summary run 使用唯一 no-clobber ID；若目标 namespace 已存在，创建新 run，不覆盖。
- batch/reduce/review 入队键由 §7 的完整 `attempt_spec_sha256` 派生，保证 TaskQueue 幂等且模型/适配器变化不会误复用旧任务。
- 并行模型 worker 不打开 `coverage.sqlite3`，也不直接发布 summary namespace 文件；它们只返回绑定 task/input hash 的候选结果。单一受审计 coordinator 通过注册的 OperationRunner 写操作验证候选、原子写 content-addressed attempt artifact，并在 `BEGIN IMMEDIATE` 事务中提交 ledger。唯一约束保证即使多个 coordinator invocation 竞争，同一 attempt/primary disposition 也只能提交一次。
- worker 开始、完成、失败均进入 TaskQueue/OperationRunner audit；候选结果经 schema、request/response hash 和 finish reason 验证后才由 coordinator 原子发布。
- 模型超时、暂时服务错误可做有界重试；schema invalid、citation invalid 或内容缺失不能当成功，必须保留失败输出和诊断。
- 崩溃恢复重用同一成功 input/output hash；不会重新分配 primary messages，也不会产生重复 completed 计数。
- reducer 只有在全部依赖 batch 成功且通过验证后才能运行；某一 batch 失败只阻塞其依赖摘要，不允许 reducer 猜测缺失内容。
- 任何 hash、账本或 source binding 不一致都在模型调用和发布前 fail-closed。

## 13. 测试与独立复核

实现必须先测试后代码，至少覆盖：

1. 831,915 基线 + 11,119 closure − 609 overlap = 842,425 的严格并集。
2. raw boundary 中未列入 11,119 明确 IDs 的行被排除。
3. 609 个重叠快照双份保留，closure snapshot 为主；真实 edited/content/embed 差异不丢失。
4. target-local、时间稳定、最多 500 primary messages 的确定性分批。
5. secondary reply context 不增加 primary coverage。
6. duplicate/missing/extra assignment 硬失败。
7. webhook delivery identity 与 claimed source identity 不被错误合并。
8. 每条消息 disposition 完整，`other/unresolved` 合法但不能缺行。
9. primary disposition exactly-one、semantic tags 0..N，数据库唯一约束生效。
10. acquisition disposition 与 semantic status 正交、各自互斥完备；2,910 个旧 failed 不能批量假定 terminal。
11. SHA 去重保留全部 occurrence provenance。
12. batch crash/retry、并行 commit race、invalid JSON/schema、provider truncation、缺段、orphan citation、错误 snapshot/hash/target citation 均不能越过发布门。
13. evidence-support reviewer 能识别主体、否定、时态、数值和标的不一致。
14. serialized-request budget 的等于/超过边界、UTF-8 分段不切断 code point、segments 全量重组 hash、单条不可分超限 blocked。
15. 恶意消息中的读文件/执行命令/访问 URL 指令无法获得工具；任何 tool-use metadata 非空都使 attempt invalid。
16. 相同 input 但 task kind/provider/model/sampling/adapter/prompt/schema 任一变化时生成不同 attempt/enqueue key。
17. token、Authorization、签名 URL query 和内部 logical key 不进入派生产物。
18. no-clobber 发布、绑定路径/SHA 替换和 source hash tamper detection。
19. 全仓 `make test` 为 0 failure、0 ResourceWarning。

每个实现阶段完成后做独立 code review；Critical/Important 必须全部修复。最终发布前再做一次不读取模型解释、只读取原始账本和 hashes 的独立 coverage reconstruction。

## 14. 交付阶段

### Stage A：语料与覆盖账本

- 先按仓库硬约束核验运行时 skill 数与文档一致；不一致先执行既有 `skill-sync --apply` 流程。
- 实现 corpus reconstruction、snapshot provenance、deterministic batching、coverage ledger。
- 生成零模型的 `coverage.json/md`，先证明 842,425 个 ID 的分配完整性。

### Stage B：全量文本 Map

- 全部 primary batches 经 TaskQueue 处理。
- 持续显示 completed/failed/in-progress 消息数和批次数，不以估算替代账本。

### Stage C：Reduce、复核与首版总结

- 生成频道/thread、身份、时间区间、共识/冲突摘要。
- 完成 citation/identity/conflict 独立复核；达到 `text_primary_complete` 后发布首版，并并列披露 `reply_context_complete`。

### Stage D：媒体补强

- 正式纳入 10,510 新消息的 message-evidence/media pipeline。
- 对 captured binary 做 OCR/图像/音视频语义处理；按 evidence impact 重开 summary claims。
- 持续发布媒体覆盖和无法提取清单，不阻塞已完成文本的可见性。

### Stage E：交易事件与回测（独立后续设计）

- 将证据转成开单、改单、撤单、止损、分批止盈、主动平仓事件。
- 补齐所需标的 1m K 线至闭合上界，建立无未来函数、含手续费/滑点/funding 的回测。
- 旧 10 频道回测只保留为失败样本，不与新账本混合。

## 15. 验收标准

本设计完成的定义是：

1. 能从绑定 hashes 独立重建 842,425 个 canonical message IDs。
2. 能证明每个 ID 恰有一个 primary assignment、恰有一个最终 disposition，并追溯到原始 snapshot。
3. 能在媒体尚未全部处理时先发布 `text_primary_complete` 的可核验总结，同时明确显示回复上下文、媒体和权限缺口。
4. 能在媒体后续到达时只更新受影响结论，保留旧版本和全部 citations。
5. 任何 403、reference-only、terminal failed、pending、低置信度身份或 unresolved reply 都不会被隐去或误报为完整。
6. 所有写入均经过 OperationRunner，后台模型任务均经过 TaskQueue，最终全仓测试与独立审查通过。

用户批准本文后，下一步只编写逐任务实施计划；实施计划再次核对文件、测试、提交边界，再进入 TDD 实现。
