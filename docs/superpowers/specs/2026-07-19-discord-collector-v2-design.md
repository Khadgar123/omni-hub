# Discord Collector v2 证据优先采集设计

日期：2026-07-19

## 1. 目标与当前事实

本阶段不继续沿用旧脚本“文件存在即跳过”和固定 10,000 条上限，也不先相信旧回测结论。目标是建立一个可续跑、可审计、不会静默漏页的 Discord Bot API 原始证据层，然后才允许 OCR、语义整理和回测消费。

当前已核验：

- Bot token 只保存在 `~/.config/dce/bot-token`，权限为 `0600`；身份、guild、频道列表、消息正文和新版 pins API 均实测成功。
- 旧任务实际包含 32 个唯一频道目标和 1 个显式 thread，共 33 项；现有文件均可解析，共 86,704 条消息。
- 其中 4 个文件恰好为 10,000 条，属于旧采集器硬上限风险，不能视为完整。
- 旧 raw 目录中共发现 419 个消息内嵌 thread 对象，但只有 9 个 thread 被独立抓取，至少 410 个需要重新枚举核对。
- 旧归档线程分页游标错误：public/private archived threads 应使用最后一项的 `thread_metadata.archive_timestamp`；joined-private archived threads 才使用 thread snowflake ID。
- 旧脚本没有系统抓取 pinned messages、private/joined archived threads 和媒体原文件，也没有逐流终止证明。

用户随后提供了个人 Discord UI 中的置顶清单。该 UI 状态本身不由 Bot API 暴露，但手工清单已被规范化为 130 个名称：128 个可由旧目录直接定位，另外两个通过当前 Bot 的 metadata-only 查询解析；同名/近邻对象按“宁可多收、不静默漏收”原则保留。最终 expanded target 快照为 132 个唯一对象，forum 下后续发现的 thread 不计入这个静态基数并会动态追加。旧 32+1 只作为历史完整性对照，不再是正式采集范围。

## 2. 能力边界

Collector v2 只声明 Bot API 能证明的覆盖范围。

| 对象 | 可采集条件 | v2 行为 |
| --- | --- | --- |
| 普通文字、Bot/模型消息、回复、转发快照、components、poll、embeds | Bot 有 `VIEW_CHANNEL` + `READ_MESSAGE_HISTORY`，并启用 Message Content intent | 原样保存完整 Message JSON，不先做有损字段映射 |
| 图片、文件、语音消息、消息内嵌图像/视频 URL | 上述权限且 CDN URL 可访问 | 下载原文件，计算 SHA-256，保留 MIME、尺寸、源消息和原 URL |
| forum/media 子帖、active threads | Bot 可见 | 从 guild active threads、消息内嵌 thread 和 parent 枚举合并 |
| public/private/joined-private archived threads | Bot 具备对应权限 | 分别调用三个端点并使用各自正确游标 |
| pinned messages | Bot 可见频道 | 使用新版 `/channels/{id}/messages/pins` 分页，保留 `pinned_at` |
| voice/stage 的文字聊天 | Bot 同时具备频道可见、历史读取及所需连接权限 | 像普通频道一样尝试，失败必须记为 blocked，不伪装为空 |
| Discord Go Live 直播画面/音频 | 公共 Bot REST API 不提供帧或音轨 | 明确记为 `not_api_exposed`；不能声称已看直播内容 |
| 用户个人 Favorites/侧栏置顶频道 | 这是用户私有 UI 状态，不由 Bot API 暴露 | Bot 无法读取；目标需由已有 32+1 清单或用户手工提供 |

“聊天区内的模型”如果是以 Bot、Webhook 或普通账号发出可见消息，Collector 能看到并保留作者类型；模型后台推理、未发送内容和不可见频道不能看到。

## 3. 选定架构

采用“主仓库 stdlib REST connector + OperationRunner 写操作”架构：

- `src/omni_hub/connectors/discord.py`：凭据校验、HTTP/rate-limit、端点和游标逻辑；不写文件。
- `src/omni_hub/discord_collector.py`：目标解析、线程发现、消息/pins/媒体编排和证据归档。
- `src/omni_hub/cli/discord.py`：`discord-probe`（只读）和 `discord-collect`（本地写）入口。
- `src/omni_hub/builtins.py`：注册两个 OperationSpec handler，使 policy 与 audit 覆盖写操作。

这不是 `Channel.listen/reply` 的实时消息适配器，因此不替换现有 Discord Channel stub；也不引入 `discord.py` 重依赖。

## 4. 凭据与安全不变量

1. CLI 不接受 `--token`，只接受 token 文件路径，默认 `~/.config/dce/bot-token`。
2. token 文件必须是普通文件、非符号链接、owner 为当前用户，且 group/other 无任何权限；默认路径期望 `0600`。
3. Authorization header 只在进程内构造；异常、日志、manifest、audit payload 和测试快照均不得包含 token。
4. 不使用会把 token 放进进程参数的 curl 命令，不把响应写入系统共享 `/tmp`。
5. 所有输出必须位于 OperationRunner workspace 内；路径逃逸直接失败。

## 5. 原始证据布局

每次运行使用新 run ID；旧 run 不覆盖：

```text
<workspace>/<output-dir>/runs/<run-id>/
├── request.json                 # 无密钥的输入快照与目标哈希
├── inventory/
│   ├── bot.json
│   ├── guild.json
│   ├── channels.json
│   └── targets.json
├── pages/<stream-key>/000001.json
├── assets/sha256/<aa>/<sha256>.<ext>
├── asset-index.jsonl
├── errors.jsonl
├── checkpoint.json             # 原子替换，可续跑游标
└── manifest.json               # 最终覆盖证明
```

- `pages` 保存 API 返回对象与请求元数据；页文件以排他创建写入，已存在但内容哈希不一致即失败。
- `assets` 以内容 SHA-256 去重，`asset-index.jsonl` 记录 source message、attachment/embed 字段、URL、MIME、字节数和下载结果。
- `checkpoint.json` 只描述下一游标和已完成流；不能替代 raw page。
- `manifest.json` 汇总所有流的页数、对象数、首末 ID/时间、终止原因、错误和哈希。

## 6. 枚举与分页

### 6.1 Guild 与目标

先抓 bot identity、guild metadata、完整 guild channel graph 和 guild active threads。目标文件支持 JSON，至少含 `guild_id` 和 `targets[{id,name,kind}]`；本次同时固化旧 32 个频道 + 1 个显式 thread 的审计基线，以及用户置顶清单解析出的 132-object expanded 快照，不把 token 写入其中。运行时仍须重新验证 ID、类型、parent 和可读性。

### 6.2 消息

对每个目标频道及发现的 thread 调用：

`GET /channels/{channel_id}/messages?limit=100&before={oldest_message_id}`

默认无页数上限；空页才是历史终点。测试/烟雾运行允许 `max_pages`，但对应流必须标记 `truncated_by_limit`，绝不能标记 complete。消息按 ID 去重只生成派生索引，raw page 保持不变。

### 6.3 Threads

发现源合并去重：

1. `GET /guilds/{guild_id}/threads/active`；
2. parent 消息中的完整 `thread` 对象；
3. `GET /channels/{parent_id}/threads/archived/public`，下一 `before` 为最后一项 `archive_timestamp`；
4. `GET /channels/{parent_id}/threads/archived/private`，同样用 `archive_timestamp`；
5. `GET /channels/{parent_id}/users/@me/threads/archived/private`，下一 `before` 为最后一个 thread ID；
6. 目标清单中显式 thread。

每个来源独立出 stream。403/404/权限缺失记录为 `blocked` 或 `not_found`，不等同于“没有 thread”。

### 6.4 Pins

对所有成功读取消息的频道/thread 独立调用新版 pins endpoint，`limit=50`，下一 `before` 为最后一项 `pinned_at`；以 `has_more=false` 作为终点。旧 `/pins` 端点不作为完成证明。

### 6.5 媒体

从 `attachments` 以及 embeds 的 `image`、`thumbnail`、`video` 中提取 URL。每个下载流式限额、计算哈希并原子落盘；HTTP 失败、大小超限或 MIME 不符均写入 asset index。当前消息的 signed CDN URL 已失效时可重新获取该消息刷新 URL后重试一次；仍失败则 manifest 不得把媒体层标完整。

## 7. 完整性状态

每个流只允许：

- `complete`：API 明确到达空页或 `has_more=false`；
- `truncated_by_limit`：烟雾/人工页数限制终止；
- `blocked`：403 或缺少权限；
- `not_found`：404；
- `failed`：不可恢复的协议、校验或 I/O 错误；
- `in_progress`：存在 checkpoint 尚未到终点。

run 只有在所有必需 stream 完成且所有必需媒体成功时才可为 `complete`；否则必须为 `partial`。manifest 另外列出 API 天生不可见项，防止把“API 覆盖完成”误写成“Discord 全部内容完成”。

## 8. 续跑与幂等

- 同一 run ID 可从 checkpoint 续跑；已落盘页先校验哈希，不重复覆盖。
- 新 run ID 始终重新观察 API，适合增量审计和比较。
- 429 按 `retry_after` 等待；可重试 5xx 使用有界指数退避；401 立即失败并提示 token 无效；403 记录端点级阻塞。
- 不再使用“文件大于 100 字节就跳过”作为完成判据。

## 9. 后续派生层

Collector 完成后再按独立版本迭代：

1. OCR/视觉：图片文字、图表价位、方向、标注和低置信度复核；
2. ASR：语音消息和实际可下载视频附件；Go Live 不在输入范围；
3. 事件化交易状态：开仓、限价等待/取消、加减仓、移动止损、分批 TP、平仓和引用链；
4. 按博主方案回测：自报结果与 K 线双证据、5m/15m/1h/4h/1d/3d/1w、多种成交假设、逐月统计和冲突审计；
5. 只有 raw evidence ID 可追溯的结果才能进入知识库与排行榜。

回测 K 线现有主路径为 `/Users/hzh/quant/market/`，代码位于 `/Users/hzh/Desktop/简历/个人知识库/agent-harness/quant/quant/`；旧 `/Users/hzh/discord-exports/replay/` 产物只作为待审证据，不能作为正确答案。

## 10. 首个交付验收

1. fake transport 单测覆盖消息、三种 archived thread、pins、429、403、媒体哈希、续跑和敏感信息不落盘。
2. CLI 写操作经 OperationRunner，输出路径逃逸被拒绝。
3. 对 1 个真实目标做限页烟雾运行，manifest 必须诚实显示 truncated。
4. 先用旧 32+1 做历史缺口对照，再对 132-object expanded 目标启动无上限 run；启动前输出目标核对表与预计规模，运行过程可中断续跑。
5. 全仓测试最终必须 0 failure、0 ResourceWarning；当前量化测试的重构后遗留路径单独修复并验证。

## 11. 周期采集与高质量总结

历史回填完成后增加增量模式和分层审阅，但不得让摘要层反向决定 raw 是否保存。

### 11.1 调度与增量

- raw collector 每小时通过 `TaskQueue` 的 python lane 执行一次；只读取 expanded allowlist 及其动态 thread 集，不采 DM。
- 每个频道保存已确认落盘的 newest message ID。增量抓取用 `after` 读取新消息，只有整页 raw、pins 与媒体索引成功落盘后才推进游标。
- 每日生成前一自然日摘要，每周生成前 7 日复盘；同时保留可指定任意 `[start,end)` 的 on-demand 命令。
- launchd 只负责入队；实际采集和总结均由现有 worker 消费，遵守后台任务必须经 `TaskQueue` 的约束。

### 11.2 “少过滤、直接阅读”原则

1. 所有消息与媒体先入 raw；关键词、作者评级和旧排行榜都不能作为采集过滤器。
2. 派生层只自动折叠可证明的重复：相同 message ID、相同 attachment SHA-256，以及正文/媒体完全相同的镜像消息；每个重复仍保留反向链接。
3. 每条非重复消息必须被批次 reader 消费并写入 coverage ledger；无法分类的进入 `other/unresolved`，不删除。
4. 每个唯一图片最终都进入视觉队列。OCR 只作辅助索引，不能替代模型直接看图；回填期可排优先级，但不能永久丢弃低优先项。
5. 视频附件抽取关键帧并做 ASR；语音消息做 ASR；Discord Go Live 因 API 不可得继续标为范围外。

### 11.3 证据化喊单状态

模型不是用单条正则提取“开仓”，而是沿作者、回复、引用和时间链维护事件状态：

`idea → entry_intent/limit_pending → filled_or_unconfirmed → add/reduce → stop_move/cancel → partial_tp/exit → postmortem`

每个事件保留 message ID、channel/thread ID、时间、作者、文本片段位置、媒体 SHA、视觉/OCR/ASR 证据、symbol、direction、order type、entry range、SL/TP、condition、timeframe 和置信度。限价单在没有成交证据时不得自动当作成交；博主自报结果与 K 线模拟分开保存再交叉审计。

### 11.4 分层模型审阅

```text
全消息/全媒体 coverage batches
  → channel/thread evidence cards
  → trader state timelines
  → interval summary + cross-channel consensus/conflicts
  → independent citation/coverage reviewer
```

最终摘要中的每个事实和交易事件必须回链 raw evidence ID。质量门至少包括：raw stream 完整性、100% 批次消费覆盖、媒体队列覆盖、无孤立 claim、矛盾列表、低置信度列表和第二遍 reviewer 结果。无法满足时输出 `partial`，不得用流畅文字掩盖缺口。
