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
    button {
      cursor: pointer;
      transition: transform .12s ease, opacity .12s ease, box-shadow .12s ease, background-color .12s ease;
    }
    button:active:not(:disabled) { transform: translateY(1px) scale(.99); }
    button:disabled { cursor: not-allowed; opacity: .68; }
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
    .notice.compact { padding: 10px 12px; }
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
      grid-template-columns: 1fr;
      gap: 14px;
      align-items: start;
    }
    form { display: grid; gap: 10px; }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .dense-form { grid-template-columns: repeat(4, minmax(0, 1fr)); }
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
    .primary, .secondary {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      white-space: nowrap;
    }
    .primary[data-loading="true"], .secondary[data-loading="true"] {
      box-shadow: inset 0 0 0 999px rgba(255,255,255,.12);
    }
    .primary[data-loading="true"]::before, .secondary[data-loading="true"]::before {
      content: "";
      width: 12px;
      height: 12px;
      flex: 0 0 auto;
      border-radius: 999px;
      border: 2px solid currentColor;
      border-right-color: transparent;
      animation: button-spin .75s linear infinite;
    }
    @keyframes button-spin { to { transform: rotate(360deg); } }
    .primary {
      border-color: #1f4fc4;
      background: var(--blue);
      color: #fff;
      font-weight: 600;
    }
    .danger {
      border-color: #e5a5a0;
      background: var(--soft-red);
      color: var(--red);
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
    .compact-body { padding: 10px; }
    .split-stack {
      display: grid;
      gap: 14px;
    }
    .vendor-workspace {
      grid-template-columns: 1fr;
    }
    .vendor-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
      gap: 8px;
    }
    .vendor-card {
      min-height: 58px;
      padding: 10px;
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
      grid-template-columns: 34px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
    }
    .config-row:hover { border-color: #b7c6e5; background: #fff; }
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
    .config-title {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .provider-avatar {
      width: 30px;
      height: 30px;
      flex: 0 0 auto;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
      display: grid;
      place-items: center;
      font-weight: 700;
      color: var(--blue);
    }
    .config-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      justify-content: flex-end;
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
    .project-console {
      display: grid;
      grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
      gap: 14px;
      align-items: start;
    }
    .project-list {
      display: grid;
      gap: 8px;
      padding: 12px;
    }
    .project-card {
      width: 100%;
      min-height: 54px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      text-align: left;
      color: var(--ink);
    }
    .project-card.active {
      border-color: #7da2ee;
      background: var(--soft-blue);
    }
    .project-card strong, .project-card span { display: block; }
    .project-card span { margin-top: 3px; color: var(--muted); font-size: 12px; }
    .project-toolbar {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto;
      gap: 10px;
      align-items: end;
    }
    .project-model-layout {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr);
      gap: 12px;
      align-items: start;
    }
    .slot-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
    }
    .slot-card {
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
    }
    .slot-card.active {
      border-color: #7da2ee;
      background: #f3f7ff;
    }
    .slot-card strong { display: block; font-size: 13px; }
    .slot-card span { display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }
    .slot-card .slot-meta { min-height: 18px; margin-top: 6px; }
    .model-chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 9px;
      min-height: 31px;
    }
    .model-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      min-height: 28px;
      padding: 0 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      color: var(--ink);
      font-size: 12px;
    }
    .model-chip button {
      width: 20px;
      height: 20px;
      border: 0;
      border-radius: 999px;
      background: #edf1f5;
      color: var(--muted);
      padding: 0;
    }
    .model-palette {
      position: sticky;
      top: 10px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
    }
    .model-palette-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .model-palette-head strong { font-size: 13px; }
    .model-palette-list {
      display: grid;
      gap: 6px;
      max-height: 460px;
      overflow: auto;
      margin-top: 8px;
    }
    .model-option {
      min-height: 44px;
      padding: 8px 9px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      text-align: left;
    }
    .model-option.active {
      border-color: #7da2ee;
      background: var(--soft-blue);
    }
    .model-option:disabled {
      opacity: .68;
      cursor: default;
    }
    .model-option strong, .model-option span { display: block; }
    .model-option span { margin-top: 2px; color: var(--muted); font-size: 12px; }
    .project-bundle {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      overflow: hidden;
    }
    .project-bundle summary {
      cursor: pointer;
      padding: 10px 12px;
      font-weight: 600;
    }
    @media (max-width: 980px) {
      .project-console, .project-model-layout, .project-toolbar {
        grid-template-columns: 1fr;
      }
      .model-palette { position: static; }
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
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 15;
      display: none;
      place-items: center;
      padding: 22px;
      background: rgba(16, 24, 32, .48);
    }
    .modal-backdrop.open { display: grid; }
    .modal {
      width: min(980px, 100%);
      max-height: min(860px, calc(100vh - 44px));
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: 0 24px 72px rgba(20, 33, 43, .28);
      overflow: hidden;
    }
    .modal-head {
      min-height: 58px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .modal-head h3 { margin: 0; font-size: 16px; }
    .modal-body {
      min-height: 0;
      overflow: auto;
      padding: 14px;
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
      .workspace, .form-grid, .dense-form, .config-row, .vendor-grid { grid-template-columns: 1fr; }
      .config-actions { justify-content: flex-start; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">
        <h1>万象中枢</h1>
        <p>本地模型配置、项目模型包与 Skill 控制台</p>
      </div>
      <nav>
        <button class="nav-item" data-view="overview">总览</button>
        <button class="nav-item" data-view="channels">模型配置</button>
        <button class="nav-item" data-view="projects">项目编组</button>
        <button class="nav-item" data-view="monitor">监控检测</button>
        <button class="nav-item" data-view="skills">Skills</button>
      </nav>
    </aside>
    <div class="main">
      <header>
        <div class="topbar">
          <div class="title">
            <h2 id="page-title">总览</h2>
            <p id="page-subtitle">模型厂商、渠道队列、代理、用量和项目模型包。</p>
          </div>
          <button class="secondary" id="refresh">刷新</button>
        </div>
      </header>

      <main class="content">
        <section class="view" data-view-panel="overview">
          <div class="notice compact">按模型厂商分组管理官方和中转渠道；项目只导入需要的模型包，密钥始终以引用传递。</div>
          <div class="metrics" id="metrics"></div>
          <div class="grid">
            <button class="action" data-jump="channels"><strong>配置模型厂商</strong><span>官方和中转渠道统一加入当前厂商列表</span></button>
            <button class="action" data-jump="projects"><strong>导入项目模型包</strong><span>为项目生成可读配置和路由清单</span></button>
            <button class="action" data-jump="monitor"><strong>看延迟和额度</strong><span>检测、实时刷新和可视化监控</span></button>
          </div>
          <div class="panel table-box">
            <div class="panel-head"><h3>当前状态</h3><span class="subtle">模型配置、渠道、项目编组</span></div>
            <div id="overviewTable"></div>
          </div>
        </section>

        <section class="view" data-view-panel="channels">
          <div class="workspace vendor-workspace">
            <div class="panel">
              <div class="panel-head">
                <div><h3>模型厂商</h3><span class="subtle">选择厂商后，列表只显示它的官方和中转渠道</span></div>
                <button class="primary" id="add-channel">添加渠道</button>
              </div>
              <div class="panel-body compact-body">
                <div class="vendor-grid" id="official-provider-list"></div>
              </div>
            </div>

            <div class="panel table-box">
              <div class="panel-head"><h3 id="provider-list-title">当前厂商渠道</h3><span class="subtle">拖拽左侧排序块即可调整启用顺序</span></div>
              <div id="providerConfigList"></div>
            </div>
          </div>
        </section>

        <section class="view" data-view-panel="projects">
          <div class="project-console">
            <div class="panel">
              <div class="panel-head"><h3>项目</h3><button class="secondary" id="project-new" type="button">新建项目</button></div>
              <div id="project-list" class="project-list"></div>
            </div>
            <div class="panel">
              <div class="panel-head"><h3>项目模型配置</h3><span class="subtle">选择模型并设置顺位，项目接入文件在详情里生成</span></div>
              <div class="panel-body">
                <form id="project-import-form">
                  <div class="project-toolbar">
                    <label>项目 ID<input name="project_id" id="project-id" placeholder="auto-driving-research" required></label>
                    <div class="buttons">
                      <button class="primary">保存模型顺序</button>
                      <button type="button" class="secondary" id="copy-project-bundle">复制项目接入文件</button>
                    </div>
                  </div>
                  <div class="project-model-layout">
                    <div class="slot-grid" id="model-slot-grid"></div>
                    <div class="model-palette">
                      <div class="model-palette-head">
                        <strong>可选模型</strong>
                        <span class="subtle" id="project-active-slot">默认文本</span>
                      </div>
                      <input id="project-model-search" placeholder="搜索模型、厂商或渠道">
                      <div id="project-model-palette" class="model-palette-list"></div>
                    </div>
                  </div>
                  <details class="project-bundle">
                    <summary>项目接入文件</summary>
                    <pre class="result" id="project-bundle-preview">{}</pre>
                  </details>
                  <div class="buttons">
                    <button type="button" class="secondary" data-project-copy-section="manifest">复制 omni-hub.project.json</button>
                    <button type="button" class="secondary" data-project-copy-section="resolver">复制 resolver 请求</button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </section>

        <section class="view" data-view-panel="monitor">
          <div class="grid">
            <div class="panel">
              <div class="panel-head"><h3>最近模型探测延迟</h3><button class="secondary" id="realtime-toggle">开始定期查额度</button></div>
              <div class="panel-body"><div class="chart" id="latency-chart"></div></div>
            </div>
            <div class="panel">
              <div class="panel-head"><h3>额度监控</h3><span class="subtle">余额可定期查，延迟需手动探测</span></div>
              <div class="panel-body subtle">查额度只调用供应商余额接口，不做模型请求。模型探测会发送最小真实请求，可能产生极小 token 成本，所以不会自动实时刷新。</div>
            </div>
          </div>
            <div class="panel table-box">
            <div class="panel-head"><h3>渠道额度</h3><button class="secondary" id="check-all">全部查额度</button></div>
            <div id="monitorTable"></div>
          </div>
          <div class="panel table-box">
            <div class="panel-head"><h3>模型级健康</h3><span class="subtle">同一渠道下不同模型分别探测</span></div>
            <div id="modelMonitorTable"></div>
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

  <div class="modal-backdrop" id="provider-modal" role="dialog" aria-modal="true" aria-labelledby="channel-form-title">
    <div class="modal">
      <div class="modal-head">
        <div>
          <h3 id="channel-form-title">添加渠道</h3>
          <span class="subtle">官方和中转站都属于当前模型厂商；添加和修改使用同一个弹窗</span>
        </div>
        <button class="secondary" id="close-provider-modal">关闭</button>
      </div>
      <div class="modal-body">
        <form id="official-form">
          <input name="provider" id="official-provider" type="hidden" value="openai">
          <div class="form-grid dense-form">
            <label>渠道 ID<input name="account_id" id="account-id" required></label>
            <label>渠道名称<input name="name" id="provider-name" required placeholder="官方 / OpenRouter / 胜算云"></label>
            <label class="wide">接口地址<input name="base_url" id="base-url" placeholder="由预设填充；中转站可直接改" required></label>
            <label class="wide">API Key<input name="api_key" id="api-key" type="password" autocomplete="off" placeholder="可直接填；保存到本地 .omni/secrets.json，数据库只存 local 引用"></label>
            <label>密钥引用<input name="secret_ref" id="secret-ref" placeholder="保存后自动生成 local:omni-hub/渠道ID"></label>
            <label>代理连接<input name="proxy_url" id="proxy-url" placeholder="留空表示 unset；如 http://127.0.0.1:7890"></label>
            <label>调用优先级<input name="priority" id="provider-priority" type="number" value="90"></label>
            <label>并发上限<input name="max_concurrency" id="max-concurrency" type="number" min="0" placeholder="未知可留空"></label>
            <label>RPM 限制<input name="rpm_limit" id="rpm-limit" type="number" min="0" placeholder="每分钟请求数"></label>
            <label>TPM 限制<input name="tpm_limit" id="tpm-limit" type="number" min="0" placeholder="每分钟 token"></label>
            <label class="wide">模型列表<textarea name="model_ids" id="model-ids" placeholder="每行一个模型 ID；可以点击发现模型自动填充"></textarea></label>
            <label>默认模型<select name="default_model" id="default-model"><option value="">自动使用首个模型</option></select></label>
            <label>推理强度<select name="model_reasoning_effort" id="model-reasoning-effort">
              <option value="high">high</option>
              <option value="xhigh">xhigh</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
              <option value="minimal">minimal</option>
            </select></label>
          </div>
          <details class="advanced">
            <summary>高级配置：测试、协议与计费</summary>
            <div class="form-grid dense-form">
              <label>API 格式<select name="api_format" id="api-format">
                <option value="">按厂商默认</option>
                <option value="openai_chat">OpenAI Chat Completions</option>
                <option value="openai_responses">OpenAI Responses</option>
                <option value="anthropic">Anthropic Messages</option>
                <option value="gemini_native">Gemini Native</option>
              </select></label>
              <label>认证字段<select name="auth_field" id="auth-field">
                <option value="">按厂商默认</option>
                <option value="Authorization">Authorization: Bearer</option>
                <option value="x-api-key">x-api-key</option>
                <option value="ANTHROPIC_AUTH_TOKEN">ANTHROPIC_AUTH_TOKEN</option>
                <option value="ANTHROPIC_API_KEY">ANTHROPIC_API_KEY</option>
              </select></label>
              <label>完整端点模式<select name="is_full_url" id="is-full-url">
                <option value="">否，自动拼接 endpoint</option>
                <option value="true">是，base URL 已是完整 endpoint</option>
              </select></label>
              <label>模型发现 URL<input name="models_url" id="models-url" placeholder="可选，覆盖 /v1/models 候选"></label>
              <label>测试模型<input name="test_model" id="test-model" placeholder="留空使用列表首个模型"></label>
              <label>测试提示词<input name="test_prompt" id="test-prompt" placeholder="Who are you?"></label>
              <label>超时秒数<input name="timeout_secs" id="timeout-secs" type="number" min="1" placeholder="45"></label>
              <label>最大重试<input name="max_retries" id="max-retries" type="number" min="0" placeholder="2"></label>
              <label>降级阈值 ms<input name="degraded_threshold_ms" id="degraded-threshold-ms" type="number" min="100" placeholder="6000"></label>
              <label>成本倍率<input name="cost_multiplier" id="cost-multiplier" type="number" min="0" step="0.01" placeholder="1.0"></label>
              <label>计费模型<select name="pricing_model_source" id="pricing-model-source">
                <option value="">继承默认</option>
                <option value="request">按请求模型</option>
                <option value="response">按返回模型</option>
              </select></label>
              <label>Codex 协议<select name="wire_api" id="wire-api">
                <option value="responses">responses</option>
                <option value="chat">chat</option>
              </select></label>
              <label>OpenAI 认证<select name="requires_openai_auth" id="requires-openai-auth">
                <option value="true">true</option>
                <option value="false">false</option>
              </select></label>
              <label>禁用响应存储<select name="disable_response_storage" id="disable-response-storage">
                <option value="true">true</option>
                <option value="false">false</option>
              </select></label>
              <label>额度模板<select name="usage_template" id="usage-template">
                <option value="auto">自动探测</option>
                <option value="newapi">New API</option>
                <option value="generic">通用余额</option>
                <option value="cursorlink">CursorLink</option>
              </select></label>
              <label>用量 Base URL<input name="usage_base_url" id="usage-base-url" placeholder="留空使用接口地址"></label>
              <label>自定义用量路径<input name="usage_endpoint" id="usage-endpoint" placeholder="/v1/usage"></label>
              <label>用量超时秒数<input name="usage_timeout_secs" id="usage-timeout-secs" type="number" min="1" placeholder="20"></label>
              <label>用量重试次数<input name="usage_max_retries" id="usage-max-retries" type="number" min="0" max="3" placeholder="1"></label>
              <label>New API User ID<input name="usage_user_id" id="usage-user-id" placeholder="New API 用户 ID"></label>
              <label>New API Access Token<input name="usage_access_token" id="usage-access-token" type="password" autocomplete="off" placeholder="保存到本地 secret；查额度时使用"></label>
              <input name="usage_access_token_ref" id="usage-access-token-ref" type="hidden">
              <label class="wide">能力<input name="capabilities" id="provider-capabilities" placeholder="text, tools, vision, reasoning, batch, embedding"></label>
              <label class="wide">额度入口<input name="quota_ref" id="quota-ref" placeholder="dashboard 或 quota API 引用"></label>
            </div>
          </details>
          <input name="status" type="hidden" value="active">
          <div class="buttons">
            <button class="primary" id="save-provider-button">保存渠道</button>
            <button type="button" class="secondary" id="fetch-models">发现模型</button>
            <button type="button" class="secondary" id="test-official-draft">测试连接</button>
            <button type="button" class="secondary" id="copy-script">导出 export 脚本</button>
            <button type="button" class="secondary" id="copy-codex-config">导出 Codex 配置</button>
          </div>
          <pre class="script-box" id="script-preview"></pre>
        </form>
      </div>
    </div>
  </div>

  <div id="toast" class="toast" role="status" aria-live="polite"></div>

  <script>
    const PAGE_SIZE = 8;
    const views = {
      overview: ['总览', '模型厂商、渠道队列、代理、用量和项目模型包。'],
      channels: ['模型配置', '按模型厂商管理官方和中转渠道。'],
      projects: ['项目编组', '为项目导入模型包和运行参数。'],
      monitor: ['监控检测', '定期查额度，按需探测模型延迟和模型级健康。'],
      skills: ['Skills', '技能安装、同步、质量评分和项目推荐的控制面。']
    };
    const state = {data: null, view: 'overview', officialProvider: null, providerModalMode: 'add', realtime: false, realtimeTimer: null, dragAccount: null, projectBundle: null, balances: {}, selectedProjectId: '', selectedSlot: 'default', projectDrafts: {}, projectModelSearch: '', creatingProject: false};
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
    function setButtonLoading(button, loading, text = '处理中') {
      if (!button) return;
      if (loading) {
        if (button.dataset.loading === 'true') return;
        button.dataset.originalText = button.textContent.trim();
        button.dataset.loading = 'true';
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.textContent = text || button.dataset.originalText || '处理中';
        return;
      }
      if (button.dataset.originalText) button.textContent = button.dataset.originalText;
      delete button.dataset.originalText;
      delete button.dataset.loading;
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
    async function withButtonLoading(button, text, task) {
      if (button?.dataset.loading === 'true') return null;
      setButtonLoading(button, true, text);
      try {
        return await task();
      } catch (err) {
        showToast(err.message || '操作失败', 'bad');
        return null;
      } finally {
        setButtonLoading(button, false);
      }
    }
    const pill = value => {
      const text = String(value || 'unknown');
      const cls = ['active', 'healthy'].includes(text) ? 'ok' : (['down', 'disabled'].includes(text) ? 'bad' : 'warn');
      return `<span class="pill ${cls}">${escapeHtml(text)}</span>`;
    };
    const proxyText = account => account.proxy_url ? account.proxy_url : 'unset';
    const noteValue = (notes, key) => {
      const prefix = `${key}=`;
      const line = String(notes || '').split('\n').find(item => item.startsWith(prefix));
      return line ? line.slice(prefix.length).trim() : '';
    };
    const modelIdsFromTextarea = () => document.getElementById('model-ids').value
      .split(/\n|,/)
      .map(item => item.trim())
      .filter(Boolean);
    function syncDefaultModelOptions(selected = '') {
      const select = document.getElementById('default-model');
      if (!select) return;
      const current = selected || select.value;
      const ids = modelIdsFromTextarea();
      select.innerHTML = '<option value="">自动使用首个模型</option>' + ids.map(id => (
        `<option value="${escapeAttr(id)}">${escapeHtml(id)}</option>`
      )).join('');
      select.value = ids.includes(current) ? current : '';
    }
    const quotaText = account => {
      const notes = account.notes || '';
      const quotaRef = noteValue(notes, 'quota_ref');
      if (quotaRef.startsWith('dashboard:')) return `官网用量页 ${quotaRef.slice('dashboard:'.length)}`;
      if (quotaRef.startsWith('api:')) return `余额接口 ${quotaRef.slice('api:'.length)}`;
      if (quotaRef.startsWith('cursorlink:')) return `CursorLink ${quotaRef.slice('cursorlink:'.length)}`;
      if (quotaRef) return quotaRef;
      const found = notes.split('\n').find(line => /额度|balance/i.test(line));
      return found || '未配置';
    };
    const missingSecretText = data => data?.error_code === 'missing_secret' || /secret is not available|secret.*not.*found|api key is empty/i.test(String(data?.error || ''));
    const balanceText = account => {
      const cached = state.balances[account.account_id];
      if (!cached) return quotaText(account);
      if (!cached.success) return missingSecretText(cached) ? `待填写 API Key · ${quotaText(account)}` : (cached.error ? `失败：${cached.error}` : '余额查询失败');
      const rows = cached.data || [];
      return rows.map(row => {
        const pieces = [
          row.plan_name || '余额',
          row.remaining == null ? '-' : row.remaining,
          row.unit || ''
        ].filter(Boolean);
        const extra = row.extra || {};
        const suffix = [
          row.used == null ? '' : `已用 ${row.used}`,
          extra.total_requests == null ? '' : `请求 ${extra.total_requests}`,
          extra.remain_days == null ? '' : `剩余 ${extra.remain_days} 天`
        ].filter(Boolean).join(' · ');
        return suffix ? `${pieces.join(' ')} · ${suffix}` : pieces.join(' ');
      }).join('；') || '余额接口无数据';
    };
    const balanceStatus = account => {
      const cached = state.balances[account.account_id];
      if (!cached) return {text: '未查询', cls: 'warn'};
      if (!cached.success) {
        return missingSecretText(cached)
          ? {text: '待填 Key', cls: 'warn'}
          : {text: '查询失败', cls: 'bad'};
      }
      const rows = cached.data || [];
      if (rows.some(row => row.is_valid === false)) return {text: '额度无效', cls: 'bad'};
      if (rows.some(row => row.remaining != null && Number(row.remaining) <= 0)) return {text: '额度耗尽', cls: 'bad'};
      return {text: rows.length ? '可用' : '已查询', cls: 'ok'};
    };
    const balancePill = account => {
      const status = balanceStatus(account);
      return `<span class="pill ${status.cls}">${escapeHtml(status.text)}</span>`;
    };
    const balanceResultText = account => {
      const cached = state.balances[account.account_id];
      if (!cached) return '';
      if (!cached.success) return missingSecretText(cached) ? '等待本机密钥' : (cached.error || '余额查询失败');
      const invalid = (cached.data || []).find(row => row.is_valid === false);
      return invalid ? (invalid.invalid_message || '套餐不可用') : '';
    };
    const sameBaseUrl = (left, right) => String(left || '').trim().replace(/\/+$/, '').toLowerCase() === String(right || '').trim().replace(/\/+$/, '').toLowerCase();
    const channelGroup = (preset, baseUrl) => sameBaseUrl(baseUrl, preset?.base_url || '') ? 'official' : 'relay';
    const idFromBaseUrl = (preset, baseUrl) => {
      let host = 'relay';
      try {
        host = new URL(baseUrl).host || host;
      } catch (_) {
        host = String(baseUrl || '').replace(/^https?:\/\//, '').split('/')[0] || host;
      }
      const suffix = host.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'relay';
      return `${preset.slug}-${suffix}`.slice(0, 64).replace(/-+$/g, '') || `${preset.slug}-relay`;
    };
    const isPresetDefaultAccountId = (preset, accountId) => [`${preset.slug}-main`, `${preset.slug}-official`].includes(accountId);
    function normalizeDraftProviderPayload(payload) {
      const preset = state.officialProvider;
      if (!preset) return payload;
      const group = channelGroup(preset, payload.base_url);
      if (state.providerModalMode === 'add' && group === 'relay' && isPresetDefaultAccountId(preset, payload.account_id)) {
        payload.account_id = idFromBaseUrl(preset, payload.base_url);
        document.getElementById('account-id').value = payload.account_id;
        const defaultOfficialName = `${preset.name} 官方`;
        if (!payload.name || payload.name === defaultOfficialName) {
          const host = payload.account_id.replace(`${preset.slug}-`, '');
          payload.name = `${preset.name} 中转 · ${host}`;
          document.getElementById('provider-name').value = payload.name;
        }
      }
      return payload;
    }
    const officialProviders = () => state.data?.official_providers || [];
    const providerPreset = slug => officialProviders().find(item => item.slug === slug || item.provider === slug);
    const providerName = slug => providerPreset(slug)?.name || slug;
    const selectedProvider = () => state.officialProvider?.provider || state.officialProvider?.slug || officialProviders()[0]?.provider || '';
    const starterChannels = () => (state.officialProvider?.starter_channels || []).slice().sort((a, b) => Number(b.rank || 0) - Number(a.rank || 0));
    const starterChannelById = id => starterChannels().find(channel => channel.account_id === id);
    const accountModelIds = accountId => poolRows().filter(row => row.account_id === accountId).map(row => row.model_id);
    const accountPriority = accountId => {
      const rows = poolRows().filter(row => row.account_id === accountId);
      return rows.length ? Math.max(...rows.map(row => Number(row.priority || 0))) : 0;
    };
    const accountRows = (provider = null) => (state.data?.accounts || [])
      .filter(account => !provider || account.provider === provider)
      .map(account => ({
      account,
      models: accountModelIds(account.account_id),
      health: healthFor(account.account_id),
      priority: accountPriority(account.account_id),
      preset: providerPreset(account.provider)
    })).sort((a, b) => b.priority - a.priority || a.account.account_id.localeCompare(b.account.account_id));
    const modelSlots = [
      ['default', '默认文本', '总结、写作、轻量工具'],
      ['reasoning', '复杂推理', '规划、研究、长链路分析'],
      ['code', '代码与工具', '工程修改、测试、自动化'],
      ['vision', '多模态', '图片、OCR、视频帧'],
      ['batch', '批处理/低价', '异步批量和成本敏感任务'],
      ['embedding', '检索向量', '索引、召回、重排链路']
    ];
    const slotTitle = slotId => modelSlots.find(([id]) => id === slotId)?.[1] || slotId;
    const projectDraftKey = () => (document.getElementById('project-id')?.value.trim() || state.selectedProjectId || '__draft__');
    function ensureProjectDraft(projectId = projectDraftKey()) {
      const key = projectId || '__draft__';
      if (!state.projectDrafts[key]) {
        const draft = {};
        (state.data?.model_orders || [])
          .filter(order => order.project_id === key)
          .forEach(order => { draft[order.slot] = [...(order.model_ids || [])]; });
        state.projectDrafts[key] = draft;
      }
      return state.projectDrafts[key];
    }
    function slotModels(slotId) {
      return ensureProjectDraft()[slotId] || [];
    }
    function setSlotModels(slotId, models) {
      const draft = ensureProjectDraft();
      draft[slotId] = [...new Set(models.filter(Boolean))];
      state.projectBundle = null;
      renderProjectSlots();
      renderProjectModelPalette();
      renderProjectBundlePreview();
    }
    function projectRows() {
      const ids = new Set((state.data?.profiles || []).map(item => item.project_id));
      (state.data?.model_orders || []).forEach(item => ids.add(item.project_id));
      return [...ids].sort().map(project_id => ({
        project_id,
        orders: (state.data?.model_orders || []).filter(order => order.project_id === project_id)
      }));
    }
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
      const element = document.getElementById(id);
      if (!element) return;
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
      element.innerHTML = `
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
    function healthFor(accountId, modelId = '') {
      if (modelId) {
        return (state.data?.health || []).find(item => item.account_id === accountId && item.model_id === modelId) || {};
      }
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
    function availableModelRows() {
      return (state.data?.models || []).map(model => {
        const rows = poolRows().filter(row => row.model_id === model.model_id && row.enabled);
        const accounts = rows.map(row => row.account).filter(account => account.account_id);
        const priority = rows.length ? Math.max(...rows.map(row => Number(row.priority || 0))) : 0;
        return {
          model,
          rows,
          priority,
          providers: [...new Set(accounts.map(account => providerName(account.provider)))],
          channels: accounts.map(account => account.name || account.account_id),
          healthy: rows.some(row => ['healthy', 'unknown', undefined].includes(row.health?.status))
        };
      }).sort((a, b) => Number(b.priority || 0) - Number(a.priority || 0) || a.model.model_id.localeCompare(b.model.model_id));
    }
    function renderOfficialProviders() {
      const presets = officialProviders();
      document.getElementById('official-provider-list').innerHTML = presets.map((preset, index) => (
        `<button type="button" class="vendor-card ${state.officialProvider?.slug === preset.slug ? 'active' : ''}" data-official-index="${index}">
          <strong>${escapeHtml(preset.name)}</strong>
          <span>${accountRows(preset.provider).length} 个渠道 · ${(preset.models || []).length} 个预设模型</span>
        </button>`
      )).join('');
      if (!state.officialProvider && presets[0]) applyOfficialProvider(presets[0], false);
    }

    function applyOfficialProvider(preset, notify = true) {
      state.officialProvider = preset;
      document.getElementById('official-provider').value = preset.slug;
      document.getElementById('account-id').value = `${preset.slug}-official`;
      document.getElementById('provider-name').value = `${preset.name} 官方`;
      document.getElementById('base-url').value = preset.base_url || '';
      document.getElementById('secret-ref').value = '';
      document.getElementById('quota-ref').value = preset.quota_ref || '';
      document.getElementById('model-ids').value = (preset.models || []).join('\n');
      syncDefaultModelOptions('');
      document.getElementById('model-reasoning-effort').value = 'high';
      document.getElementById('wire-api').value = 'responses';
      document.getElementById('requires-openai-auth').value = 'true';
      document.getElementById('disable-response-storage').value = 'true';
      document.getElementById('usage-template').value = 'auto';
      document.getElementById('usage-base-url').value = '';
      document.getElementById('usage-endpoint').value = '';
      document.getElementById('usage-timeout-secs').value = '';
      document.getElementById('usage-max-retries').value = '';
      document.getElementById('usage-user-id').value = '';
      document.getElementById('usage-access-token').value = '';
      document.getElementById('usage-access-token-ref').value = '';
      document.getElementById('provider-capabilities').value = (preset.capabilities || []).join(', ');
      document.getElementById('provider-priority').value = String(preset.rank || 90);
      document.getElementById('api-key').value = '';
      ['max-concurrency', 'rpm-limit', 'tpm-limit', 'api-format', 'auth-field', 'is-full-url',
       'models-url', 'test-model', 'test-prompt', 'timeout-secs', 'max-retries',
       'degraded-threshold-ms', 'cost-multiplier', 'pricing-model-source'].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.value = '';
      });
      document.getElementById('channel-form-title').textContent = `添加 ${preset.name} 渠道`;
      document.getElementById('save-provider-button').textContent = '添加渠道';
      document.getElementById('provider-list-title').textContent = `${preset.name} 渠道列表`;
      renderOfficialProviders();
      renderTables();
      updateScriptPreview();
      if (notify) showToast(`已选择 ${preset.name}`);
    }

    function applyStarterChannel(channel, notify = true) {
      const preset = state.officialProvider;
      if (!preset || !channel) return;
      state.providerModalMode = 'add';
      document.getElementById('official-provider').value = preset.slug;
      document.getElementById('account-id').value = channel.account_id || idFromBaseUrl(preset, channel.base_url);
      document.getElementById('provider-name').value = channel.name || `${preset.name} 中转`;
      document.getElementById('base-url').value = channel.base_url || preset.base_url || '';
      document.getElementById('secret-ref').value = '';
      document.getElementById('proxy-url').value = '';
      document.getElementById('quota-ref').value = channel.quota_ref || preset.quota_ref || '';
      document.getElementById('model-ids').value = (channel.models || []).join('\n');
      syncDefaultModelOptions(channel.default_model || '');
      document.getElementById('default-model').value = channel.default_model || '';
      document.getElementById('provider-capabilities').value = (channel.capabilities || preset.capabilities || []).join(', ');
      document.getElementById('provider-priority').value = String(channel.rank || preset.rank || 90);
      document.getElementById('api-key').value = '';
      document.getElementById('api-format').value = channel.api_format || '';
      document.getElementById('wire-api').value = channel.wire_api || 'chat';
      document.getElementById('requires-openai-auth').value = channel.requires_openai_auth || 'true';
      document.getElementById('disable-response-storage').value = channel.disable_response_storage || 'true';
      document.getElementById('usage-template').value = channel.usage_template || 'auto';
      document.getElementById('usage-base-url').value = channel.usage_base_url || '';
      document.getElementById('usage-endpoint').value = channel.usage_endpoint || '';
      document.getElementById('usage-timeout-secs').value = channel.usage_timeout_secs || '';
      document.getElementById('usage-max-retries').value = channel.usage_max_retries || '';
      document.getElementById('usage-user-id').value = '';
      document.getElementById('usage-access-token').value = '';
      document.getElementById('usage-access-token-ref').value = '';
      ['max-concurrency', 'rpm-limit', 'tpm-limit', 'auth-field', 'is-full-url',
       'models-url', 'test-model', 'test-prompt', 'timeout-secs', 'max-retries',
       'degraded-threshold-ms', 'cost-multiplier', 'pricing-model-source'].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.value = channel[id.replaceAll('-', '_')] || '';
      });
      document.getElementById('channel-form-title').textContent = `添加 ${channel.name || preset.name}`;
      document.getElementById('save-provider-button').textContent = '添加渠道';
      updateScriptPreview();
      openProviderModal('add');
      if (notify) showToast(`已填入 ${channel.name}`);
    }

    function openProviderModal(mode = 'add') {
      state.providerModalMode = mode;
      document.getElementById('provider-modal').classList.add('open');
      document.getElementById('account-id').focus();
    }

    function closeProviderModal() {
      document.getElementById('provider-modal').classList.remove('open');
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

    function renderProjectSlots() {
      const target = document.getElementById('model-slot-grid');
      if (!target) return;
      ensureProjectDraft();
      target.innerHTML = modelSlots.map(([id, title, desc]) => {
        const selected = slotModels(id);
        return `<div class="slot-card" data-slot="${escapeAttr(id)}">
          <button type="button" class="model-option ${state.selectedSlot === id ? 'active' : ''}" data-project-slot-select="${escapeAttr(id)}">
            <strong>${escapeHtml(title)}</strong>
            <span>${escapeHtml(desc)}</span>
          </button>
          <div class="model-chip-row">
            ${selected.length ? selected.map((modelId, index) => `<span class="model-chip">
              ${escapeHtml(index + 1)}. ${escapeHtml(modelId)}
              <button type="button" title="上移" data-model-up="${escapeAttr(modelId)}" data-slot="${escapeAttr(id)}">↑</button>
              <button type="button" title="下移" data-model-down="${escapeAttr(modelId)}" data-slot="${escapeAttr(id)}">↓</button>
              <button type="button" title="移除" data-model-remove="${escapeAttr(modelId)}" data-slot="${escapeAttr(id)}">×</button>
            </span>`).join('') : '<span class="subtle">未选择模型。点击右侧模型库添加。</span>'}
          </div>
          <span class="slot-meta">顺位越靠前越先用；同名模型按模型配置页渠道排序解析。</span>
        </div>`;
      }).join('');
      const activeSlot = document.getElementById('project-active-slot');
      if (activeSlot) activeSlot.textContent = slotTitle(state.selectedSlot);
    }

    function renderProjectList() {
      const target = document.getElementById('project-list');
      if (!target) return;
      const rows = projectRows();
      if (!state.creatingProject && !state.selectedProjectId && rows[0]) state.selectedProjectId = rows[0].project_id;
      if (state.selectedProjectId) {
        const input = document.getElementById('project-id');
        if (input && input.value !== state.selectedProjectId) input.value = state.selectedProjectId;
      }
      target.innerHTML = rows.length ? rows.map(row => `<button type="button" class="project-card ${row.project_id === state.selectedProjectId ? 'active' : ''}" data-project-id="${escapeAttr(row.project_id)}">
        <strong>${escapeHtml(row.project_id)}</strong>
        <span>${escapeHtml(row.orders.length)} 个能力槽 · ${escapeHtml((row.orders || []).map(order => order.slot).join(', ') || '未配置')}</span>
      </button>`).join('') : '<div class="subtle">还没有项目。点击新建后填写项目 ID。</div>';
    }

    function renderProjectModelPalette() {
      const target = document.getElementById('project-model-palette');
      if (!target) return;
      const selected = new Set(slotModels(state.selectedSlot));
      const query = state.projectModelSearch.trim().toLowerCase();
      const rows = availableModelRows().filter(row => !query || searchText(row).includes(query)).slice(0, 80);
      target.innerHTML = rows.length ? rows.map(row => {
        const modelId = row.model.model_id;
        const used = selected.has(modelId);
        const meta = [
          row.providers.join('/'),
          `${row.rows.length} 个渠道`,
          row.priority ? `优先级 ${row.priority}` : '未排序'
        ].filter(Boolean).join(' · ');
        return `<button type="button" class="model-option ${used ? 'active' : ''}" data-model-add="${escapeAttr(modelId)}" ${used ? 'disabled' : ''}>
          <strong>${escapeHtml(modelId)}</strong>
          <span>${escapeHtml(meta || '暂无可用渠道')}</span>
        </button>`;
      }).join('') : '<div class="subtle">没有匹配模型。先到模型配置页发现或添加模型。</div>';
    }

    function renderMetrics() {
      const stats = state.data?.stats || {};
      const labels = {
        provider_accounts: 'API 渠道',
        model_catalog: '模型',
        route_abilities: '模型配置',
        project_route_profiles: '项目',
        project_model_orders: '模型顺序',
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
        ...(data.profiles || []).map(item => ({type: '项目', id: item.project_id, detail: `能力槽 ${(data.model_orders || []).filter(order => order.project_id === item.project_id).length} 个`, status: item.max_cost_usd ? `预算 ${item.max_cost_usd}` : 'active'}))
      ];
      renderDataTable('overviewTable', '当前状态', [
        {label: '类型', render: row => `<span class="pill info">${escapeHtml(row.type)}</span>`},
        {key: 'id', label: '名称'},
        {key: 'detail', label: '说明'},
        {label: '状态', render: row => pill(row.status)}
      ], overview);
      renderProviderConfigList();
      renderProjectList();
      renderProjectSlots();
      renderProjectModelPalette();
      renderProjectBundlePreview();
      renderDataTable('monitorTable', '监控', [
        {key: 'account_id', label: '渠道'},
        {label: '额度状态', render: row => balancePill(row)},
        {label: '模型数', render: row => String(poolRows().filter(item => item.account_id === row.account_id).length)},
        {label: '余额', render: row => escapeHtml(balanceText(row))},
        {label: '结果', render: row => escapeHtml(balanceResultText(row))},
        {label: '操作', render: row => `<div class="buttons"><button class="secondary" data-account-action="quota" data-account-id="${escapeAttr(row.account_id)}">查额度</button><button class="secondary" data-account-action="test" data-account-id="${escapeAttr(row.account_id)}">模型探测</button></div>`}
      ], (data.accounts || []).map(account => ({...account, health: healthFor(account.account_id)})));
      renderDataTable('modelMonitorTable', '模型级健康', [
        {label: '渠道', render: row => escapeHtml(row.account.name || row.account_id)},
        {key: 'model_id', label: '模型'},
        {label: '状态', render: row => pill(row.model_health.status || 'unknown')},
        {label: '延迟', render: row => row.model_health.latency_ms == null ? '待探测' : `${row.model_health.latency_ms} ms`},
        {label: '结果', render: row => escapeHtml(row.model_health.last_error || '')},
        {label: '操作', render: row => `<button class="secondary" data-model-action="probe" data-account-id="${escapeAttr(row.account_id)}" data-model-id="${escapeAttr(row.model_id)}">探测模型</button>`}
      ], poolRows().map(row => ({...row, model_health: healthFor(row.account_id, row.model_id)})));
      renderLatencyChart(data.accounts || []);
    }

    function renderProviderConfigList() {
      const container = document.getElementById('providerConfigList');
      if (!container) return;
      const provider = selectedProvider();
      const configured = accountRows(provider);
      const configuredIds = new Set(configured.map(row => row.account.account_id));
      const pending = starterChannels()
        .filter(channel => !configuredIds.has(channel.account_id))
        .map(channel => ({pending: true, channel, priority: Number(channel.rank || 0)}));
      const rows = [...configured, ...pending].sort((a, b) => Number(b.priority || 0) - Number(a.priority || 0));
      container.innerHTML = rows.length ? `<div class="config-list">${rows.map(row => {
        if (row.pending) {
          const channel = row.channel;
          const modelText = (channel.models || []).join(', ') || '未配置模型';
          const quota = channel.quota_ref?.startsWith('cursorlink:') ? `CursorLink ${channel.quota_ref.slice('cursorlink:'.length)}` : (channel.quota_ref || '待配置');
          return `<div class="config-row pending">
            <div class="drag-handle" title="保存后可拖拽排序">待配</div>
            <div class="config-main">
              <div class="config-title">
                <span class="provider-avatar">${escapeHtml(providerName(provider).slice(0, 1))}</span>
                <strong>${escapeHtml(providerName(provider))} · ${escapeHtml(channel.name || channel.account_id)}</strong>
              </div>
              <div class="tag-row">
                <span class="pill warn">待填写 Key</span>
                <span class="pill info">中转</span>
                <span class="pill info">优先级 ${escapeHtml(channel.rank || 0)}</span>
                <span class="pill info">批处理 待测</span>
                <span class="pill info">并发 待测</span>
              </div>
              <span class="subtle">配置 ID：${escapeHtml(channel.account_id)} · 默认：${escapeHtml(channel.default_model || '未设置')} · 模型：${escapeHtml(modelText)} · 额度：${escapeHtml(quota)}</span>
            </div>
            <div class="config-actions">
              <button class="primary" data-channel-action="configure" data-channel-id="${escapeAttr(channel.account_id)}">配置</button>
            </div>
          </div>`;
        }
        const account = row.account;
        const quota = balanceText(account);
        const modelText = row.models.join(', ') || '未配置模型';
        const defaultModel = noteValue(account.notes, 'default_model') || row.models[0] || '未设置';
        const group = account.account_group === 'official' ? '官方' : '中转';
        const concurrency = noteValue(account.notes, 'max_concurrency') || noteValue(account.notes, 'probed_concurrency') || '未知';
        const rps = noteValue(account.notes, 'rps_limit') || '未知';
        const rpm = noteValue(account.notes, 'rpm_limit') || '未知';
        const batch = noteValue(account.notes, 'batch_support') || '未知';
        return `<div class="config-row" draggable="true" data-account-row="${escapeAttr(account.account_id)}">
          <div class="drag-handle" title="拖拽调整优先级">拖拽</div>
          <div class="config-main">
            <div class="config-title">
              <span class="provider-avatar">${escapeHtml(providerName(account.provider).slice(0, 1))}</span>
              <strong>${escapeHtml(providerName(account.provider))} · ${escapeHtml(account.name || account.account_id)}</strong>
            </div>
            <div class="tag-row">
              ${pill(account.status)}
              ${pill(row.health.status || 'unknown')}
              <span class="pill info">${escapeHtml(group)}</span>
              <span class="pill info">优先级 ${escapeHtml(row.priority)}</span>
              <span class="pill info">代理 ${escapeHtml(proxyText(account))}</span>
              <span class="pill info">并发 ${escapeHtml(concurrency)}</span>
              <span class="pill info">批处理 ${escapeHtml(batch)}</span>
              <span class="pill info">RPS ${escapeHtml(rps)}</span>
              <span class="pill info">RPM ${escapeHtml(rpm)}</span>
            </div>
            <span class="subtle">配置 ID：${escapeHtml(account.account_id)} · 默认：${escapeHtml(defaultModel)} · 模型：${escapeHtml(modelText)} · 额度：${escapeHtml(quota)}</span>
          </div>
          <div class="config-actions">
            <button class="primary" data-account-action="refresh" data-account-id="${escapeAttr(account.account_id)}">刷新</button>
            <button class="secondary" data-account-action="capability" data-account-id="${escapeAttr(account.account_id)}">测0-10并发/RPS</button>
            <button class="secondary" data-account-action="duplicate" data-account-id="${escapeAttr(account.account_id)}">复制条目</button>
            <button class="secondary" data-account-action="export-shell" data-account-id="${escapeAttr(account.account_id)}">导出脚本</button>
            <button class="secondary" data-account-action="export-codex" data-account-id="${escapeAttr(account.account_id)}">导出 Codex</button>
            <button class="secondary" data-account-action="edit" data-account-id="${escapeAttr(account.account_id)}">修改</button>
            <button class="secondary danger" data-account-action="delete" data-account-id="${escapeAttr(account.account_id)}">删除</button>
          </div>
        </div>`;
      }).join('')}</div>` : '<div class="panel-body subtle">当前厂商还没有渠道。添加官方渠道或中转站后，会只出现在这个厂商列表里。</div>';
    }

    function renderProjectBundlePreview() {
      const target = document.getElementById('project-bundle-preview');
      if (!target) return;
      target.textContent = JSON.stringify(state.projectBundle || {
        project_id: document.getElementById('project-id')?.value || '',
        slots: modelSlots.map(([slot, label, description]) => ({slot, label, description})),
        model_orders: projectOrderPayload().orders,
        rule: '同名模型按模型配置页渠道优先级解析；不可用时切到下一渠道或下一模型',
        integration: {
          manifest_path: '.omni/omni-hub.project.json',
          resolver_endpoint: 'http://127.0.0.1:8765/api/project-resolve',
          request_shape: {project_id: document.getElementById('project-id')?.value || '', slot: state.selectedSlot}
        },
        routes: []
      }, null, 2);
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
      renderProjectSlots();
      renderMetrics();
      renderTables();
      if (showMessage) showToast('数据已刷新');
    }

    async function checkAccount(accountId, modelId = '') {
      if (!accountId) throw new Error('请先选择配置');
      const payload = {account_id: accountId};
      if (modelId) payload.model_id = modelId;
      const data = await api('/api/model-probe', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      await refresh();
      const model = data.stream_check?.model_id || '';
      const latency = data.stream_check?.responseTimeMs;
      showToast(`连接测试完成：${data.stream_check?.status || data.health.status}${model ? ` · ${model}` : ''}${latency == null ? '' : ` · ${latency}ms`}`);
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
      const rows = accountRows(selectedProvider());
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
      document.getElementById('secret-ref').value = account.secret_ref || '';
      document.getElementById('proxy-url').value = account.proxy_url || '';
      document.getElementById('quota-ref').value = quotaText(account).replace(/^quota_ref=/, '');
      document.getElementById('model-ids').value = accountModelIds(accountId).join('\n');
      syncDefaultModelOptions(noteValue(account.notes, 'default_model'));
      document.getElementById('provider-capabilities').value = '';
      document.getElementById('provider-priority').value = String(accountPriority(accountId) || preset.rank || 90);
      document.getElementById('api-key').value = '';
      document.getElementById('max-concurrency').value = noteValue(account.notes, 'max_concurrency');
      document.getElementById('rpm-limit').value = noteValue(account.notes, 'rpm_limit');
      document.getElementById('tpm-limit').value = noteValue(account.notes, 'tpm_limit');
      document.getElementById('api-format').value = noteValue(account.notes, 'api_format');
      document.getElementById('auth-field').value = noteValue(account.notes, 'auth_field');
      document.getElementById('is-full-url').value = noteValue(account.notes, 'is_full_url');
      document.getElementById('models-url').value = noteValue(account.notes, 'models_url');
      document.getElementById('test-model').value = noteValue(account.notes, 'test_model');
      document.getElementById('test-prompt').value = noteValue(account.notes, 'test_prompt');
      document.getElementById('timeout-secs').value = noteValue(account.notes, 'timeout_secs');
      document.getElementById('max-retries').value = noteValue(account.notes, 'max_retries');
      document.getElementById('degraded-threshold-ms').value = noteValue(account.notes, 'degraded_threshold_ms');
      document.getElementById('cost-multiplier').value = noteValue(account.notes, 'cost_multiplier');
      document.getElementById('pricing-model-source').value = noteValue(account.notes, 'pricing_model_source');
      document.getElementById('model-reasoning-effort').value = noteValue(account.notes, 'model_reasoning_effort') || 'high';
      document.getElementById('wire-api').value = noteValue(account.notes, 'wire_api') || 'responses';
      document.getElementById('requires-openai-auth').value = noteValue(account.notes, 'requires_openai_auth') || 'true';
      document.getElementById('disable-response-storage').value = noteValue(account.notes, 'disable_response_storage') || 'true';
      document.getElementById('usage-template').value = noteValue(account.notes, 'usage_template') || 'auto';
      document.getElementById('usage-base-url').value = noteValue(account.notes, 'usage_base_url');
      document.getElementById('usage-endpoint').value = noteValue(account.notes, 'usage_endpoint');
      document.getElementById('usage-timeout-secs').value = noteValue(account.notes, 'usage_timeout_secs');
      document.getElementById('usage-max-retries').value = noteValue(account.notes, 'usage_max_retries');
      document.getElementById('usage-user-id').value = noteValue(account.notes, 'usage_user_id');
      document.getElementById('usage-access-token').value = '';
      document.getElementById('usage-access-token-ref').value = noteValue(account.notes, 'usage_access_token_ref');
      document.getElementById('channel-form-title').textContent = `修改 ${preset.name} 渠道`;
      document.getElementById('save-provider-button').textContent = '保存修改';
      document.getElementById('provider-list-title').textContent = `${preset.name} 渠道列表`;
      renderOfficialProviders();
      renderTables();
      updateScriptPreview();
      openProviderModal('edit');
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
      const data = await api('/api/provider-script', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId, format: 'shell'})
      });
      await copyText(data.script, '带 Key 的 export 脚本已复制');
    }

    async function copyAccountCodexConfig(accountId) {
      const account = accountById(accountId);
      if (!account.account_id) throw new Error('请先保存渠道');
      const data = await api('/api/provider-script', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId, format: 'codex_toml'})
      });
      await copyText(data.script, 'Codex config.toml 片段已导出');
    }

    async function checkBalance(accountId, notify = true) {
      const account = accountById(accountId);
      if (!account.account_id) throw new Error('配置不存在');
      const data = await api('/api/balance-check', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId})
      });
      state.balances[accountId] = data;
      renderTables();
      if (!notify) return data;
      if (!data.success) {
        const fallback = quotaText(account);
        showToast(data.error === 'unsupported balance provider' && fallback !== '未配置' ? `该厂商未接入余额接口；可去 ${fallback}` : `额度查询失败：${data.error || '未支持'}`, 'warn');
        return data;
      }
      const rows = data.data || [];
      const text = rows.map(row => `${row.plan_name || '余额'} ${row.remaining ?? '-'} ${row.unit || ''}`).join('；') || '余额接口无数据';
      showToast(`额度查询完成：${text}`);
      return data;
    }

    async function showQuota(accountId) {
      return checkBalance(accountId, true);
    }

    async function refreshAllBalances(notify = true) {
      const accounts = state.data?.accounts || [];
      for (const account of accounts) {
        try {
          await checkBalance(account.account_id, false);
        } catch (err) {
          state.balances[account.account_id] = {success: false, data: null, error: err.message || '余额查询失败'};
        }
      }
      renderTables();
      if (notify) showToast(`已查询 ${accounts.length} 个渠道额度`);
    }

    async function refreshAccount(accountId) {
      const account = accountById(accountId);
      if (!account.account_id) throw new Error('配置不存在');
      const [healthResult, balanceResult] = await Promise.allSettled([
        api('/api/model-probe', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({account_id: accountId})
        }),
        api('/api/balance-check', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({account_id: accountId})
        })
      ]);
      if (balanceResult.status === 'fulfilled') {
        state.balances[accountId] = balanceResult.value;
      } else {
        state.balances[accountId] = {success: false, data: null, error: balanceResult.reason?.message || '余额查询失败'};
      }
      await refresh(false);
      const healthData = healthResult.status === 'fulfilled' ? healthResult.value : null;
      const health = healthData
        ? (healthData.stream_check?.stage === 'secret' ? '待填写 API Key' : (healthData.stream_check?.status || healthData.health?.status || '完成'))
        : `失败：${healthResult.reason?.message || '连接测试失败'}`;
      const balance = state.balances[accountId];
      const balanceMessage = balance.success
        ? (balance.data || []).map(row => `${row.plan_name || '余额'} ${row.remaining ?? '-'} ${row.unit || ''}`).join('；')
        : (missingSecretText(balance) ? '余额待填写 API Key' : `余额失败：${balance.error || '未支持'}`);
      showToast(`刷新完成：${health}${balanceMessage ? ` · ${balanceMessage}` : ''}`);
    }

    async function capabilityProbe(accountId) {
      const account = accountById(accountId);
      if (!account.account_id) throw new Error('配置不存在');
      showToast('正在做 0-10 并发/RPS 探测，可能需要几十秒', 'warn');
      const data = await api('/api/channel-capability-probe', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId, max_concurrency: 10, max_rps: 10})
      });
      if (!data.success) {
        showToast(data.error_code === 'missing_secret' ? '请先填写 API Key，再测试并发和批处理' : `能力测试失败：${data.error || '未知错误'}`, 'warn');
        return;
      }
      await refresh(false);
      showToast(`能力测试完成：并发 ${data.concurrency.max_passed}/10 · RPS ${data.rate.max_passed}/10 · RPM ${data.rate.max_passed * 60} · 批处理 ${data.batch.supported ? '支持' : '未确认'}`);
    }

    async function duplicateAccount(accountId) {
      const data = await api('/api/provider-duplicate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId})
      });
      await refresh(false);
      showToast(`已复制为 ${data.account.account_id}`);
    }

    async function deleteAccount(accountId) {
      const account = accountById(accountId);
      if (!account.account_id) throw new Error('配置不存在');
      if (!confirm(`删除渠道 ${account.name || account.account_id}？相关模型路由和项目覆盖会一起移除。`)) return;
      await api('/api/provider-delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: accountId})
      });
      delete state.balances[accountId];
      await refresh(false);
      showToast('渠道已删除');
    }

    async function fetchModelsForDraft() {
      const payload = normalizeDraftProviderPayload(formPayload(document.getElementById('official-form')));
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
      syncDefaultModelOptions(document.getElementById('default-model').value || models[0]?.id || '');
      showToast(`已发现 ${models.length} 个模型，可选择默认模型`);
    }

    async function saveOfficialConfig(notify = true) {
      const payload = normalizeDraftProviderPayload(formPayload(document.getElementById('official-form')));
      const data = await api('/api/official-provider-config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      document.getElementById('api-key').value = '';
      document.getElementById('usage-access-token').value = '';
      if (data.account?.secret_ref) {
        document.getElementById('secret-ref').value = data.account.secret_ref;
      }
      const usageRef = noteValue(data.account?.notes, 'usage_access_token_ref');
      if (usageRef) document.getElementById('usage-access-token-ref').value = usageRef;
      await refresh();
      if (notify) {
        const secretMessage = data.secret_mode === 'local' ? 'API Key 已写入 .omni/secrets.json' : '密钥引用已保存';
        showToast(`渠道已保存；${secretMessage}`);
      }
      return data;
    }

    function projectOrderPayload() {
      const form = document.getElementById('project-import-form');
      const payload = formPayload(form);
      const draft = ensureProjectDraft(payload.project_id || projectDraftKey());
      payload.orders = modelSlots.map(([slot]) => ({
        slot,
        model_ids: draft[slot] || []
      }));
      return payload;
    }

    async function importProjectRoutes() {
      const payload = projectOrderPayload();
      const data = await api('/api/project-model-orders', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      state.projectBundle = data.bundle;
      state.selectedProjectId = payload.project_id;
      state.creatingProject = false;
      await refresh();
      renderProjectBundlePreview();
      showToast(`已保存 ${data.orders.length} 个能力槽模型顺序`);
      return data;
    }

    async function copyProjectBundle() {
      const projectId = document.getElementById('project-id').value.trim();
      if (!projectId) throw new Error('请先填写项目 ID');
      if (!state.projectBundle || state.projectBundle.project_id !== projectId) {
        const data = await api('/api/project-bundle', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({project_id: projectId})
        });
        state.projectBundle = data.bundle;
        renderProjectBundlePreview();
      }
      await copyText(JSON.stringify(state.projectBundle, null, 2), '项目模型包已复制');
    }

    async function copyProjectSection(section) {
      const projectId = document.getElementById('project-id').value.trim();
      if (!projectId) throw new Error('请先填写项目 ID');
      if (!state.projectBundle || state.projectBundle.project_id !== projectId) {
        const data = await api('/api/project-bundle', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({project_id: projectId})
        });
        state.projectBundle = data.bundle;
      }
      const integration = state.projectBundle.integration || {};
      if (section === 'manifest') {
        await copyText(JSON.stringify(integration.manifest || state.projectBundle, null, 2), 'omni-hub.project.json 已复制');
        return;
      }
      const request = integration.request_shape || {method: 'POST', body: {project_id: projectId, slot: state.selectedSlot}};
      const text = `curl -sS -X ${request.method || 'POST'} ${integration.resolver_endpoint || 'http://127.0.0.1:8765/api/project-resolve'} \\
  -H 'Content-Type: application/json' \\
  -d '${JSON.stringify(request.body || {project_id: projectId, slot: state.selectedSlot})}'`;
      await copyText(text, 'resolver 请求已复制');
    }

    document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
    document.getElementById('refresh').addEventListener('click', event => {
      withButtonLoading(event.currentTarget, '刷新中', () => refresh(true));
    });
    document.getElementById('add-channel').addEventListener('click', () => {
      const preset = state.officialProvider || officialProviders()[0];
      if (preset) applyOfficialProvider(preset, false);
      openProviderModal('add');
    });
    document.getElementById('project-new').addEventListener('click', () => {
      state.selectedProjectId = '';
      state.selectedSlot = 'default';
      state.projectBundle = null;
      state.creatingProject = true;
      state.projectDrafts.__draft__ = {};
      document.getElementById('project-id').value = '';
      document.getElementById('project-model-search').value = '';
      state.projectModelSearch = '';
      renderProjectList();
      renderProjectSlots();
      renderProjectModelPalette();
      renderProjectBundlePreview();
      showToast('已进入新建项目');
    });
    document.getElementById('close-provider-modal').addEventListener('click', closeProviderModal);
    document.getElementById('provider-modal').addEventListener('click', event => {
      if (event.target.id === 'provider-modal') closeProviderModal();
    });
    window.addEventListener('hashchange', () => setView(location.hash.replace('#', '') || 'overview'));
    window.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeProviderModal();
    });
    document.addEventListener('click', event => {
      const jump = event.target.closest('[data-jump]');
      if (jump) setView(jump.dataset.jump);
      const officialButton = event.target.closest('[data-official-index]');
      if (officialButton) applyOfficialProvider(officialProviders()[Number(officialButton.dataset.officialIndex)]);
      const channelButton = event.target.closest('[data-channel-action]');
      if (channelButton) {
        applyStarterChannel(starterChannelById(channelButton.dataset.channelId));
      }
      const accountAction = event.target.closest('[data-account-action]');
      if (accountAction) {
        const accountId = accountAction.dataset.accountId;
        const action = accountAction.dataset.accountAction;
        if (action === 'edit') editAccount(accountId);
        if (action === 'test') withButtonLoading(accountAction, '测试中', () => checkAccount(accountId));
        if (action === 'refresh') withButtonLoading(accountAction, '刷新中', () => refreshAccount(accountId));
        if (action === 'capability') withButtonLoading(accountAction, '探测中', () => capabilityProbe(accountId));
        if (action === 'duplicate') withButtonLoading(accountAction, '复制中', () => duplicateAccount(accountId));
        if (action === 'delete') withButtonLoading(accountAction, '删除中', () => deleteAccount(accountId));
        if (action === 'export-shell') withButtonLoading(accountAction, '导出中', () => copyAccountScript(accountId));
        if (action === 'export-codex') withButtonLoading(accountAction, '导出中', () => copyAccountCodexConfig(accountId));
        if (action === 'quota') withButtonLoading(accountAction, '查询中', () => showQuota(accountId));
      }
      const projectButton = event.target.closest('[data-project-id]');
      if (projectButton) {
        state.selectedProjectId = projectButton.dataset.projectId;
        state.creatingProject = false;
        state.projectBundle = null;
        document.getElementById('project-id').value = state.selectedProjectId;
        ensureProjectDraft(state.selectedProjectId);
        renderProjectList();
        renderProjectSlots();
        renderProjectModelPalette();
        renderProjectBundlePreview();
      }
      const slotButton = event.target.closest('[data-project-slot-select]');
      if (slotButton) {
        state.selectedSlot = slotButton.dataset.projectSlotSelect;
        renderProjectSlots();
        renderProjectModelPalette();
        renderProjectBundlePreview();
      }
      const modelAdd = event.target.closest('[data-model-add]');
      if (modelAdd) {
        const models = slotModels(state.selectedSlot);
        setSlotModels(state.selectedSlot, [...models, modelAdd.dataset.modelAdd]);
      }
      const modelRemove = event.target.closest('[data-model-remove]');
      if (modelRemove) {
        const slot = modelRemove.dataset.slot;
        setSlotModels(slot, slotModels(slot).filter(item => item !== modelRemove.dataset.modelRemove));
      }
      const modelUp = event.target.closest('[data-model-up]');
      if (modelUp) {
        const slot = modelUp.dataset.slot;
        const models = [...slotModels(slot)];
        const index = models.indexOf(modelUp.dataset.modelUp);
        if (index > 0) {
          [models[index - 1], models[index]] = [models[index], models[index - 1]];
          setSlotModels(slot, models);
        }
      }
      const modelDown = event.target.closest('[data-model-down]');
      if (modelDown) {
        const slot = modelDown.dataset.slot;
        const models = [...slotModels(slot)];
        const index = models.indexOf(modelDown.dataset.modelDown);
        if (index >= 0 && index < models.length - 1) {
          [models[index + 1], models[index]] = [models[index], models[index + 1]];
          setSlotModels(slot, models);
        }
      }
      const projectCopySection = event.target.closest('[data-project-copy-section]');
      if (projectCopySection) {
        withButtonLoading(projectCopySection, '复制中', () => copyProjectSection(projectCopySection.dataset.projectCopySection));
      }
      const modelAction = event.target.closest('[data-model-action]');
      if (modelAction) {
        const action = modelAction.dataset.modelAction;
        if (action === 'probe') {
          withButtonLoading(modelAction, '探测中', () => checkAccount(modelAction.dataset.accountId, modelAction.dataset.modelId));
        }
      }
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
        if (event.target.id === 'project-id') {
          state.selectedProjectId = event.target.value.trim();
          state.creatingProject = !projectRows().some(row => row.project_id === state.selectedProjectId);
          state.projectBundle = null;
          ensureProjectDraft(state.selectedProjectId || '__draft__');
          renderProjectList();
          renderProjectSlots();
          renderProjectModelPalette();
          renderProjectBundlePreview();
        }
        if (event.target.id === 'project-model-search') {
          state.projectModelSearch = event.target.value;
          renderProjectModelPalette();
        }
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
      const button = event.submitter || document.getElementById('save-provider-button');
      await withButtonLoading(button, '保存中', async () => {
        await saveOfficialConfig(true);
        closeProviderModal();
      });
    });
    document.getElementById('test-official-draft').addEventListener('click', async event => {
      await withButtonLoading(event.currentTarget, '测试中', async () => {
        const data = await saveOfficialConfig(false);
        await checkAccount(data.account.account_id);
      });
    });
    document.getElementById('fetch-models').addEventListener('click', async event => {
      await withButtonLoading(event.currentTarget, '发现中', async () => {
        await fetchModelsForDraft();
      });
    });
    document.getElementById('copy-script').addEventListener('click', async event => {
      await withButtonLoading(event.currentTarget, '复制中', async () => {
        const accountId = document.getElementById('account-id').value;
        if (!accountById(accountId).account_id) throw new Error('请先保存渠道再复制带 Key 脚本');
        await copyAccountScript(accountId);
      });
    });
    document.getElementById('copy-codex-config').addEventListener('click', async event => {
      await withButtonLoading(event.currentTarget, '导出中', async () => {
        const accountId = document.getElementById('account-id').value;
        await copyAccountCodexConfig(accountId);
      });
    });
    document.getElementById('model-ids').addEventListener('input', () => syncDefaultModelOptions());
    document.getElementById('realtime-toggle').addEventListener('click', event => {
      state.realtime = !state.realtime;
      event.target.textContent = state.realtime ? '停止定期查额度' : '开始定期查额度';
      if (state.realtime) {
        refreshAllBalances(false);
        state.realtimeTimer = setInterval(() => refreshAllBalances(false), 5 * 60 * 1000);
        showToast('定期查额度已开启；不会自动做模型探测');
      } else {
        clearInterval(state.realtimeTimer);
        showToast('定期查额度已停止');
      }
    });
    document.getElementById('check-all').addEventListener('click', async event => {
      await withButtonLoading(event.currentTarget, '刷新中', async () => {
        await refreshAllBalances(true);
      });
    });
    document.getElementById('project-import-form').addEventListener('submit', async event => {
      event.preventDefault();
      const button = event.submitter || event.currentTarget.querySelector('button');
      await withButtonLoading(button, '保存中', async () => {
        await importProjectRoutes();
      });
    });
    document.getElementById('copy-project-bundle').addEventListener('click', async event => {
      await withButtonLoading(event.currentTarget, '复制中', async () => {
        await copyProjectBundle();
      });
    });

    setView(location.hash.replace('#', '') || 'overview');
    refresh();
  </script>
</body>
</html>
"""
