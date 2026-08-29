# -*- coding: utf-8 -*-
"""把每日报告整理为 GitHub Pages 站点(docs/)：日期索引 + 回测/买入参考/价值标的页。

用法:
    python build_pages.py
"""

import datetime
import glob
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
MAX_DAYS = 120
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# import 同级 market_insights（避免在 sys.path 未就绪时失败）
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
try:
    import market_insights  # noqa: E402
except Exception:  # pragma: no cover
    market_insights = None


def collect():
    entries = {}
    for label, out_dir, key in MARKETS:
        full_dir = os.path.join(BASE_DIR, out_dir)
        if not os.path.isdir(full_dir):
            continue
        for name in os.listdir(full_dir):
            day_dir = os.path.join(full_dir, name)
            rep = os.path.join(day_dir, f"report_{name}.html")
            if DATE_RE.match(name) and os.path.isdir(day_dir) and os.path.exists(rep):
                entries.setdefault(name, {})[key] = rep
    return entries


def _latest(pattern, out_dir=None):
    base = out_dir if out_dir else os.path.join(BASE_DIR, "output")
    files = sorted(glob.glob(os.path.join(base, pattern)))
    return files[-1] if files else None


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


def collect_extras():
    """回测报告 / 最新买入参考 / 最新价值标的。返回 docs 文件名列表。"""
    extras = []
    for src_key, dst_key in BT_SOURCES:
        src = os.path.join(BASE_DIR, "backtest_results", src_key, "backtest_report.html")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(DOCS_DIR, f"backtest-{dst_key}.html"))
            extras.append(f"backtest-{dst_key}.html")
        csv_src = os.path.join(BASE_DIR, "backtest_results", src_key, "backtest_report_trades.csv")
        if os.path.exists(csv_src):
            shutil.copy(csv_src, os.path.join(DOCS_DIR, f"backtest-{dst_key}-trades.csv"))
    bl = _latest("buylist_*.html")
    if bl:
        shutil.copy(bl, os.path.join(DOCS_DIR, "buylist.html"))
        extras.append("buylist.html")
    vl = _latest("value_*.html")
    if vl:
        shutil.copy(vl, os.path.join(DOCS_DIR, "value.html"))
        extras.append("value.html")
    return extras


def collect_buy_fragments():
    """读取 run_buy_daily.py 写出的「今日推荐」与「昨日回归」HTML 片段。"""
    def _read(name):
        p = os.path.join(BASE_DIR, "output", name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
        return ""
    return _read("buy_today.html"), _read("buy_review.html")


def build_index(dates, extras, buy_today="", buy_review=""):
    total_reports = sum(len(k) for _, k in dates)
    latest = dates[0][0] if dates else None
    market_count = {key: 0 for _, _, key in MARKETS}
    for _, keys in dates:
        for k in keys:
            market_count[k] = market_count.get(k, 0) + 1

    # ---- 每个日期卡片做 data-date / data-weekday 便于搜索/筛选 ----
    cards_entries = []
    for date, keys in dates:
        wd_idx = datetime.datetime.strptime(date, "%Y-%m-%d").weekday()
        wd = WEEKDAYS[wd_idx]
        wd_en = ["mo","tu","we","th","fr","sa","su"][wd_idx]
        ym = date[:7]  # YYYY-MM
        btns = "".join(
            f'<a class="b {key}" href="{date}/{key}.html">{label}</a>'
            for label, _, key in MARKETS if key in keys)
        markets_have = " ".join(sorted(keys.keys()))  # 按市场搜索: a / etf / hk
        tag = '<span class="new">最新</span>' if date == latest else ""
        cards_entries.append((date, wd, wd_en, ym, markets_have, tag, btns))

    def _card(date, wd, wd_en, ym, markets_have, tag, btns):
        today_cls = " today" if date == latest else ""
        return (f'<div class="day{today_cls}" data-date="{date}" data-wd="{wd_en}" '
                f'data-ym="{ym}" data-mkts="{markets_have}">'
                f'<div class="d-left"><b>{date}</b><span class="wd">{wd}{tag}</span></div>'
                f'<div class="btns">{btns}</div></div>\n')

    cards = "".join(_card(*e) for e in cards_entries)
    body = cards or "<p style='text-align:center;color:#999'>暂无报告</p>"

    nav = ""
    if "insights.html" in extras:
        nav += '<a class="pill ins" href="insights.html">📡 市场洞察</a>'
    if "value.html" in extras:
        nav += '<a class="pill val" href="value.html">💎 价值标的</a>'
    if "buylist.html" in extras:
        nav += '<a class="pill buy" href="buylist.html">🎯 今日买入参考</a>'
    for src_key, dst_key in BT_SOURCES:
        label = {"a": "A股", "etf": "ETF", "hk": "港股"}[dst_key]
        if f"backtest-{dst_key}.html" in extras:
            nav += f'<a class="pill bt" href="backtest-{dst_key}.html">🧪 回测·{label}</a>'
    nav_html = f'<nav>{nav}</nav>' if nav else ""

    # ---- 买入推荐 / 昨日回归 → 可折叠手风琴（默认展开"今日推荐"、收起"昨日回归"）----
    buy_html = ""
    if buy_today or buy_review:
        blocks = []
        if buy_today:
            blocks.append((
                "buy-today", "🎯 今日推荐（{today}）".format(today=latest or "当日"), True,
                f'<div class="spot">{buy_today}</div>'))
        if buy_review:
            blocks.append((
                "buy-review", "📅 昨日推荐回归", False,
                f'<div class="spot">{buy_review}</div>'))
        accordion = ""
        for idx, (tid, title, opened, inner) in enumerate(blocks):
            check = " checked" if opened else ""
            panel_style = "" if opened else ' style="display:none"'
            accordion += f"""
<div class="acc">
<input type="checkbox" id="acc-{tid}" class="acc-cb"{check}>
<label for="acc-{tid}" class="acc-label">{title}<span class="acc-ico">▾</span></label>
<div class="acc-panel" data-accid="{tid}"{panel_style}>{inner}</div>
</div>"""
        # ---- 顶部搜索/筛选条 ----
        search_bar = f"""
<div class="toolbar">
  <div class="search">
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#8791a8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.35-4.35"></path></svg>
    <input id="searchInput" type="text" placeholder="搜索日期（2026-08 / 周一 / A股 ETF 港股）…">
  </div>
  <div class="chips">
    <button class="chip-b" data-filter="all">全部（{len(dates)}）</button>
    <button class="chip-b" data-mkt="a">A股（{market_count.get('a',0)}）</button>
    <button class="chip-b" data-mkt="etf">ETF（{market_count.get('etf',0)}）</button>
    <button class="chip-b" data-mkt="hk">港股（{market_count.get('hk',0)}）</button>
  </div>
</div>
<div id="emptyHint" style="display:none;text-align:center;color:#8791a8;padding:30px 10px">没有匹配的日期 😶</div>"""
        buy_html = search_bar + '<div class="spotlight">' + accordion + "</div>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日指标报告 · A股/ETF/港股</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{margin:0;background:linear-gradient(180deg,#eef1f7 0%,#f7f8fc 320px);color:#1c2333;
font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif}}
header{{background:linear-gradient(135deg,#141e30 0%,#243b55 60%,#2d4a6e 100%);
padding:38px 16px 30px;text-align:center;position:relative;overflow:hidden}}
header:before{{content:"";position:absolute;inset:0;
background:radial-gradient(ellipse at 20% 0%,rgba(255,255,255,.10),transparent 55%),
radial-gradient(ellipse at 85% 100%,rgba(77,171,247,.18),transparent 50%)}}
header .head-wrap{{max-width:980px;margin:0 auto;position:relative}}
header h1{{margin:0;font-size:26px;color:#fff;letter-spacing:1px;position:relative}}
header h1 span{{background:linear-gradient(90deg,#ffd43b,#ff922b);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}}
header p{{margin:10px 0 0;color:#aab4cf;font-size:13.5px;position:relative}}
.stats{{display:flex;gap:10px;justify-content:center;margin-top:16px;flex-wrap:wrap;position:relative}}
.chip{{background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.14);
border-radius:99px;padding:5px 14px;color:#dbe4f3;font-size:12px;backdrop-filter:blur(6px)}}
nav{{margin-top:18px;display:flex;gap:9px;justify-content:center;flex-wrap:wrap;position:relative}}
.pill{{display:inline-block;padding:8px 20px;border-radius:99px;color:#fff;text-decoration:none;
font-size:13px;font-weight:600;box-shadow:0 3px 10px rgba(0,0,0,.22);transition:transform .15s}}
.pill:hover{{transform:translateY(-2px)}}
.pill.val{{background:linear-gradient(90deg,#f59f00,#fd7e14)}}
.pill.buy{{background:linear-gradient(90deg,#e8590c,#fa5252)}}
.pill.ins{{background:linear-gradient(90deg,#5f3dc4,#7048e8)}}
.pill.bt{{background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.22);backdrop-filter:blur(6px);box-shadow:none;font-weight:500}}
main{{max-width:980px;margin:22px auto 30px;padding:0 14px}}

/* ---- toolbar (search + filter chips) ---- */
.toolbar{{background:#fff;border-radius:14px;padding:10px 12px;margin-bottom:18px;
box-shadow:0 1px 4px rgba(28,35,51,.05);border:1px solid #eceef4;
display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.search{{flex:1;min-width:220px;position:relative;display:flex;align-items:center;gap:8px;
background:#f7f8fc;border:1px solid #e9ecef;border-radius:10px;padding:7px 10px;transition:all .15s}}
.search:focus-within{{background:#fff;border-color:#7048e8;box-shadow:0 0 0 3px rgba(112,72,232,.12)}}
.search input{{flex:1;border:0;outline:0;background:transparent;font-size:13px;color:#1c2333}}
.search input::placeholder{{color:#98a1b3}}
.chips{{display:flex;gap:6px;flex-wrap:wrap}}
.chip-b{{border:1px solid #dee2e6;background:#fff;border-radius:99px;padding:6px 12px;
font-size:12px;color:#495057;cursor:pointer;transition:all .15s;font-weight:600}}
.chip-b:hover{{border-color:#adb5bd;color:#1c2333}}
.chip-b.active{{background:linear-gradient(90deg,#5f3dc4,#7048e8);border-color:transparent;color:#fff;box-shadow:0 2px 8px rgba(95,61,196,.28)}}

/* ---- spotlight + accordion ---- */
.spotlight{{display:flex;flex-direction:column;gap:12px;margin-bottom:22px}}
.acc{{background:#fff;border-radius:14px;border:1px solid #eceef4;overflow:hidden;
box-shadow:0 1px 3px rgba(28,35,51,.05)}}
.acc-cb{{display:none}}
.acc-label{{display:flex;justify-content:space-between;align-items:center;cursor:pointer;
padding:11px 16px;font-size:14px;font-weight:700;color:#1c2333;
background:linear-gradient(180deg,#fafbff,#fff);user-select:none}}
.acc-ico{{transition:transform .2s;color:#8791a8;font-size:14px}}
.acc-cb:checked + .acc-label .acc-ico{{transform:rotate(180deg)}}
.acc-cb:checked + .acc-label{{background:linear-gradient(180deg,#eef2ff,#fafbff);color:#3b278f}}
.acc-panel{{border-top:1px dashed #eceef4;padding:2px;transition:opacity .18s}}
.spot{{background:#fff}}

/* ---- grid of days ---- */
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
font-size:12.5px;font-weight:600;transition:opacity .15s,transform .15s}}
a.b:hover{{opacity:.88;transform:translateY(-1px)}}
a.b.a{{background:linear-gradient(90deg,#e03131,#f76707)}}
a.b.etf{{background:linear-gradient(90deg,#1971c2,#4dabf7)}}
a.b.hk{{background:linear-gradient(90deg,#d9480f,#f08c00)}}
footer{{text-align:center;color:#98a1b3;font-size:12px;padding:18px 12px 30px;line-height:1.8}}
@media(max-width:640px){{
    header h1{{font-size:21px}}
    .grid{{grid-template-columns:1fr}}
    .day{{padding:12px 14px}}
    .toolbar{{flex-direction:column;align-items:stretch}}
    .chips{{justify-content:space-between}}
    .chip-b{{flex:1;text-align:center}}
}}
</style></head><body>
<header><div class="head-wrap">
<h1>📈 每日<span>指标报告</span></h1>
<p>A股 · ETF · 港股通 — KDJ 多周期信号 / 高胜率策略 / 价值筛选</p>
<div class="stats">
<span class="chip">📅 已收录 {len(dates)} 个交易日</span>
<span class="chip">📊 {total_reports} 份报告</span>
<span class="chip">🔄 每交易日约 17:00 后更新</span>
</div>
{nav_html}
</div></header>
<main>{buy_html}<div class="grid" id="dayGrid">
{body}</div></main>
<footer>由 GitHub Actions 每个交易日自动构建部署<br>数据仅供研究参考，不构成任何投资建议</footer>
<script>
/* ---- 手风琴折叠（纯CSS + checkbox 已实现折叠效果；此处只做无障碍/兼容性） ---- */

/* ---- 搜索 + 市场筛选 ---- */
(function(){{
    var q = document.getElementById('searchInput');
    var grid = document.getElementById('dayGrid');
    var empty = document.getElementById('emptyHint');
    var allBtns = document.querySelectorAll('.chip-b');
    var curMkt = 'all';

    function apply(){{
        var kw = (q ? q.value || '' : '').trim().toLowerCase();
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
        if (empty) empty.style.display = visible ? 'none' : '';
    }}

    if (q) q.addEventListener('input', apply);
    allBtns.forEach(function(b){{
        b.addEventListener('click', function(){{
            allBtns.forEach(function(x){{ x.classList.remove('active'); }});
            b.classList.add('active');
            var m = b.getAttribute('data-mkt');
            curMkt = m ? m : 'all';
            apply();
        }});
    }});
    // 初始化：默认选中第一个
    if (allBtns[0]) allBtns[0].classList.add('active');
    apply();
}})();
</script>
</body></html>"""


def main():
    entries = collect()
    dates = [(d, entries[d]) for d in sorted(entries, reverse=True)[:MAX_DAYS]]
    if os.path.isdir(DOCS_DIR):
        shutil.rmtree(DOCS_DIR)
    os.makedirs(DOCS_DIR, exist_ok=True)
    for date, keys in dates:
        dst = os.path.join(DOCS_DIR, date)
        os.makedirs(dst, exist_ok=True)
        for key, src in keys.items():
            shutil.copy(src, os.path.join(dst, f"{key}.html"))
    extras = collect_extras()

    latest = dates[0][0] if dates else None
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

    buy_today, buy_review = collect_buy_fragments()
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(dates, extras, buy_today, buy_review))
    n_reports = sum(len(k) for _, k in dates)
    print(f"站点已生成: {len(dates)} 天 / {n_reports} 份报告 + {len(extras)} 个附加页 -> {DOCS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
