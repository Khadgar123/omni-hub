from __future__ import annotations


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>万象中枢控制台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --side: #18202a;
      --side-ink: #eef3f8;
      --side-muted: #9aa8b7;
      --panel: #ffffff;
      --ink: #14212b;
      --muted: #637181;
      --line: #d9e1e8;
      --blue: #2457d6;
      --green: #13795b;
      --red: #b42318;
      --amber: #946200;
      --teal: #087f8c;
      --soft-blue: #edf3ff;
      --soft-green: #edf8f4;
      --soft-red: #fff1f0;
      --soft-amber: #fff8e6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    button, input, select, textarea { font: inherit; letter-spacing: 0; }
    button { cursor: pointer; }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 250px minmax(0, 1fr);
    }
    aside {
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      padding: 18px 14px;
      background: var(--side);
      color: var(--side-ink);
    }
    .brand {
      padding: 4px 8px 16px;
      border-bottom: 1px solid rgba(255,255,255,.12);
      margin-bottom: 12px;
    }
    .brand h1 { margin: 0; font-size: 18px; }
    .brand p { margin: 6px 0 0; color: var(--side-muted); font-size: 12px; }
    nav { display: grid; gap: 4px; }
    .nav-item {
      width: 100%;
      min-height: 38px;
      border: 0;
      border-radius: 7px;
      padding: 0 10px;
      background: transparent;
      color: var(--side-muted);
      text-align: left;
    }
    .nav-item.active { background: #263344; color: #fff; }
    .main { min-width: 0; }
    header {
      position: sticky;
      top: 0;
      z-index: 5;
      background: rgba(255,255,255,.94);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(12px);
    }
    .topbar {
      min-height: 64px;
      padding: 12px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }
    .title h2 { margin: 0; font-size: 18px; }
    .title p { margin: 3px 0 0; color: var(--muted); font-size: 12px; }
    .content {
      width: 100%;
      max-width: 1560px;
      padding: 20px 22px 34px;
      display: grid;
      gap: 16px;
    }
    .view { display: none; gap: 16px; }
    .view.active { display: grid; }
    .panel, .metric, .notice {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .notice {
      padding: 12px 14px;
      background: var(--soft-blue);
      color: #19376d;
      border-color: #b7c6e5;
    }
    .panel-head {
      min-height: 52px;
      padding: 13px 15px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .panel-head h3 { margin: 0; font-size: 15px; }
    .panel-body { padding: 14px; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(136px, 1fr));
      gap: 10px;
    }
    .metric { min-height: 82px; padding: 12px; }
    .metric span { color: var(--muted); font-size: 12px; }
    .metric strong { display: block; margin-top: 7px; font-size: 24px; line-height: 1; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
    }
    .workspace {
      display: grid;
      grid-template-columns: minmax(360px, 500px) minmax(0, 1fr);
      gap: 14px;
      align-items: start;
    }
    form { display: grid; gap: 10px; }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; min-width: 0; }
    label.wide, .wide { grid-column: 1 / -1; }
    input, select, textarea {
      width: 100%;
      min-width: 0;
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      background: #fff;
      color: var(--ink);
    }
    textarea { height: auto; min-height: 96px; resize: vertical; }
    .primary, .secondary, .choice, .action {
      min-height: 36px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 0 12px;
    }
    .primary {
      border-color: #1f4fc4;
      background: var(--blue);
      color: #fff;
      font-weight: 600;
    }
    .choice, .action {
      height: auto;
      min-height: 58px;
      padding: 10px 11px;
      text-align: left;
      display: grid;
      gap: 4px;
    }
    .choice.active { border-color: #7da2ee; background: var(--soft-blue); }
    .choice strong, .action strong { display: block; font-size: 14px; }
    .choice span, .action span { display: block; color: var(--muted); font-size: 12px; }
    .preset-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(138px, 1fr));
      gap: 8px;
    }
    .preset-select {
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
    }
    .advanced {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfd;
    }
    .advanced summary {
      cursor: pointer;
      font-weight: 600;
      color: var(--ink);
    }
    .advanced .form-grid { margin-top: 10px; }
    .split-stack {
      display: grid;
      gap: 14px;
    }
    .vendor-workspace {
      grid-template-columns: minmax(380px, 560px) minmax(0, 1fr);
    }
    .vendor-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .vendor-card {
      min-height: 74px;
      padding: 11px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      text-align: left;
      display: grid;
      gap: 5px;
    }
    .vendor-card.active {
      border-color: #7da2ee;
      background: var(--soft-blue);
    }
    .vendor-card strong { font-size: 15px; }
    .vendor-card span { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .config-list {
      display: grid;
      gap: 10px;
      padding: 12px;
    }
    .config-row {
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr) minmax(210px, auto);
      gap: 10px;
      align-items: center;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
    }
    .config-row.dragging { opacity: .55; }
    .drag-handle {
      min-height: 34px;
      display: grid;
      place-items: center;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      cursor: grab;
      font-size: 12px;
    }
    .config-main {
      min-width: 0;
      display: grid;
      gap: 5px;
    }
    .tag-row {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      align-items: center;
    }
    .script-box {
      min-height: 118px;
      margin: 0;
      padding: 12px;
      border-radius: 8px;
      overflow: auto;
      background: #101820;
      color: #e7edf3;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .segment {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #edf1f5;
    }
    .segment button {
      min-height: 34px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
    }
    .segment button.active {
      background: #fff;
      color: var(--ink);
      box-shadow: 0 1px 2px rgba(20, 33, 43, .08);
    }
    [data-config-mode] { display: none; }
    [data-config-mode].active { display: block; }
    .role-row {
      display: grid;
      grid-template-columns: 88px minmax(130px, 1fr) minmax(130px, 1fr) minmax(120px, 1fr) 72px;
      gap: 8px;
      align-items: end;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
    }
    .mode-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 8px;
    }
    .chart {
      min-height: 160px;
      display: grid;
      gap: 8px;
      align-content: end;
    }
    .bar-row {
      display: grid;
      grid-template-columns: minmax(110px, 180px) minmax(0, 1fr) 72px;
      gap: 8px;
      align-items: center;
    }
    .bar-track {
      height: 12px;
      border-radius: 999px;
      background: #e8edf2;
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      min-width: 2px;
      border-radius: inherit;
      background: var(--blue);
    }
    .table-box { min-width: 0; overflow: hidden; }
    .table-tools {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .table-tools input { max-width: 300px; }
    .table-scroll { overflow: auto; max-height: 520px; }
    table { width: 100%; min-width: 720px; border-collapse: collapse; table-layout: fixed; }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th { color: var(--muted); font-size: 12px; font-weight: 600; }
    .pager {
      min-height: 44px;
      padding: 8px 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .buttons { display: flex; gap: 8px; flex-wrap: wrap; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      border: 1px solid var(--line);
      background: #fff;
      white-space: nowrap;
    }
    .ok { color: var(--green); background: var(--soft-green); border-color: #bfe3d4; }
    .bad { color: var(--red); background: var(--soft-red); border-color: #f5c5c0; }
    .warn { color: var(--amber); background: var(--soft-amber); border-color: #efd492; }
    .info { color: var(--teal); background: #eaf7f8; border-color: #b8dde2; }
    .subtle { color: var(--muted); }
    .result {
      min-height: 260px;
      padding: 14px;
      background: #101820;
      color: #e7edf3;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
      overflow: auto;
      border-radius: 8px;
    }
    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 20;
      max-width: min(420px, calc(100vw - 36px));
      padding: 12px 14px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      box-shadow: 0 12px 32px rgba(21, 33, 43, .18);
      opacity: 0;
      transform: translateY(8px);
      pointer-events: none;
      transition: opacity .16s ease, transform .16s ease;
    }
    .toast.show { opacity: 1; transform: translateY(0); }
    @media (max-width: 1040px) {
      .shell { grid-template-columns: 1fr; }
      aside { position: static; height: auto; }
      nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .workspace, .form-grid, .role-row, .config-row, .vendor-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">
        <h1>万象中枢</h1>
        <p>本地模型配置、项目 Agent 与 Skill 控制台</p>
      </div>
      <nav>
        <button class="nav-item" data-view="overview">总览</button>
        <button class="nav-item" data-view="channels">模型配置</button>
        <button class="nav-item" data-view="projects">项目编组</button>
        <button class="nav-item" data-view="select">使用选择</button>
        <button class="nav-item" data-view="monitor">监控检测</button>
        <button class="nav-item" data-view="skills">Skills</button>
      </nav>
    </aside>
    <div class="main">
      <header>
        <div class="topbar">
          <div class="title">
            <h2 id="page-title">总览</h2>
            <p id="page-subtitle">官方厂商、调用队列、代理、用量和项目 AI 编组。</p>
          </div>
          <button class="secondary" id="refresh">刷新</button>
        </div>
      </header>

      <main class="content">
        <section class="view" data-view-panel="overview">
          <div class="notice">先按官方厂商配置：OpenAI、Claude、Qwen、DeepSeek、GLM、MiniMax。每个厂商可以保存多套配置，配置列表支持修改、流式健康检查、模型发现、复制脚本、查额度、监控和优先级排序；路由会按优先级选择，故障时自动切到下一级。</div>
          <div class="metrics" id="metrics"></div>
          <div class="grid">
            <button class="action" data-jump="channels"><strong>配置官方厂商</strong><span>填写 API Key，一键加入厂商配置列表</span></button>
            <button class="action" data-jump="projects"><strong>分配给项目 Agent</strong><span>为不同 agent 指定模型和 skills</span></button>
            <button class="action" data-jump="monitor"><strong>看延迟和额度</strong><span>检测、实时刷新和可视化监控</span></button>
          </div>
          <div class="panel table-box">
            <div class="panel-head"><h3>当前状态</h3><span class="subtle">模型配置、渠道、项目编组</span></div>
            <div id="overviewTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="channels">
          <div class="workspace vendor-workspace">
            <div class="split-stack">
              <div class="panel">
                <div class="panel-head"><h3>官方厂商</h3><span class="subtle">先选厂商，再填写密钥</span></div>
                <div class="panel-body">
                  <div class="vendor-grid" id="official-provider-list"></div>
                </div>
              </div>
              <div class="panel">
                <div class="panel-head"><h3>厂商配置</h3><span class="subtle">API Key 可填写；默认脚本可复制</span></div>
                <div class="panel-body">
                  <form id="official-form">
                    <input name="provider" id="official-provider" type="hidden" value="openai">
                    <div class="form-grid">
                      <label>配置 ID<input name="account_id" id="account-id" required></label>
                      <label>显示名称<input name="name" id="provider-name" required></label>
                      <label class="wide">API Key<input name="api_key" id="api-key" type="password" autocomplete="off" placeholder="可直接填；保存到 macOS Keychain，数据库只存 keychain 引用"></label>
                      <label>密钥引用<input name="secret_ref" id="secret-ref" placeholder="env:OPENAI_API_KEY"></label>
                      <label>代理连接<input name="proxy_url" id="proxy-url" placeholder="留空表示 unset；如 http://127.0.0.1:7890"></label>
                      <label>调用优先级<input name="priority" id="provider-priority" type="number" value="90"></label>
                      <label class="wide">模型列表<textarea name="model_ids" id="model-ids" placeholder="每行一个模型 ID"></textarea></label>
                      <label class="wide">额度入口<input name="quota_ref" id="quota-ref" placeholder="dashboard 或 quota API 引用"></label>
                    </div>
                    <details class="advanced">
                      <summary>高级配置</summary>
                      <div class="form-grid">
                        <label class="wide">接口地址<input name="base_url" id="base-url" placeholder="由预设填充；自定义时手动填写" required></label>
                        <label class="wide">能力<input name="capabilities" id="provider-capabilities" placeholder="text, tools, vision"></label>
                      </div>
                    </details>
                    <input name="status" type="hidden" value="active">
                    <div class="buttons">
                      <button class="primary">一键添加到列表</button>
                      <button type="button" class="secondary" id="fetch-models">发现模型</button>
                      <button type="button" class="secondary" id="test-official-draft">流式健康检查</button>
                      <button type="button" class="secondary" id="copy-script">复制默认脚本</button>
                    </div>
                    <pre class="script-box" id="script-preview"></pre>
                  </form>
                </div>
              </div>
            </div>
            <div class="split-stack">
              <div class="panel table-box">
                <div class="panel-head"><h3>厂商配置列表</h3><span class="subtle">拖拽或用上移/下移调整调用优先级</span></div>
                <div id="providerConfigList"></div>
              </div>
              <div class="panel table-box">
                <div class="panel-head"><h3>自动切换队列</h3><span class="subtle">上一个不可用时切下一级</span></div>
                <div id="fallbackQueueTable"></div>
              </div>
            </div>
          </div>
        </section>

        <section class="view" data-view-panel="projects">
          <div class="workspace">
            <div class="panel">
              <div class="panel-head"><h3>项目 AI 编组</h3><span class="subtle">不同角色可来自不同渠道</span></div>
              <div class="panel-body">
                <form id="project-form">
                  <label>项目 ID<input name="project_id" id="project-id" placeholder="auto-driving-research" required></label>
                  <div class="role-row" data-role="研究Agent" data-priority="95"></div>
                  <div class="role-row" data-role="代码Agent" data-priority="90"></div>
                  <div class="role-row" data-role="视觉Agent" data-priority="88"></div>
                  <div class="role-row" data-role="批量Agent" data-priority="60"></div>
                  <div class="role-row" data-role="备用Agent" data-priority="30"></div>
                  <div class="buttons">
                    <button class="primary">保存项目编组</button>
                    <button type="button" class="secondary" data-jump="select">去选择使用</button>
                  </div>
                </form>
              </div>
            </div>
            <div class="panel table-box">
              <div class="panel-head"><h3>项目配置</h3><span class="subtle">偏好与专属优先级</span></div>
              <div id="profilesTable"></div>
              <div id="overridesTable"></div>
            </div>
          </div>
        </section>

        <section class="view" data-view-panel="select">
          <div class="workspace">
            <div class="panel">
              <div class="panel-head"><h3>选择当前可用 AI</h3><span class="subtle">不用写长任务描述</span></div>
              <div class="panel-body">
                <form id="select-form">
                  <label>项目<select name="project_id" id="select-project"></select></label>
                  <div class="mode-row">
                    <button type="button" class="choice active" data-mode="text"><strong>文本</strong><span>总结、写作、检索</span></button>
                    <button type="button" class="choice" data-mode="code"><strong>代码</strong><span>工具调用和工程任务</span></button>
                    <button type="button" class="choice" data-mode="vision"><strong>多模态</strong><span>图片、视频帧、OCR</span></button>
                    <button type="button" class="choice" data-mode="batch"><strong>批处理</strong><span>低价和异步任务</span></button>
                  </div>
                  <div class="form-grid">
                    <label>输入 token 估计<input name="input_tokens" type="number" min="0" value="1000"></label>
                    <label>输出 token 估计<input name="output_tokens" type="number" min="0" value="800"></label>
                    <label>预算上限<input name="max_cost_usd" type="number" min="0" step="0.0001" placeholder="可选"></label>
                  </div>
                  <button class="primary">选择模型</button>
                </form>
              </div>
            </div>
            <div class="panel">
              <div class="panel-head"><h3>选择结果</h3><span class="subtle">包含代理使用状态</span></div>
              <pre class="result" id="select-result">{}</pre>
            </div>
          </div>
        </section>

        <section class="view" data-view-panel="monitor">
          <div class="grid">
            <div class="panel">
              <div class="panel-head"><h3>实时延迟</h3><button class="secondary" id="realtime-toggle">开始实时刷新</button></div>
              <div class="panel-body"><div class="chart" id="latency-chart"></div></div>
            </div>
            <div class="panel">
              <div class="panel-head"><h3>额度与代理</h3><span class="subtle">流式健康检查</span></div>
              <div class="panel-body subtle">健康检查会按渠道协议发起最小流式请求，收到首个 chunk 即判定连通；余额查询走厂商专用接口。代理配置跟随渠道调用，留空就是 unset。</div>
            </div>
          </div>
          <div class="panel table-box">
            <div class="panel-head"><h3>模型配置健康与用量</h3><button class="secondary" id="check-all">检测全部</button></div>
            <div id="monitorTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="skills">
          <div class="notice">CC Switch 的 Skill 设计重点不是“聊天里解释技能”，而是把 GitHub/ZIP/local skill 包安装、同步、备份和跨客户端写入统一管理。万象中枢后续应做 Skill registry、质量评分、冲突检测和项目推荐。</div>
          <div class="grid">
            <div class="panel"><div class="panel-head"><h3>安装来源</h3></div><div class="panel-body subtle">GitHub repo、ZIP、本地目录、私有仓库。</div></div>
            <div class="panel"><div class="panel-head"><h3>同步目标</h3></div><div class="panel-body subtle">Codex、Claude、Gemini、Cursor、OpenClaw，以及项目内 .agents/skills。</div></div>
            <div class="panel"><div class="panel-head"><h3>质量控制</h3></div><div class="panel-body subtle">版本、权限、冲突文件、依赖、最近更新时间、使用反馈和组合推荐。</div></div>
          </div>
        </section>
      </main>
    </div>
  </div>

  <div id="toast" class="toast" role="status" aria-live="polite"></div>

  <script>
    const PAGE_SIZE = 8;
    const views = {
      overview: ['总览', '官方厂商、调用队列、代理、用量和项目 AI 编组。'],
      channels: ['模型配置', '按官方厂商配置，并维护自动失败切换队列。'],
      projects: ['项目编组', '为项目 Agent 分配模型和 Skills。'],
      select: ['使用选择', '按项目和任务类型选择当前应使用的模型。'],
      monitor: ['监控检测', '检测模型配置、实时延迟、额度、代理和失败。'],
      skills: ['Skills', '技能安装、同步、质量评分和项目推荐的控制面。']
    };
    const state = {data: null, view: 'overview', officialProvider: null, mode: 'text', realtime: false, realtimeTimer: null, dragAccount: null};
    const tableState = {};

    const api = async (url, options = {}) => {
      const res = await fetch(url, options);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '请求失败');
      return data;
    };
    const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
    const escapeAttr = value => escapeHtml(value).replace(/`/g, '&#96;');
    const formPayload = form => Object.fromEntries([...new FormData(form).entries()]);
    const showToast = (message, tone = 'ok') => {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.className = `toast show ${tone}`;
      clearTimeout(showToast.timer);
      showToast.timer = setTimeout(() => toast.className = 'toast', 2400);
    };
    const pill = value => {
      const text = String(value || 'unknown');
      const cls = ['active', 'healthy'].includes(text) ? 'ok' : (['down', 'disabled'].includes(text) ? 'bad' : 'warn');
      return `<span class="pill ${cls}">${escapeHtml(text)}</span>`;
    };
    const proxyText = account => account.proxy_url ? account.proxy_url : 'unset';
    const quotaText = account => {
      const notes = account.notes || '';
      const found = notes.split('\n').find(line => /quota|额度|balance|dashboard/i.test(line));
      return found || '未配置';
    };
    const officialProviders = () => state.data?.official_providers || [];
    const providerPreset = slug => officialProviders().find(item => item.slug === slug || item.provider === slug);
    const providerName = slug => providerPreset(slug)?.name || slug;
    const accountModelIds = accountId => poolRows().filter(row => row.account_id === accountId).map(row => row.model_id);
    const accountPriority = accountId => {
      const rows = poolRows().filter(row => row.account_id === accountId);
      return rows.length ? Math.max(...rows.map(row => Number(row.priority || 0))) : 0;
    };
    const accountRows = () => (state.data?.accounts || []).map(account => ({
      account,
      models: accountModelIds(account.account_id),
      health: healthFor(account.account_id),
      priority: accountPriority(account.account_id),
      preset: providerPreset(account.provider)
    })).sort((a, b) => b.priority - a.priority || a.account.account_id.localeCompare(b.account.account_id));
    const searchText = row => JSON.stringify(row).toLowerCase();

    function setView(view) {
      state.view = views[view] ? view : 'overview';
      location.hash = state.view;
      document.querySelectorAll('[data-view-panel]').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.viewPanel === state.view);
      });
      document.querySelectorAll('[data-view]').forEach(button => {
        button.classList.toggle('active', button.dataset.view === state.view);
      });
      document.getElementById('page-title').textContent = views[state.view][0];
      document.getElementById('page-subtitle').textContent = views[state.view][1];
    }

    function renderDataTable(id, title, columns, rows) {
      const table = tableState[id] || {page: 1, query: ''};
      tableState[id] = table;
      const query = table.query.trim().toLowerCase();
      const filtered = query ? rows.filter(row => searchText(row).includes(query)) : rows;
      const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
      table.page = Math.min(Math.max(table.page, 1), pages);
      const visible = filtered.slice((table.page - 1) * PAGE_SIZE, table.page * PAGE_SIZE);
      const body = visible.length
        ? visible.map(row => '<tr>' + columns.map(col => `<td>${col.render ? col.render(row) : escapeHtml(row[col.key])}</td>`).join('') + '</tr>').join('')
        : `<tr><td colspan="${columns.length}" class="subtle">暂无数据</td></tr>`;
      document.getElementById(id).innerHTML = `
        <div class="table-tools">
          <div><strong>${escapeHtml(title)}</strong> <span class="subtle">${filtered.length} 条</span></div>
          <input data-table-search="${id}" value="${escapeAttr(table.query)}" placeholder="搜索">
        </div>
        <div class="table-scroll"><table>
          <thead><tr>${columns.map(col => `<th>${escapeHtml(col.label)}</th>`).join('')}</tr></thead>
          <tbody>${body}</tbody>
        </table></div>
        <div class="pager"><span class="subtle">第 ${table.page} / ${pages} 页</span>
          <div class="buttons">
            <button class="secondary" data-page="${id}" data-dir="-1">上一页</button>
            <button class="secondary" data-page="${id}" data-dir="1">下一页</button>
          </div>
        </div>`;
    }

    function modelById(id) {
      return (state.data?.models || []).find(model => model.model_id === id) || {};
    }
    function accountById(id) {
      return (state.data?.accounts || []).find(account => account.account_id === id) || {};
    }
    function healthFor(accountId) {
      return (state.data?.health || []).find(item => item.account_id === accountId && !item.model_id) || {};
    }
    function poolRows() {
      return (state.data?.abilities || []).map(ability => ({
        ...ability,
        account: accountById(ability.account_id),
        model: modelById(ability.model_id),
        health: healthFor(ability.account_id)
      }));
    }
    function renderOfficialProviders() {
      const presets = officialProviders();
      document.getElementById('official-provider-list').innerHTML = presets.map((preset, index) => (
        `<button type="button" class="vendor-card ${state.officialProvider?.slug === preset.slug ? 'active' : ''}" data-official-index="${index}">
          <strong>${escapeHtml(preset.name)}</strong>
          <span>${escapeHtml((preset.models || []).slice(0, 3).join(' / '))}</span>
          <span>${escapeHtml(preset.base_url)}</span>
        </button>`
      )).join('');
      if (!state.officialProvider && presets[0]) applyOfficialProvider(presets[0], false);
    }

    function applyOfficialProvider(preset, notify = true) {
      state.officialProvider = preset;
      document.getElementById('official-provider').value = preset.slug;
      document.getElementById('account-id').value = `${preset.slug}-main`;
      document.getElementById('provider-name').value = `${preset.name} Main`;
      document.getElementById('base-url').value = preset.base_url || '';
      document.getElementById('secret-ref').value = preset.secret_ref || '';
      document.getElementById('quota-ref').value = preset.quota_ref || '';
      document.getElementById('model-ids').value = (preset.models || []).join('\n');
      document.getElementById('provider-capabilities').value = (preset.capabilities || []).join(', ');
      document.getElementById('provider-priority').value = String(preset.rank || 90);
      document.getElementById('api-key').value = '';
      renderOfficialProviders();
      updateScriptPreview();
      if (notify) showToast(`已选择 ${preset.name}`);
    }

    function updateScriptPreview(account = null) {
      const preset = account ? providerPreset(account.provider) : state.officialProvider;
      if (!preset) return;
      const secretRef = account ? account.secret_ref : document.getElementById('secret-ref').value;
      const baseUrl = account ? account.base_url : document.getElementById('base-url').value;
      const proxyUrl = account ? account.proxy_url : document.getElementById('proxy-url').value;
      const envVar = secretRef?.startsWith('env:') ? secretRef.split(':', 2)[1] : (preset.env_var || 'PROVIDER_API_KEY');
      const baseEnv = preset.base_env_var || `${envVar.replace(/_API_KEY$/, '')}_BASE_URL`;
      const lines = [
        `export ${envVar}="<API_KEY>"`,
        `export ${baseEnv}="${baseUrl || preset.base_url || ''}"`,
      ];
      if (proxyUrl) {
        lines.push(`export HTTPS_PROXY="${proxyUrl}"`);
        lines.push(`export HTTP_PROXY="${proxyUrl}"`);
      } else {
        lines.push('unset HTTPS_PROXY HTTP_PROXY');
      }
      const script = lines.join('\n');
      const preview = document.getElementById('script-preview');
      if (preview && !account) preview.textContent = script;
      return script;
    }

    function renderSelectors() {
      const accounts = state.data?.accounts || [];
      const models = state.data?.models || [];
      const projects = state.data?.profiles || [];
      const accountOptions = accounts.map(item => `<option value="${escapeAttr(item.account_id)}">${escapeHtml(item.name || item.account_id)}</option>`).join('');
      const modelOptions = models.map(item => `<option value="${escapeAttr(item.model_id)}">${escapeHtml(item.display_name || item.model_id)}</option>`).join('');
      document.querySelectorAll('[data-role]').forEach(row => {
        const role = row.dataset.role;
        const priority = row.dataset.priority;
        row.innerHTML = `
          <strong>${escapeHtml(role)}</strong>
          <label>模型<select data-role-model>${modelOptions || '<option value="">先添加模型配置</option>'}</select></label>
          <label>渠道<select data-role-account>${accountOptions || '<option value="">先添加渠道</option>'}</select></label>
          <label>Skills<input data-role-skills placeholder="research, browser, github"></label>
          <label>优先级<input data-role-priority type="number" value="${escapeAttr(priority)}"></label>`;
      });
      document.getElementById('select-project').innerHTML =
        '<option value="">不指定项目</option>' + projects.map(item => `<option value="${escapeAttr(item.project_id)}">${escapeHtml(item.project_id)}</option>`).join('');
    }

    function renderMetrics() {
      const stats = state.data?.stats || {};
      const labels = {
        provider_accounts: 'API 渠道',
        model_catalog: '模型',
        route_abilities: '模型配置',
        project_route_profiles: '项目',
        project_route_overrides: '编组',
        provider_health: '检测记录',
        usage_request_logs: '调用日志'
      };
      document.getElementById('metrics').innerHTML = Object.entries(labels).map(([key, label]) => (
        `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(stats[key] || 0)}</strong></div>`
      )).join('');
    }

    function renderTables() {
      const data = state.data || {};
      const overview = [
        ...(data.accounts || []).map(item => ({type: '渠道', id: item.account_id, detail: `${item.name} · 代理 ${proxyText(item)}`, status: item.status})),
        ...(data.profiles || []).map(item => ({type: '项目', id: item.project_id, detail: `偏好渠道 ${(item.preferred_accounts || []).join(', ') || '未设置'}`, status: item.max_cost_usd ? `预算 ${item.max_cost_usd}` : 'active'}))
      ];
      renderDataTable('overviewTable', '当前状态', [
        {label: '类型', render: row => `<span class="pill info">${escapeHtml(row.type)}</span>`},
        {key: 'id', label: '名称'},
        {key: 'detail', label: '说明'},
        {label: '状态', render: row => pill(row.status)}
      ], overview);
      renderProviderConfigList();
      renderFallbackQueueTable();
      renderDataTable('profilesTable', '项目偏好', [
        {key: 'project_id', label: '项目'},
        {label: '能力', render: row => escapeHtml((row.default_capabilities || []).join(', '))},
        {key: 'max_cost_usd', label: '预算'},
        {label: '偏好渠道', render: row => escapeHtml((row.preferred_accounts || []).join(', '))}
      ], data.profiles || []);
      renderDataTable('overridesTable', '项目 AI 编组', [
        {key: 'project_id', label: '项目'},
        {key: 'account_id', label: '渠道'},
        {key: 'model_id', label: '模型'},
        {key: 'priority', label: '优先级'},
        {key: 'notes', label: 'Agent / Skills'}
      ], data.overrides || []);
      renderDataTable('monitorTable', '监控', [
        {key: 'account_id', label: '渠道'},
        {label: '状态', render: row => pill(row.health.status || 'unknown')},
        {label: '延迟', render: row => row.health.latency_ms == null ? '待探测' : `${row.health.latency_ms} ms`},
        {label: '模型数', render: row => String(poolRows().filter(item => item.account_id === row.account_id).length)},
        {label: '额度来源', render: row => escapeHtml(quotaText(row))},
        {label: '代理', render: row => escapeHtml(proxyText(row))},
        {label: '结果', render: row => escapeHtml(row.health.last_error || '')},
        {label: '操作', render: row => `<button class="secondary" data-check-account="${escapeAttr(row.account_id)}">健康检查</button>`}
      ], (data.accounts || []).map(account => ({...account, health: healthFor(account.account_id)})));
      renderLatencyChart(data.accounts || []);
    }

    function renderProviderConfigList() {
      const container = document.getElementById('providerConfigList');
      if (!container) return;
      const rows = accountRows();
      container.innerHTML = rows.length ? `<div class="config-list">${rows.map(row => {
        const account = row.account;
        const quota = quotaText(account);
        const modelText = row.models.join(', ') || '未配置模型';
        return `<div class="config-row" draggable="true" data-account-row="${escapeAttr(account.account_id)}">
          <div class="drag-handle" title="拖拽调整优先级">拖拽</div>
          <div class="config-main">
            <strong>${escapeHtml(providerName(account.provider))} · ${escapeHtml(account.name || account.account_id)}</strong>
            <div class="tag-row">
              ${pill(account.status)}
              ${pill(row.health.status || 'unknown')}
              <span class="pill info">优先级 ${escapeHtml(row.priority)}</span>
              <span class="pill info">代理 ${escapeHtml(proxyText(account))}</span>
            </div>
            <span class="subtle">配置 ID：${escapeHtml(account.account_id)} · 模型：${escapeHtml(modelText)} · 额度：${escapeHtml(quota)}</span>
          </div>
          <div class="buttons">
            <button class="secondary" data-account-action="edit" data-account-id="${escapeAttr(account.account_id)}">修改</button>
            <button class="secondary" data-account-action="test" data-account-id="${escapeAttr(account.account_id)}">健康检查</button>
            <button class="secondary" data-account-action="copy" data-account-id="${escapeAttr(account.account_id)}">复制</button>
            <button class="secondary" data-account-action="quota" data-account-id="${escapeAttr(account.account_id)}">查额度</button>
            <button class="secondary" data-account-action="monitor" data-account-id="${escapeAttr(account.account_id)}">监控</button>
            <button class="secondary" data-account-action="up" data-account-id="${escapeAttr(account.account_id)}">上移</button>
            <button class="secondary" data-account-action="down" data-account-id="${escapeAttr(account.account_id)}">下移</button>
          </div>
        </div>`;
      }).join('')}</div>` : '<div class="panel-body subtle">暂无厂商配置。先在左侧选择官方厂商并添加到列表。</div>';
    }

    function renderFallbackQueueTable() {
      const rows = poolRows().slice().sort((a, b) => Number(b.priority || 0) - Number(a.priority || 0) || a.account_id.localeCompare(b.account_id));
      const queue = rows.map((row, index) => ({
        ...row,
        order: index + 1,
        next: rows[index + 1] ? `${rows[index + 1].account.name || rows[index + 1].account_id} / ${rows[index + 1].model_id}` : '无'
      }));
      renderDataTable('fallbackQueueTable', '自动切换队列', [
        {key: 'order', label: '顺序'},
        {label: '厂商', render: row => escapeHtml(providerName(row.account.provider))},
        {label: '配置', render: row => escapeHtml(row.account.name || row.account_id)},
        {key: 'model_id', label: '模型'},
        {key: 'priority', label: '优先级'},
        {label: '健康', render: row => pill(row.health.status || 'unknown')},
        {label: '延迟', render: row => row.health.latency_ms == null ? '待检测' : `${row.health.latency_ms} ms`},
        {key: 'next', label: '失败后切换'}
      ], queue);
    }

    function renderLatencyChart(accounts) {
      const rows = accounts.map(account => ({account, health: healthFor(account.account_id)}));
      const max = Math.max(1000, ...rows.map(row => row.health.latency_ms || 0));
      document.getElementById('latency-chart').innerHTML = rows.length ? rows.map(row => {
        const latency = row.health.latency_ms;
        const width = latency == null ? 2 : Math.max(2, Math.min(100, latency / max * 100));
        return `<div class="bar-row">
          <span>${escapeHtml(row.account.name || row.account.account_id)}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
          <span class="subtle">${latency == null ? '-' : `${latency}ms`}</span>
        </div>`;
      }).join('') : '<div class="subtle">暂无检测数据</div>';
    }

    async function refresh(showMessage = false) {
      state.data = await api('/api/state');
      renderOfficialProviders();
      renderSelectors();
      renderMetrics();
      renderTables();
      if (showMessage) showToast('数据已刷新');
    }

    async function checkAccount(accountId) {
      if (!accountId) throw new Error('请先选择配置');
      const data = await api('/api/stream-check', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId})
      });
      await refresh();
      const model = data.stream_check?.model_id || '';
      const latency = data.stream_check?.responseTimeMs;
      showToast(`健康检查完成：${data.stream_check?.status || data.health.status}${model ? ` · ${model}` : ''}${latency == null ? '' : ` · ${latency}ms`}`);
    }

    async function updateAccountPriority(accountId, priority) {
      const rows = poolRows().filter(item => item.account_id === accountId);
      if (!rows.length) throw new Error('该配置还没有模型');
      for (const row of rows) {
        await api('/api/route-abilities', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            account_id: accountId,
            model_id: row.model_id,
            priority,
            weight: row.weight || 1,
            model_mapping: row.model_mapping || row.model_id,
            enabled: row.enabled !== false,
            notes: row.notes || ''
          })
        });
      }
    }

    async function reorderAccounts(sourceId, targetId = null, direction = 0) {
      const rows = accountRows();
      const from = rows.findIndex(row => row.account.account_id === sourceId);
      if (from < 0) throw new Error('配置不存在');
      let to = targetId ? rows.findIndex(row => row.account.account_id === targetId) : from + direction;
      to = Math.max(0, Math.min(rows.length - 1, to));
      if (from === to) return;
      const [moved] = rows.splice(from, 1);
      rows.splice(to, 0, moved);
      for (let index = 0; index < rows.length; index += 1) {
        await updateAccountPriority(rows[index].account.account_id, Math.max(100 - index * 10, 0));
      }
      await refresh();
      showToast('调用优先级已更新');
    }

    function editAccount(accountId) {
      const account = accountById(accountId);
      if (!account.account_id) throw new Error('配置不存在');
      const preset = providerPreset(account.provider) || officialProviders()[0];
      state.officialProvider = preset;
      document.getElementById('official-provider').value = preset.slug;
      document.getElementById('account-id').value = account.account_id;
      document.getElementById('provider-name').value = account.name || account.account_id;
      document.getElementById('base-url').value = account.base_url || preset.base_url || '';
      document.getElementById('secret-ref').value = account.secret_ref || preset.secret_ref || '';
      document.getElementById('proxy-url').value = account.proxy_url || '';
      document.getElementById('quota-ref').value = quotaText(account).replace(/^quota_ref=/, '');
      document.getElementById('model-ids').value = accountModelIds(accountId).join('\n');
      document.getElementById('provider-capabilities').value = '';
      document.getElementById('provider-priority').value = String(accountPriority(accountId) || preset.rank || 90);
      document.getElementById('api-key').value = '';
      renderOfficialProviders();
      updateScriptPreview();
      showToast('已载入配置，可修改后保存');
    }

    async function copyText(text, message) {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        showToast(message);
      } else {
        showToast('浏览器不支持自动复制，请手动复制脚本', 'warn');
      }
    }

    async function copyAccountScript(accountId) {
      const account = accountById(accountId);
      if (!account.account_id) throw new Error('配置不存在');
      await copyText(updateScriptPreview(account), '默认脚本已复制');
    }

    async function showQuota(accountId) {
      const account = accountById(accountId);
      if (!account.account_id) throw new Error('配置不存在');
      const data = await api('/api/balance-check', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId})
      });
      if (!data.success) {
        const fallback = quotaText(account);
        showToast(data.error === 'unsupported balance provider' && fallback !== '未配置' ? `该厂商未接入余额接口；可去 ${fallback}` : `额度查询失败：${data.error || '未支持'}`, 'warn');
        return;
      }
      const rows = data.data || [];
      const text = rows.map(row => `${row.plan_name || '余额'} ${row.remaining ?? '-'} ${row.unit || ''}`).join('；') || '余额接口无数据';
      showToast(`额度查询完成：${text}`);
    }

    async function fetchModelsForDraft() {
      const payload = formPayload(document.getElementById('official-form'));
      const data = await api('/api/model-fetch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const models = data.models || [];
      if (!models.length) {
        showToast('没有发现可用模型', 'warn');
        return;
      }
      document.getElementById('model-ids').value = models.map(item => item.id).join('\n');
      showToast(`已发现 ${models.length} 个模型`);
    }

    async function saveOfficialConfig(notify = true) {
      const payload = formPayload(document.getElementById('official-form'));
      const data = await api('/api/official-provider-config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      document.getElementById('api-key').value = '';
      await refresh();
      if (notify) {
        showToast(data.secret_mode === 'keychain' ? '配置已加入列表；API Key 已写入 Keychain，数据库只保存引用' : '配置已加入列表');
      }
      return data;
    }

    document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
    document.getElementById('refresh').addEventListener('click', () => refresh(true));
    window.addEventListener('hashchange', () => setView(location.hash.replace('#', '') || 'overview'));
    document.addEventListener('click', event => {
      const jump = event.target.closest('[data-jump]');
      if (jump) setView(jump.dataset.jump);
      const officialButton = event.target.closest('[data-official-index]');
      if (officialButton) applyOfficialProvider(officialProviders()[Number(officialButton.dataset.officialIndex)]);
      const accountAction = event.target.closest('[data-account-action]');
      if (accountAction) {
        const accountId = accountAction.dataset.accountId;
        const action = accountAction.dataset.accountAction;
        if (action === 'edit') editAccount(accountId);
        if (action === 'test') checkAccount(accountId).catch(err => showToast(err.message, 'bad'));
        if (action === 'copy') copyAccountScript(accountId).catch(err => showToast(err.message, 'bad'));
        if (action === 'quota') showQuota(accountId).catch(err => showToast(err.message, 'bad'));
        if (action === 'monitor') setView('monitor');
        if (action === 'up') reorderAccounts(accountId, null, -1).catch(err => showToast(err.message, 'bad'));
        if (action === 'down') reorderAccounts(accountId, null, 1).catch(err => showToast(err.message, 'bad'));
      }
      const modeButton = event.target.closest('[data-mode]');
      if (modeButton) {
        state.mode = modeButton.dataset.mode;
        document.querySelectorAll('[data-mode]').forEach(item => item.classList.toggle('active', item === modeButton));
        showToast(`已选择${modeButton.querySelector('strong').textContent}模式`);
      }
      const checkButton = event.target.closest('[data-check-account]');
      if (checkButton) checkAccount(checkButton.dataset.checkAccount).catch(err => showToast(err.message, 'bad'));
      const pageButton = event.target.closest('[data-page]');
      if (pageButton) {
        const id = pageButton.dataset.page;
        tableState[id] = tableState[id] || {page: 1, query: ''};
        tableState[id].page += Number(pageButton.dataset.dir || 0);
        renderTables();
      }
    });
    document.addEventListener('input', event => {
      const id = event.target.dataset.tableSearch;
      if (!id) {
        if (event.target.closest('#official-form')) updateScriptPreview();
        return;
      }
      tableState[id] = tableState[id] || {page: 1, query: ''};
      tableState[id].query = event.target.value;
      tableState[id].page = 1;
      renderTables();
      const input = document.querySelector(`[data-table-search="${id}"]`);
      if (input) input.focus();
    });
    document.addEventListener('dragstart', event => {
      const row = event.target.closest('[data-account-row]');
      if (!row) return;
      state.dragAccount = row.dataset.accountRow;
      row.classList.add('dragging');
      event.dataTransfer.effectAllowed = 'move';
    });
    document.addEventListener('dragend', event => {
      const row = event.target.closest('[data-account-row]');
      if (row) row.classList.remove('dragging');
      state.dragAccount = null;
    });
    document.addEventListener('dragover', event => {
      if (event.target.closest('[data-account-row]')) event.preventDefault();
    });
    document.addEventListener('drop', event => {
      const row = event.target.closest('[data-account-row]');
      if (!row || !state.dragAccount) return;
      event.preventDefault();
      reorderAccounts(state.dragAccount, row.dataset.accountRow).catch(err => showToast(err.message, 'bad'));
    });

    document.getElementById('official-form').addEventListener('submit', async event => {
      event.preventDefault();
      try {
        await saveOfficialConfig(true);
      } catch (err) {
        showToast(err.message, 'bad');
      }
    });
    document.getElementById('test-official-draft').addEventListener('click', async () => {
      try {
        const data = await saveOfficialConfig(false);
        await checkAccount(data.account.account_id);
      } catch (err) {
        showToast(err.message, 'bad');
      }
    });
    document.getElementById('fetch-models').addEventListener('click', async () => {
      try {
        await fetchModelsForDraft();
      } catch (err) {
        showToast(err.message, 'bad');
      }
    });
    document.getElementById('copy-script').addEventListener('click', async () => {
      try {
        await copyText(updateScriptPreview(), '默认脚本已复制');
      } catch (err) {
        showToast(err.message, 'bad');
      }
    });
    document.getElementById('realtime-toggle').addEventListener('click', event => {
      state.realtime = !state.realtime;
      event.target.textContent = state.realtime ? '停止实时刷新' : '开始实时刷新';
      if (state.realtime) {
        state.realtimeTimer = setInterval(() => refresh(false), 10000);
        showToast('实时刷新已开启');
      } else {
        clearInterval(state.realtimeTimer);
        showToast('实时刷新已停止');
      }
    });
    document.getElementById('check-all').addEventListener('click', async () => {
      for (const account of state.data.accounts || []) {
        await checkAccount(account.account_id);
      }
    });
    document.getElementById('project-form').addEventListener('submit', async event => {
      event.preventDefault();
      const projectId = document.getElementById('project-id').value;
      const routes = [...document.querySelectorAll('[data-role]')].map(row => ({
        role: row.dataset.role,
        account_id: row.querySelector('[data-role-account]').value,
        model_id: row.querySelector('[data-role-model]').value,
        skills: row.querySelector('[data-role-skills]').value,
        priority: row.querySelector('[data-role-priority]').value,
        weight: 1,
        enabled: true
      })).filter(item => item.account_id && item.model_id);
      try {
        await api('/api/project-profiles', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({project_id: projectId, default_capabilities: ['text'], preferred_accounts: [...new Set(routes.map(item => item.account_id))]})
        });
        await api('/api/project-group', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project_id: projectId, routes})});
        await refresh();
        showToast('项目编组已保存');
      } catch (err) {
        showToast(err.message, 'bad');
      }
    });
    document.getElementById('select-form').addEventListener('submit', async event => {
      event.preventDefault();
      const payload = {...formPayload(event.currentTarget), mode: state.mode};
      try {
        const data = await api('/api/agent-select', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        const invocation = data.invocation || {};
        document.getElementById('select-result').textContent = JSON.stringify({
          status: data.status,
          channel: invocation.account_name || invocation.account_id,
          model: invocation.provider_model_id || invocation.model_id,
          proxy: invocation.proxy_mode === 'configured' ? invocation.proxy_url : 'unset',
          estimated_cost_usd: invocation.estimated_cost_usd,
          warnings: invocation.warnings || [],
          error: data.error || ''
        }, null, 2);
        showToast(data.status === 'planned' ? '已选择可用模型' : '没有可用模型', data.status === 'planned' ? 'ok' : 'bad');
      } catch (err) {
        showToast(err.message, 'bad');
      }
    });

    setView(location.hash.replace('#', '') || 'overview');
    refresh();
  </script>
</body>
</html>
"""
