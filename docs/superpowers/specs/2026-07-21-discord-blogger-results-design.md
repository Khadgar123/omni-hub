# Discord 博主当前喊单与可信回测设计

日期：2026-07-21

状态：用户已通过“只要博主的回测结果、现在的内容和喊单，关闭自动任务后继续”明确批准本缩减范围。

## 1. 交付物

本阶段只交付两类用户可直接使用的结果：

1. **最新观点/喊单表**：按博主列出当前观点、开仓、加减仓、止损、止盈、撤单和平仓，并保留 UTC 时间、消息证据引用与媒体状态。
2. **首批可信回测表**：对币圈所长、舒琴、always-win-trader、分析师 Nick 的 BTCUSDT/ETHUSDT 信号，用本地 1m K 线还原交易生命周期并计算结果。

全量档案闭合、所有频道摘要和所有媒体语义处理继续作为背景工作，不再阻塞上述两张表。

## 2. 证据边界

- 基线历史与 closure 显式 message ID 取并集，按 message ID 去重；不把 closure 边界页的额外 raw rows 扫入。
- 消息输入绑定 raw page/evidence/merge/closure 哈希；篡改、无法定位或证据冲突时 fail closed。
- 派生报告不写入 Bot Token、签名 URL、批量正文或内部 `logical_key`。
- 123 个 private archived 403、已删消息、编辑前正文与不可访问媒体均是客观边界，不声称“博主全部历史”。

## 3. 数据流

```text
已验证基线页 + closure 显式 IDs
  -> 严格去重的优先频道消息迭代器
  -> 每条消息唯一 decision（non-signal/candidate/excluded/event）
  -> 绑定回复、引用和相关媒体
  -> 交易事件与生命周期
  -> quant 子进程读取 1m K 线并保守模拟
  -> 最新喊单表 + 博主回测表 + 覆盖/排除漏斗
```

主仓库仅使用 stdlib 完成证据迭代、事件化和发布；DuckDB/Parquet K 线读取与模拟放在 `agent-harness/quant` 中，主仓库通过现有受控子进程 seam 调用。

## 4. 事件与生命周期

四位博主使用独立、版本化的解析 profile，不使用一个万能正则。事件最少包含 `OPEN`、`AMEND`、`CANCEL`、`FILL_CONFIRM`、`PARTIAL_CLOSE`、`CLOSE_TP`、`CLOSE_SL`、`CLOSE_MANUAL`。每个输入消息恰好产生一个 decision，保留非信号、媒体依赖、解析歧义、不支持标的和超出 K 线覆盖等排除原因。

生命周期依据同一 profile、标的、方向、引用/reply 关系和时间顺序确定性链接。无法唯一链接的事件保留为 unresolved，不猜测并入交易。

## 5. 回测语义

- 所有交易决策只能使用消息观测时间已闭合的 K 线；禁止未闭合 candle 和未来消息。
- 市价单从消息之后下一根 1m bar 的 open 成交；限价单从下一根 bar 起生效，只有 high/low 触及时成交。
- 成交前撤单记为 unfilled，禁止旧脚本的 forced-market fallback。
- 同一分钟内 TP/SL 先后无法辨别时采用 stop-first 悲观路径；入场与出场同 bar 也采用悲观路径。
- 支持分批 TP；手续费和滑点是显式参数并写入 manifest。若未接入 funding，报告必须标注“未建模”。
- 未成交、已撤单、右截尾、歧义、媒体-only和数据缺口各自计数。胜率分母只是可判定的 closed win + closed loss。
- 旧 `discord-exports/replay/full_backtest.py` 及旧胜率只作 invalid baseline，不复用为真结果。

## 6. 相关媒体

只处理与喊单/观点候选有证据关系的媒体，不做整分片重试。当前严格子集共 951 个 occurrence：

- 784 个已验证 binary（782 图片、2 个 MP4）；
- 10 个失败（7 个 HTTP 404、3 个 resolution unresolved）；
- 6 个 reference-only；
- 151 个 closure delta pending（146 图片、4 视频、1 other）。

已验证图片才可进入 OCR；MP4 进入后续抽帧；failed/reference-only/pending 只在报告中披露状态。同 SHA 可复用语义处理结果，但不合并不同 occurrence 的消息证据关系。

## 7. 发布与验收

写操作经 `OperationRunner` 并使用 owner-only、原子、no-clobber 的派生目录。产物至少包含：

- `message-decisions.jsonl`；
- `trade-events.jsonl`；
- `event-manifest.json`；
- `latest-calls.json` / `latest-calls.md`；
- `trades.jsonl`；
- `backtest-report.json` / `backtest-report.md`。

验收要求：输入哈希与范围验证通过；每条输入恰好一个 decision；无未来函数；交易、排除和右截尾漏斗守恒；产物确定性且不泄露敏感值；主仓库 `make test` 与 quant 独立测试均 0 失败、0 ResourceWarning。
