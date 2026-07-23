# Discord Blogger Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** 从已验证 Discord 证据中交付最新博主观点/喊单，并对首批四位博主的 BTCUSDT/ETHUSDT 信号做无未来函数的 1m 回测。

**Architecture:** 主仓库 stdlib 模块验证并迭代 baseline + closure 证据、将消息确定性事件化并原子发布。`agent-harness/quant` 内的独立子进程读取 DuckDB/Parquet 1m K 线并保守模拟成交，主仓库不 import quant 重依赖。

**Tech Stack:** Python 3.12 stdlib, `unittest`, `OperationRunner`, `agent-harness/quant` 现有 DuckDB/Parquet 运行时与 pytest。

## Global Constraints

- 不重启 collector，不重抓历史，不运行 s03/s04 retry，不实现 Gateway 或发送 Discord 消息。
- 只使用 baseline 历史与 closure audit 显式列出的 message IDs；禁止扫入 closure 边界页的其他 raw rows。
- 所有输入按 message ID 去重，closure snapshot 优先作当前态，baseline snapshot 保留证据引用。
- 引用与 raw page/evidence/merge/closure 哈希绑定；篡改、missing、extra、duplicate 或无法定位均 fail closed。
- 主仓库保持 stdlib-only；新 CLI 只扩展 `src/omni_hub/cli/discord.py`；所有写入操作注册到 `src/omni_hub/builtins.py` 并经 `OperationRunner`。
- 产物不包含 Token、Authorization、原始/签名 URL、批量正文、内部 `logical_key` 或未脱敏异常。
- 回测仅处理 BTCUSDT/ETHUSDT；旧 `discord-exports/replay/full_backtest.py` 及旧胜率只作 invalid baseline。
- 不得使用未闭合 1m candle；市价单 next-bar open，限价单 next-bar 起触及才成交，同 bar TP/SL 使用 stop-first，禁止 forced-market fallback。
- 手续费和滑点显式写入 manifest；funding 未建模时必须披露。未成交、撤单、右截尾、歧义、媒体-only、数据缺口各自计数。
- 仅处理与候选喊单/观点有证据关系的 951 个媒体 occurrence；不做整分片重试。
- 每个实现任务严格 TDD；主仓库最终 `make test` 0 failure/0 ResourceWarning，quant 独立测试 0 failure。
- 保留用户已有未提交变更；不编辑或 stage `prompts/**` 及旧 `.superpowers/` 文件。

### Task 1: 已验证的优先频道消息迭代器

**Files:**

- Create: `src/omni_hub/discord_blogger_corpus.py`
- Test: `tests/test_discord_blogger_corpus.py`

**Interface:**

```python
@dataclass(frozen=True, slots=True)
class BloggerMessage:
    message_id: str
    channel_id: str
    author_id: str | None
    timestamp: str
    edited_timestamp: str | None
    content: str
    reply_message_id: str | None
    snapshot_ref: str
    snapshot_sha256: str
    media_occurrence_refs: tuple[str, ...]

def iter_verified_blogger_messages(
    *,
    export_root: Path,
    target_ids: Sequence[str],
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[BloggerMessage]: ...
```

**Steps:**

1. 先在 `tests/test_discord_blogger_corpus.py` 写失败测试：基线+显式 closure ID 并集、overlap closure 优先、message ID 去重、边界 raw row 排除、target/time 过滤、回复定位、raw/evidence/merge/closure 哈希篡改 fail closed。
2. 运行 `PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -W error::ResourceWarning -m unittest tests.test_discord_blogger_corpus -v`，确认因模块/行为缺失而失败。
3. 实现最小迭代器：复用 `discord_sharding` 的 canonical JSON/hash 和已发布 merge/closure 验证，不复制 collector 私有解析逻辑。
4. 重跑 focused tests，期待全部通过且无 warning。
5. 提交仅本任务文件：`feat(discord): iterate verified blogger corpus`。

### Task 2: 消息 decision、四博主事件化与最新喊单报告

**Files:**

- Create: `src/omni_hub/discord_trade_events.py`
- Create: `src/omni_hub/discord_blogger_results.py`
- Modify: `src/omni_hub/builtins.py`
- Modify: `src/omni_hub/cli/discord.py`
- Create: `tests/test_discord_trade_events.py`
- Create: `tests/test_discord_blogger_results.py`
- Modify: `tests/test_discord_cli.py`

**Interfaces:**

```python
def parse_message(profile: str, message: BloggerMessage) -> MessageDecision: ...
def link_trade_lifecycles(decisions: Sequence[MessageDecision]) -> tuple[TradeLifecycle, ...]: ...
def build_latest_calls_report(
    *, decisions: Sequence[MessageDecision], asof: datetime
) -> dict[str, object]: ...
def publish_blogger_event_artifacts(
    *, output_dir: Path, source_manifest: Mapping[str, object]
) -> dict[str, object]: ...
```

**Steps:**

1. 先写失败测试：币圈所长、舒琴、always-win-trader、分析师 Nick 各自 golden fixtures；每条消息恰好一个 decision；OPEN/AMEND/CANCEL/FILL/PARTIAL_CLOSE/TP/SL/MANUAL_CLOSE；引用链接；无法唯一链接时 unresolved；编辑时间生效；不支持标的/媒体-only 显式排除。
2. 运行 `PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -W error::ResourceWarning -m unittest tests.test_discord_trade_events tests.test_discord_blogger_results tests.test_discord_cli -v`，确认红灯。
3. 实现四个版本化 profile 和稳定 SHA-256 event/lifecycle IDs；不保存正文到 manifest/report。
4. 注册 `discord_blogger_events_build` LOCAL_WRITE handler，扩展 `discord-blogger-events-build` CLI；原子/no-clobber 发布 `message-decisions.jsonl`、`trade-events.jsonl`、`event-manifest.json`、`latest-calls.json/md`。
5. 重跑 focused tests，期待全部通过且无 warning。
6. 提交：`feat(discord): build blogger trade events`。

### Task 3: 相关媒体状态清单（无 retry）

**Files:**

- Create: `src/omni_hub/discord_candidate_media.py`
- Create: `tests/test_discord_candidate_media.py`

**Interfaces:**

```python
def build_candidate_media_manifest(
    *,
    export_root: Path,
    candidate_message_refs: Sequence[str],
    source_hashes: Mapping[str, str],
) -> dict[str, object]: ...

def iter_ocr_input_rows(
    *, export_root: Path, manifest: Mapping[str, object]
) -> Iterator[dict[str, object]]: ...
```

**Steps:**

1. 先写失败测试：只按 message-source 关系纳入、captured/failed/reference/pending 四分区互斥守恒、blob path contained/regular/no-symlink/SHA/bytes/MIME 验证、closure overlap 去重、确定性、敏感字段禁入、OCR 只输出已验证图片。
2. 运行 `PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -W error::ResourceWarning -m unittest tests.test_discord_candidate_media -v`，确认红灯。
3. 实现只读 manifest 构建与 OCR input iterator；不复制 blob，不下载，不运行 retry。
4. 用冻结数据验证严格子集为 951 = 784 + 10 + 6 + 151；数字变化时 fail closed 并更新证据基线，不硬改期望。
5. 重跑 focused tests，提交：`feat(discord): inventory candidate media`。

### Task 4: Quant 1m 保守模拟器与回测发布

**Files:**

- Create: `agent-harness/quant/quant/discord_backtest.py`
- Create: `agent-harness/quant/tests/test_discord_backtest.py`
- Create: `src/omni_hub/discord_backtest.py`
- Create: `tests/test_discord_backtest.py`
- Modify: `src/omni_hub/builtins.py`
- Modify: `src/omni_hub/cli/discord.py`
- Modify: `tests/test_discord_cli.py`

**Interfaces:**

```python
# agent-harness/quant/quant/discord_backtest.py
def simulate_lifecycles(
    *, lifecycles: Sequence[Mapping[str, object]], bars: Sequence[Mapping[str, object]],
    fee_bps: float, slippage_bps: float,
) -> dict[str, object]: ...

# src/omni_hub/discord_backtest.py
def run_quant_blogger_backtest(
    *, event_manifest: Path, market_root: Path, output_dir: Path,
    fee_bps: float, slippage_bps: float, timeout_seconds: int = 300,
) -> dict[str, object]: ...
```

**Steps:**

1. quant 侧先写失败测试：next-bar/no-lookahead、limit 未触及、cancel-before-fill、stop-first、同 bar 入退场悲观路径、分批 TP、手续费/滑点、bar gap、unfilled/right-censored 和确定性。
2. 运行 `cd agent-harness/quant && .venv/bin/python -m pytest tests/test_discord_backtest.py -q`，确认红灯。
3. 实现 pure simulator 与 `python -m quant.discord_backtest` JSON CLI；读 bar 时严格使用已闭合 1m window，不直接复用会暴露未闭合 bar 的旧 helper。
4. 主仓库先写失败测试：quant 子进程 timeout、非 JSON、非零退出、异常脱敏、输入/输出 SHA 绑定、原子 no-clobber。
5. 注册 `discord_blogger_backtest_run` LOCAL_WRITE handler，扩展 `discord-blogger-backtest-run` CLI，发布 `trades.jsonl`、`backtest-report.json/md`；每位博主报告 messages → candidates → linked → executable → closed/unfilled/right-censored/excluded 漏斗。
6. 运行 quant focused tests 和 `PYTHONPATH=src /Users/hzh/opt/anaconda3/envs/omni-hub/bin/python -W error::ResourceWarning -m unittest tests.test_discord_backtest tests.test_discord_cli -v`，期待全部通过。
7. 提交：`feat(discord): backtest blogger signals conservatively`。

### Task 5: 正式数据运行、独立复核与交付

**Files:**

- Create under no-clobber derivative namespace using `blogger-results-YYYYMMDDTHHMMSSZ-SOURCESHA12`: `/Users/hzh/discord-exports/v2/derivatives/blogger-results/`
- Update only if required by actual commands: `docs/discord-collector.md`

**Steps:**

1. 重验正式 plan/merge/closure 路径与 SHA、四分片 SQLite `quick_check`、磁盘空间 >= 50 GiB 和 Token 泄露扫描 0；不输出敏感匹配内容。
2. 先运行优先频道最新报告，再运行四博主事件化和 BTC/ETH 回测；将 K 线上界减去持仓观察窗口作为信号 cutoff。
3. 核对漏斗守恒、每条引用可定位、媒体四分区守恒和胜率分母；抽样复核开仓、加减仓、止盈、止损、撤单各至少 5 个生命周期。
4. 运行 `make test`，要求 0 failure/0 ResourceWarning；运行 `cd agent-harness/quant && .venv/bin/python -m pytest -q`，要求 0 failure。
5. 对整个分支做独立 review，修复全部 Critical/Important；重跑受影响测试与最终硬门。
6. 交付链接与简短结论，明确标注 private archived 403、媒体待处理、funding 未建模、K 线缺口与任何 unresolved lifecycle。

## Plan Self-Review

- 范围仅包含用户要的当前内容/喊单和首批可信回测；全量摘要与媒体重试不是前置条件。
- 无占位符或未定签名；所有新写入都经 OperationRunner。
- 主仓库与 quant 依赖边界一致；无重复的回测引擎或 collector 重抓路径。
- 测试覆盖证据篡改、解析、生命周期、无未来函数、保守同 bar 语义、敏感数据禁入和发布原子性。
