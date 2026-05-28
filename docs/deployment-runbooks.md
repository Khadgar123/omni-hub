# Deployment Runbooks — v0.43+

部署运维项的最小路径,按 ROI 排序。每条都是**可选**的(主仓默认不依赖)。

---

## 1. metapi + ccLoad (API 网关启动)

**作用**:把现在的 API 管理从 *configured* 推到 *reachable*。统一 LLM channel、cost cap、failover。

**当前状态** (`omni-hub api-management-status`):
- ✅ 配置文件就位 (`api-management/defaults.json`)
- ✅ 仓库 fork pinned (`api-management/ccLoad` Go 服务 / `api-management/metapi` 控制面)
- ❌ 服务未运行 (reachable=false)

### 启动方式 A: 直接跑 Go 二进制 (5 分钟)

```bash
cd /Users/hzh/Desktop/简历/个人知识库/api-management/ccLoad
go build -o ccload ./cmd/ccload   # 第一次要 Go 1.21+
./ccload --addr :8080 --db .omni/ccload.sqlite3 &
# 验证
curl -s http://localhost:8080/v1/models | jq .
```

之后 omni-hub LLMJudge 默认就走 ccload 路由(`OMNI_CCLOAD_BASE=http://localhost:8080`)。

### 启动方式 B: launchd 持久化

```bash
cat > ~/Library/LaunchAgents/com.omni-hub.ccload.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.omni-hub.ccload</string>
  <key>ProgramArguments</key>
    <array>
      <string>/Users/hzh/Desktop/简历/个人知识库/api-management/ccLoad/ccload</string>
      <string>--addr</string><string>:8080</string>
    </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.omni-hub.ccload.plist
```

metapi 同样模式,但默认 port 8081。

---

## 2. RSSHub 自托管 (中文社媒)

**作用**:解锁小红书 / 微博 / 知乎 / 公众号 / Truth Social 的 RSS 流量。
omni-hub 现有 connector `gov_cn` / `truth_social` / `wechat_mp` 等都通过 RSSHub。当前用公共 `rsshub.app`(不稳),自托管后稳定。

### docker-compose 一键

```yaml
# ~/rsshub/docker-compose.yml
version: '3'
services:
  rsshub:
    image: diygod/rsshub:latest
    ports: ["1200:1200"]
    environment:
      CACHE_TYPE: redis
      REDIS_URL: 'redis://redis:6379/'
      # 可选: 小红书/微博需要 cookie
      # XHS_COOKIE: 'a1=...'
    depends_on: [redis]
  redis:
    image: redis:alpine
    volumes: [redis-data:/data]
volumes:
  redis-data:
```

```bash
docker compose up -d
# 验证
curl http://localhost:1200/truthsocial/realDonaldTrump
```

然后 omni-hub:

```bash
export OMNI_RSSHUB_BASE=http://localhost:1200
# 或者写进 ~/.zshrc 持久化
```

`truth_social` connector 的 check() 立刻从 warn → ok。

---

## 3. SearXNG 自托管 (broad search)

**作用**:免费、自主索引的 metasearch (聚合 Google/Bing/DuckDuckGo/Brave/Yandex)。
作为 Tavily/Exa/Brave 之外的 Tier 0 broad_search 兜底。

### docker 一键

```bash
docker run --rm -d \
  -p 8888:8080 \
  -v ~/searxng:/etc/searxng \
  -e BASE_URL=http://localhost:8888 \
  -e INSTANCE_NAME=omni-hub-searxng \
  searxng/searxng
# 验证
curl 'http://localhost:8888/search?q=Karpathy&format=json' | jq '.results | length'
```

要进 omni-hub cascade 还需写一个 SearxngSource connector (~80 行,跟其他 search connector 同模式)。**当前未做**——优先级看你需求,Tavily/Exa 已经够用。

---

## 4. Crawl4AI (动态网页 → LLM-ready markdown)

**作用**:静态网页 Trafilatura 已经够用,SPA / JS-heavy 站点 (e.g. Notion、Twitter web、企业产品页) 需要 headless browser。

### 安装

```bash
/Users/hzh/opt/anaconda3/envs/quant/bin/python3.12 -m pip install crawl4ai
crawl4ai-setup       # 装 playwright + chromium (~200MB)
```

### 单文件 broker (放 agent-harness)

```python
# agent-harness/integrations/crawl4ai/broker.py
import sys, json, asyncio
from crawl4ai import AsyncWebCrawler

async def crawl(url):
    async with AsyncWebCrawler() as c:
        result = await c.arun(url=url)
        return {"url": url, "title": result.metadata.get("title",""),
                "markdown": result.markdown[:50000],
                "char_count": len(result.markdown)}

if __name__ == "__main__":
    print(json.dumps(asyncio.run(crawl(sys.argv[1])), ensure_ascii=False))
```

```bash
python broker.py https://www.dwarkesh.com/p/andrej-karpathy
```

omni-hub 端做个 subprocess wrapper connector `crawl4ai`,跟 `xhs` / `bilibili` 同模式。**当前未做**,等用户决定要不要。

---

## 5. metapi 启动 (Provider Router GUI)

`metapi` 是 control plane:列模型、看余额、配 LLM provider key。Web UI 在 :8081。

```bash
cd api-management/metapi
go build -o metapi ./cmd/metapi
./metapi --addr :8081 &
open http://localhost:8081
```

第一次进会问 admin password — 设一个写进 `.omni/secrets.json::omni-hub/api/metapi/admin_pass`。

---

## 6. UCDP / Tushare / OpenCorporates token 申请路径

| Token | 申请 URL | 时间 | 已有 fallback |
|---|---|---|---|
| UCDP | 发邮件给 Mert Can Yilmaz @ UCDP | 1-3 工作日 | secrets `omni-hub/api/ucdp/default` 接好 |
| Tushare | https://tushare.pro/register | 即得,但有 500 积分门槛 | secrets `omni-hub/api/tushare/default` 接好 |
| OpenCorporates | https://opencorporates.com/api_accounts/new | 即得(注册时可能 Cloudflare WAF 拦) | secrets `omni-hub/api/opencorporates/default` 接好 |
| ACLED | https://acleddata.com/register/ | 24h 内邮件审批 | env `ACLED_EMAIL` / `ACLED_KEY` |
| Brave Search | https://api.search.brave.com/app/keys | 即得(要绑卡) | secrets `omni-hub/api/brave/default` 接好 |
| Pixabay | https://pixabay.com/api/docs/ | 即得 | secrets `omni-hub/api/pixabay/default` 接好 |

拿到后:
```bash
PY=/Users/hzh/opt/anaconda3/envs/quant/bin/python3.12
$PY -c "
import sys; sys.path.insert(0,'src')
from omni_hub.secrets import store_api_key
print(store_api_key('api/<service>/default', input('paste: ').strip()))
"
```

---

## 部署优先级建议

| 序 | 部署项 | 工作量 | 解锁能力 |
|---|---|---|---|
| 1 | UCDP token (邮件申请) | 5 分钟 + 等 1-3 天 | 高质量冲突事件数据 |
| 2 | DATA_GOV (已配 ✅) | done | congress + regulations live |
| 3 | metapi + ccLoad | 30 分钟 (Go build + launchd) | API gateway, cost cap, failover |
| 4 | RSSHub self-host | 1 小时 (Docker) | 中文社媒 + Truth Social 稳定 |
| 5 | Crawl4AI broker | 1 小时 (broker + connector wrapper) | SPA / 动态网页捕获 |
| 6 | SearXNG | 30 分钟 (Docker + ~80 行 connector) | broad_search 兜底 |

我推荐顺序: **先发 UCDP 邮件 → 跑 metapi/ccLoad → 装 RSSHub → 其他按需**。
