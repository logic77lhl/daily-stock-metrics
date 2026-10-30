# -*- coding: utf-8 -*-
"""站点样式资产的**单一来源**。

为什么要有它：实测归档的 90 个报告页各自内联了同一份样式表
（合计约 421KB，其中 416KB 是重复），而生成它的 `<style>` 在 6 个模块里有
12 处发射点。改一处颜色要改 12 个地方，漏掉一处就产生「同一站点里两种样式」
这种只在肉眼比对时才发现的缺陷。

**现在真正的单一来源是 `site_theme.py`** —— 这里只做两件事：
1. 给报告页补少量专属规则（汇总卡网格、名称列左对齐）；
2. 保持 `REPORT_CSS` 这个对外接口不变（报告内联 + 站点外链都读它）。

报告本身仍然内联这份样式：邮件客户端会剥掉 `<link>`，`output/` 下的报告
也要能单独打开。站点侧由 `build_pages.externalize_style()` 换成
指向 `assets/report.css` 的链接。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from site_theme import page_css  # noqa: E402

# 报告页专属：汇总卡是 4 列自适应，名称列左对齐、价格加粗
_REPORT_EXTRA = """
td.name,td.tl,.name{text-align:left;font-weight:500;font-family:inherit;font-size:13px}
.price{font-weight:600}
.chg.flat{color:var(--dim2)}
.container{max-width:1400px}   /* 报告是宽表，比正文页宽一档，这是刻意例外 */
@media(max-width:768px){.container{max-width:100%}}
"""

REPORT_CSS = page_css(_REPORT_EXTRA)

# 站点侧引用的相对路径（报告位于 docs/<日期>/<市场>.html）
ASSET_DIR = "assets"
ASSET_NAME = "report.css"
REPORT_CSS_HREF_FROM_DATE_PAGE = f"../{ASSET_DIR}/{ASSET_NAME}"
