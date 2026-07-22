# Discord 媒体解析失败分类与窄恢复设计

日期：2026-07-20

## 1. 背景与已核验证据

Discord Collector v2 的媒体连接策略要求：URL 必须是无凭据 HTTPS，DNS 结果必须全部为公网地址；只有请求显式绑定 RFC 2544 fake-IP 策略时，精确 Discord 媒体主机、443 端口和 `198.18.0.0/15` 才允许通过。连接阶段仍会重新解析、固定已验证地址并使用原主机完成 TLS/SNI 校验。

正式四分片运行期间出现了一个分类缺口：部分 `unsafe_media_url` 实际指向请求策略允许的精确 Discord CDN/proxy 主机。以四进程首次意外终止后的 checkpoint 窗口 `2026-07-20T06:58:52+08:00`—`2026-07-20T06:59:45+08:00` 为上下文进行的非规范活动查询中，610 个当前失败记录落在精确官方主机，历史 attempt 中共有 1,141 次 `unsafe_media_url`。这些数字只用于定位问题，会随 resume 变化；正式验收以冻结后的 asset ledger 和带哈希审计产物为准。

根因已经沿调用链确认：

1. `src/omni_hub/connectors/discord.py::_resolved_public_addresses` 将 resolver `OSError`、空答案、真实非公网答案和 mixed answer 都包装为 `DiscordMediaSecurityError`。
2. `src/omni_hub/discord_collector.py::_download_asset` 将所有 `DiscordMediaSecurityError` 永久记为 `unsafe_media_url`。
3. `unsafe_media_url` 不在 retry 集合中；candidate 恢复循环也会跳过已存在的 unsafe attempt。
4. 现有 attempt 只保存 `unsafe_media_url`，未保存异常 subtype。因此不能事后声称某一条旧记录“一定只是 DNS 故障”，也不能直接改写旧失败。
5. 现有 YouTube `/embed/<id>` reference 校正只解决窄播放器语义，不会恢复 Discord 官方 CDN/proxy 的解析误分类。

## 2. 目标与非目标

### 2.1 目标

- 将临时名称解析不可用、不可解析/无答案、resolver 非法输出与真正的 URL/网络安全拒绝分开。
- 保持 fail-closed：无法得到完整可验证地址集合时不建立连接，任何非公网或 mixed answer 继续永久拒绝。
- 对符合严格双重条件的旧官方媒体失败做一次 legacy eligibility override；后续临时解析重试受跨 resume 的固定三次总预算约束。
- 原失败 attempt 永久保留，只追加新 attempt；每次恢复都能说明触发规则、旧 attempt、固定策略输入和重试序号。
- 保持现有 HTTP、MIME、大小、reference-only 和 candidate fallback 语义，并生成不含签名 URL 的逐项补偿/恢复审计产物。

### 2.2 非目标

- 不重试所有 `unsafe_media_url`，也不允许“历史上曾 unsafe”覆盖当前 complete、warning、reference、400/404 或其他 terminal outcome。
- 不扩大 RFC 2544 主机 allowlist、网络、端口或协议范围。
- 不改变 YouTube reference-only、代理成功清除旧 reference provenance、附件/缩略图 400/404 等既有语义。
- 不实现 Gateway、并发下载池、共享 rate limiter 或通用网络重试；这些属于独立设计。
- 不热更新或发送信号给当前运行进程；新代码只供进程退出后的同请求 resume 使用。

## 3. 选定方案

采用“稳定异常 taxonomy + 当前 record/candidate 双重 eligibility + 原子 marker + 固定三次 resolution 预算 + 哈希绑定审计产物”的组合方案。

未选方案：

- 只修未来分类、旧失败全部手工补偿：实现最小，但 signed media URL 可能过期，无法满足当前严格恢复目标。
- 将全部 `unsafe_media_url` 设为 retryable：会混淆真正的 SSRF 拒绝并反复触达不应连接的目标，不可接受。
- 仅靠 legacy marker 限制重试：marker 只能保证 eligibility override 一次，不能阻止后续 transient attempt 跨 resume 无界增长。

## 4. 新的异常与终止原因

### 4.1 Transport taxonomy

新增 `DiscordMediaResolutionError(DiscordAPIError)`。异常携带稳定枚举属性 `reason_code`，collector 必须读取属性，不能解析异常字符串。再新增 `DiscordMediaResolutionInvalidAnswer(DiscordAPIError)` 表示 resolver 返回结构或地址值非法。两类异常都表示连接尚未发生，但都不证明 URL “不安全”。

只把以下窄集合视为 transient：

- `socket.gaierror` 的 errno 为平台可用的 `EAI_AGAIN`；
- `TimeoutError` / `socket.timeout`；
- resolver `OSError.errno == ETIMEDOUT`。

其他 resolver 结果 fail-closed，但不是自动 transient：

| resolver/URL 情况 | 异常与 `reason_code` | Collector terminal reason | 自动重试 |
| --- | --- | --- | --- |
| `EAI_AGAIN` | `DiscordMediaResolutionError(resolver_eai_again)` | `media_resolution_failed_transient`；第 3 次为 `media_resolution_retry_exhausted` | 最多 3 次总预算 |
| timeout / `ETIMEDOUT` | `DiscordMediaResolutionError(resolver_timeout)` | 同上 | 最多 3 次总预算 |
| `EAI_NONAME` | `DiscordMediaResolutionError(resolver_name_not_found)` | `media_resolution_unresolved` | 否 |
| 平台定义的 `EAI_NODATA` | `DiscordMediaResolutionError(resolver_no_data)` | `media_resolution_unresolved` | 否 |
| 空 list/tuple | `DiscordMediaResolutionError(resolver_empty_answer)` | `media_resolution_unresolved` | 否 |
| 其他 resolver `OSError` | `DiscordMediaResolutionError(resolver_os_error_unclassified)` | `media_resolution_unresolved` | 否 |
| resolver tuple/地址值非法 | `DiscordMediaResolutionInvalidAnswer(resolver_invalid_answer)` | `media_resolution_invalid_answer` | 否 |
| 非法 URL、非 HTTPS、凭据、非法端口 | `DiscordMediaSecurityError` | `unsafe_media_url` | 否 |
| IP literal 非公网 | `DiscordMediaSecurityError` | `unsafe_media_url` | 否 |
| 任一 DNS answer 非公网或 mixed answer（精确 RFC 2544 例外除外） | `DiscordMediaSecurityError` | `unsafe_media_url` | 否 |
| 已验证地址连接失败、读超时或 reset | 既有 I/O/API 错误 | 沿用 `download_failed_transient` | 沿用既有规则 |

`DiscordMediaResolutionError` 和 `DiscordMediaResolutionInvalidAnswer` 只改变证据分类，不绕过 `_validate_public_media_url`、connect-time revalidation、DNS pin、TLS/SNI 或 redirect revalidation。

### 4.2 Attempt 诊断与固定预算

resolution attempt 的 `failure_detail` 只允许上表七个稳定枚举之一；不保存原始 exception 文本、Authorization、query 参数或签名 URL 的派生日志。`failure_detail` 与 terminal reason 的合法配对为：

- `resolver_eai_again`、`resolver_timeout` ↔ `media_resolution_failed_transient` 或 `media_resolution_retry_exhausted`；
- `resolver_name_not_found`、`resolver_no_data`、`resolver_empty_answer`、`resolver_os_error_unclassified` ↔ `media_resolution_unresolved`；
- `resolver_invalid_answer` ↔ `media_resolution_invalid_answer`。

固定预算键为 `(logical_key, exact candidate_url, request_sha256)`。同一 invocation 对同一键最多分配一个新的 committed logical sequence；整个 run 的 `attempt_history` 中最多允许 3 个带 resolution taxonomy 的 committed logical sequence。没有基于墙钟时间的 deadline；三次历史 sequence 是跨 resume、确定性且可验证的唯一上限。legacy recovery attempt 是该键的第 1 个 typed sequence。第 3 个 sequence 仍为 transient 时写 `media_resolution_retry_exhausted`；后续 terminal resume 不再解析或连接该 candidate。`unresolved` 与 `invalid_answer` 在首次结果后即为 terminal，不消费后续自动重试。

三次上限不声称物理网络 exactly-once：若进程在某个已提交的 `in_progress` sequence 中崩溃，resume 会复用同一 sequence，可能再次执行 resolver/socket I/O，但绝不分配第 4 个 logical sequence。任意崩溃次数下可严格验收的是 committed sequence 数；若要限制崩溃重放产生的物理 I/O，需要独立的 durable lease/deadline 设计，不在本变更内。

每个 typed attempt 保存；下例是当前四个 RFC opt-in 正式 run：

```json
{
  "status": "failed",
  "terminal_reason": "media_resolution_failed_transient",
  "failure_detail": "resolver_eai_again",
  "resolution_retry_sequence": 1,
  "policy_inputs_sha256": "17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325"
}
```

`resolution_retry_sequence` 必须从 1 开始连续递增且不超过 3；它和历史实际计数不一致时，load/resume 在任何网络 I/O 前失败关闭。

`policy_inputs_sha256` 的语义由 request identity 决定：启用 RFC 2544 例外的请求必须在每个 typed attempt 中保存 request 绑定的精确 policy hash；未启用该例外的请求必须显式保存 null，不能伪造 RFC policy hash。两类请求都由不可变 `request_sha256` 参与预算键，也都可使用 committed sequence 1—3。legacy recovery 仅适用于启用 RFC 2544 且 hash 精确匹配的请求；非 opt-in 请求只获得新的 taxonomy 与普通 bounded retry，永不获得 legacy override。

## 5. 旧记录一次性恢复

### 5.1 Eligibility

一个旧 candidate 只有同时满足以下全部条件才进入 legacy recovery：

1. 当前 record **必须**恰为 `status == failed` 且 `terminal_reason == unsafe_media_url`。
2. 该 candidate 的最新 attempt **也必须**恰为 `failed + unsafe_media_url`；这里只是 AND，不接受“record 或历史 candidate 任一满足”。当前 record 为 complete、captured、reference-only、HTTP 400/404/415 或其他原因时均不恢复。
3. record 与被引用旧 attempt 的 `actual_bytes == 0`，且 `http_content_type`、`http_content_length`、`sha256`、`blob_path` 全为 null；同一 record 的 history 中不存在任何 `complete`/`captured_with_warning` binary attempt。
4. candidate 是可规范化的无凭据 HTTPS URL，显式端口缺省或为 443，规范化后的有效端口必须是 443。
5. canonical host 精确属于现有 `RFC2544_FAKE_IP_MEDIA_HOSTS`，不接受子域、相似域或新增 host。
6. 当前请求已绑定 `allow_rfc2544_fake_ip == true`，且 request 中的完整 policy descriptor 与当前 `rfc2544_fake_ip_media_policy_descriptor()` 完全一致；`inputs_sha256` 必须为 `17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325`。
7. 同一 `(candidate_url, legacy_resolver_security_conflation_v1, policy_inputs_sha256)` 尚无 legacy marker。
8. 若 observation 同时给出 external direct 与 Discord proxy，只有 `declared_metadata.proxy_url` 且通过既有 `_is_discord_external_proxy_url` 的 proxy candidate 可恢复；direct candidate 不恢复。若 proxy 从未产生 `failed + unsafe_media_url` attempt，也不能凭“存在 proxy URL”创建恢复。

不符合任一项即保持原证据，不尝试解析或连接。特别地，历史 unsafe 之后最新 attempt 为 400/404 的 candidate 不符合第 2 条，不能被隐式再下载。

### 5.2 追加式 provenance

不修改、删除或重排旧 `attempt_history`。legacy attempt 在开始时追加：

```json
{
  "status": "in_progress",
  "retry_trigger": "legacy_resolver_security_conflation_v1",
  "retry_of_attempt_number": 3,
  "policy_inputs_sha256": "17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325",
  "resolution_retry_sequence": 1
}
```

如果第 1 或第 2 次结果为 transient，下一次 resume 可以追加普通 typed retry：

```json
{
  "status": "in_progress",
  "retry_trigger": "media_resolution_retry_v1",
  "retry_of_attempt_number": 4,
  "policy_inputs_sha256": "17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325",
  "resolution_retry_sequence": 2
}
```

每次 load/resume 必须验证，而不只是写入时验证：

- `retry_of_attempt_number` 是非 bool 的 1-based int，严格小于当前 attempt number；
- legacy marker 引用同一 exact candidate URL 的更早 `failed + unsafe_media_url` attempt，且旧 attempt 满足 zero-byte/no-metadata 条件；
- 普通 retry 引用同一 candidate 的更早 typed transient attempt，序号恰为前一序号加一；
- legacy marker 的 policy hash 等于 request 绑定的完整 policy descriptor；RFC opt-in 的普通 typed retry 也必须等于该 hash，非 opt-in typed attempt 则必须为 null；
- 同一 candidate/rule/policy hash 最多一个 legacy marker，typed sequence 不重复、不跳号且不超过 3；
- `failure_detail` 只与第 4.2 节允许的 terminal reason 配对；in-progress attempt 尚无 `failure_detail`。

任何交叉验证失败都视为资产证据损坏，在 resolver/socket I/O 前停止该 run。无新字段的既有 schema-v3 record 保持合法，无需批量迁移。

### 5.3 结果语义

- 下载成功：record 变为既有 `complete` 或 `captured_with_warning`；旧 unsafe attempt 仍在 history。
- 新解析 transient：序号 1/2 写 `media_resolution_failed_transient`；序号 3 写 `media_resolution_retry_exhausted`。
- 新解析无答案/不可解析：写 `media_resolution_unresolved`，不再自动重试。
- resolver 输出非法：写 `media_resolution_invalid_answer`，不再自动重试。
- 新安全拒绝：写 `unsafe_media_url`，不再触发 legacy override。
- HTTP、MIME、大小结果完全沿用既有 `_download_asset`、`_mime_outcome`、`_declared_size_mismatch` 和 `_supports_candidate_fallback` 语义：`content_length_mismatch` 仍可 retry，`declared_size_mismatch` 仍是 captured warning，untyped embed 仍可 reference-only，真正的 media-type mismatch/size limit 仍按既有 hard outcome 处理。
- 某 candidate 为 HTTP 400/404/415 而后续 candidate 成功时，record 可最终 covered；失败 attempt 仍不可删除，并在审计产物标为 `candidate_failed_record_covered`。若 record 最终仍 failed，则标为补偿 blocker。
- 若已有 proxy binary 成功，继续使用现有规则清除过时 reference provenance；不清除旧 failed attempt。

## 6. 原子性、崩溃恢复与幂等边界

legacy marker 与新的 `in_progress` attempt 必须作为同一 asset-record 内容，通过现有 asset ledger pending/committed 两阶段提交；提交成功发生在任何 resolver、socket 或临时下载文件 I/O 之前。这里的 exactly-once 只指“legacy eligibility decision/marker 最多一次”，不承诺网络 exactly-once。

| 崩溃点 | resume 行为 |
| --- | --- |
| append/ledger commit 前 | committed record 中没有 marker，可重新做 eligibility 判定 |
| marker commit 后、resolver 前 | 复用同一个 marked `in_progress` attempt，不追加第二个 marker |
| resolver/下载后、final record commit 前 | 先由 asset ledger reconcile committed/pending；可能存在未引用 content-addressed blob。复用同一 logical sequence，允许重新发起物理 I/O，不把孤立 blob 当作成功证据 |
| final terminal commit 后 | 读取 terminal attempt；仅 typed transient 且 sequence < 3 时才可追加下一普通 retry，其他结果 no-op |
| ledger/record SHA 不一致或 pending 无法 reconcile | fail-closed，禁止恢复和网络 I/O |

当前四个 collector 进程继续使用已加载代码，不热更新。新代码合入后，只在四进程全部退出且 request/plan/policy 哈希仍一致时，以原 run-id、原 shard、原请求 resume。本设计不引入并发，也不提高 Discord REST 频率。

## 7. 可验收审计产物

collector 的既有 `manifest.media` 总数保持兼容；另由 `_write_derived_outputs` 确定性生成：

`runs/<run-id>/media-recovery-audit.json`

顶层 schema：

```json
{
  "version": 1,
  "kind": "discord_media_resolution_recovery_audit",
  "run_id": "<run-id>",
  "request_sha256": "<bound request sha256>",
  "policy_inputs_sha256": "17b89647c19c760f58058291784f0fa55a6b55f7c91c23db738a4221d704e325",
  "asset_index_sha256": "<asset-index sha256>",
  "counts": {},
  "items": []
}
```

上例是当前 RFC opt-in 正式 run；非 opt-in run 的顶层 `policy_inputs_sha256` 必须为 null，与 attempt 语义一致。

### 7.1 唯一行模型

`items` 是两个不重叠 row kind 的并集：

1. `attempt` row：每个满足以下并集条件的 attempt 恰好一行——带 `legacy_resolver_security_conflation_v1`/`media_resolution_retry_v1` trigger、带 resolution terminal reason，或 terminal reason 为 HTTP 400/404/415。同一 attempt 同时满足多个条件仍只产生一行。
2. `record` row：每个当前 `status == failed` 或 `status == reference_only` 的 asset record 恰好一行。record row 与其 attempt row 可以同时存在，因为两者证据层级不同。

唯一键与排序规则：

- attempt row 的 identity payload 为 `{"item_kind":"attempt","logical_key":<exact string>,"attempt_number":<1-based int>}`；record row 为 `{"item_kind":"record","logical_key":<exact string>}`；
- `row_id` 为 `sha256(_canonical_json_bytes(identity_payload, newline=False))`，不得由 Python `hash()` 生成；所有 `row_id` 必须唯一；
- attempt number 是 `attempt_history` 的 1-based index，不按内容重新编号；
- items 按 `(logical_key 的 UTF-8 bytes, item_kind_order, attempt_number)` 升序，`item_kind_order` 固定为 attempt=0、record=1，record 的 attempt number 视为 0；
- `candidate_url_sha256 = sha256(exact candidate URL UTF-8 bytes)`，不先去 query、不规范化后再 hash；artifact 不输出原始 URL；
- `candidate_host` 是 URL parser 得到的 lowercase、去单个 trailing dot canonical host；无法安全解析时为 null。

每行固定字段为：`row_id`、`item_kind`、`logical_key`、`candidate_url_sha256`、`candidate_host`、`attempt_number`、`retry_trigger`、`status`、`terminal_reason`、`failure_detail`、`actual_bytes`、`binary_captured`、`final_record_status`、`final_record_terminal_reason`、`disposition`。record row 的 `attempt_number`、`retry_trigger`、`failure_detail` 为 null；`candidate_url_sha256` 绑定 record 当前 URL。

`binary_captured` 只在该行有正字节、有效 SHA/blob 且 status 为 `complete` 或 `captured_with_warning` 时为 true；`reference_only` 永远为 false。disposition 按以下优先级唯一赋值：

1. 有效 binary `complete` → `binary_captured`；有效 binary `captured_with_warning` → `captured_with_warning`；
2. `reference_only` → `reference_only_not_binary`；
3. attempt failed 但最终 record covered → `candidate_failed_record_covered`；
4. 带 typed retry trigger 且仍为 `in_progress`（terminal reason 为 null）或 `interrupted`（terminal reason 为 `interrupted`），两者 `failure_detail` 均为 null → `resolution_retry_pending`；
5. `media_resolution_failed_transient` → `resolution_retry_pending`；
6. `media_resolution_retry_exhausted` → `resolution_retry_exhausted_blocker`；
7. `media_resolution_unresolved` → `resolution_unresolved_blocker`；
8. `media_resolution_invalid_answer` → `resolution_invalid_answer_blocker`；
9. `unsafe_media_url` → `unsafe_blocker`；
10. HTTP 400/404/415 → `http_compensation_blocker`；
11. 既有 `_HARD_ASSET_FAILURE_REASONS` 或 media-type hard outcome → `hard_media_failure_blocker`；
12. 其他 failed outcome → `other_media_failure_blocker`。

第 4 条仅用于 marker 已持久化但进程在解析终态提交前中断的 attempt row；它不产生
record row，不计 binary，也不把当前 `in_progress` record 伪装成 `failed`。manifest 的
media partial 状态仍独立阻止 closure。

### 7.2 固定 counts 与不变量

`counts` 必须且只能包含以下整数键：

```json
{
  "rows_total": 0,
  "attempt_rows": 0,
  "record_rows": 0,
  "legacy_attempt_rows": 0,
  "typed_resolution_attempt_rows": 0,
  "http_400_404_415_attempt_rows": 0,
  "binary_captured_attempt_rows": 0,
  "candidate_failed_record_covered_attempt_rows": 0,
  "current_failed_records": 0,
  "current_reference_only_records": 0,
  "resolution_retry_pending_records": 0,
  "resolution_retry_exhausted_records": 0,
  "resolution_unresolved_records": 0,
  "resolution_invalid_answer_records": 0,
  "unsafe_records": 0,
  "http_compensation_records": 0,
  "hard_media_failure_records": 0,
  "other_media_failure_records": 0,
  "unresolved_blockers": 0
}
```

所有 counts 直接从 rows 重算；其中：

- `rows_total == attempt_rows + record_rows`；
- `record_rows == current_failed_records + current_reference_only_records`；
- `unresolved_blockers == current_failed_records`，包括尚可 retry 的 pending record；
- 八个失败 record bucket（retry pending、retry exhausted、resolution unresolved、invalid answer、unsafe、HTTP compensation、hard media、other media）两两互斥且总和等于 `current_failed_records`；
- `legacy_attempt_rows` 是 trigger 为 legacy 的 attempt rows；`typed_resolution_attempt_rows` 是带任一 typed trigger 或 resolution terminal reason 的 attempt rows；
- `http_400_404_415_attempt_rows` 只数三个精确 terminal reason；
- `binary_captured_attempt_rows` 和 `candidate_failed_record_covered_attempt_rows` 分别按 attempt row 的 disposition 计数。

文件使用 `_canonical_json_bytes` 的稳定 UTF-8 JSON 编码，`items` 已按上述规则排序；artifact SHA-256 对最终文件 bytes 计算。closure validator 从 asset records 独立重建 rows/counts 并逐字节验证，不能信任 artifact 自报数字。

run manifest 新增哈希绑定：

```json
{
  "media_recovery_audit": {
    "version": 1,
    "path": "media-recovery-audit.json",
    "sha256": "<artifact sha256>",
    "counts": {}
  }
}
```

closure audit 必须验证 artifact path/hash、counts 与 asset-index/records 一致；`unresolved_blockers > 0` 或 `manifest.media.failed > 0` 阻止完成。`candidate_failed_record_covered` 不把 covered record 改回 failed，但永久保留在补偿证据中。

## 8. 测试设计

### 8.1 Transport 单测

1. `EAI_AGAIN`、timeout/`ETIMEDOUT`：抛带稳定 `reason_code` 的 `DiscordMediaResolutionError`，opener/socket 调用数为 0。
2. `EAI_NONAME`、平台可用的 `EAI_NODATA`、空答案、未分类 OSError：映射为各自 unresolved code，不标 unsafe，不发起连接。
3. malformed resolver tuple/address：抛 `DiscordMediaResolutionInvalidAnswer`，不标 unsafe，不发起连接。
4. private answer、mixed public/private answer：仍抛 `DiscordMediaSecurityError`，socket 调用数为 0。
5. 精确 Discord host + 443 + fake-IP opt-in：既有成功测试继续通过。
6. 非 allowlist host、相似域、非 443、IP literal 和 DNS rebinding：既有拒绝测试继续通过。

### 8.2 Collector 单测

1. 各 resolution code 映射到第 4 节精确 terminal reason/`failure_detail`，collector 不解析异常字符串。
2. eligibility 要求 record 与 candidate latest attempt 同时为 `failed + unsafe_media_url`；complete/reference/current 404 即使有历史 unsafe 也不恢复。
3. 构造 external direct/proxy：只恢复已有合格 unsafe attempt 的官方 proxy candidate。
4. legacy attempt 保存 trigger、旧 attempt 编号、固定 policy hash 和 sequence 1；旧 attempt byte-for-byte 不变。
5. transient resume 只产生 committed logical sequence 2/3；第三次 exhausted，terminal 第四次 resume 的 resolver/socket 调用数为 0。另测同一 in-progress sequence 的 crash replay 可重复物理 I/O，但 sequence 数不增加。
6. unresolved、invalid-answer、真正 unsafe 在下一次 resume 均 no-op。
7. 非官方、错误策略、非 443、带凭据、有 bytes/blob 或已有 covered binary 的记录不恢复。
8. load-time 篡改测试覆盖错误 `retry_of`、跨 candidate 引用、重复 marker、policy mismatch、sequence 跳号/重复/大于 3、非法 detail/reason 配对；都在网络前失败。
9. crash 注入覆盖四个状态点：marker 前、marker commit 后、blob 后/final commit 前、terminal commit 后；同一 legacy marker 始终最多一个。
10. HTTP 404 candidate + 后续 binary 成功时，record covered、404 attempt 保留，审计 disposition 为 `candidate_failed_record_covered`；最终 404 则为 blocker。
11. `mime_mismatch`、`declared_size_mismatch`、`media_type_unverified`、`media_reference_not_binary`、`content_length_mismatch`、hard media failure 的既有语义无回归。
12. audit artifact 不含 raw/signed URL，row identity、URL hash、排序、去重和固定 counts 可独立重算；篡改 artifact、顺序、rows 或 counts 时 closure validation 失败。
13. 非 RFC-opt-in request 的 typed attempt 保存 null policy hash、普通 retry 最多产生 logical sequence 1—3 且不触发 legacy recovery；伪造 RFC hash 时 load 失败。
14. YouTube reference 和 proxy binary stale-reference 清理无回归。
15. marker 已提交后中断的 typed `in_progress`/`interrupted` attempt 仍能产生确定性审计行；前者 terminal reason 为 null，后者为 `interrupted`，两者 `failure_detail` 均为 null。disposition 为 `resolution_retry_pending`，且不计入 binary 或当前 failed-record counts。

## 9. 验收标准

1. 定向测试先在旧代码上因缺少 resolution taxonomy、原子 marker、跨 resume 预算和 audit artifact 而失败，再由最小实现转绿。
2. 全仓 `make test` 为 0 failure、0 ResourceWarning。
3. 私网、mixed answer、DNS rebinding、fake-IP allowlist 负向测试全部通过，证明 SSRF 接受边界未放宽。
4. 对正式 run 做 resume 时，request SHA、计划 SHA 和 policy inputs SHA 全部匹配。
5. 每个恢复成功的 binary 都有新 attempt、blob SHA 和回链；每个失败仍有明确 terminal reason，旧 attempt 不变。
6. `media-recovery-audit.json` 逐项列出 resolution、400/404/415、当前 failed 和 reference-only；reference-only 不计 binary，所有 counts 与 manifest/asset index 一致。
7. 只有在媒体失败逐项审计、审计 artifact 哈希验证通过且其他 authorized-scope 门全部通过后，才能进入 closure/merge；本修复本身不构成“完整”声明。
