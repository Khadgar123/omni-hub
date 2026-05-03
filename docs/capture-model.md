# 捕获与入库模型

v0.2 的目标是先把外部信息稳定进入万象中枢，而不是一开始就追求复杂总结和自动建图。

## 数据分层

```text
source
└── capture operation
    ├── raw content       # .omni/content/<content_id>/raw.*
    ├── metadata          # .omni/content/<content_id>/metadata.json
    └── inbox card        # vault/00_Inbox/<timestamp>-<content_id>.md
```

## 当前支持

- 普通网页 URL 抓取
- HTML title / description / canonical URL 提取
- HTML 正文粗提取
- YouTube URL 识别和 video id 解析
- 不联网模式：只生成 URL 元数据卡片

## 暂不做的事

- 不直接绕过平台限制抓取登录后内容。
- 不把 B站、小红书、X 评论区爬虫作为核心能力。
- 不让 Agent 直接污染稳定知识图谱。

后续平台采集都应先进入 Raw layer，再由 Proposal layer 生成摘要、实体、关系建议，最后进入 Canonical layer。
