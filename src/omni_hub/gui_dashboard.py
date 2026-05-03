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
    .role-row {
      display: grid;
      grid-template-columns: 96px minmax(130px, 1fr) minmax(130px, 1fr) 72px;
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
      .workspace, .form-grid, .role-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">
        <h1>万象中枢</h1>
        <p>本地 AI 渠道、模型池与 Skill 控制台</p>
      </div>
      <nav>
        <button class="nav-item" data-view="overview">总览</button>
        <button class="nav-item" data-view="channels">渠道模型</button>
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
            <p id="page-subtitle">渠道、模型池、代理、用量和项目 AI 编组。</p>
          </div>
          <button class="secondary" id="refresh">刷新</button>
        </div>
      </header>

      <main class="content">
        <section class="view" data-view-panel="overview">
          <div class="notice">渠道可以是官方 API、中转站、公司网关或本地代理网关。每个渠道下面挂一组模型池；项目再从多个渠道和模型中编组，不需要每次写长文本预演。</div>
          <div class="metrics" id="metrics"></div>
          <div class="grid">
            <button class="action" data-jump="channels"><strong>添加渠道并导入模型池</strong><span>支持你列出的 CC Switch 类中转站预设</span></button>
            <button class="action" data-jump="projects"><strong>为项目编组多个 AI</strong><span>主力、快速、视觉、批量、备用可以来自不同 API</span></button>
            <button class="action" data-jump="monitor"><strong>检测和监控</strong><span>连接状态、代理、延迟、失败和用量入口</span></button>
          </div>
          <div class="panel table-box">
            <div class="panel-head"><h3>当前状态</h3><span class="subtle">渠道、模型池、项目编组</span></div>
            <div id="overviewTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="channels">
          <div class="workspace">
            <div class="split-stack">
              <div class="panel">
                <div class="panel-head"><h3>按中转站配置</h3><span class="subtle">先管理密钥、代理、额度和健康</span></div>
                <div class="panel-body">
                  <form id="channel-form">
                    <div class="preset-select">
                      <label>中转站厂商<select id="preset-select"></select></label>
                      <div class="preset-list" id="preset-list"></div>
                    </div>
                    <div class="form-grid">
                      <label>渠道 ID<input name="account_id" id="account-id" required></label>
                      <label>Provider 标识<input name="provider" id="provider-id" required></label>
                      <label class="wide">显示名称<input name="name" id="provider-name" required></label>
                      <label>密钥引用<input name="secret_ref" id="secret-ref" placeholder="env:OPENROUTER_API_KEY"></label>
                      <label>代理连接<input name="proxy_url" id="proxy-url" placeholder="留空表示 unset；如 http://127.0.0.1:7890"></label>
                    </div>
                    <details class="advanced">
                      <summary>高级配置</summary>
                      <div class="form-grid">
                        <label class="wide">接口地址<input name="base_url" id="base-url" placeholder="由预设填充；自定义时手动填写" required></label>
                      </div>
                    </details>
                    <input name="status" type="hidden" value="active">
                    <input name="account_group" type="hidden" value="default">
                    <div class="buttons">
                      <button class="primary">保存渠道</button>
                      <button type="button" class="secondary" id="check-channel">检测渠道</button>
                    </div>
                  </form>
                </div>
              </div>
              <div class="panel">
                <div class="panel-head"><h3>按模型配置</h3><span class="subtle">模型 ID 默认手写，别名只作填充</span></div>
                <div class="panel-body">
                  <form id="model-form">
                    <div class="preset-list" id="model-preset-list"></div>
                    <div class="form-grid">
                      <label>挂到渠道<select name="account_id" id="model-account"></select></label>
                      <label>模型 ID<input name="model_id" id="model-id" placeholder="例如 claude-sonnet-4.5" required></label>
                      <label>模型别名<input name="display_name" id="model-name" placeholder="可选；没有明确别名可留空"></label>
                      <label>Provider 模型名<input name="model_mapping" id="model-mapping" placeholder="和模型 ID 不同时填写"></label>
                      <label>能力<input name="capabilities" id="model-capabilities" placeholder="text, tools, vision"></label>
                      <label>优先级<input name="priority" type="number" value="70"></label>
                    </div>
                    <button class="primary">添加模型到渠道</button>
                  </form>
                </div>
              </div>
            </div>
            <div class="panel table-box">
              <div class="panel-head"><h3>渠道与模型池</h3><span class="subtle">一个渠道可挂多个模型</span></div>
              <div id="channelsTable"></div>
              <div id="poolTable"></div>
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
                  <div class="role-row" data-role="主力" data-priority="95"></div>
                  <div class="role-row" data-role="快速" data-priority="75"></div>
                  <div class="role-row" data-role="视觉" data-priority="88"></div>
                  <div class="role-row" data-role="批量" data-priority="60"></div>
                  <div class="role-row" data-role="备用" data-priority="30"></div>
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
              <div class="panel-head"><h3>检测机制</h3><span class="subtle">阶段 1</span></div>
              <div class="panel-body subtle">检测会验证渠道、模型池、密钥引用、代理设置，并尝试探测接口延迟。后续 worker 会把真实调用延迟、错误率和额度写入同一张监控表。</div>
            </div>
            <div class="panel">
              <div class="panel-head"><h3>代理规则</h3><span class="subtle">调用时生效</span></div>
              <div class="panel-body subtle">渠道配置了代理就随调用传递代理；留空则明确 unset，不继承该渠道的代理配置。</div>
            </div>
          </div>
          <div class="panel table-box">
            <div class="panel-head"><h3>渠道健康与用量</h3><button class="secondary" id="check-all">检测全部</button></div>
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
      overview: ['总览', '渠道、模型池、代理、用量和项目 AI 编组。'],
      channels: ['渠道模型', '从预设或自定义端点创建 API 渠道，并为渠道导入模型池。'],
      projects: ['项目编组', '为一个项目配置多个 AI 角色和不同来源的模型。'],
      select: ['使用选择', '按项目和任务类型选择当前应使用的模型。'],
      monitor: ['监控检测', '检测渠道连通性、代理、延迟、失败和用量入口。'],
      skills: ['Skills', '技能安装、同步、质量评分和项目推荐的控制面。']
    };
    const state = {data: null, view: 'overview', preset: null, mode: 'text'};
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

    function renderPresets() {
      const presets = [...(state.data?.provider_presets || [])].sort((a, b) => (b.rank || 0) - (a.rank || 0));
      const hot = presets.slice(0, 8);
      document.getElementById('preset-select').innerHTML = presets.map((preset, index) => (
        `<option value="${index}">${escapeHtml(preset.name)}${preset.base_url ? '' : '（需补接口）'}</option>`
      )).join('');
      document.getElementById('preset-list').innerHTML = hot.map((preset, index) => (
        `<button type="button" class="choice ${index === 0 ? 'active' : ''}" data-preset-index="${presets.indexOf(preset)}">
          <strong>${escapeHtml(preset.name)}</strong>
          <span>${preset.category === 'official' ? '官方' : '热门'} · ${preset.base_url ? '已带默认接口' : '需补接口'}</span>
        </button>`
      )).join('');
      document.getElementById('model-preset-list').innerHTML = (state.data?.model_presets || []).slice(0, 7).map((model, index) => (
        `<button type="button" class="choice" data-model-preset-index="${index}">
          <strong>${escapeHtml(model.alias)}</strong>
          <span>${escapeHtml(model.model_id)} · 推荐 ${escapeHtml(model.provider)}</span>
        </button>`
      )).join('');
      if (!state.preset && presets[0]) applyPreset(presets[0], false);
    }

    function applyPreset(preset, notify = true) {
      state.preset = preset;
      document.getElementById('account-id').value = `${preset.slug}-main`;
      document.getElementById('provider-id').value = preset.slug;
      document.getElementById('provider-name').value = preset.name;
      document.getElementById('base-url').value = preset.base_url || '';
      document.getElementById('secret-ref').value = preset.secret_ref || '';
      if (notify) showToast(preset.base_url ? `已套用 ${preset.name}` : `${preset.name} 需要补接口地址`, preset.base_url ? 'ok' : 'warn');
    }

    function renderSelectors() {
      const accounts = state.data?.accounts || [];
      const models = state.data?.models || [];
      const projects = state.data?.profiles || [];
      const accountOptions = accounts.map(item => `<option value="${escapeAttr(item.account_id)}">${escapeHtml(item.name || item.account_id)}</option>`).join('');
      const modelOptions = models.map(item => `<option value="${escapeAttr(item.model_id)}">${escapeHtml(item.display_name || item.model_id)}</option>`).join('');
      document.getElementById('model-account').innerHTML = accountOptions || '<option value="">先保存渠道</option>';
      document.querySelectorAll('[data-role]').forEach(row => {
        const role = row.dataset.role;
        const priority = row.dataset.priority;
        row.innerHTML = `
          <strong>${escapeHtml(role)}</strong>
          <label>渠道<select data-role-account>${accountOptions || '<option value="">先添加渠道</option>'}</select></label>
          <label>模型<select data-role-model>${modelOptions || '<option value="">先导入模型池</option>'}</select></label>
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
        route_abilities: '模型池',
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
      renderDataTable('channelsTable', 'API 渠道', [
        {key: 'account_id', label: '渠道'},
        {key: 'name', label: '名称'},
        {label: '代理', render: row => escapeHtml(proxyText(row))},
        {label: '检测', render: row => pill(healthFor(row.account_id).status || 'unknown')},
        {label: '操作', render: row => `<button class="secondary" data-check-account="${escapeAttr(row.account_id)}">检测</button>`}
      ], data.accounts || []);
      renderDataTable('poolTable', '模型池', [
        {key: 'account_id', label: '渠道'},
        {key: 'model_id', label: '内部模型'},
        {label: '显示名', render: row => escapeHtml(row.model.display_name || row.model_id)},
        {label: '能力', render: row => escapeHtml((row.model.capabilities || []).join(', '))},
        {key: 'priority', label: '优先级'},
        {key: 'model_mapping', label: 'Provider 模型名'}
      ], poolRows());
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
        {key: 'notes', label: '角色'}
      ], data.overrides || []);
      renderDataTable('monitorTable', '监控', [
        {key: 'account_id', label: '渠道'},
        {label: '状态', render: row => pill(row.health.status || 'unknown')},
        {label: '延迟', render: row => row.health.latency_ms == null ? '待探测' : `${row.health.latency_ms} ms`},
        {label: '模型数', render: row => String(poolRows().filter(item => item.account_id === row.account_id).length)},
        {label: '代理', render: row => escapeHtml(proxyText(row))},
        {label: '错误', render: row => escapeHtml(row.health.last_error || '')},
        {label: '操作', render: row => `<button class="secondary" data-check-account="${escapeAttr(row.account_id)}">检测</button>`}
      ], (data.accounts || []).map(account => ({...account, health: healthFor(account.account_id)})));
    }

    async function refresh(showMessage = false) {
      state.data = await api('/api/state');
      renderPresets();
      renderSelectors();
      renderMetrics();
      renderTables();
      if (showMessage) showToast('数据已刷新');
    }

    async function checkAccount(accountId) {
      const data = await api('/api/provider-check', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId})
      });
      await refresh();
      showToast(`检测完成：${data.health.status}`);
    }

    document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
    document.getElementById('refresh').addEventListener('click', () => refresh(true));
    window.addEventListener('hashchange', () => setView(location.hash.replace('#', '') || 'overview'));
    document.addEventListener('click', event => {
      const jump = event.target.closest('[data-jump]');
      if (jump) setView(jump.dataset.jump);
      const presetButton = event.target.closest('[data-preset-index]');
      if (presetButton) {
        document.querySelectorAll('[data-preset-index]').forEach(item => item.classList.toggle('active', item === presetButton));
        applyPreset(state.data.provider_presets[Number(presetButton.dataset.presetIndex)]);
        document.getElementById('preset-select').value = presetButton.dataset.presetIndex;
      }
      const modelPresetButton = event.target.closest('[data-model-preset-index]');
      if (modelPresetButton) {
        const model = state.data.model_presets[Number(modelPresetButton.dataset.modelPresetIndex)];
        document.querySelectorAll('[data-model-preset-index]').forEach(item => item.classList.toggle('active', item === modelPresetButton));
        document.getElementById('model-id').value = model.model_id;
        document.getElementById('model-name').value = model.alias;
        document.getElementById('model-capabilities').value = (model.capabilities || []).join(', ');
        showToast(`已填入模型别名 ${model.alias}`);
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
      if (!id) return;
      tableState[id] = tableState[id] || {page: 1, query: ''};
      tableState[id].query = event.target.value;
      tableState[id].page = 1;
      renderTables();
      const input = document.querySelector(`[data-table-search="${id}"]`);
      if (input) input.focus();
    });
    document.getElementById('preset-select').addEventListener('change', event => {
      const index = Number(event.target.value);
      applyPreset(state.data.provider_presets[index]);
      document.querySelectorAll('[data-preset-index]').forEach(item => {
        item.classList.toggle('active', Number(item.dataset.presetIndex) === index);
      });
    });

    document.getElementById('channel-form').addEventListener('submit', async event => {
      event.preventDefault();
      const payload = formPayload(event.currentTarget);
      try {
        await api('/api/providers', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        await refresh();
        showToast('渠道已保存');
      } catch (err) {
        showToast(err.message, 'bad');
      }
    });
    document.getElementById('model-form').addEventListener('submit', async event => {
      event.preventDefault();
      const payload = formPayload(event.currentTarget);
      try {
        await api('/api/channel-model', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        await refresh();
        showToast('模型已加入渠道');
      } catch (err) {
        showToast(err.message, 'bad');
      }
    });
    document.getElementById('check-channel').addEventListener('click', async () => {
      try {
        await checkAccount(document.getElementById('account-id').value);
      } catch (err) {
        showToast(err.message, 'bad');
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
