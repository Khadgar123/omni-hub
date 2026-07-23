# Discord 全博主喊单提取与可信回测修复设计

日期：2026-07-23

状态：方案 A（重建完整证据链）已于 2026-07-23 获用户书面批准；进入实施。

替代关系：本文取代 `2026-07-21-discord-blogger-results-design.md` 中“四位博主、BTC/ETH、固定样本”的交付口径。旧设计、旧产物和旧审计证据保留，但只能标记为 pilot/invalid baseline，不能继续作为“所有博主回测结果”或排行榜。

## 1. 问题定义

当前系统已经取得大规模 Discord 可读历史，但“消息采集”与“喊单识别、交易生命周期还原、可回测样本形成”是不同层。现有结果交易数少，不是因为 Discord 里只有这些单，而是下游派生管线主动缩窄了范围：

- canonical authorized corpus 有 842,425 个唯一消息，覆盖 132 个用户明确指定的 target；时间范围约为 2025-10-18 至 2026-07-21。
- 旧事件化只读取 4 个 profile，共 9,414 条消息，约占 corpus 的 1.12%。
- 旧解析器只支持 BTC/ETH，图片-only 消息直接排除，多订单并存时无法可靠链接改单/平仓。
- 旧正式 curation 固定输入 324 行、固定选中 216 行；它不是从全部生命周期自动派生。
- 旧回测最终只有 108 个 closed、103 个 24 小时内未成交、5 个 right-censored，不能代表 132 个 target。
- 旧 1m K 线只覆盖 BTC/ETH，且末端比消息上界早约 8 天。
- 另一个只读启发式扫描已经在 117 个 target 找到候选，在 60 个 target 找到完整结构候选；这只能证明旧漏斗过窄，不能直接当成真实交易。

因此，本修复的核心不是“调低一个正则阈值”，而是建立从每条 canonical message 到每个最终回测状态的守恒账本，消除静默漏单和固定样本偏差。

## 2. 已绑定的事实边界

### 2.1 Canonical message corpus

本设计只消费已经验证并冻结的 authorized readable corpus，不重新扩大 Discord 权限边界：

- 唯一 canonical message：842,425。
- 明确 target：132/132 均有已验证消息。
- exact-target inventory：`exact-target-inventory-20260722-v6.json`。
- inventory SHA-256：`21065f955a6600cde196357a292f33e3d854ab66437a8d061c4641ceb069e691`。
- corpus commitment：`9baac0174f4a411171a4f8d37ab5b4d3e2ed59fd20f57b90cbd9d95092e8365c`。
- 历史/闭合消息并集仍遵守 `831,915 + 11,119 - 609 = 842,425`；不得把 23,978 行 raw boundary response 全量旁路扫入。
- 609 个双快照 message ID 保留两份 provenance，当前态使用 closure snapshot，历史快照不删除。
- 共同闭合上界 `H = 2026-07-21T00:57:18.979Z`；“当前喊单”只能表示该证据上界时的当前态，后续实时增量需另行闭合后再更新。
- 123 个未加入的 private archived thread 枚举返回 403；它们不属于已验证可读范围，不能声称读取完成。

132 个 target 的 target-row family rollup 可重叠，不能相加后冒充唯一消息数。唯一分母始终是 842,425 个 canonical message ID。

842,425 是历史回测的冻结、可复现基线。最终发布“现在的喊单”前，系统必须对 132 个显式目标及其 family census 执行一次只读 head catch-up，以共同毫秒上界 `H2` 验证并合并 `(H, H2]` 的新消息、新建 authorized child threads 和公开/已加入归档 thread 增量；仍开放 lifecycle 的源消息还要定点重验 edit/delete 状态。增量使用独立 commitment 和 ledger，不改写历史 corpus。若未完成 family census 与定点重验，结果只能标为 `new_message_catchup_as_of=H2`，不能声称 current state 已闭合；完全未做增量时只能标为 `as_of=H`。

### 2.2 当前媒体事实

exact-target inventory 对媒体维度明确标记为 `not_asserted`，因此“132/132 有消息”不能推导为“图片都已解析”。已审计的全局媒体基线为：

- media records：146,017；
- verified binary：142,704；
- failed：2,910；
- reference-only：403；
- closure 新增待 acquisition occurrence：1,849。

这些是全局 asset/occurrence 状态，不是候选喊单图片的语义完成数。binary 已保存也只代表 bytes/SHA 可验证，不代表 OCR、图表或视频含义已进入事件解析；2,910 failed 和 403 reference-only 也不能计为已读图片。

新管线必须先生成 hash-bound `media-occurrence-input-manifest.json`，绑定 146,017 条基线记录的原始 asset index 路径/SHA、1,849 条 closure pending occurrence 的路径/SHA，以及 H2 增量；逐 occurrence 对齐 message/target/author provenance 后，才能计算 eligible media 分母。manifest 若出现 missing、extra、duplicate 或 source hash 漂移即 fail closed。

### 2.3 旧结果的处置

以下旧产物保持不可变，用于回归和定位漏斗，不得覆盖：

- 四 profile 的 `event-manifest.json` 与交易事件文件；
- 固定 324/216 curation；
- 四人 BTC/ETH 回测；
- `full_backtest.py` 等存在未来函数、选择偏差或异常 PnL 的历史结果；
- `all-sources-reviewed-*` 启发式候选报告。

新管线发布到独立 namespace，并在 manifest 中显式记录 `supersedes_for_interpretation`，但不删除旧证据。

## 3. 目标、非目标与完成定义

### 3.1 目标

1. 132 个 target 都有独立漏斗，不再通过硬编码 profile 列表限制人数。
2. 842,425 条 canonical message 每条恰好有一个 primary decision；任何未进入交易语义的消息都有明确原因。
3. 文本、embed、附件、图片、视频、回复快照和 thread 上下文共同进入证据模型；media-only 不再静默排除。
4. 区分 Discord delivery identity、真实作者和 target/团队归属；不把 webhook 名称或聊天参与者自动冒充博主。
5. 支持加密货币、黄金/外汇、股票、期货等多市场标的；符号有歧义时不猜。
6. 将 OPEN、成交、改单、止盈止损、撤单、部分平仓和最终平仓还原成可审计生命周期。
7. 对有完整消息、身份、媒体和 K 线覆盖的 lifecycle 做 point-in-time 回测；其余输出准确 blocker。
8. 生成每位博主的当前观点、最新喊单、交易漏斗、样本结果与覆盖等级；小样本可以展示，但不能伪装成可靠排名。

### 3.2 非目标

- 不绕过 Discord 权限，不修改频道权限，不枚举 bot 无权读取的 private archived threads。
- 不实现 Gateway，不发送 Discord 消息，不自动下单。
- 不把 132 个 target 都假设为单一博主；新闻、社区聊天、多作者团队需按身份模型分别处理。
- 不为得到更多交易数而猜测图片内容、作者、标的、方向、入场价或退出原因。
- 不把 reference-only、404、缺 K 线或未处理媒体算作完整证据。
- 不要求所有聊天都调用大模型；确定性守恒、身份和候选账本优先，模型只处理需要语义理解的记录。
- Discord REST 历史中从未持久化的语音/屏幕直播画面不能事后重建；只有消息中保存的附件、embed、录屏、转录或可验证引用能进入证据链。

### 3.3 完成定义

“全部完成”必须同时满足：

- corpus 守恒；
- target/作者归属守恒；
- 候选与非候选 decision 守恒；
- 相关媒体 acquisition/semantic 状态守恒；
- event/lifecycle 守恒；
- market data 覆盖守恒；
- backtest disposition 守恒；
- 132 个 target 报告行齐全。

任何一层 unresolved、invalid、403、terminal media failure 或 data gap 必须单列，不能用总消息量掩盖。

## 4. 方案比较与选型

### 4.1 方案 A：重建完整证据链（采用）

从 132-target corpus 开始建立 message decision ledger、候选媒体层、身份层、生命周期和多市场回测。工作量较大，但能回答“为什么某人没有交易”和“哪一步漏了”。

### 4.2 方案 B：扩充正则与 profile

在旧四人解析器上继续增加博主、关键词和币种。短期可能提高数量，但固定 curation、media-only 丢失、多订单链接错误和身份混淆仍存在，无法证明完整性。

### 4.3 方案 C：只用模型从聊天生成喊单

启动快，但无法稳定守恒、重跑、解释链接和控制幻觉，也不能单独承担回测事实层。

采用 A；允许在 A 的 candidate/event semantic 层使用 schema-bound 模型，但模型输出必须回到确定性账本并通过证据与约束验证。

删除旧记录或从零重抓不属于任何修复方案：Discord REST 限速、权限 403、已失效媒体和下游解析缺陷不会因此消失，反而会破坏已有 provenance 和编辑快照。

## 5. 分系统架构

本修复拆成五个可独立测试、按契约连接的子系统：

1. **Target Identity + Message Decision Ledger**：确定 132 个 target、作者归属以及每条消息的唯一 disposition。
2. **Author-eligible Media Semantics**：捕获并理解所有 author-eligible 消息中的图片、视频和 embed，再决定是否属于喊单/观点。
3. **Trade Event + Lifecycle Reconciliation**：从候选证据形成事件，并确定性链接到并发交易生命周期。
4. **Instrument + Point-in-time Market Data**：规范化标的、市场、时区和可用 K 线/费用数据。
5. **Backtest + Per-target Reporting**：保守模拟并发布每 target 漏斗、结果和 blocker。

```text
842,425 canonical messages + 132-target inventory
  -> identity resolution + one decision/message
  -> all author-eligible media occurrences -> semantic evidence
  -> candidate evidence cards
  -> normalized trade events
  -> lifecycle state machine + unresolved event pool
  -> normalized instruments + point-in-time market data
  -> conservative simulation
  -> 132 target funnels + current calls + comparable metrics
```

上游 contract 未通过时，下游不能把其记录当作 resolved；但局部 blocker 不阻塞其他已完整 lifecycle 产生结果。

## 6. Target 与作者身份模型

### 6.1 Target 类型

每个 explicit target 必须标记一种类型：

- `single_author_analyst`
- `multi_author_team`
- `signal_delivery_channel`
- `community_chat`
- `news_or_aggregation`
- `unknown`

target 本身有消息不等于其中每条消息都属于博主。community/news target 可以有内容统计，但没有 author-verified trade 时不生成个人胜率。

消息的唯一 primary owner 是它真实所在的 exact channel/thread；Forum/parent family 只做 rollup projection。一个 message decision、event 或 lifecycle 在全局只记一次，可以投影到 exact target 和 parent 报告，但不能因父子 target 同时存在而重复回测或重复计入作者绩效。

### 6.2 作者解析

保存并区分：

- Discord author ID；
- webhook/bot delivery identity；
- display name snapshot；
- externally claimed analyst/博主 identity；
- target membership/allowlist；
- attribution status 与证据来源。

允许的 attribution status：

- `verified_author`
- `verified_team_member`
- `delivery_proxy_verified`
- `community_participant`
- `unknown_author`
- `conflicting_identity`

`verified_author` 可以进入该 principal 的个人回测；`verified_team_member` 默认只进入 team 聚合，除非 registry 另有经过复核的个人 principal 映射；`delivery_proxy_verified` 只有明确映射到 principal/team 时才进入对应聚合，否则仅作 channel-level evidence。聊天区其他人的喊单保留为 community evidence，不混入频道主人的胜率。身份不确定时输出 blocker，不能依据昵称相似度强行归属。

本设计中的 `author-eligible` 至少包含：所有 verified owner/team/proxy，以及 analyst/team/signal-delivery target 中尚未被证据证明为 community participant、system 或 admin 的 `unknown_author`、`conflicting_identity` 和待确认 delivery proxy。身份未知可以阻塞个人归属与绩效，但不能阻塞内容/媒体识别；只有版本化 registry 已明确排除的 system/admin/community occurrence 才可不进入个人媒体语义分母。

身份映射保存在版本化 `identity-registry.json`，每条记录绑定精确 Discord author/webhook ID、适用 target、`valid_from`/`valid_to`、delivery proxy 规则、证据引用、人工复核状态、canonical `performance_owner_id`、`aggregation_scope = individual|team|channel_only` 和 registry SHA。每个进入绩效的 event/lifecycle 恰好有一个 performance owner；无法唯一确定时为 identity blocked。昵称、头像或 display name 相似只能生成待复核候选，不能把状态提升为 verified。registry 变更不回写旧 run；必须新建派生版本并重算受影响 lifecycle。

## 7. Message Decision Ledger

### 7.1 一条消息一个 primary decision

coverage ledger 先为每个 canonical message ID 建立唯一 processing row，`processing_status` 与语义 decision 正交：`pending`、`running`、`retryable`、`committed`、`blocked`、`invalid_attempt`。失败/截断 attempt 只改变 processing status，不伪造终态 `invalid_evidence`。

每个 canonical message ID 在可发布的 current materialized view 中恰好有一条已 committed primary decision：

- `non_signal`
- `analysis_or_view`
- `trade_candidate`
- `trade_event`
- `media_dependent`
- `unsupported_or_ambiguous_instrument`
- `identity_unresolved`
- `context_unresolved`
- `invalid_evidence`

消息还可以具有零到多个 semantic tags，例如：`open_intent`、`entry_update`、`stop_update`、`target_update`、`cancel`、`close`、`correction`、`media_only`、`forwarded`、`quoted_context`。

所有 decision revision 必须 append-only，保存稳定 `reason_code`、parser/model schema version、source snapshot、相关 evidence 和 `supersedes_revision_id`；current view 只选择每条消息唯一未被 supersede 的 revision。`media_dependent -> trade_event/non_signal` 等合法演进不得删除旧 revision。每条消息可以产生 0..N 个 event，“一个 primary decision”不等于“最多一个事件”。不能存在无记录的过滤分支。

### 7.2 候选识别

候选层采用两阶段：

1. 确定性层读取文本、embed 字段、components、附件类型、回复关系和 target/author metadata，用于结构化提取、优先级和 exact duplicate 复用；不以“必须同时有入场+止损+止盈”作为候选门，也不得仅凭关键词缺失给出终态 `non_signal`。
2. 每条 author-eligible、带实际内容的 primary message 都必须获得 schema-bound semantic decision；exact duplicate 只可复用内容级 OCR/字段候选，结合作者、target、时间和上下文后的 decision 必须逐 occurrence 形成。只有 Discord system/admin 类型等可由确定性结构完全证明的记录才能跳过语义模型直接进入 `non_signal`。
3. media-only 的 author-eligible message 直接进入 `media_dependent`，待媒体语义处理后再决定是否产生 event。

模型无工具、无 URL fetch，只能处理注入的证据包。这样确定性规则用于提速和验证，而不是再次把不符合旧词典的喊单过滤掉。

模型结果不是事实真源。缺失 message ID、伪造字段、schema 错误、truncation、工具调用或证据引用不守恒时，该 attempt 标为 `invalid_attempt`/`retryable`，不产生 primary decision。partial release 必须单列 pending/blocked，完整 release 则要求全部 canonical rows 均已 committed 且各有唯一 current decision。

### 7.3 漏斗守恒

每个 target 至少披露：

- total canonical messages
- author-verified messages
- community/other-author messages
- non-signal
- analysis/view
- trade candidate
- media dependent/pending
- event produced
- identity/context/instrument unresolved
- invalid

这些分类与允许的 tag 重叠规则必须机器验证，不能只在 Markdown 中解释。

### 7.4 识别质量硬门

守恒只能证明“每条消息有结果”，不能证明结果正确。正式 classifier/profile 冻结前必须建立 owner-only、内容不入 git 的分层 gold + holdout：

- 覆盖 132 个 target 的 target 类型、主要语言、早/中/晚时期、文本/图片/视频、OPEN/改单/撤单/平仓和 hard negative；
- gold/holdout 只保存受控 evidence 引用、标注与 commitment，holdout 在规则冻结前不向实现者暴露；
- 报告 micro/macro precision、recall、false-negative、按 target 类型/媒体类型/事件类型的分层结果，禁止只报全局平均；
- trade-candidate macro recall 必须不低于 95%，OPEN/AMEND/CLOSE 高风险类 recall 不低于 97%，precision 不低于 90%；任一有足够正例的 material stratum recall 低于 90% 时不得标 complete；
- 正例不足以估计阈值的 target 明确标 `insufficient_gold_coverage`，不能借用其他 target 的平均分；
- 对最终 `non_signal` 做独立分层抽样 false-negative audit，并将新发现漏单加入下一版 regression pack；阈值未通过时只允许发布 partial/blocked 漏斗，不得发布排行榜。

gold 只用于评估，不得以 holdout 结果反复调规则后继续声称同一 holdout 是外样本；修改 parser/profile 后需新版本与时间外复核。

## 8. 图片、视频与嵌入内容

### 8.1 Occurrence 级账本

同一个二进制 SHA 只能复用内容级 OCR/视觉字段抽取结果，不能复用作者归属、事件身份或 lifecycle 判断。每个 message occurrence 必须独立保存：

- message/target/author provenance；
- asset/index reference；
- acquisition status；
- MIME、尺寸、SHA；
- semantic status；
- 与候选事件的关系。

不把同图转发到不同消息的语境合并为一条证据。

跨频道转发、重复 webhook delivery 或同一喊单的复述必须保留全部 message occurrences。只有明确 message reference、delivery provenance、order ID 或人工复核证据证明是同一订单时才可合并 event/lifecycle；相同文字或图片本身不能证明同一订单。证据不足时保留独立候选或 `duplicate_identity_unresolved`，去重依据必须写入 ledger，避免为去重而再次少算交易。

### 8.2 Acquisition 状态

- `binary_verified`
- `reference_only`
- `terminal_http_failure`
- `transient_failure`
- `resolution_unresolved`
- `hard_mismatch`
- `not_downloadable`
- `pending`

既有 SSRF、MIME、size、candidate、YouTube 和 HTTP 400/404/415 语义保持不变；不能为了提高覆盖率放宽安全规则或把 reference 算作 bytes。

### 8.3 Semantic 处理

- 图片：OCR + 图表语义，保留原始文字区域/坐标、标的、方向、入场区间、SL/TP、时间框架与不确定度。
- 视频：容器验证、确定性抽帧、可用音轨转录；只将有时间定位的帧/转录片段作为证据。
- embed：解析 Discord 已保存字段；外部网页未验证时不推断页面内容。
- author-eligible message 只要带有未完成语义处理的图片、视频或富媒体，就保持 `media_dependent`，即使同时有普通文字。只有 semantic success 后，媒体内容才能支持终态 `non_signal`/event。

terminal/reference-only/404/resolution blocker 默认使该 occurrence/message 保持 `media_blocked`，不能据此断言非喊单。仅当独立证据复核证明媒体对文本中已经完整确定的 event 不具实质影响时，才可用 `non_material_to_event` reason 生成 text-only event；媒体维度仍为 partial/blocked。所有 author-eligible message 的媒体均进入语义处理分母，不能先通过文本候选门筛掉图片中的真实喊单。其他 corpus media occurrence 至少有 acquisition/selection disposition；只有结构上确认与个人分析无关的作者/系统记录才可 `not_selected`，并保存 reason code。

## 9. Instrument Identity 与市场覆盖

统一标的结构 `InstrumentIdentity` 至少包含：

- `asset_class`
- `canonical_symbol`
- `base` / `quote`
- `venue`
- `instrument_type`
- `contract_or_expiry`
- `tick_size`
- `timezone` / `trading_session`
- `resolution_status`

支持范围按 adapter 显式扩展：

- crypto spot/perpetual/futures；
- gold、FX；
- equities；
- index/commodity futures。

BTC、XAU、GOLD、ES、NQ、股票代码等必须结合 target profile、消息语境和 venue 解析；多解时保持 `ambiguous_instrument`。不能把所有交易强行映射为 BTCUSDT/ETHUSDT。

每类市场 adapter 自己声明并版本化：交易日历、session、bar 语义、最大挂单/持仓 horizon、允许的 bar gap、手续费、滑点、funding、合约乘数、公司行动和换月规则。规则与阈值在结果发生前冻结并绑定 hash。对净结果有实质影响的数据缺失时阻塞 net performance，只允许明确标记的 gross/scenario 结果；不能只在 manifest 写“未建模”后仍发布可比较净收益。

## 10. Trade Event Schema

事件类型至少包括：

- `OPEN_INTENT`
- `FILL_CONFIRM`
- `AMEND_ENTRY`
- `AMEND_STOP`
- `AMEND_TARGET`
- `ADD_POSITION`
- `REDUCE_POSITION`
- `CANCEL`
- `PARTIAL_CLOSE`
- `CLOSE_TP`
- `CLOSE_SL`
- `CLOSE_MANUAL`
- `EXPIRE`
- `CORRECTION`
- `RETRACT`
- `SOURCE_EDITED`
- `SOURCE_DELETED`

每个 event 保存 target/author、`published_at`、`edited_at`、`captured_at`、`effective_at`、instrument、direction、order type、entry/SL/TP、size（如有）、引用关系、媒体证据、parser/model provenance、confidence 和 unresolved fields。每个提取字段还必须保存独立 `known_at` 与 source snapshot/message；`effective_at` 是该 event 所需全部字段 `known_at` 的最大值。事件抽取证据包在形成该时点参数时不得包含 `known_at` 更晚的回复或媒体。

`FILL_CONFIRM`、`CLOSE_TP/SL` 等博主自报事件使用 `reported_by_author` evidence layer；K 线触发的成交和退出使用独立 `simulated_from_market_data` layer。两层可以对照，但不得互相替代或用作者事后自报改写模拟结果。

编辑、纠错、撤回和源消息删除通过 append-only event revision/supersession 表达，并触发受影响 lifecycle 从最早变更 `known_at` 确定性重算。`SOURCE_DELETED` 只表示定点重验发现源消息已删除，不能自动解释为撤单，也不能继续假装当前源仍有效；缺少独立 `RETRACT/CANCEL` 证据时，相关当前态进入 `source_deleted_unresolved`，历史已保存 snapshot 与删除事实同时保留。

解析器按 target/author 的版本化 profile 提供词汇和格式提示，但 profile 不得决定是否读取该 target，也不得硬编码最终样本数。

## 11. Lifecycle State Machine

### 11.1 状态

- `intent_open`
- `working_unfilled`
- `partially_filled`
- `filled_open`
- `partially_closed`
- `closed`
- `cancelled`
- `expired`
- `right_censored`
- `unresolved`

### 11.2 链接优先级

事件链接顺序：

1. 明确 order/trade ID；
2. 与事件类型和 instrument/direction 兼容的明确 reply/reference message；
3. 在版本化时间窗内，唯一、仍 active 且兼容的 author + instrument + direction + 参数指纹 lifecycle；
4. 在同一版本化时间窗内唯一、仍 active 且兼容的 lifecycle；
5. 否则进入 unresolved event pool。

order ID 与 reply 指向不同 lifecycle、reply 内容是新的 `OPEN_INTENT`、或 reference 只是讨论/引用而非订单更新时，不得按 reply 强制链接；冲突进入 `link_conflict`。禁止把一个模糊“止盈/平仓”追加到所有同方向 open lifecycle。多个并发订单必须分别保留。一个 resolved event 最多属于一个 lifecycle；一个 lifecycle 可以含多个 event。后续 reply 只能建立链接或生成新的后续 event，不能把方向、entry、SL/TP 或身份回填成更早时点已知字段。链接、拆分、合并和撤回都要保存 reason code。

### 11.3 Curation

正式 backtest curation 从 lifecycle ledger 自动派生，不再接受固定 324 输入/216 选中或固定 profile count。每个 lifecycle 恰好得到一个 `eligibility_status`：

- `evaluable`
- `identity_blocked`
- `media_blocked`
- `instrument_blocked`
- `market_data_blocked`
- `lifecycle_unresolved`
- `invalid`

并且恰好得到一个独立 `execution_disposition`：`not_run_blocked`、`working`、`unfilled`、`cancelled`、`expired`、`right_censored`、`simulated_closed` 或 `reported_only`。eligibility 与执行结果不得混成一个字段，避免用“后来没成交/没平仓”反向改变入选资格。

Eligibility predicate 在结果发生前冻结，只允许使用当时已知的 verified author、`OPEN_INTENT`、published/known time、resolved instrument/direction、可执行的 market/limit entry 语义和版本化 adapter 配置；不得要求后来出现平仓、TP、盈利截图或作者自报成交。每个 `OPEN_INTENT` 无论后来盈亏，都必须进入上述一个 disposition 或产生一个模拟结果，不能从 ledger 消失。

缺 SL/size 不自动删除 lifecycle：可评估价格路径时仍展示 entry/exit 描述结果，但 R、累计 R、仓位回撤和净资金绩效为 N/A 或单独标记的 scenario，不能用等权仓位假设冒充主结果。部分止盈 allocation 未知且会改变胜负/R 时，主结果只能给 outcome range/N/A，不得生成单一 `closed_win/loss` 或进入排行榜。撤单、未成交和失败交易不能事后从分母静默删除；报告分别展示其发生率和 closed-trade 绩效。

## 12. Point-in-time Market Data

每个 instrument 的数据覆盖必须：

- 起点早于最早可评估 signal；
- 终点晚于最后 signal 加最大持仓/挂单 horizon；
- 保存 source、下载时间、bar interval、timezone、hash、gap audit；
- 只使用当时已经形成的信息；
- 对 equities/futures 应用交易日历，对 perpetual 显式建模 funding（若数据可得）。

现有 BTC/ETH 1m 数据可以作为 adapter 输入，但不能定义全局范围，也不能覆盖 2026-07-13 之后的消息。某 instrument 缺数据只阻塞相关 lifecycle，不阻塞其他 target 的结果。

每个 adapter 的 horizon 与 gap 容忍度必须进入 market-data manifest 和测试夹具；超过阈值的生命周期为 `market_data_blocked`，不得用插值或缩短持仓窗口制造闭合交易。

## 13. 回测语义

默认采用保守、可重复的 point-in-time 规则：

- 市价意图从 event `effective_at` 后下一根完整可交易 bar open 生效；
- 限价单从下一根 bar 起，仅在 high/low 触及时成交；
- 未成交前撤单保持 unfilled/cancelled，不做 forced-market fallback；
- 同 bar 同时触发 TP/SL 时 stop-first；
- 同 bar 入场与退出采用不利路径；
- 主结果只按消息明确给出的部分止盈比例/仓位执行；比例未知时，等权或其他分配只能作为单独 scenario；
- 手续费、滑点、funding、合约乘数均是版本化参数；
- 消息编辑只能从系统已观察到 edited snapshot 的时间之后生效；
- 解析器/策略调整需用时间切分 holdout 或 walk-forward 检查，禁止在全历史结果上反复调规则后当作外样本。

在只有 1m OHLC、没有 tick/quote 顺序证据时，OPEN、AMEND、CANCEL、PARTIAL_CLOSE 和 CLOSE 指令一律从 `effective_at` 之后下一根完整 bar 开始影响模拟；不得使用消息所在 bar 在消息之前已经发生的 high/low。若 adapter 有经过验证的 tick 数据，可以使用精确事件时间，但必须走独立版本化语义与测试。

时间字段必须分开保存 `published_at`、`edited_at`、`captured_at`。未编辑消息以 Discord publication time 生效；有编辑标记但缺少对应时点历史 snapshot 时，不得把当前正文中的入场/止损/止盈字段回填到更早时点，该 lifecycle 保持 edit-history blocked。只有已保存的双快照才能按各自可观察时间重放变更。reply/thread context 也按逐字段 `known_at` 截断；必须有测试证明后来“止盈了/已平仓”等内容不会改变更早 OPEN 的方向、参数、身份或 eligibility。

结果状态至少包括：`closed_win`、`closed_loss`、`breakeven`、`unfilled`、`cancelled`、`expired`、`right_censored`、`data_blocked`、`unresolved`。

## 14. 每位博主的测评方式

每个 target/verified author 先报告证据覆盖，再报告绩效：

1. **活跃与可操作性**：喊单频率、完整字段率、改单及时性、撤单/未成交率。
2. **风险纪律**：入场时有无 SL、平均初始风险、是否扩大止损、风险收益结构。
3. **执行结果**：closed win/loss、胜率、平均 R、累计 R、最大回撤、持仓时长、费用敏感性。
4. **稳定性**：按月、标的、方向、市场状态分层；展示 Wilson 区间或 bootstrap 区间。
5. **证据质量**：身份、文本、媒体、生命周期、K 线的独立覆盖等级。

这里的“独立 lifecycle”指经过 exact message occurrence 保留、明确 forward/delivery provenance 去重、同一订单多次更新聚类后的独立交易意图；同一喊单的转发和多个 TP 不增加独立样本数。少于 30 个独立 closed lifecycle 时仍展示逐笔和描述统计，但标记 `small_sample_not_ranked`，不参与跨博主排行榜。达到阈值也不等于统计独立或可信，仍需覆盖、集中度和时间外验证通过。

不能把社区聊天的其他作者、复盘后补发的图、没有时间证据的历史截图或无法定位的直播观点计入博主实时绩效。

## 15. 输出与用户可见结果

新 namespace 至少发布：

- `corpus-decision-manifest.json`
- `message-decisions.jsonl`
- `identity-registry.json` / `target-identity.jsonl`
- `classifier-evaluation.json`
- `media-occurrence-input-manifest.json`
- `media-occurrences.jsonl`
- `media-semantics.jsonl`
- `trade-events.jsonl`
- `lifecycle-ledger.jsonl`
- `curation-manifest.json`
- `market-coverage.json`
- `trades.jsonl`
- `current-calls.json` / `current-calls.md`
- `per-target-funnel.json` / `per-target-funnel.md`
- `backtest-report.json` / `backtest-report.md`
- `blockers.json` / `blockers.md`

132 个 explicit target 在 `per-target-funnel` 中必须各有一行，即使结果为 N/A。每行至少包含：

- target/type/verified author count；
- corpus messages 与时间范围；
- candidate/media/event/lifecycle/evaluable/closed 数；
- 当前 open calls 与最近 update；
- text/identity/media acquisition/media semantic/lifecycle/market coverage；
- blocker reason；
- 绩效或 `N/A`/`small_sample_not_ranked`。

`current-calls` 必须保存实际共同闭合上界 `as_of` 和每条 lifecycle 的最后证据时间。没有增量时 `as_of = H`；只有 delta/union commitment、family census、新 child thread、open-source edit/delete 重验和 delta 派生守恒全部通过后才可使用 `as_of = H2`。只完成新消息抓取时必须标 `new_message_catchup_as_of=H2`，不得把它写成 current state closed。

## 16. 覆盖等级

不再用一个 E0-E3 混合所有问题。以下维度独立计分：

- `text_coverage`
- `identity_coverage`
- `reply_context_coverage`
- `media_acquisition_coverage`
- `media_semantic_coverage`
- `lifecycle_coverage`
- `market_data_coverage`
- `private_scope`

前七个内容/派生维度状态为 `complete`、`partial`、`blocked`、`not_applicable`。`private_scope` 独立使用 `full_private_scope_complete` 或 `known_scope_only`，不得被其他维度抵消。顶层同时发布两个判断：

- `authorized_known_scope_complete`：842,425 条冻结语料及其适用派生维度是否完整；
- `full_private_scope_complete`：未加入的 private archived threads 是否已被权限允许并枚举，且所有新发现 thread 已完成 acquisition、message decision、eligible media、event/lifecycle 与 blocker 守恒。

只有二者都为 true 才能声称目标家族全部可见历史完整；当前 123 个 403 存在时，后者必须为 false。

## 17. 验收硬门

### 17.1 Corpus 与身份

- inventory path/SHA、corpus commitment、closure bindings 全部重验。
- canonical message ID 恰好 842,425；missing/extra/duplicate 均为 0。
- 报告 explicit target ID 集合与 inventory 的 132 个 ID exact-set 相等，duplicate/missing/unexpected 均为 0；不能只校验行数。
- H2 发布时额外绑定 `(H,H2]` delta commitment、baseline+delta union commitment、family census 与 discovered child thread 集合；每条 delta message 同样必须有 decision，并进入 event/lifecycle/disposition 守恒。
- 仍开放 lifecycle 的源消息 edit/delete 定点重验全部有结果；否则 current state 只能 partial。
- `full_private_scope_complete=true` 时，所有枚举出的 private archived thread 消息必须进入同一 end-to-end acquisition/decision/media/event/lifecycle 守恒；只有“权限允许并枚举”但尚未派生时仍为 false。
- 每条消息一个 primary decision；每个 target 的漏斗守恒。
- 每条进入绩效的交易事件必须通过 hash-bound identity registry 在对应有效时间解析到唯一 `performance_owner_id` 与 aggregation scope；team/proxy/channel 证据不得自动进入个人指标，未知/冲突归属保持 blocked。
- 分层 gold/holdout 的 candidate 与 OPEN/AMEND/CLOSE precision/recall 门全部通过；未通过的 target/stratum 只能 partial/blocked。

### 17.2 媒体与事件

- `media-occurrence-input-manifest` 与 146,017 基线、1,849 closure pending 及 H2 delta 的路径/SHA/occurrence 集合完全对齐。
- 每个 author-eligible media occurrence 有 acquisition 与 semantic disposition；带未处理媒体的消息不得终态 `non_signal`。
- binary、reference-only、terminal、transient、pending 分开计数。
- media-only candidate 不得被静默归为 non-signal。
- event 输入/输出 message ID、evidence reference 和 lifecycle link 守恒。
- unresolved close 不得同时污染多个 open lifecycle。
- 内容级 duplicate 复用不得自动合并事件；事件去重必须有 delivery/reference/order provenance。

### 17.3 Curation、市场与回测

- 代码中不存在固定 4-profile、324、216 或 BTC/ETH-only 的正式门。
- 所有 lifecycle 恰好一个 eligibility status 和一个 execution disposition。
- 每个 `OPEN_INTENT` 都进入 lifecycle/curation 守恒，eligibility 不读取结果发生后的字段。
- 所有 evaluable lifecycle 恰好一个 backtest result。
- author-reported fill/outcome 与 market-simulated fill/outcome 分层，不能互相替代。
- K 线 range/gap/session/hash 验证通过；缺口只产生明确 blocker。
- 无未来函数、forced fill、事后删除未成交或选择赢家。
- 缺 SL/size/实质费用或公司行动数据时，受影响的 R/net 绩效为 N/A/blocked，不进入可比较主指标。
- 132-target 报告行完整；不够样本输出 N/A/小样本，不伪造排名。

### 17.4 工程与安全

- 新写操作注册到 `builtins.py` 并通过 `OperationRunner`。
- 模型/后台工作通过 `TaskQueue`；CLI/Application Plane 不直接调模型。把私有 Discord 内容发送到外部模型属于 `EXTERNAL_SEND`，必须通过 `OperationRunner` 审批并绑定 provider、retention、redaction 和 request hash；无批准时只能使用合规本地隔离 worker 或保持 pending。
- OCR/视频/模型等重依赖放在 `agent-harness` integration、quant 环境或受控 subprocess seam；主仓库继续 stdlib-only。
- 派生目录 `0700`、文件 `0600`，原子、content-addressed、no-clobber。
- crash/lease-expiry/retry 测试证明 deterministic IDs、UNIQUE/CAS、lease fencing、ack-after-commit 与 OperationRunner idempotency key 不会重复生成 decision/event/lifecycle/交易。
- 不输出 Token、签名 URL、批量消息正文或内部 logical key。
- 写代码前运行 `omni-hub skill-list | jq '.output.skills | length'` 并按 AGENTS 在不一致时先 `skill-sync --apply`；每个新模块至少一份单测。
- `make test` 与 quant 测试 0 失败、0 `ResourceWarning`。
- 每阶段至少一次独立 review，Critical/Important 全部关闭。

## 18. 错误恢复与幂等

- 每个 attempt 绑定 corpus/inventory/code/profile/schema/model/market-data hash。
- 单 writer append-only ledger；任务可以 retry，但同一 revision 不重复 commit。合法状态演进写入新 revision + `supersedes` 链，current materialized view 对每个 message/lifecycle 仍只有一个当前状态。
- decision revision、event 和 lifecycle ID 都由 run/input/schema/source identity 的 canonical bytes 做 SHA-256 确定性派生；SQLite 对这些 ID 以及每个实体唯一 current revision 建 `UNIQUE` 约束。
- revision 提交对 predecessor/current version 使用 compare-and-swap；TaskQueue worker 必须携带 lease fencing token，过期/被替代 lease 无权 commit。
- TaskQueue 只在 ledger transaction 与派生产物原子提交后 ack；crash/重投可以重算，但 deterministic ID + UNIQUE/CAS 使其成为 no-op，而不是重复事件/交易。
- OperationRunner 使用绑定 run ID、task ID、input SHA 和 operation kind 的 idempotency key；外部模型 send 与本地发布分别记录 attempt/commit，不因重试重复发送或覆盖已发布结果。
- crash 后从最后 committed batch 恢复；partial output 不能进入发布视图。
- source/hash 漂移、schema mismatch、missing evidence、truncation 或安全检查失败时 fail closed。
- 发布使用新 run ID；旧 run 永不原地改写。

## 19. 实施阶段

### Stage A：全量身份与 decision ledger

移除正式四 profile 输入门，建立 identity registry、分层 gold/holdout，为 842,425 条消息生成守恒 decision 和 132-target funnel。先同时证明“哪些消息为何没有成为交易”和 classifier 的实际漏单率是否通过硬门。

### Stage B：Author-eligible 媒体语义

先发布 hash-bound media occurrence input manifest，再将所有 author-eligible 媒体的图片/视频关键字段补入 evidence card，完成 occurrence acquisition/semantic 守恒；terminal blocker 单列。

### Stage C：事件与生命周期

实现多订单 state machine、显式 unresolved pool 和 lifecycle-derived curation，移除固定 324/216。

### Stage D：多市场 K 线与费用 adapter

按实际 instrument census 接入 crypto、gold/FX、equity/futures 的 point-in-time 覆盖，先覆盖候选最多且证据最完整的市场。

### Stage E：全面回测与报告

先完成 `(H, H2]` 的只读 family census/head 增量和开放 lifecycle 源消息定点重验，再对所有 evaluable lifecycle 运行保守模拟，发布带 `as_of` 的 current calls、逐 target 漏斗、结果、覆盖和 blocker。只有本阶段全部硬门通过后才允许生成跨博主比较视图。

## 20. 预期结果与边界

修复后可验证交易数量可能高于 216 个固定样本，但设计不预设一个“必须达到”的交易数，也不把数量增加本身当成正确性。真实数量只能由 842,425 条消息的守恒 ledger、识别质量门、身份核验、媒体语义和 lifecycle reconciliation 得出。用预设数量倒逼解析会制造假单。

用户将能看到两类同时成立的结果：

1. 已有足够证据和行情覆盖的博主，直接给出当前喊单、逐笔生命周期和可信回测；
2. 仍无法评估的博主，准确显示是无个人信号、作者不明、图片未取到、事件无法链接、标的不支持、K 线缺口还是权限 403，而不是消失在报告里。
