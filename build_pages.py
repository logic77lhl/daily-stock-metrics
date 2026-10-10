# -*- coding: utf-8 -*-
"""把每日报告整理为 GitHub Pages 站点(docs/)：日期索引 + 回测/价值标的页。

用法:
    python build_pages.py
"""

import datetime
import glob
import html
import os
import re
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
MARKETS = [
    ("A股", "output", "a"),
    ("ETF", "output_etf", "etf"),
    ("港股", "output_hk", "hk"),
]
BT_SOURCES = [("个股", "a"), ("ETF", "etf"), ("HK", "hk")]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 用于 _strip_outer_div 的深度扫描与 _split_leading_title 的前导块剥离
_DIV_TOKEN = re.compile(r"<div\b[^>]*>|</div>")
_STYLE_BLOCK = re.compile(r"^\s*<style\b[^>]*>.*?</style>\s*", re.DOTALL | re.IGNORECASE)
_LEADING_TITLE = re.compile(r"^\s*<div\b[^>]*>([^<]*)</div>\s*")
# 归档页最多展示多少天。必须与 prune_outputs 的保留策略取同一个数，
# 否则站点会宣称 120 天、链接却指向仓库里已经被裁掉的报告。
MAX_DAYS = int(os.environ.get("DSM_KEEP_DAYS", "30"))
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# import 同级 market_insights（避免在 sys.path 未就绪时失败）
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import site_css  # noqa: E402
try:
    import market_insights  # noqa: E402
except Exception:  # pragma: no cover
    market_insights = None


def _base_style_re():
    """匹配报告里的**基础样式**块。

    报告里不止一个 `<style>`：基础样式之外，嵌入的 market_insights /
    strategy_summary 片段也各带一小段。基础样式是唯一以 `* {` 开头的那一份，
    按这个特征定位才不会误伤片段样式（它们的顺序和数量都随数据变化）。
    """
    return re.compile(r"<style>\s*\* \{.*?</style>", re.DOTALL)


def externalize_style(html_text):
    """把内联的基础样式换成指向 `assets/report.css` 的链接。

    为什么站点侧外链、报告侧保持内联：
      * 邮件客户端会剥掉 `<link>`，`output/` 下的报告也要能单独打开 ——
        所以 `generate_report` 产出的报告必须自包含；
      * 站点侧有 90 个归档页共用**同一份 4686 字节**样式表（合计约 421KB，
        其中 416KB 是纯重复），外链后浏览器只下载一次。

    只在复制进 docs/ 时转换，源报告文件保持原样。
    """
    replacement = f'<link rel="stylesheet" href="{site_css.REPORT_CSS_HREF_FROM_DATE_PAGE}">'
    new_text, n = _base_style_re().subn(lambda m: replacement, html_text, count=1)
    if n == 0:
        # 不是致命错误（报告仍自包含、能正常显示），但说明样式结构变了、
        # 外链优化静默失效 —— 必须留痕。
        print("::warning::报告里没找到基础样式块，未能外链（页面仍可正常显示）")
    return new_text


def write_css_asset():
    """写出站点样式资产。返回写入的字节数。"""
    asset_dir = os.path.join(DOCS_DIR, site_css.ASSET_DIR)
    os.makedirs(asset_dir, exist_ok=True)
    path = os.path.join(asset_dir, site_css.ASSET_NAME)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(site_css.REPORT_CSS)
    return len(site_css.REPORT_CSS.encode("utf-8"))


def collect():
    """收集每个市场每天的 **数据**（metrics CSV），而不是派生的报告 HTML。

    为什么改判据：日报 HTML/MD 已不再入库（实测一次日常提交里 .html 占
    2973/4014 行 = 74%，而它完全可由 CSV 重建）。判据必须锚在**源头**上，
    否则站点会在「报告没入库」时整片空掉。报告由 `materialize()` 在构建时
    从 CSV 物化出来。
    """
    entries = {}
    for label, out_dir, key in MARKETS:
        full_dir = os.path.join(BASE_DIR, out_dir)
        if not os.path.isdir(full_dir):
            continue
        for name in os.listdir(full_dir):
            day_dir = os.path.join(full_dir, name)
            if not (DATE_RE.match(name) and os.path.isdir(day_dir)):
                continue
            metrics = os.path.join(day_dir, f"metrics_{name}.csv")
            if os.path.exists(metrics):
                entries.setdefault(name, {})[key] = metrics
    return entries


def materialize(dates):
    """把 (日期, {key: metrics_csv}) 物化成 (日期, {key: report_html})。

    报告是派生数据，不入库；构建时按 CSV 重建。已在磁盘上的（例如刚跑完采集
    的当日）不会重复生成。生成失败的 key 会被剔除，而不是留下一个指向空文件
    的链接 —— 首页的过期横幅与 warnings 仍会如实列出缺块。
    """
    import reports

    out = []
    for date, keys in dates:
        resolved = {}
        for key, metrics in keys.items():
            day_dir = os.path.dirname(metrics)
            html = reports.ensure(metrics, day_dir, date, key)
            if html:
                resolved[key] = html
            else:
                print(f"::warning::{date} {key} 报告物化失败（数据在但报告没生成）")
        if resolved:
            out.append((date, resolved))
    return out


def _latest(pattern, out_dir=None):
    base = out_dir if out_dir else os.path.join(BASE_DIR, "output")
    files = sorted(glob.glob(os.path.join(base, pattern)))
    return files[-1] if files else None


def stale_market_warnings():
    """站点内容是否落后于「最近一个已收盘交易日」。

    这是三周冻结事故的直接补救。当时站点停在 2026-09-18 而 Actions 全绿：
    每个市场都在各自的日期上「成功」过，页面也照常构建，读者完全无法区分
    「今天没有数据」和「已经三周没有数据」。数据源故障必须**在页面上**可见，
    否则沉默就是最坏的失败模式。
    """
    try:
        import trading_calendar
    except Exception as exc:  # pragma: no cover
        print(f"[warn] 无法加载 trading_calendar，跳过数据新鲜度检查: {exc}")
        return []

    out = []
    for label, out_dir, market in (("A股", "output", "A"),
                                   ("ETF", "output_etf", "A"),
                                   ("港股通", "output_hk", "HK")):
        try:
            target = trading_calendar.latest_closed_trading_day(market=market)
        except Exception as exc:  # pragma: no cover
            print(f"[warn] {label} 目标交易日推导失败: {exc}")
            continue
        if target is None:
            continue
        done = os.path.join(BASE_DIR, out_dir, target.isoformat(), "DONE")
        if not os.path.exists(done):
            out.append(f"{label}：最近一个已收盘交易日 {target.isoformat()} 没有数据"
                       f"（数据源故障，或采集被跳过）")
    return out


def build_insights_page(latest_date):
    """构造 insights.html：顶部 Tab 切换（4个），每块独立展示，首屏加 KPI 摘要带。"""
    if market_insights is None or latest_date is None:
        return ""
    m_csv = os.path.join(BASE_DIR, "output", latest_date, f"metrics_{latest_date}.csv")
    b_csv = os.path.join(BASE_DIR, "output", latest_date, f"market_breadth_{latest_date}.csv")
    if not os.path.exists(m_csv):
        return ""

    sector = market_insights.sector_temperature(m_csv, market="A")
    breadth = market_insights.market_breadth_dashboard(b_csv, m_csv, market="A")
    opp = market_insights.opportunity_board(m_csv, market="A", top_n=30)

    # ---- KPI 摘要带：挑最核心 6 个一眼看的指标 ----
    import pandas as _pd
    kpis = []
    if os.path.exists(b_csv):
        try:
            b = _pd.read_csv(b_csv).iloc[0]
            up_down = f"{int(b['上涨家数'])}<span style='color:#ff8787'>↑</span> / {int(b['下跌家数'])}<span style='color:#69db7c'>↓</span>"
            limit = f"{int(b['涨停家数'])}<span style='color:#fa5252'>停涨</span> / {int(b['跌停家数'])}<span style='color:#37b24d'>停跌</span>"
            vol_str = f"{b['成交额_亿']:,.0f}<span style='color:#8791a8;font-size:12px'>亿</span>"
            from market_insights import _temp_color, _temp_label
            t_cmp = float(b["综合温度"])
            temp_str = (f"<span style='display:inline-block;background:{_temp_color(t_cmp)};color:#fff;"
                        f"padding:3px 10px;border-radius:999px;font-weight:700;font-size:14px'>"
                        f"{t_cmp:.0f} · {_temp_label(t_cmp)}</span>")
            kpis = [("上涨/下跌", up_down), ("涨停/跌停", limit), ("成交额", vol_str),
                    ("综合温度", temp_str), ("活跃市值", f"{b['活跃市值_亿']:,.0f}亿"),
                    ("上涨占比", f"{b['上涨占比%']:.1f}%")]
        except Exception:
            pass
    # 池内 KPI（补充 2 个）
    try:
        df = _pd.read_csv(m_csv, dtype={"代码": str})
        n = len(df)
        dj = _pd.to_numeric(df["日线J"], errors="coerce") if "日线J" in df.columns else None
        if dj is not None and dj.notna().any():
            newlow = int((dj.dropna() < 0).sum())
            newhigh = int((dj.dropna() > 100).sum())
            kpis.append((f"Top{n}池 新极值", f"<span style='color:#1971c2'>新低{newlow}</span> / <span style='color:#d9480f'>新高{newhigh}</span>"))
        if "双均线多头" in df.columns:
            bull = _pd.to_numeric(df["双均线多头"], errors="coerce").dropna()
            if len(bull):
                pct = bull.mean() * 100
                kpis.append((f"Top{n}池 多头占比", f"<b>{pct:.0f}%</b>"))
    except Exception:
        pass

    kpi_chips = ""
    for label, value in kpis[:8]:
        kpi_chips += (f'<div class="kpi"><div class="k-label">{label}</div>'
                      f'<div class="k-value">{value}</div></div>')

    # ---- 板块温度完整表（所有行业，不在 Tab1 里展示，挪到 Tab2）----
    sector_table_html = ""
    sdf = sector["data"]
    if sdf is not None and not sdf.empty:
        import pandas as _pd2
        def _cell(v, digits=1, suffix=""):
            if _pd2.isna(v):
                return "-"
            return f"{float(v):.{digits}f}{suffix}"

        rows = ""
        from market_insights import _temp_color, _temp_label
        for _, r in sdf.iterrows():
            temp_v = r.get("板块温度")
            color = _temp_color(temp_v)
            band = _temp_label(temp_v) if _pd2.notna(temp_v) else "-"
            # NaN 安全
            n_val = r.get("标的数", 0)
            try:
                n_int = int(n_val)
            except Exception:
                n_int = 0
            rows += f"""<tr>
<td class="tl">{r.get("行业","")} <span class="mute">({n_int})</span></td>
<td style="text-align:center"><span class="temp" style="background:{color}">{_cell(temp_v,1)} · {band}</span></td>
<td class="tr">{_cell(r.get("平均涨跌幅%"), 2, "%")}</td>
<td class="tr">{_cell(r.get("平均日线J"),1)}</td>
<td class="tr">{_cell(r.get("平均周线J"),1)}</td>
<td class="tr">{_cell(r.get("平均PE分位%"),1)}</td>
<td class="tr">{_cell(r.get("平均PB分位%"),1)}</td>
<td class="tr">{_cell(r.get("均线多头占比%"),0,"%")}</td>
</tr>"""
        sector_table_html = f"""
<div class="tblwrap">
<table class="datatable">
<thead><tr>
<th>行业</th><th>板块温度</th><th>均涨跌幅</th><th>日J</th><th>周J</th><th>PE分位</th><th>PB分位</th><th>多头占比</th>
</tr></thead>
<tbody>{rows}</tbody></table>
</div>"""

    # 把每个面板包成 panel div（与 Tab 交互配合）
    def _panel(tab_id, inner, title_extra=""):
        return (f'<section class="panel" id="panel-{tab_id}" data-tab="{tab_id}">'
                f'{inner}'
                f'</section>')

    tab_items = [
        ("breadth", "📡 大盘宽度", (breadth.get("html") or '<div class="muted">暂无大盘宽度数据</div>')),
        ("sector", "🏭 板块温度", ((sector.get("html") or '<div class="muted">暂无板块温度数据</div>')
                                   + ("<div class='section-sub'>全部行业明细表</div>" + sector_table_html if sector_table_html else ""))),
        ("oversell", "🧊 超跌机会", (opp.get("oversold_html") or '<div class="muted">今日无符合条件的超跌标的</div>')),
        ("overbuy", "🔥 超买观察", (opp.get("overbought_html") or '<div class="muted">今日无符合条件的超买标的</div>')),
    ]

    tabs_html = ""
    panels_html = ""
    tab_idx = {}
    for i, (tid, tlabel, tcontent) in enumerate(tab_items):
        tab_idx[tid] = i
        active_cls = " active" if i == 0 else ""
        show_style = "" if i == 0 else ' style="display:none"'
        tabs_html += (f'<button class="tab-btn{active_cls}" data-tabtarget="{tid}" '
                      f'onclick="switchTab(this)">{tlabel}</button>')
        panels_html += _panel(tid, tcontent).replace(
            '<section class="panel"',
            f'<section class="panel" {show_style}', 1)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>市场洞察 · {latest_date}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:linear-gradient(180deg,#eef1f7 0%,#f7f8fc 260px,#f7f8fc);color:#1c2333;
font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
min-height:100vh}}
a{{text-decoration:none;color:inherit}}

/* ---- HEADER ---- */
header{{background:linear-gradient(135deg,#141e30 0%,#243b55 60%,#2d4a6e 100%);
padding:34px 16px 24px;text-align:center;position:relative;overflow:hidden}}
header:before{{content:"";position:absolute;inset:0;
background:radial-gradient(ellipse at 15% 0%,rgba(255,255,255,.10),transparent 55%),
radial-gradient(ellipse at 85% 100%,rgba(77,171,247,.20),transparent 50%)}}
header .wrap{{max-width:1180px;margin:0 auto;position:relative}}
header h1{{margin:0;font-size:23px;color:#fff;letter-spacing:.6px;font-weight:700}}
header p{{margin:8px 0 0;color:#aab4cf;font-size:12.5px}}
.breadcrumb{{display:inline-block;margin-top:14px;padding:7px 16px;border-radius:99px;
color:#fff;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);
font-size:12px;backdrop-filter:blur(8px)}}

/* ---- KPI CHIP ROW ---- */
.kpi-row{{max-width:1180px;margin:-26px auto 14px;padding:0 14px;position:relative;z-index:2;
display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
.kpi{{background:#fff;border-radius:12px;padding:12px 14px;
box-shadow:0 4px 16px rgba(28,35,51,.08);border:1px solid #eceef4;
min-height:68px}}
.k-label{{font-size:11px;color:#8791a8;letter-spacing:.2px}}
.k-value{{margin-top:6px;font-size:16px;font-weight:700;color:#1c2333;line-height:1.25}}
@media(max-width:780px){{.kpi-row{{grid-template-columns:repeat(2,1fr)}}}}

/* ---- MAIN ---- */
main{{max-width:1180px;margin:0 auto 30px;padding:0 14px}}

/* ---- TABS ---- */
.tabs{{background:#fff;border-radius:12px;padding:6px;margin:4px 0 16px;
box-shadow:0 1px 4px rgba(28,35,51,.05);border:1px solid #eceef4;
display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}}
.tabs::-webkit-scrollbar{{display:none}}
.tab-btn{{flex:1 0 auto;min-width:120px;border:none;background:transparent;cursor:pointer;
border-radius:8px;padding:9px 14px;font-size:13.5px;font-weight:600;color:#657289;
transition:all .2s;white-space:nowrap}}
/* 窄屏改为换行：4 个 tab × min-width:120px 在 390px 上放不下，而滚动条是
   显式隐藏的（.tabs::-webkit-scrollbar），于是第 4 个 tab 直接看不见、
   也没有任何「可以横向滚动」的提示。换行比隐藏滚动条诚实。 */
@media(max-width:520px){{
  .tabs{{flex-wrap:wrap}}
  .tab-btn{{flex:1 1 calc(50% - 4px);min-width:0}}
}}
.tab-btn:hover{{background:#f5f7fb;color:#1c2333}}
.tab-btn.active{{background:linear-gradient(135deg,#5f3dc4,#7048e8);color:#fff;
box-shadow:0 3px 10px rgba(95,61,196,.30)}}

/* ---- PANELS (cards inside) ---- */
.panel{{animation:fade .22s ease}}
@keyframes fade{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1;transform:none}}}}
.card{{background:#fff;border-radius:12px;padding:16px 18px;margin-bottom:14px;
box-shadow:0 1px 3px rgba(28,35,51,.05);border:1px solid #eceef4}}
.card-title{{font-weight:700;color:#1a1a2e;margin-bottom:10px;font-size:15px;
display:flex;align-items:center;gap:8px}}
.card-title:after{{content:"";flex:1;height:1px;background:linear-gradient(90deg,#e9ecef,transparent);margin-left:8px}}
.section-sub{{margin:14px 0 8px;font-size:13.5px;font-weight:700;color:#495057}}
.muted{{color:#8791a8;padding:20px;text-align:center;font-size:13px}}

/* ---- TABLES ---- */
.tblwrap{{overflow:auto;border:1px solid #eceef4;border-radius:10px}}
table.datatable{{width:100%;border-collapse:collapse;font-size:13px}}
.datatable th{{background:#1a1a2e;color:#fff;padding:9px 8px;font-size:12px;font-weight:600;
text-align:center;white-space:nowrap;position:sticky;top:0;z-index:1}}
.datatable td{{padding:7px 8px;border-bottom:1px solid #f4f6fa;color:#343a40}}
.datatable tr:nth-child(even) td{{background:#fafbfd}}
.datatable tr:hover td{{background:#eef4ff}}
.datatable .tl{{text-align:left;font-weight:600}}
.datatable .tr{{text-align:right;font-variant-numeric:tabular-nums}}
.datatable .mute{{color:#8791a8;font-size:11px;font-weight:500}}
.temp{{color:#fff;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:600}}

/* ---- GENERATED INNER MARKET INSIGHT CARDS (来自 market_insights 的默认卡片做样式覆盖/统一) ---- */
.panel > div[style*="border-radius"]{{margin-bottom:0 !important}}

/* ---- FOOTER ---- */
footer{{max-width:1180px;margin:0 auto;text-align:center;color:#98a1b3;font-size:12px;
padding:12px 14px 32px;line-height:1.8}}

/* ---- RESPONSIVE ---- */
@media(max-width:640px){{
    header h1{{font-size:19px}}
    main{{padding:0 10px}}
    .card{{padding:12px 14px}}
    .datatable th,.datatable td{{padding:5px 3px;font-size:11.5px}}
    .kpi{{padding:10px 12px;min-height:60px}}
    .k-value{{font-size:14px}}
}}
</style></head><body>
<header>
<div class="wrap">
<h1>📡 市场洞察</h1>
<p>最新数据日：{latest_date}｜来源：A股 Top 池 + 全市场活跃市值快照</p>
<a class="breadcrumb" href="index.html">← 返回每日报告首页</a>
</div>
</header>

<div class="kpi-row">{kpi_chips}</div>

<main>
<div class="tabs" role="tablist">{tabs_html}</div>
{panels_html}
</main>

<footer>由 GitHub Actions 每交易日自动构建部署<br>数据仅供研究参考，不构成任何投资建议</footer>

<script>
function switchTab(btn){{
    var tid = btn.getAttribute('data-tabtarget');
    document.querySelectorAll('.tab-btn').forEach(function(b){{
        b.classList.toggle('active', b === btn);
    }});
    document.querySelectorAll('.panel').forEach(function(p){{
        var show = p.getAttribute('data-tab') === tid;
        p.style.display = show ? '' : 'none';
    }});
    // 滚动到 Tab 顶部（移动端友好）
    document.querySelector('.tabs').scrollIntoView({{behavior:'smooth', block:'start'}});
}}
// 支持 #hash 直达指定 tab
(function(){{
    var map = {{}};
    document.querySelectorAll('.tab-btn').forEach(function(b){{ map[b.getAttribute('data-tabtarget')] = b; }});
    var h = (location.hash || '').replace('#','');
    if (h && map[h]) switchTab(map[h]);
    document.querySelectorAll('.tab-btn').forEach(function(b){{
        b.addEventListener('click', function(){{
            history.replaceState(null, '', '#' + b.getAttribute('data-tabtarget'));
        }});
    }});
}})();
</script>
</body></html>"""
    return html


def _latest_a_paths(latest):
    m_csv = os.path.join(BASE_DIR, "output", latest, f"metrics_{latest}.csv")
    b_csv = os.path.join(BASE_DIR, "output", latest, f"market_breadth_{latest}.csv")
    return m_csv, b_csv


def build_kpi_chips(latest):
    """KPI 摘要带（首页与 insights 页共用）。返回 html 字符串（可能为空）。"""
    import pandas as _pd
    if market_insights is None or not latest:
        return ""
    m_csv, b_csv = _latest_a_paths(latest)
    kpis = []
    if os.path.exists(b_csv):
        try:
            b = _pd.read_csv(b_csv).iloc[0]
            from market_insights import _temp_color, _temp_label
            t_cmp = float(b["综合温度"])
            temp_str = (f"<span style='display:inline-block;background:{_temp_color(t_cmp)};color:#fff;"
                        f"padding:3px 10px;border-radius:999px;font-weight:700;font-size:14px'>"
                        f"{t_cmp:.0f} · {_temp_label(t_cmp)}</span>")
            kpis = [
                ("上涨/下跌", f"{int(b['上涨家数'])}<span style='color:#ff8787'>↑</span> / {int(b['下跌家数'])}<span style='color:#69db7c'>↓</span>"),
                ("涨停/跌停", f"{int(b['涨停家数'])}<span style='color:#fa5252'>停涨</span> / {int(b['跌停家数'])}<span style='color:#37b24d'>停跌</span>"),
                ("成交额", f"{b['成交额_亿']:,.0f}<span style='color:#8791a8;font-size:12px'>亿</span>"),
                ("综合温度", temp_str),
                ("活跃市值", f"{b['活跃市值_亿']:,.0f}亿"),
                ("上涨占比", f"{b['上涨占比%']:.1f}%"),
            ]
        except Exception:
            pass
    try:
        df = _pd.read_csv(m_csv, dtype={"代码": str})
        n = len(df)
        dj = _pd.to_numeric(df["日线J"], errors="coerce") if "日线J" in df.columns else None
        if dj is not None and dj.notna().any():
            newlow = int((dj.dropna() < 0).sum())
            newhigh = int((dj.dropna() > 100).sum())
            kpis.append((f"Top{n}池 新极值", f"<span style='color:#1971c2'>新低{newlow}</span> / <span style='color:#d9480f'>新高{newhigh}</span>"))
        if "双均线多头" in df.columns:
            bull = _pd.to_numeric(df["双均线多头"], errors="coerce").dropna()
            if len(bull):
                kpis.append((f"Top{n}池 多头占比", f"<b>{bull.mean() * 100:.0f}%</b>"))
    except Exception:
        pass
    chips = ""
    for label, value in kpis[:8]:
        chips += (f'<div class="kpi"><div class="k-label">{label}</div>'
                  f'<div class="k-value">{value}</div></div>')
    return chips


def build_value_summary(latest):
    """首页「价值标的速览」：每市场综合分 TOP3 一行式。"""
    try:
        import build_value
        df = build_value.build(latest)
    except Exception:
        return ""
    if df is None or df.empty:
        return ""
    blocks = ""
    for market, g in df.groupby("市场"):
        items = []
        for _, r in g.head(3).iterrows():
            items.append(f'<span class="v-item"><b>{r["名称"]}</b>'
                         f'<em>{int(r["综合分"])}分</em></span>')
        blocks += (f'<div class="v-row"><span class="v-mkt { {"A股":"a","ETF":"etf","港股":"hk"}.get(market,"a") }">{market}</span>'
                   + "".join(items) + "</div>")
    if not blocks:
        return ""
    return (f'<div class="mini-card"><div class="mini-title">💎 价值标的速览'
            f'<a class="more" href="value.html">完整榜单 →</a></div>{blocks}</div>')


def build_backtest_summary():
    """首页「回测超额速览」：三市场 持有期=5日 超额收益 TOP2 策略。

    这里原来叫「回测胜率速览」，按**胜率**取 TOP2 并把胜率当作亮点展示 ——
    那会系统性误导：本样本的基线胜率只有 47% 左右（全样本、同一持有期），
    脱离基线看 55% 会读成「好」。现在改为展示**相对基线的超额**，
    并把基线值一并印出来；若 summary.csv 还是旧格式（没有超额列），
    就退回只显示基线，而不是退回显示裸胜率。
    """
    srcs = [("A股", "个股"), ("ETF", "ETF"), ("港股", "HK")]
    blocks = ""
    base_bits = []
    for label, folder in srcs:
        path = os.path.join(BASE_DIR, "backtest_results", folder, "summary.csv")
        if not os.path.exists(path):
            continue
        try:
            import pandas as _pd
            df = _pd.read_csv(path)
            df = df[_pd.to_numeric(df["持有期(交易日)"], errors="coerce") == 5]
            if df.empty:
                continue
            base_wr = None
            if "基线胜率%" in df.columns:
                b = df[df["策略"].astype(str).str.startswith("全样本")]
                if len(b):
                    base_wr = _pd.to_numeric(b.iloc[0]["基线胜率%"], errors="coerce")
            if "超额收益%" in df.columns:
                df = df.assign(_ex=_pd.to_numeric(df["超额收益%"], errors="coerce"))
                df = df[df["策略"].astype(str).str.startswith("全样本") == False]  # noqa: E712
                df = df.sort_values("_ex", ascending=False).head(2)
                items = "".join(
                    f'<span class="v-item"><b>{r["策略"]}</b>'
                    f'<em>{r["_ex"]:+.2f}%</em></span>'
                    for _, r in df.iterrows() if _pd.notna(r["_ex"]))
            else:
                df = df.sort_values("胜率%", ascending=False).head(2)
                items = "".join(
                    f'<span class="v-item"><b>{r["策略"]}</b>'
                    f'<em>{r["胜率%"]:.0f}%</em></span>'
                    for _, r in df.iterrows())
            if not items:
                continue
            if base_wr is not None and _pd.notna(base_wr):
                base_bits.append(f"{label} 基线胜率 {float(base_wr):.1f}%")
            blocks += (f'<div class="v-row"><span class="v-mkt { {"A股":"a","ETF":"etf","HK":"hk","港股":"hk"}.get(label,"a") }">{label}</span>'
                       + items + "</div>")
        except Exception:
            continue
    if not blocks:
        return ""
    note = "；".join(base_bits) if base_bits else ""
    note = ("数字为持有5日的超额收益（策略均值 − 全样本基线均值）。"
            + (f"{note}。" if note else "")
            + "56 个假设无一通过多重比较校正，请视为噪声尺度下的读数")
    return (f'<div class="mini-card"><div class="mini-title">🧪 回测超额速览（持有5日）'
            f'<a class="more" href="backtest-a.html">完整报告 →</a></div>{blocks}'
            f'<div class="mini-note">{note}</div></div>')


def build_strategy_summary(latest):
    """首页「策略速览」：复用 strategy_summary 的今日速览卡（内含基线对照）。"""
    try:
        import strategy_summary as ss
        m_csv, _ = _latest_a_paths(latest)
        if not os.path.exists(m_csv):
            return ""
        out = ss.build_summary(m_csv, os.path.join(BASE_DIR, "output"), "A股")
        return out["html"] if out else ""
    except Exception:
        return ""


def build_dashboard_ctx(latest):
    """组装首页上下文：市场洞察 4 面板 + 摘要卡。任何一块失败都置空，不阻塞。

但**必须留下原因**（放进 ctx["warnings"]）：原来这里是裸 except: pass，
于是「数据缺失 → 整块面板消失」在页面上毫无痕迹，只有翻源码才知道会这样。
"""
    ctx = {"kpi_chips": "", "breadth": "", "sector": "", "oversold": "", "overbuy": "",
           "strategy": "", "value": "", "backtest": "", "warnings": []}
    if not latest:
        ctx["warnings"].append("没有任何日期产物，首页洞察区全部为空")
        return ctx
    if market_insights is None:
        ctx["warnings"].append("market_insights 模块导入失败，洞察区全部为空")
        return ctx
    m_csv, b_csv = _latest_a_paths(latest)

    def collect(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            ctx["warnings"].append(f"{name} 缺失：{type(exc).__name__}: {exc}")
            return ""

    ctx["kpi_chips"] = collect("KPI 摘要带", lambda: build_kpi_chips(latest))
    ctx["breadth"] = collect(
        "大盘宽度", lambda: market_insights.market_breadth_dashboard(
            b_csv, m_csv, market="A").get("html", ""))
    ctx["sector"] = collect(
        "板块温度", lambda: market_insights.sector_temperature(m_csv, market="A").get("html", ""))

    def _opp():
        opp = market_insights.opportunity_board(m_csv, market="A", top_n=30)
        ctx["oversold"] = opp.get("oversold_html", "")
        ctx["overbuy"] = opp.get("overbought_html", "")
        return "ok"

    collect("机会榜", _opp)
    ctx["strategy"] = collect("策略速览", lambda: build_strategy_summary(latest))
    ctx["value"] = collect("价值标的", lambda: build_value_summary(latest))
    ctx["backtest"] = collect("回测摘要", lambda: build_backtest_summary())
    return ctx


def build_archive_page(dates):
    """archive.html：完整日期报告归档 + 搜索/市场筛选。"""
    total_reports = sum(len(k) for _, k in dates)
    latest = dates[0][0] if dates else None
    market_count = {key: 0 for _, _, key in MARKETS}
    for _, keys in dates:
        for k in keys:
            market_count[k] = market_count.get(k, 0) + 1

    rows = ""
    for date, keys in dates:
        wd_idx = datetime.datetime.strptime(date, "%Y-%m-%d").weekday()
        wd = WEEKDAYS[wd_idx]
        wd_en = ["mo", "tu", "we", "th", "fr", "sa", "su"][wd_idx]
        ym = date[:7]
        btns = "".join(
            f'<a class="b {key}" href="{date}/{key}.html">{label}</a>'
            for label, _, key in MARKETS if key in keys)
        mkts = " ".join(sorted(keys.keys()))
        tag = '<span class="new">最新</span>' if date == latest else ""
        today_cls = " today" if date == latest else ""
        rows += (f'<div class="day{today_cls}" data-date="{date}" data-wd="{wd_en}" '
                 f'data-ym="{ym}" data-mkts="{mkts}">'
                 f'<div class="d-left"><b>{date}</b><span class="wd">{wd}{tag}</span></div>'
                 f'<div class="btns">{btns}</div></div>\n')

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>报告归档 · 每日指标</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:linear-gradient(180deg,#eef1f7 0%,#f7f8fc 260px);color:#1c2333;
font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;min-height:100vh}}
header{{background:linear-gradient(135deg,#141e30 0%,#243b55 60%,#2d4a6e 100%);
padding:30px 16px 24px;text-align:center;position:relative;overflow:hidden}}
header h1{{margin:0;font-size:22px;color:#fff;letter-spacing:.6px}}
header p{{margin:8px 0 0;color:#aab4cf;font-size:12.5px}}
.breadcrumb{{display:inline-block;margin-top:13px;padding:7px 16px;border-radius:99px;
color:#fff;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);font-size:12px}}
main{{max-width:980px;margin:20px auto 30px;padding:0 14px}}
.toolbar{{background:#fff;border-radius:14px;padding:10px 12px;margin-bottom:18px;
box-shadow:0 1px 4px rgba(28,35,51,.05);border:1px solid #eceef4;
display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.search{{flex:1;min-width:220px;display:flex;align-items:center;gap:8px;
background:#f7f8fc;border:1px solid #e9ecef;border-radius:10px;padding:7px 10px;transition:all .15s}}
.search:focus-within{{background:#fff;border-color:#7048e8;box-shadow:0 0 0 3px rgba(112,72,232,.12)}}
.search input{{flex:1;border:0;outline:0;background:transparent;font-size:13px;color:#1c2333}}
.chips{{display:flex;gap:6px;flex-wrap:wrap}}
.chip-b{{border:1px solid #dee2e6;background:#fff;border-radius:99px;padding:6px 12px;
font-size:12px;color:#495057;cursor:pointer;transition:all .15s;font-weight:600}}
.chip-b:hover{{border-color:#adb5bd}}
.chip-b.active{{background:linear-gradient(90deg,#5f3dc4,#7048e8);border-color:transparent;color:#fff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}
.day{{background:#fff;border-radius:14px;padding:15px 18px;display:flex;align-items:center;
justify-content:space-between;flex-wrap:wrap;gap:10px;border:1px solid #eceef4;
box-shadow:0 1px 3px rgba(28,35,51,.05);transition:transform .15s,box-shadow .15s}}
.day:hover{{transform:translateY(-3px);box-shadow:0 8px 22px rgba(28,35,51,.10)}}
.day.today{{border:1.5px solid #ffa94d;background:linear-gradient(180deg,#fff,#fffaf2)}}
.d-left b{{font-size:16.5px;letter-spacing:.5px}}
.wd{{display:inline-block;margin-left:8px;font-size:11px;color:#8791a8;background:#f1f3f9;
border-radius:6px;padding:2px 7px;vertical-align:middle}}
.today .wd{{background:#ffe8cc;color:#d9480f}}
.new{{display:inline-block;margin-left:6px;font-size:10.5px;background:linear-gradient(90deg,#ff922b,#fa5252);
color:#fff;border-radius:99px;padding:2px 8px;font-weight:600;vertical-align:middle}}
.btns{{display:flex;gap:7px;flex-wrap:wrap}}
a.b{{display:inline-block;padding:6.5px 17px;border-radius:99px;color:#fff;text-decoration:none;
font-size:12.5px;font-weight:600;transition:opacity .15s}}
a.b:hover{{opacity:.88}}
a.b.a{{background:linear-gradient(90deg,#e03131,#f76707)}}
a.b.etf{{background:linear-gradient(90deg,#1971c2,#4dabf7)}}
a.b.hk{{background:linear-gradient(90deg,#d9480f,#f08c00)}}
#emptyHint{{display:none;text-align:center;color:#8791a8;padding:30px 10px}}
footer{{text-align:center;color:#98a1b3;font-size:12px;padding:14px 12px 30px;line-height:1.8}}
@media(max-width:640px){{.toolbar{{flex-direction:column;align-items:stretch}}.chips{{justify-content:space-between}}.chip-b{{flex:1;text-align:center}}}}
</style></head><body>
<header><h1>🗂 报告归档</h1>
<p>共 {len(dates)} 个交易日 · {total_reports} 份报告</p>
<a class="breadcrumb" href="index.html">← 返回首页</a></header>
<main>
<div class="toolbar">
  <div class="search">
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#8791a8" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.35-4.35"></path></svg>
    <input id="searchInput" type="text" placeholder="搜索：2026-08 / 周一 / a etf hk …">
  </div>
  <div class="chips">
    <button class="chip-b" data-filter="all">全部（{len(dates)}）</button>
    <button class="chip-b" data-mkt="a">A股（{market_count.get('a', 0)}）</button>
    <button class="chip-b" data-mkt="etf">ETF（{market_count.get('etf', 0)}）</button>
    <button class="chip-b" data-mkt="hk">港股（{market_count.get('hk', 0)}）</button>
  </div>
</div>
<div class="grid" id="dayGrid">
{rows or "<p style='text-align:center;color:#999'>暂无报告</p>"}
</div>
<div id="emptyHint">没有匹配的日期 😶</div>
</main>
<footer>由 GitHub Actions 每个交易日自动构建部署</footer>
<script>
(function(){{
    var q = document.getElementById('searchInput');
    var grid = document.getElementById('dayGrid');
    var empty = document.getElementById('emptyHint');
    var allBtns = document.querySelectorAll('.chip-b');
    var curMkt = 'all';
    function apply(){{
        var kw = (q && q.value || '').trim().toLowerCase();
        var visible = 0;
        var days = grid.querySelectorAll('.day');
        for (var i = 0; i < days.length; i++) {{
            var d = days[i];
            var text = (d.getAttribute('data-date') + ' ' + d.getAttribute('data-wd') +
                        ' ' + d.getAttribute('data-ym') + ' ' + d.getAttribute('data-mkts')).toLowerCase();
            var passMkt = (curMkt === 'all') || (d.getAttribute('data-mkts') || '').indexOf(curMkt) >= 0;
            var passKw = !kw || text.indexOf(kw) >= 0;
            var show = passMkt && passKw;
            d.style.display = show ? '' : 'none';
            if (show) visible++;
        }}
        empty.style.display = visible ? 'none' : '';
    }}
    if (q) q.addEventListener('input', apply);
    allBtns.forEach(function(b){{
        b.addEventListener('click', function(){{
            allBtns.forEach(function(x){{ x.classList.remove('active'); }});
            b.classList.add('active');
            curMkt = b.getAttribute('data-mkt') || 'all';
            apply();
        }});
    }});
    if (allBtns[0]) allBtns[0].classList.add('active');
    apply();
}})();
</script>
</body></html>"""


def collect_extras():
    """回测报告 / 最新价值标的。返回 docs 文件名列表。

    这里原来还会复制 buylist.html（「今日买入参考」）。该功能已被回测证伪并
    整体删除，详见 strategy_summary.py 末尾的说明。
    """
    extras = []
    for src_key, dst_key in BT_SOURCES:
        src = os.path.join(BASE_DIR, "backtest_results", src_key, "backtest_report.html")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(DOCS_DIR, f"backtest-{dst_key}.html"))
            extras.append(f"backtest-{dst_key}.html")
        csv_src = os.path.join(BASE_DIR, "backtest_results", src_key, "backtest_report_trades.csv")
        if os.path.exists(csv_src):
            shutil.copy(csv_src, os.path.join(DOCS_DIR, f"backtest-{dst_key}-trades.csv"))
    vl = _latest("value_*.html")
    if vl:
        shutil.copy(vl, os.path.join(DOCS_DIR, "value.html"))
        extras.append("value.html")
    return extras


def _split_leading_title(body, fallback):
    """取出正文开头的标题行，返回 (显示标题, 剩余正文)。

    为什么需要：market_insights / strategy_summary 的片段自带标题 div，而
    `_insight_card` 还会渲染 `.icard-head` —— 首页上标题就出现两次
    （截图里肉眼可见）。这里把内层标题**提升**为卡片标题：只留一个，
    且用信息量更大的那个（「超跌机会」→「超跌/低吸机会榜（TOP 28）」）。

    前导的 `<style>` 块必须留在正文里（媒体查询靠它生效），所以只剥标题 div。
    """
    head = ""
    rest = body or ""
    while True:
        match = _STYLE_BLOCK.match(rest)
        if not match:
            break
        head += match.group(0)
        rest = rest[match.end():]

    match = _LEADING_TITLE.match(rest)
    if not match:
        return fallback, body

    inner = match.group(1).strip()
    def _norm(text):  # noqa: E306
        return re.sub(r"[^\w\u4e00-\u9fff]", "", text or "")
    got, want = _norm(inner), _norm(fallback)
    # 短、纯文本、且与卡片标题有重叠 → 认定是重复的标题行
    if inner and len(inner) <= 40 and got and want and (
            got[:2] == want[:2] or want in got or got in want):
        # 内层标题自带 emoji，而卡片头已经渲染了自己的图标 —— 去掉前导符号，
        # 否则会出现「📡 📡 大盘宽度仪表盘」这种重复图标
        cleaned = re.sub(r"^[^\w\u4e00-\u9fff]+", "", inner).strip()
        return (cleaned or inner), head + rest[match.end():]
    return fallback, body


def _insight_card(title, icon, body, tone=""):
    """统一外壳：覆盖 market_insights 内联 margin/border-radius/box-shadow，视觉对齐。"""
    tone_border = ""
    tone_accent = ""
    if tone == "cool":
        tone_border = ";border-left:3px solid #4dabf7"
        tone_accent = "#1971c2"
    elif tone == "hot":
        tone_border = ";border-left:3px solid #fa5252"
        tone_accent = "#d9480f"
    elif tone == "value":
        tone_border = ";border-left:3px solid #7048e8"
        tone_accent = "#5f3dc4"
    else:
        tone_accent = "#1c2333"
    if not body:
        body = '<div class="empty-line">暂无数据</div>'
    else:
        title, body = _split_leading_title(body, title)
    # 覆盖内联 margin-bottom / border-radius / box-shadow / padding
    wrapper = (f'<section class="icard" style="background:#fff;border-radius:14px;padding:0;'
               f'box-shadow:0 1px 3px rgba(28,35,51,.06);border:1px solid #eceef4{tone_border};">'
               f'<div class="icard-head">{icon} <span class="icard-title">{title}</span></div>'
               f'<div class="icard-body">{body}</div></section>')
    return wrapper


def _strip_outer_div(body):
    """剥掉 market_insights 返回片段的最外层 div（仅当它真的包裹整体）。

    原来的正则是 `^<div style="[^"]*">(.*)</div>$`（DOTALL、贪婪）。当片段是
    **两个并列的兄弟 div**（`opportunity_board` 的 oversold/overbought 就是）
    时，它会吃掉第一个 div 的开标签和最后一个 div 的闭标签，产出
    `标题</div><div ...>` 这种**孤儿闭标签 + 未闭合 div** 的畸形 HTML。
    浏览器能容错，但结构确实是错的。

    改成按深度扫描：只有「第一个 <div> 的配对 </div> 恰好在末尾」时才剥，
    否则原样返回。
    """
    text = (body or "").strip()
    if not text.startswith("<div"):
        return text

    depth = 0
    open_end = None
    close_end = None
    for token in _DIV_TOKEN.finditer(text):
        if token.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                close_end = token.end()
                break
        else:
            if depth == 0:
                open_end = token.end()
            depth += 1

    if close_end is None or close_end != len(text):
        return text          # 并列兄弟节点，不能剥
    return text[open_end:close_end - len("</div>")].strip()


def build_index(dates, extras, ctx):
    """首页 = 单页流式仪表盘：KPI 摘要带 + 市场洞察（大盘宽度/板块温度/超跌+超买双列）+ 今日摘要。"""
    total_reports = sum(len(k) for _, k in dates)

    nav = ""
    if "insights.html" in extras:
        nav += '<a class="pill ins" href="insights.html">📡 市场洞察</a>'
    if "value.html" in extras:
        nav += '<a class="pill val" href="value.html">💎 价值标的</a>'
    for src_key, dst_key in BT_SOURCES:
        label = {"a": "A股", "etf": "ETF", "hk": "港股"}[dst_key]
        if f"backtest-{dst_key}.html" in extras:
            nav += f'<a class="pill bt" href="backtest-{dst_key}.html">🧪 回测·{label}</a>'
    if "archive.html" in extras:
        nav += '<a class="pill arch" href="archive.html">🗂 报告归档</a>'
    nav_html = f'<nav>{nav}</nav>' if nav else ""

    # 缺块必须在页面上可见 —— 原来数据缺失只是「整块消失」，读者无从判断
    # 是「今天没有」还是「抓取失败了」。
    warnings = ctx.get("warnings") or []
    banner_html = ""
    if warnings:
        items = "".join(f"<li>{html.escape(str(w))}</li>" for w in warnings)
        banner_html = (
            '<div class="banner" style="max-width:1200px;margin:12px auto;padding:10px 14px;'
            'border:1px solid #f0c36d;background:#fff8e6;border-radius:8px;'
            'font-size:13px;line-height:1.7">'
            f'⚠️ 本次有 {len(warnings)} 个板块未能生成（数据缺失或抓取失败）：'
            f'<ul style="margin:6px 0 0;padding-left:20px">{items}</ul></div>'
        )

    # 数据落后于最近一个已收盘交易日 —— 单独一条醒目的红条。
    # 不这样做的代价已经付过：站点整整三周停在 2026-09-18，而页面上
    # 没有任何提示，Actions 也全绿。
    stale = ctx.get("stale") or []
    stale_html = ""
    if stale:
        items = "".join(f"<li>{html.escape(str(s))}</li>" for s in stale)
        stale_html = (
            '<div class="banner" style="max-width:1200px;margin:12px auto;padding:12px 16px;'
            'border:1px solid #e03131;background:#fff5f5;border-radius:8px;'
            'font-size:13.5px;line-height:1.75;color:#8a1c1c">'
            '🚨 <b>数据已过期</b> —— 下列市场缺少最近一个交易日的产物：'
            f'<ul style="margin:6px 0 0;padding-left:20px">{items}</ul>'
            '<div style="margin-top:6px;color:#a33">下方展示的是更早日期的数据，'
            '请勿当作最新行情使用。</div></div>'
        )

    # ---- 洞察区：大盘宽度（宽卡）+ 板块温度（宽卡）+ 超跌/超买双列 ----
    breadth_card = _insight_card("大盘宽度仪表盘", "📡",
                                  _strip_outer_div(ctx.get("breadth", "")), tone="value")
    sector_card = _insight_card("行业板块温度榜", "🏭",
                                _strip_outer_div(ctx.get("sector", "")), tone="hot")
    oversell_card = _insight_card("超跌机会", "🧊",
                                  _strip_outer_div(ctx.get("oversold", "")), tone="cool")
    overbuy_card = _insight_card("超买观察", "🔥",
                                 _strip_outer_div(ctx.get("overbuy", "")), tone="hot")
    opp_grid = f'<div class="opp-grid">{oversell_card}{overbuy_card}</div>'

    # ---- 今日摘要（策略 / 价值 / 回测，空块自动跳过）----
    summary_blocks = [b for b in [ctx.get("strategy", ""), ctx.get("value", ""),
                                  ctx.get("backtest", "")] if b]
    summary_html = "".join(summary_blocks) or '<div class="empty-line">暂无摘要数据，等待下一个交易日生成</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>市场洞察 · 每日指标</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:linear-gradient(180deg,#eef1f7 0%,#f7f8fc 260px);color:#1c2333;
font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;min-height:100vh;line-height:1.5}}
a{{text-decoration:none;color:inherit}}
header{{background:linear-gradient(135deg,#141e30 0%,#243b55 60%,#2d4a6e 100%);
padding:34px 16px 24px;text-align:center;position:relative;overflow:hidden}}
header:before{{content:"";position:absolute;inset:0;
background:radial-gradient(ellipse at 20% 0%,rgba(255,255,255,.10),transparent 55%),
radial-gradient(ellipse at 85% 100%,rgba(77,171,247,.18),transparent 50%)}}
header .head-wrap{{max-width:1080px;margin:0 auto;position:relative}}
header h1{{margin:0;font-size:24px;color:#fff;letter-spacing:.5px}}
header h1 span{{background:linear-gradient(90deg,#ffd43b,#ff922b);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}}
header p{{margin:8px 0 0;color:#aab4cf;font-size:13px}}
.stats{{display:flex;gap:8px;justify-content:center;margin-top:14px;flex-wrap:wrap}}
.chip{{background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.14);
border-radius:99px;padding:4px 12px;color:#dbe4f3;font-size:11.5px;backdrop-filter:blur(6px)}}
nav{{margin-top:14px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap}}
.pill{{display:inline-block;padding:6px 16px;border-radius:99px;color:#fff;
font-size:12.5px;font-weight:600;box-shadow:0 2px 8px rgba(0,0,0,.18);transition:transform .15s}}
.pill:hover{{transform:translateY(-1px)}}
.pill.ins{{background:linear-gradient(90deg,#5f3dc4,#7048e8)}}
.pill.val{{background:linear-gradient(90deg,#f59f00,#fd7e14)}}
.pill.buy{{background:linear-gradient(90deg,#e8590c,#fa5252)}}
.pill.arch{{background:linear-gradient(90deg,#495057,#868e96)}}
.pill.bt{{background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.22);backdrop-filter:blur(6px);box-shadow:none;font-weight:500}}

main{{max-width:1080px;margin:0 auto 30px;padding:0 14px}}

/* ---- KPI band ---- */
.kpi-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;
margin:-24px auto 14px;position:relative;z-index:2}}
.kpi{{background:#fff;border-radius:12px;padding:11px 13px;
box-shadow:0 4px 14px rgba(28,35,51,.07);border:1px solid #eceef4;min-height:62px}}
.k-label{{font-size:11px;color:#8791a8}}
.k-value{{margin-top:5px;font-size:14.5px;font-weight:700;line-height:1.25}}
@media(max-width:780px){{.kpi-row{{grid-template-columns:repeat(2,1fr)}}}}

/* ---- insight cards (统一外壳，覆盖内联样式) ---- */
.icard{{margin-bottom:14px;min-width:0}}
.icard-head{{display:flex;align-items:center;gap:6px;padding:12px 18px;
border-bottom:1px solid #f0f2f8;font-size:14px;font-weight:700;color:#1c2333}}
.icard-title{{font-size:14px}}
.icard-body{{padding:14px 18px;font-size:13px}}
/* 覆盖 market_insights 内联 margin/border-radius/box-shadow */
.icard-body > div[style*="border-radius"],
.icard-body > div[style*="box-shadow"],
.icard-body > div[style*="margin-bottom"]{{
    margin-bottom:0 !important;border-radius:0 !important;box-shadow:none !important;
    background:transparent !important;padding:0 !important;
}}
/* 覆盖 chip2 */
.icard-body .chip2{{display:inline-block;padding:3px 9px;border-radius:99px;
font-size:11.5px;background:#f1f3f9;color:#495057;border:1px solid #e5e8f0;
margin:0 4px 4px 0}}

/* ---- opp grid (超跌 + 超买双列) ---- */
.opp-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:0}}
.opp-grid > *{{min-width:0}}
@media(max-width:780px){{.opp-grid{{grid-template-columns:1fr}}}}
.opp-grid .icard{{margin-bottom:0}}

/* ---- summary section ---- */
.sec-title{{margin:22px 0 12px;font-size:14.5px;font-weight:700;color:#1a1a2e;
display:flex;align-items:center;gap:8px}}
.sec-title:after{{content:"";flex:1;height:1px;background:linear-gradient(90deg,#e9ecef,transparent)}}
.dash{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;align-items:start}}
@media(max-width:900px){{.dash{{grid-template-columns:1fr 1fr}}}}
@media(max-width:580px){{.dash{{grid-template-columns:1fr}}}}
.dash > div{{margin-bottom:0 !important}}
.empty-line{{text-align:center;color:#8791a8;padding:24px 10px;font-size:12.5px}}

/* ---- mini cards (strategy / value / backtest) ---- */
.mini-card{{background:#fff;border-radius:12px;padding:13px 16px;
box-shadow:0 1px 3px rgba(28,35,51,.05);border:1px solid #eceef4}}
.mini-title{{font-weight:700;color:#1a1a2e;margin-bottom:8px;font-size:13.5px;
display:flex;align-items:center;justify-content:space-between}}
.mini-title .more{{font-size:11.5px;color:#7048e8;font-weight:600}}
.mini-tbl{{width:100%;border-collapse:collapse;font-size:12.5px}}
.mini-tbl td{{padding:5.5px 4px;border-bottom:1px dashed #eef1f7}}
.mini-tbl tr:last-child td{{border-bottom:0}}
.mini-tbl .tl{{color:#657289}}
.mini-tbl .tr{{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}}
.v-row{{display:flex;align-items:center;gap:8px;padding:5px 0;flex-wrap:wrap;
border-bottom:1px dashed #eef1f7}}
.v-row:last-of-type{{border-bottom:0}}
.v-mkt{{display:inline-block;min-width:38px;text-align:center;border-radius:7px;
color:#fff;font-size:11px;font-weight:700;padding:2.5px 0}}
.v-mkt.a{{background:linear-gradient(90deg,#e03131,#f76707)}}
.v-mkt.etf{{background:linear-gradient(90deg,#1971c2,#4dabf7)}}
.v-mkt.hk{{background:linear-gradient(90deg,#d9480f,#f08c00)}}
.v-item{{display:inline-flex;align-items:center;gap:5px;font-size:12.5px}}
.v-item em{{font-style:normal;color:#7048e8;font-weight:700;font-size:11.5px;
background:#f3f0ff;border-radius:5px;padding:1px 5px}}
.mini-note{{margin-top:6px;font-size:10.5px;color:#98a1b3}}

footer{{text-align:center;color:#98a1b3;font-size:11.5px;padding:16px 12px 28px;line-height:1.8}}
@media(max-width:640px){{
    header h1{{font-size:20px}}
    main{{padding:0 10px}}
    .kpi{{padding:9px 11px;min-height:56px}}
    .k-value{{font-size:13px}}
    .icard-body{{padding:12px 14px}}
    .icard-head{{padding:10px 14px}}
}}
</style></head><body>
<header><div class="head-wrap">
<h1>📈 每日<span>市场洞察</span></h1>
<p>大盘温度 · 板块景气 · 超跌机会 · 策略命中 · 价值标的</p>
<div class="stats">
<span class="chip">📅 已收录 {len(dates)} 个交易日</span>
<span class="chip">📊 {total_reports} 份报告</span>
<span class="chip">🔄 每交易日收盘后自动更新</span>
</div>
{nav_html}
{banner_html}
</div></header>
<main>
{stale_html}
<div class="kpi-row">{ctx.get("kpi_chips", "")}</div>
{breadth_card}
{sector_card}
{opp_grid}
<div class="sec-title">📌 今日摘要</div>
<div class="dash">{summary_html}</div>
</main>
<footer>由 GitHub Actions 每个交易日自动构建部署<br>数据仅供研究参考，不构成任何投资建议</footer>
</body></html>"""


def main():
    entries = collect()
    # 只物化会进归档的那 MAX_DAYS 天：更早的日期只是滚动统计的历史，
    # 不出现在站点上，没必要为它们生成 HTML（实测 90 份约 52 秒）。
    dates = materialize([(d, entries[d]) for d in sorted(entries, reverse=True)[:MAX_DAYS]])
    if os.path.isdir(DOCS_DIR):
        shutil.rmtree(DOCS_DIR)
    os.makedirs(DOCS_DIR, exist_ok=True)
    css_bytes = write_css_asset()
    for date, keys in dates:
        dst = os.path.join(DOCS_DIR, date)
        os.makedirs(dst, exist_ok=True)
        for key, src in keys.items():
            with open(src, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            with open(os.path.join(dst, f"{key}.html"), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(externalize_style(body))
    extras = collect_extras()

    # 「最新」必须是**有 A 股报告**的那一天：洞察页/今日摘要/价值标的
    # 全都读 A 股的产物。原来取的是「任一市场的最新日期」—— 一旦某天只有
    # ETF 或港股成功，洞察页就会整块消失、导航 pill 也随之不见，
    # 而日志里没有任何提示（静默降级）。
    latest = next((d for d, keys in dates if "a" in keys), None)
    if latest is None and dates:
        latest = dates[0][0]
        print("::warning::没有任何日期含 A 股报告，洞察页退化为使用其它市场的日期构建")
    if latest:
        try:
            insights_body = build_insights_page(latest)
        except Exception as e:  # 兜底，不让洞察页失败阻塞整个站点构建
            print(f"[warn] 市场洞察页构建失败: {type(e).__name__}: {e}")
            insights_body = ""
        if insights_body:
            ipath = os.path.join(DOCS_DIR, "insights.html")
            with open(ipath, "w", encoding="utf-8") as f:
                f.write(insights_body)
            extras.append("insights.html")

    # 每日报告归档页（全部日期 + 搜索/市场筛选），供首页导航直达
    if dates:
        apath = os.path.join(DOCS_DIR, "archive.html")
        with open(apath, "w", encoding="utf-8") as f:
            f.write(build_archive_page(dates))
        extras.append("archive.html")

    ctx = build_dashboard_ctx(latest)
    # 数据新鲜度检查：站点内容落后于「最近一个已收盘交易日」时必须在页面上可见
    ctx["stale"] = stale_market_warnings()
    for warning in ctx.get("warnings", []):
        # 让 workflow 日志里也能直接看到，而不是只有翻 HTML 才发现
        print(f"::warning::站点缺块：{warning}")
    for warning in ctx["stale"]:
        print(f"::warning::数据过期：{warning}")
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(dates, extras, ctx))
    n_reports = sum(len(k) for _, k in dates)
    print(f"站点已生成: {len(dates)} 天 / {n_reports} 份报告 + {len(extras)} 个附加页 "
          f"+ assets/report.css({css_bytes} 字节) -> {DOCS_DIR}")

    # 返回值分级：最新一天三个市场的报告全缺 → 真的没东西可发布，判失败；
    # 只缺子板块 → 通过（已在页面上用 banner 说明）。
    if not dates:
        print("::error::没有任何日期产物，站点无内容可发布")
        return 1
    latest_keys = dates[0][1]
    if not any(key in latest_keys for _label, _src, key in MARKETS):
        print(f"::error::{dates[0][0]} 三个市场的报告全部缺失")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
