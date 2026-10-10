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

    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f0f2f5; color: #333; padding: 20px; }
    .container { max-width: 1400px; margin: 0 auto; }
    h1 { font-size: 24px; margin-bottom: 4px; color: #1a1a2e; }
    .subtitle { color: #666; font-size: 14px; margin-bottom: 20px; }
    .legend { background: #fff; border-radius: 10px; padding: 16px 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); font-size: 14px; line-height: 1.8; }
    .legend strong { color: #1a1a2e; }
    .legend .tag { display: inline-block; padding: 0 8px; border-radius: 4px; font-size: 12px; font-weight: 600; margin: 0 2px; }
    .tag-ob { background: #ffebee; color: #c62828; }
    .tag-os { background: #e8f5e9; color: #2e7d32; }
    .tag-nl { background: #fff3e0; color: #e65100; }
    .tag-bull { background: #e3f2fd; color: #1565c0; }
    .tag-bear { background: #f3e5f5; color: #6a1b9a; }
    .tag-div { background: #fff8e1; color: #f57f17; }
    .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .summary-card { background: #fff; border-radius: 10px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    .summary-card .num { font-size: 28px; font-weight: 700; }
    .summary-card .label { font-size: 13px; color: #888; margin-top: 2px; }
    .card-red .num { color: #c62828; }
    .card-green .num { color: #2e7d32; }
    .card-orange .num { color: #e65100; }
    .card-blue .num { color: #1565c0; }
    .card-purple .num { color: #6a1b9a; }
    .card-gold .num { color: #f57f17; }
    .card-gray .num { color: #666; }
    table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    th { background: #1a1a2e; color: #fff; padding: 12px 10px; font-size: 13px; font-weight: 600; text-align: center; white-space: nowrap; }
    td { padding: 10px; text-align: center; font-size: 13px; border-bottom: 1px solid #f0f0f0; }
    tr:hover { background: #f8f9ff; }
    .name { text-align: left; font-weight: 500; }
    .flag { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; min-width: 50px; }
    .flag.overbought { background: #ffebee; color: #c62828; }
    .flag.oversold { background: #e8f5e9; color: #2e7d32; }
    .flag.newlow { background: #fff3e0; color: #e65100; }
    .flag.normal { background: #f5f5f5; color: #666; }
    .signal { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; white-space: nowrap; }
    .signal.resonance_strong { background: #e3f2fd; color: #1565c0; }
    .signal.resonance_weak { background: #f3e5f5; color: #6a1b9a; }
    .signal.overbought_resonance { background: #d32f2f; color: #fff; }
    .signal.oversold_resonance { background: #2e7d32; color: #fff; }
    .signal.newlow_resonance { background: #e65100; color: #fff; }
    .signal.divergence_dw { background: #fff8e1; color: #f57f17; }
    .signal.divergence_wd { background: #fff8e1; color: #f57f17; }
    .signal.partial { background: #f5f5f5; color: #888; }
    .signal.insufficient { background: #eceff1; color: #90a4ae; }
    .price { font-weight: 600; }
    .chg { font-weight: 600; }
    .chg.up { color: #c62828; }
    .chg.down { color: #2e7d32; }
    .chg.flat { color: #666; }
    .filter-bar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
    .filter-btn { padding: 5px 14px; border: 1px solid #ddd; border-radius: 16px; background: #fff; font-size: 12px; cursor: pointer; transition: all 0.15s; }
    .filter-btn:hover { border-color: #1a1a2e; }
    .filter-btn.active { background: #1a1a2e; color: #fff; border-color: #1a1a2e; }
    .footer { margin-top: 16px; font-size: 12px; color: #999; text-align: center; }
    .section-title { font-size: 16px; font-weight: 600; margin: 20px 0 10px; color: #1a1a2e; }
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
