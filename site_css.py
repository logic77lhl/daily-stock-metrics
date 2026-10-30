# -*- coding: utf-8 -*-
"""站点样式资产的**单一来源**。

为什么要有它：实测归档的 90 个报告页各自内联了**同一份 4680 字节**样式表
（合计约 421KB，其中 416KB 是重复），而生成它的 `<style>` 在 6 个模块里有
12 处发射点。改一处颜色要改 12 个地方，漏掉一处就产生「同一站点里两种样式」
这种只在肉眼比对时才发现的缺陷。

这里的内容是**从已生成页面逐字节提取**的（不是手抄 generate_report.py 的
f-string —— 那里的 `{{ }}` 是双写转义）。报告本身仍然内联这份样式：邮件客户端
会剥掉 `<link>`，`output/` 下的报告也要能单独打开。站点侧由
`build_pages.externalize_style()` 把它换成指向 `assets/report.css` 的链接。
"""

from __future__ import annotations

REPORT_CSS = """\
    :root{
      --bg:#0a0e15; --bg2:#0e141d; --panel:#131b26; --panel2:#17202d;
      --line:#22303f; --line2:#2d3d4f;
      --tx:#e8eef7; --dim:#8fa3ba; --dim2:#7b8fa5;
      --acc:#ffd21e; --ok:#34d399; --warn:#fbbf24; --bad:#fb7185;
      --blue:#7dd3fc; --violet:#a78bfa;
      --mono:ui-monospace,"Cascadia Code",Consolas,"SF Mono",monospace;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      color: var(--tx); padding: 20px;
      background:
        radial-gradient(1100px 500px at 12% -10%, rgba(255,210,30,.07), transparent 60%),
        radial-gradient(900px 480px at 92% 0%, rgba(125,211,252,.06), transparent 60%),
        var(--bg);
      -webkit-font-smoothing: antialiased; }
    .container { max-width: 1400px; margin: 0 auto; }
    h1 { font-size: 24px; margin-bottom: 4px; color: var(--tx); }
    .subtitle { color: var(--dim2); font-size: 14px; margin-bottom: 20px; }
    .legend { background: var(--panel); border-radius: 12px; padding: 16px 20px; margin-bottom: 20px;
      border: 1px solid var(--line); font-size: 14px; line-height: 1.8; color: var(--dim); }
    .legend strong { color: var(--tx); }
    .legend .tag { display: inline-block; padding: 0 8px; border-radius: 4px; font-size: 12px; font-weight: 600; margin: 0 2px; }
    .tag-ob { background: rgba(251,113,133,.14); color: var(--bad); }
    .tag-os { background: rgba(52,211,153,.14); color: var(--ok); }
    .tag-nl { background: rgba(251,191,36,.14); color: var(--warn); }
    .tag-bull { background: rgba(125,211,252,.14); color: var(--blue); }
    .tag-bear { background: rgba(167,139,250,.14); color: var(--violet); }
    .tag-div { background: rgba(255,210,30,.14); color: var(--acc); }
    .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .summary-card { background: linear-gradient(180deg, var(--panel), var(--bg2));
      border: 1px solid var(--line); border-radius: 12px; padding: 16px 20px; }
    .summary-card .num { font: 800 28px/1.15 var(--mono); letter-spacing: -.5px; }
    .summary-card .label { font-size: 12px; color: var(--dim2); margin-top: 4px; }
    .card-red .num { color: var(--bad); }
    .card-green .num { color: var(--ok); }
    .card-orange .num { color: var(--warn); }
    .card-blue .num { color: var(--blue); }
    .card-purple .num { color: var(--violet); }
    .card-gold .num { color: var(--acc); }
    .card-gray .num { color: var(--dim); }
    table { width: 100%; border-collapse: collapse; background: var(--panel);
      border-radius: 12px; overflow: hidden; border: 1px solid var(--line);
      font-variant-numeric: tabular-nums; }
    th { background: var(--panel2); color: var(--dim2); padding: 11px 10px; font-size: 11.5px;
      font-weight: 600; text-align: center; white-space: nowrap; letter-spacing: .3px;
      border-bottom: 1px solid var(--line2); }
    td { padding: 10px; text-align: center; font: 12.5px/1.5 var(--mono);
      border-bottom: 1px solid var(--line); color: var(--tx); }
    td.name, td.tl { font-family: inherit; font-size: 13px; }
    tr:hover td { background: rgba(255,255,255,.022); }
    .name { text-align: left; font-weight: 500; }
    .flag { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
      font-weight: 600; min-width: 50px; font-family: inherit; }
    .flag.overbought { background: rgba(251,113,133,.14); color: var(--bad); }
    .flag.oversold { background: rgba(52,211,153,.14); color: var(--ok); }
    .flag.newlow { background: rgba(251,191,36,.14); color: var(--warn); }
    .flag.normal { background: rgba(255,255,255,.06); color: var(--dim2); }
    .signal { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
      font-weight: 600; white-space: nowrap; font-family: inherit; }
    .signal.resonance_strong { background: rgba(125,211,252,.14); color: var(--blue); }
    .signal.resonance_weak { background: rgba(167,139,250,.14); color: var(--violet); }
    .signal.overbought_resonance { background: rgba(251,113,133,.2); color: var(--bad); }
    .signal.oversold_resonance { background: rgba(52,211,153,.2); color: var(--ok); }
    .signal.newlow_resonance { background: rgba(251,191,36,.2); color: var(--warn); }
    .signal.divergence_dw { background: rgba(255,210,30,.14); color: var(--acc); }
    .signal.divergence_wd { background: rgba(255,210,30,.14); color: var(--acc); }
    .signal.partial { background: rgba(255,255,255,.06); color: var(--dim2); }
    .signal.insufficient { background: rgba(255,255,255,.04); color: var(--dim2); }
    .price { font-weight: 600; }
    .chg { font-weight: 600; }
    .chg.up { color: var(--bad); }
    .chg.down { color: var(--ok); }
    .chg.flat { color: var(--dim2); }
    .filter-bar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
    .filter-btn { padding: 5px 14px; border: 1px solid var(--line); border-radius: 999px;
      background: var(--panel); color: var(--dim); font-size: 12px; cursor: pointer;
      transition: all 0.15s; }
    .filter-btn:hover { border-color: var(--line2); color: var(--tx); }
    .filter-btn.active { background: rgba(255,210,30,.14); color: var(--acc);
      border-color: rgba(255,210,30,.35); }
    .footer { margin-top: 16px; font-size: 12px; color: var(--dim2); text-align: center; }
    .section-title { font-size: 16px; font-weight: 700; margin: 20px 0 10px; color: var(--tx); }
    @media (max-width: 768px) {
        body { padding: 8px; }
        h1 { font-size: 18px; }
        .subtitle { font-size: 12px; }
        .legend { padding: 10px 12px; font-size: 12px; }
        .summary-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; }
        .summary-card { padding: 10px 12px; }
        .summary-card .num { font-size: 20px; }
        .summary-card .label { font-size: 11px; }
        table { font-size: 11px; }
        th, td { padding: 5px 3px; white-space: nowrap; }
        .filter-bar { gap: 4px; }
        .filter-btn { padding: 4px 10px; font-size: 11px; }
        .section-title { font-size: 14px; }
    }
"""

# 站点侧引用的相对路径（报告位于 docs/<日期>/<市场>.html）
ASSET_DIR = "assets"
ASSET_NAME = "report.css"
REPORT_CSS_HREF_FROM_DATE_PAGE = f"../{ASSET_DIR}/{ASSET_NAME}"
