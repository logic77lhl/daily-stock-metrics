# -*- coding: utf-8 -*-
"""基于 output\\日期\\metrics_*.csv 每日数据的回测脚本。

用法:
    python backtest.py
    python backtest.py --horizons 1,3,5,10
    python backtest.py --strategy "自定义策略=日线J<30 and PB历史分位%<40"
"""

import argparse
import glob
import html as html_mod
import os
import sys

import numpy as np
import pandas as pd

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
RESULT_DIR = os.path.join(BASE_DIR, "backtest_results")

DEFAULT_HORIZONS = [1, 2, 3, 5, 10, 20]
MAX_CARRY_DAYS = 60

STRATEGIES = [
    ("全样本(基准)", None),
    ("三周期共振偏强(均>50)", "日线J > 50 and 周线J > 50 and 月线J > 50"),
    ("三周期共振偏弱(均<50)", "日线J < 50 and 周线J < 50 and 月线J < 50"),
    ("三周期共振超买(均>80)", "日线J > 80 and 周线J > 80 and 月线J > 80"),
    ("三周期共振超卖(均<20)", "日线J < 20 and 周线J < 20 and 月线J < 20"),
    ("三周期共振新低(均<0)", "日线J < 0 and 周线J < 0 and 月线J < 0"),
    ("分化-日高周低", "日线J > 50 and 周线J < 50"),
    ("分化-日低周高", "日线J < 50 and 周线J > 50"),
]

ALIASES = {
    "日线J": "j_d",
    "周线J": "j_w",
    "月线J": "j_m",
    "PE历史分位%": "pe_p",
    "PB历史分位%": "pb_p",
    "PE_TTM": "pe",
    "PB_MRQ": "pb",
    "涨跌幅": "pct",
}


MARKET_DIRS = {
    "个股": OUTPUT_DIR,
    "ETF":  os.path.join(BASE_DIR, "output_etf"),
    "HK":   os.path.join(BASE_DIR, "output_hk"),
}


def load_metrics(output_dir, market="个股"):
    frames = []
    dates = []
    for path in sorted(glob.glob(os.path.join(output_dir, "????-??-??", "metrics_*.csv"))):
        date = os.path.basename(os.path.dirname(path))
        df = pd.read_csv(path, dtype={"代码": str})
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        df["日期"] = pd.Timestamp(date)
        df["市场"] = market
        frames.append(df)
        dates.append(date)
    if not frames:
        print(f"[{market}] 在 {output_dir} 下未找到任何 metrics 文件，跳过")
        return None
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.drop_duplicates(subset=["日期", "代码"], keep="first")
    print(f"[{market}] 加载 {len(frames)} 天数据: {dates[0]} ~ {dates[-1]}")
    return panel


def build_trades(panel, horizons):
    prices = panel.pivot_table(index="日期", columns="代码", values="最新价", aggfunc="first").sort_index()
    dates = list(prices.index)

    last_obs = pd.DataFrame(index=prices.index, columns=prices.columns, dtype="datetime64[ns]")
    for code in prices.columns:
        s = prices[code].dropna()
        for idx in prices.index:
            tmp = s[s.index <= idx]
            if not tmp.empty:
                last_obs.at[idx, code] = tmp.index[-1]

    prices_ff = prices.ffill(limit=MAX_CARRY_DAYS)
    info = panel.set_index(["日期", "代码"])

    rows = []
    for i, d in enumerate(dates):
        for h in horizons:
            j = i + h
            if j >= len(dates):
                continue
            sel = prices.iloc[i].dropna()
            for code, buy in sel.items():
                row_key = (d, code)
                if row_key not in info.index:
                    continue
                rec = info.loc[row_key]
                if isinstance(rec, pd.DataFrame):
                    rec = rec.iloc[0]
                sell = prices_ff.iloc[j][code]
                if pd.isna(sell):
                    continue
                lo = last_obs.iloc[j][code]
                if pd.isna(lo):
                    continue
                hold_pos = prices.index.get_indexer([lo], method="pad")[0]
                carry = lo < dates[j]
                ret = sell / buy - 1.0
                orig_code = code.split("_", 1)[1] if "_" in code else code
                rows.append({
                    "市场": rec["市场"],
                    "信号日": d,
                    "代码": orig_code,
                    "名称": rec["名称"],
                    "排名": rec["排名"],
                    "持有期": h,
                    "买入价": round(float(buy), 2),
                    "卖出价": round(float(sell), 2),
                    "收益%": round(float(ret) * 100, 2),
                    "实际持有交易日": int(hold_pos - i),
                    "数据补齐": carry,
                    "日线J": rec["日线J"],
                    "周线J": rec["周线J"],
                    "月线J": rec["月线J"],
                    "PE_TTM": rec["PE_TTM"],
                    "PE历史分位%": rec["PE历史分位%"],
                    "PB_MRQ": rec["PB_MRQ"],
                    "PB历史分位%": rec["PB历史分位%"],
                })
    return pd.DataFrame(rows)


def eval_expr(df, expr):
    ren = df[["日线J", "周线J", "月线J", "PE_TTM", "PE历史分位%", "PB_MRQ", "PB历史分位%", "涨跌幅"]].rename(columns=ALIASES)
    for k, v in ALIASES.items():
        expr = expr.replace(k, v)
    return ren.eval(expr).reindex(df.index)


def run_strategy(trades, panel, expr):
    selected = trades.copy()
    if expr is not None:
        sigs = []
        for d, g in panel.groupby("日期"):
            mask = eval_expr(g, expr)
            sigs.append(pd.DataFrame({"信号日": d, "代码": g.loc[mask, "代码"]}))
        sig = pd.concat(sigs, ignore_index=True)
        selected = selected.merge(sig, on=["信号日", "代码"], how="inner")
    return selected


def summarize(trades_df, horizons):
    summary = []
    equity = {}
    for name, grp in trades_df.groupby("策略"):
        for h in horizons:
            sub = grp[grp["持有期"] == h]
            if sub.empty:
                continue
            daily = sub.groupby("信号日")["收益%"].mean()
            nav = (1 + daily / 100).cumprod()
            dd = (nav / nav.cummax() - 1).min() * 100
            summary.append({
                "策略": name,
                "持有期(交易日)": h,
                "交易次数": len(sub),
                "胜率%": round(float((sub["收益%"] > 0).mean() * 100), 1),
                "平均收益%": round(float(sub["收益%"].mean()), 2),
                "中位数收益%": round(float(sub["收益%"].median()), 2),
                "累计净值": round(float(nav.iloc[-1]), 4),
                "最大回撤%": round(float(dd), 2) if h == 1 else None,
                "数据补齐占比%": round(float(sub["数据补齐"].mean() * 100), 1),
            })
        if (grp["持有期"] == 1).any():
            d1 = grp[grp["持有期"] == 1].groupby("信号日")["收益%"].mean()
            equity[name] = (1 + d1 / 100).cumprod()
    return pd.DataFrame(summary), equity


def generate_html(summary, trades, equity, out_dir, first_date, last_date, n_days):
    def esc(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "-"
        return html_mod.escape(str(v))

    def num(v, digits=2, sign=False):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "-"
        if isinstance(v, str):
            return esc(v)
        return f"{float(v):{'+' if sign else ''}.{digits}f}"

    def cls(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "flat"
        return "up" if float(v) > 0 else "down" if float(v) < 0 else "flat"

    summary = summary.sort_values(["策略", "持有期(交易日)"])

    best1 = summary[(summary["持有期(交易日)"] == 1) & (summary["交易次数"] >= 10)].sort_values("平均收益%", ascending=False).head(1)
    best3 = summary[(summary["持有期(交易日)"] == 3) & (summary["交易次数"] >= 10)].sort_values("平均收益%", ascending=False).head(1)
    best1_name = best1.iloc[0]["策略"] if len(best1) else "-"
    best3_name = best3.iloc[0]["策略"] if len(best3) else "-"

    sum_rows = ""
    for _, r in summary.iterrows():
        wr = r["胜率%"]
        wr_cls = "good" if (pd.notna(wr) and wr >= 60) else "bad" if (pd.notna(wr) and wr <= 40) else ""
        sum_rows += f"""<tr data-wr="{num(r['胜率%'], 1)}">
            <td class="name">{esc(r['策略'])}</td>
            <td>{int(r['持有期(交易日)'])}</td>
            <td>{int(r['交易次数'])}</td>
            <td class="win {wr_cls}">{num(r['胜率%'], 1)}%</td>
            <td class="chg {cls(r['平均收益%'])}">{num(r['平均收益%'], 2, sign=True)}%</td>
            <td class="chg {cls(r['中位数收益%'])}">{num(r['中位数收益%'], 2, sign=True)}%</td>
            <td>{num(r['累计净值'], 4)}</td>
            <td>{num(r['最大回撤%'], 2)}</td>
            <td>{num(r['数据补齐占比%'], 1)}%</td>
        </tr>"""

    chart_svg = ""
    legend_html = ""
    if equity:
        all_nav = np.concatenate([s.values for s in equity.values()])
        lo, hi = float(np.nanmin(all_nav)), float(np.nanmax(all_nav))
        if hi - lo < 1e-9:
            hi = lo + 1
        W, H, pad_l, pad_r, pad_t, pad_b = 1000, 280, 60, 30, 20, 30
        palette = ["#1565c0", "#6a1b9a", "#c62828", "#2e7d32", "#e65100", "#f57f17", "#00838f", "#5d4037", "#455a64", "#d81b60"]
        lines = []
        for i, (name, s) in enumerate(equity.items()):
            if len(s) < 2:
                continue
            n = len(s)
            pts = []
            for j, v in enumerate(s.values):
                x = pad_l + (W - pad_l - pad_r) * j / (n - 1)
                y = pad_t + (H - pad_t - pad_b) * (1 - (float(v) - lo) / (hi - lo))
                pts.append(f"{x:.1f},{y:.1f}")
            color = palette[i % len(palette)]
            last_v = float(s.iloc[-1])
            lines.append(f"""<polyline points="{' '.join(pts)}" fill="none" stroke="{color}" stroke-width="2"/>""")
            legend_html += f"""<div class="legend-item"><span class="dot" style="background:{color}"></span>{esc(name)} <b class="chg {cls(last_v - 1)}">{num((last_v - 1) * 100, 1, sign=True)}%</b></div>"""
        if lines:
            grid = "".join(
                f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}" stroke="#eee" stroke-width="1"/>'
                for y in np.linspace(pad_t, H - pad_b, 5)
            )
            chart_svg = f"""<svg viewBox="0 0 {W} {H}" width="100%" preserveAspectRatio="none" style="background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
                {grid}{''.join(lines)}</svg>
                <div class="legend-box">{legend_html}</div>"""

    strat_names = trades["策略"].unique()
    strat_btns = "".join(f'<button class="filter-btn active" data-s="all">全部</button>')
    for name in strat_names:
        strat_btns += f'<button class="filter-btn" data-s="{esc(name)}">{esc(name)}</button>'
    holds = sorted(trades["持有期"].unique())
    hold_btns = '<button class="filter-btn active" data-h="all">全部</button>'
    for h in holds:
        hold_btns += f'<button class="filter-btn" data-h="{h}">{h}天</button>'

    trades = trades.sort_values(["信号日", "策略", "持有期"])
    trade_rows = ""
    for _, r in trades.iterrows():
        trade_rows += f"""<tr data-s="{esc(r['策略'])}" data-h="{int(r['持有期'])}">
            <td class="name">{esc(r['策略'])}</td>
            <td>{r['信号日']}</td>
            <td>{esc(r['代码'])}</td>
            <td class="name">{esc(r['名称'])}</td>
            <td>{int(r['持有期'])}</td>
            <td class="price">{num(r['买入价'])}</td>
            <td class="price">{num(r['卖出价'])}</td>
            <td class="chg {cls(r['收益%'])}">{num(r['收益%'], 2, sign=True)}%</td>
            <td>{int(r['实际持有交易日'])}</td>
            <td>{'<span class="flag carry">补齐</span>' if r['数据补齐'] else '<span class="flag normal">正常</span>'}</td>
            <td>{num(r['日线J'], 1)}</td>
            <td>{num(r['周线J'], 1)}</td>
            <td>{num(r['月线J'], 1)}</td>
            <td>{num(r['PE历史分位%'], 1)}</td>
            <td>{num(r['PB历史分位%'], 1)}</td>
        </tr>"""

    now_str = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>指标回测报告 - {first_date} ~ {last_date}</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f0f2f5; color: #333; padding: 20px; }}
    .container {{ max-width: 1500px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin-bottom: 4px; color: #1a1a2e; }}
    .subtitle {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .summary-card {{ background: #fff; border-radius: 10px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .summary-card .num {{ font-size: 28px; font-weight: 700; }}
    .summary-card .label {{ font-size: 13px; color: #888; margin-top: 2px; }}
    .card-blue .num {{ color: #1565c0; }}
    .card-green .num {{ color: #2e7d32; }}
    .card-red .num {{ color: #c62828; }}
    .card-gray .num {{ color: #666; }}
    .card-gold .num {{ color: #f57f17; }}
    .section-title {{ font-size: 16px; font-weight: 600; margin: 20px 0 10px; color: #1a1a2e; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    th {{ background: #1a1a2e; color: #fff; padding: 12px 10px; font-size: 13px; font-weight: 600; text-align: center; white-space: nowrap; }}
    td {{ padding: 10px; text-align: center; font-size: 13px; border-bottom: 1px solid #f0f0f0; white-space: nowrap; }}
    tr:hover {{ background: #f8f9ff; }}
    .name {{ text-align: left; font-weight: 500; }}
    .price {{ font-weight: 600; }}
    .chg {{ font-weight: 600; }}
    .chg.up {{ color: #c62828; }}
    .chg.down {{ color: #2e7d32; }}
    .chg.flat {{ color: #666; }}
    .win.good {{ color: #2e7d32; font-weight: 700; }}
    .win.bad {{ color: #c62828; font-weight: 700; }}
    .flag {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
    .flag.carry {{ background: #fff3e0; color: #e65100; }}
    .flag.normal {{ background: #f5f5f5; color: #999; }}
    .filter-bar {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }}
    .filter-btn {{ padding: 5px 14px; border: 1px solid #ddd; border-radius: 16px; background: #fff; font-size: 12px; cursor: pointer; transition: all 0.15s; }}
    .filter-btn:hover {{ border-color: #1a1a2e; }}
    .filter-btn.active {{ background: #1a1a2e; color: #fff; border-color: #1a1a2e; }}
    .filter-label {{ font-size: 13px; color: #888; line-height: 30px; margin-right: 4px; }}
    .legend-box {{ display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; }}
    .legend-item {{ font-size: 13px; color: #555; }}
    .legend-item .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }}
    .footer {{ margin-top: 16px; font-size: 12px; color: #999; text-align: center; }}
    th.sortable {{ cursor: pointer; user-select: none; position: relative; }}
    th.sortable:hover {{ background: #2a2a4e; }}
    th.sortable::after {{ content: ' ⇅'; font-size: 11px; opacity: 0.4; }}
    th.sort-asc::after {{ content: ' ↑'; opacity: 1; }}
    th.sort-desc::after {{ content: ' ↓'; opacity: 1; }}
</style>
</head>
<body>
<div class="container">
    <h1>KDJ 三周期信号回测报告</h1>
    <div class="subtitle">数据区间：{first_date} ~ {last_date}（{n_days} 个交易日）｜ 生成时间：{now_str}</div>

    <div class="section-title">概览</div>
    <div class="summary-grid">
        <div class="summary-card card-blue"><div class="num">{n_days}</div><div class="label">回测交易日数</div></div>
        <div class="summary-card card-gray"><div class="num">{len(trades)}</div><div class="label">总交易笔数</div></div>
        <div class="summary-card card-green"><div class="num">{esc(best1_name)}</div><div class="label">持有1天最佳策略</div></div>
        <div class="summary-card card-green"><div class="num">{esc(best3_name)}</div><div class="label">持有3天最佳策略</div></div>
    </div>

    <div class="section-title">汇总统计</div>
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px;">
        <div class="filter-label">胜率 ≥</div>
        <input id="wrFilter" type="range" min="0" max="100" value="0" step="1" style="width:180px;vertical-align:middle;">
        <span id="wrVal" style="font-size:13px;font-weight:600;min-width:40px;">0%</span>
        <span id="wrCount" style="font-size:12px;color:#888;"></span>
    </div>
    <div style="overflow-x: auto;">
    <table id="sumTable">
        <thead><tr>
            <th class="sortable" data-col="0" data-type="str">策略</th>
            <th class="sortable" data-col="1" data-type="num">持有期(交易日)</th>
            <th class="sortable" data-col="2" data-type="num">交易次数</th>
            <th class="sortable" data-col="3" data-type="num">胜率%</th>
            <th class="sortable" data-col="4" data-type="num">平均收益%</th>
            <th class="sortable" data-col="5" data-type="num">中位数收益%</th>
            <th class="sortable" data-col="6" data-type="num">累计净值</th>
            <th class="sortable" data-col="7" data-type="num">最大回撤%</th>
            <th class="sortable" data-col="8" data-type="num">数据补齐占比%</th>
        </tr></thead>
        <tbody>{sum_rows}</tbody>
    </table>
    </div>

    <div class="section-title">持有1日净值曲线（每日等权组合）</div>
    {chart_svg}

    <div class="section-title">交易明细（{len(trades)} 笔）</div>
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:8px;">
        <div class="filter-label">策略:</div>
        <div class="filter-bar" id="stratBar">{strat_btns}</div>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px;">
        <div class="filter-label">持有期:</div>
        <div class="filter-bar" id="holdBar">{hold_btns}</div>
    </div>
    <div style="overflow-x: auto;">
    <table id="tradeTable">
        <thead><tr>
            <th class="sortable" data-col="0" data-type="str">策略</th>
            <th class="sortable" data-col="1" data-type="str">信号日</th>
            <th class="sortable" data-col="2" data-type="str">代码</th>
            <th class="sortable" data-col="3" data-type="str">名称</th>
            <th class="sortable" data-col="4" data-type="num">持有期</th>
            <th class="sortable" data-col="5" data-type="num">买入价</th>
            <th class="sortable" data-col="6" data-type="num">卖出价</th>
            <th class="sortable" data-col="7" data-type="num">收益%</th>
            <th class="sortable" data-col="8" data-type="num">实际持有</th>
            <th class="sortable" data-col="9" data-type="str">数据</th>
            <th class="sortable" data-col="10" data-type="num">日线J</th>
            <th class="sortable" data-col="11" data-type="num">周线J</th>
            <th class="sortable" data-col="12" data-type="num">月线J</th>
            <th class="sortable" data-col="13" data-type="num">PE分位%</th>
            <th class="sortable" data-col="14" data-type="num">PB分位%</th>
        </tr></thead>
        <tbody>{trade_rows}</tbody>
    </table>
    </div>

    <div class="footer">
        回测假设：买入=信号日收盘价，卖出=持有期结束日收盘价；未计手续费/滑点；跌出市值前100的股票用其后最近收盘价补齐；
        价格未做除权除息调整；持有期&gt;1天时交易重叠，累计净值仅供参考 ｜ 本报告由 backtest.py 生成，仅供研究，不构成投资建议
    </div>
</div>
<script>
(function() {{
    var tradeRows = document.querySelectorAll('#tradeTable tbody tr');
    var sumRows = document.querySelectorAll('#sumTable tbody tr');
    var curS = 'all', curH = 'all', curWR = 0;
    function applyTrade() {{
        tradeRows.forEach(function(row) {{
            var okS = curS === 'all' || row.getAttribute('data-s') === curS;
            var okH = curH === 'all' || row.getAttribute('data-h') === curH;
            row.style.display = okS && okH ? '' : 'none';
        }});
    }}
    function applySummary() {{
        var shown = 0;
        sumRows.forEach(function(row) {{
            var wr = parseFloat(row.getAttribute('data-wr')) || 0;
            var ok = wr >= curWR;
            row.style.display = ok ? '' : 'none';
            if (ok) shown++;
        }});
        document.getElementById('wrCount').textContent = shown + '/' + sumRows.length + ' 条';
    }}
    function bind(barId, attr, setter) {{
        var bar = document.getElementById(barId);
        bar.querySelectorAll('.filter-btn').forEach(function(btn) {{
            btn.addEventListener('click', function() {{
                bar.querySelectorAll('.filter-btn').forEach(function(b) {{ b.classList.remove('active'); }});
                this.classList.add('active');
                setter(this.getAttribute(attr));
                applyTrade();
            }});
        }});
    }}
    bind('stratBar', 'data-s', function(v) {{ curS = v; }});
    bind('holdBar', 'data-h', function(v) {{ curH = v; }});

    var slider = document.getElementById('wrFilter');
    var label = document.getElementById('wrVal');
    slider.addEventListener('input', function() {{
        curWR = parseFloat(this.value);
        label.textContent = curWR + '%';
        applySummary();
    }});
    applySummary();
}})();
</script>
<script>
document.querySelectorAll('#sumTable th.sortable').forEach(function(th) {{
    th.addEventListener('click', function() {{
        var table = document.getElementById('sumTable');
        var col = parseInt(this.getAttribute('data-col'));
        var type = this.getAttribute('data-type');
        var asc = this.classList.contains('sort-asc');
        table.querySelectorAll('th.sortable').forEach(function(h) {{ h.classList.remove('sort-asc', 'sort-desc'); }});
        this.classList.add(asc ? 'sort-desc' : 'sort-asc');
        var tbody = table.querySelector('tbody');
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        rows.sort(function(a, b) {{
            var av = a.children[col].textContent.replace(/[%↑↓ ⇅]/g, '').trim();
            var bv = b.children[col].textContent.replace(/[%↑↓ ⇅]/g, '').trim();
            if (type === 'num') {{
                av = parseFloat(av) || 0;
                bv = parseFloat(bv) || 0;
                return asc ? av - bv : bv - av;
            }}
            return asc ? av.localeCompare(bv, 'zh') : bv.localeCompare(av, 'zh');
        }});
        rows.forEach(function(r) {{ tbody.appendChild(r); }});
    }});
}});
document.querySelectorAll('#tradeTable th.sortable').forEach(function(th) {{
    th.addEventListener('click', function() {{
        var table = document.getElementById('tradeTable');
        var col = parseInt(this.getAttribute('data-col'));
        var type = this.getAttribute('data-type');
        var asc = this.classList.contains('sort-asc');
        table.querySelectorAll('th.sortable').forEach(function(h) {{ h.classList.remove('sort-asc', 'sort-desc'); }});
        this.classList.add(asc ? 'sort-desc' : 'sort-asc');
        var tbody = table.querySelector('tbody');
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        rows.sort(function(a, b) {{
            var av = a.children[col].textContent.replace(/[%↑↓ ⇅]/g, '').trim();
            var bv = b.children[col].textContent.replace(/[%↑↓ ⇅]/g, '').trim();
            if (type === 'num') {{
                av = parseFloat(av) || 0;
                bv = parseFloat(bv) || 0;
                return asc ? av - bv : bv - av;
            }}
            return asc ? av.localeCompare(bv, 'zh') : bv.localeCompare(av, 'zh');
        }});
        rows.forEach(function(r) {{ tbody.appendChild(r); }});
    }});
}});
</script>
</body>
</html>"""

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "backtest_report.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="对 output 目录每日 metrics 数据进行回测")
    ap.add_argument("--market", default="all", help="市场: all(默认), 个股, ETF, HK, 或逗号分隔如 个股,ETF")
    ap.add_argument("--input", default=None, help="数据目录(仅单市场时使用, 覆盖 --market)")
    ap.add_argument("--horizons", default="1,2,3,5,10,20", help="持有期(交易日), 逗号分隔")
    ap.add_argument("--strategy", action="append", default=[], help="自定义策略, 格式: 名称=日线J<0 and 周线J<0, 可多次传入")
    ap.add_argument("--outdir", default=RESULT_DIR, help="结果输出目录")
    args = ap.parse_args()

    horizons = [int(x) for x in args.horizons.split(",")]
    strategies = list(STRATEGIES)
    for s in args.strategy:
        if "=" in s:
            name, expr = s.split("=", 1)
            strategies.append((name.strip(), expr.strip()))
        else:
            strategies.append((s.strip(), s.strip()))

    if args.input:
        markets_to_run = [("个股", args.input)]
    elif args.market == "all":
        markets_to_run = list(MARKET_DIRS.items())
    else:
        markets_to_run = []
        for m in args.market.split(","):
            m = m.strip()
            if m in MARKET_DIRS:
                markets_to_run.append((m, MARKET_DIRS[m]))
            else:
                raise SystemExit(f"未知市场 '{m}'，可选: {', '.join(MARKET_DIRS.keys())}")

    for mkt_name, mkt_dir in markets_to_run:
        print(f"\n{'='*50}")
        print(f"  回测市场: {mkt_name}")
        print(f"{'='*50}")
        panel = load_metrics(mkt_dir, market=mkt_name)
        if panel is None:
            continue

        code_map = {}
        for code in panel["代码"].unique():
            code_map[code] = f"{mkt_name}_{code}"
        panel["代码"] = panel["代码"].map(code_map)

        trades = build_trades(panel, horizons)
        if trades.empty:
            print(f"[{mkt_name}] 数据不足，跳过")
            continue
        print(f"[{mkt_name}] 共生成 {len(trades)} 条潜在交易记录")

        panel_orig = panel.copy()
        panel_orig["代码"] = panel_orig["代码"].map(lambda x: x.split("_", 1)[1] if "_" in x else x)

        framed = []
        for name, expr in strategies:
            sel = run_strategy(trades, panel_orig, expr)
            sel = sel.assign(策略=name)
            framed.append(sel)
            if expr:
                print(f"[{mkt_name}] 策略 [{name}] 触发 {len(sel)} 笔")

        all_trades = pd.concat(framed, ignore_index=True)
        summary, equity = summarize(all_trades, horizons)

        mkt_outdir = os.path.join(args.outdir, mkt_name)
        os.makedirs(mkt_outdir, exist_ok=True)
        summary.to_csv(os.path.join(mkt_outdir, "summary.csv"), index=False, encoding="utf-8-sig")
        all_trades.to_csv(os.path.join(mkt_outdir, "trades.csv"), index=False, encoding="utf-8-sig")
        if equity:
            pd.DataFrame(equity).to_csv(os.path.join(mkt_outdir, "equity_1d.csv"), encoding="utf-8-sig")

        dates_all = sorted(panel["日期"].dt.strftime("%Y-%m-%d").unique())
        html_path = generate_html(summary, all_trades, equity, mkt_outdir, dates_all[0], dates_all[-1], len(dates_all))
        print(f"[{mkt_name}] HTML报告已生成: {html_path}")

        cols = ["策略", "持有期(交易日)", "交易次数", "胜率%", "平均收益%", "中位数收益%", "累计净值", "最大回撤%", "数据补齐占比%"]
        pd.set_option("display.width", 200)
        pd.set_option("display.max_rows", 200)
        print(f"\n--- {mkt_name} 回测结果 ---")
        print(summary[cols].to_string(index=False))

    print(f"\n结果已保存到 {args.outdir}")


if __name__ == "__main__":
    main()
