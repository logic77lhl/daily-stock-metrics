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
import time

import numpy as np
import pandas as pd

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
RESULT_DIR = os.path.join(BASE_DIR, "backtest_results")

DEFAULT_HORIZONS = [1, 2, 3, 5, 10, 20, 40, 60]
DISPLAY_HORIZONS = (1, 2, 3, 5, 10, 20)
RECENT_HORIZON = 3

STRATEGIES = [
    ("全样本(基准)", None),
    # ---- 均线趋势类 ----
    ("双均线多头(MA20>MA60)", "MA20 > MA60"),
    ("双均线空头(MA20<MA60)", "MA20 < MA60"),
    ("价上MA20且多头排列", "最新价 > MA20 and MA20 > MA60"),
    # ---- KDJ 三周期共振类 ----
    ("三周期共振偏强(均>50)", "日线J > 50 and 周线J > 50 and 月线J > 50"),
    ("三周期共振偏弱(均<50)", "日线J < 50 and 周线J < 50 and 月线J < 50"),
    ("三周期共振超买(均>80)", "日线J > 80 and 周线J > 80 and 月线J > 80"),
    ("三周期共振超卖(均<20)", "日线J < 20 and 周线J < 20 and 月线J < 20"),
    ("三周期共振新低(均<0)", "日线J < 0 and 周线J < 0 and 月线J < 0"),
    # ---- 分化类 ----
    ("分化-日高周低", "日线J > 50 and 周线J < 50"),
    ("分化-日低周高", "日线J < 50 and 周线J > 50"),
    # ---- 组合类（KDJ × 趋势/量能）----
    ("超卖+多头排列", "日线J < 20 and MA20 > MA60"),
    ("超买+空头排列", "日线J > 80 and MA20 < MA60"),
    ("低位+放量(J<30且量比>1.5)", "日线J < 30 and 量比 > 1.5"),
]

# 注意：长键在前，避免子串替换冲突（如 价距MA20% 先于 MA20）
ALIASES = {
    "价距MA20%": "px_ma20_gap",
    "PE历史分位%": "pe_p",
    "PB历史分位%": "pb_p",
    "PE5年分位%": "pe_p5",
    "PB5年分位%": "pb_p5",
    "日线J": "j_d",
    "周线J": "j_w",
    "月线J": "j_m",
    "PE_TTM": "pe",
    "PB_MRQ": "pb",
    "涨跌幅": "pct",
    "最新价": "px_close",
    "双均线多头": "ma_bull",
    "MA20": "ma20",
    "MA60": "ma60",
    "量比": "vr",
}

EXPR_COLUMNS = ["日线J", "周线J", "月线J", "PE_TTM", "PE历史分位%", "PB_MRQ", "PB历史分位%",
                "涨跌幅", "最新价", "MA20", "MA60", "双均线多头", "价距MA20%", "量比",
                "PE5年分位%", "PB5年分位%"]


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


def build_price_map(panel, market, cache_dir, log=print):
    """现拉每只标的的 qfq 日线，构建全期一致的复权价格序列。

    metrics 里存的最新价是各自抓取日的前复权价，跨日除权后不可比；
    这里统一用当前时点的复权序列定价。结果缓存到 kline_cache/ 当天复用。
    """
    import fetch_metrics as fm

    os.makedirs(cache_dir, exist_ok=True)
    session = fm.get_session()
    px = {}
    codes = sorted(panel["代码"].unique())
    for n, code in enumerate(codes, 1):
        raw = code.split("_", 1)[1] if "_" in code else code
        cache = os.path.join(cache_dir, f"{market}_{raw}.csv")
        s = None
        if os.path.exists(cache):
            try:
                df = pd.read_csv(cache, parse_dates=["date"]).set_index("date")["close"]
                if df.index.max() >= pd.Timestamp.now().normalize() - pd.Timedelta(days=5):
                    s = df
            except Exception:
                s = None
        if s is None:
            for attempt in range(3):
                try:
                    df = fm.fetch_kline(session, raw, "daily", bars=800, market=market)
                    s = df.set_index(pd.to_datetime(df["date"]))["close"]
                    s.to_frame().reset_index().to_csv(cache, index=False)
                    time.sleep(0.15)
                    break
                except Exception as e:
                    if attempt == 2:
                        log(f"[{market}] {raw} 价格获取失败，剔除该标的: {e}")
                    time.sleep(1)
        if s is not None:
            px[code] = s.sort_index()
    return px


def _px_at(s, ts):
    ts = pd.Timestamp(ts)
    idx = s.index.searchsorted(ts, side="right") - 1
    if idx < 0 or idx >= len(s):
        return None
    return float(s.iloc[idx])


def build_trades(panel, horizons, px, cost_pct=0.15):
    dates = sorted(panel["日期"].unique())
    info = panel.set_index(["日期", "代码"])

    rows = []
    for i, d in enumerate(dates):
        day = panel[panel["日期"] == d]
        for h in horizons:
            j = i + h
            if j >= len(dates):
                continue
            sell_d = dates[j]
            for _, r in day.iterrows():
                code = r["代码"]
                s = px.get(code)
                if s is None:
                    continue
                rec_key = (d, code)
                if rec_key not in info.index:
                    continue
                buy = _px_at(s, d)
                sell = _px_at(s, sell_d)
                if buy is None or sell is None or buy <= 0 or sell <= 0:
                    continue
                rec = info.loc[rec_key]
                if isinstance(rec, pd.DataFrame):
                    rec = rec.iloc[0]

                buy_pos = max(0, s.index.searchsorted(pd.Timestamp(d), side="right") - 1)
                sell_pos = max(0, s.index.searchsorted(pd.Timestamp(sell_d), side="right") - 1)

                ret = sell / buy - 1.0 - cost_pct / 100.0
                orig_code = code.split("_", 1)[1] if "_" in code else code
                rows.append({
                    "市场": r["市场"],
                    "信号日": d,
                    "代码": orig_code,
                    "名称": rec["名称"],
                    "排名": rec["排名"],
                    "持有期": h,
                    "买入价": round(float(buy), 2),
                    "卖出价": round(float(sell), 2),
                    "收益%": round(float(ret) * 100, 2),
                    "实际持有交易日": int(sell_pos - buy_pos),
                    "日线J": rec["日线J"],
                    "周线J": rec["周线J"],
                    "月线J": rec["月线J"],
                    "PE_TTM": rec["PE_TTM"],
                    "PE历史分位%": rec["PE历史分位%"],
                    "PB_MRQ": rec["PB_MRQ"],
                    "PB历史分位%": rec["PB历史分位%"],
                    "MA20": rec.get("MA20"),
                    "MA60": rec.get("MA60"),
                    "量比": rec.get("量比"),
                })
    return pd.DataFrame(rows)


def eval_expr(df, expr):
    cols = [c for c in EXPR_COLUMNS if c in df.columns]
    ren = df[cols].rename(columns=ALIASES)
    for c in EXPR_COLUMNS:
        if c not in cols:
            ren[ALIASES[c]] = float("nan")
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
            })
        if (grp["持有期"] == 1).any():
            d1 = grp[grp["持有期"] == 1].groupby("信号日")["收益%"].mean()
            equity[name] = (1 + d1 / 100).cumprod()
    return pd.DataFrame(summary), equity


def generate_html(summary, trades, equity, out_dir, first_date, last_date, n_days, cost_pct=0.15):
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

    summary = summary.sort_values(["持有期(交易日)", "胜率%"], ascending=[True, False])

    best1 = summary[(summary["持有期(交易日)"] == 1) & (summary["交易次数"] >= 10)].sort_values("平均收益%", ascending=False).head(1)
    best3 = summary[(summary["持有期(交易日)"] == 3) & (summary["交易次数"] >= 10)].sort_values("平均收益%", ascending=False).head(1)
    best1_name = best1.iloc[0]["策略"] if len(best1) else "-"
    best3_name = best3.iloc[0]["策略"] if len(best3) else "-"

    # 盈亏比 + 今日信号数
    pl_map, today_cnt = {}, {}
    for (sname, h), sub in trades.groupby(["策略", "持有期"]):
        w = sub.loc[sub["收益%"] > 0, "收益%"]
        l = sub.loc[sub["收益%"] <= 0, "收益%"]
        if len(w) and len(l) and abs(l.mean()) > 1e-9:
            pl_map[(sname, int(h))] = round(float(w.mean() / abs(l.mean())), 2)
    tt = trades[trades["信号日"] == last_date]
    for sname, sub in tt.groupby("策略"):
        today_cnt[sname] = sub["代码"].nunique()

    rules_map = dict((n, e) for n, e in STRATEGIES)
    top_picks = summary[(summary["持有期(交易日)"] == 3) & (summary["交易次数"] >= 10)] \
        .sort_values("胜率%", ascending=False).head(3)
    pick_cards = ""
    for _, pr in top_picks.iterrows():
        pname, phold = pr["策略"], int(pr["持有期(交易日)"])
        rule = rules_map.get(pname, "自定义策略")
        tcnt = today_cnt.get(pname, 0)
        recent5 = trades[(trades["策略"] == pname) & (trades["持有期"] == phold)] \
            .sort_values("信号日").tail(5)
        r5 = "".join(
            f"<span class='chip'>{str(t['信号日'])[5:10]} {esc(str(t['名称']))} "
            f"<b class='chg {cls(t['收益%'])}'>{num(t['收益%'],1,sign=True)}%</b></span> "
            for _, t in recent5.iterrows())
        pick_cards += f"""<div class="pick-card">
            <div class="pk-head"><b>{esc(pname)}</b>
            <span class="flag {'carry' if tcnt else 'normal'}">{'今日信号 ' + str(tcnt) + ' 笔' if tcnt else '今日无'}</span></div>
            <div class="pk-rule">{esc(rule)}</div>
            <div class="pk-stats">胜率 <b>{num(pr['胜率%'],1)}%</b> ｜ 平均 <b class='chg {cls(pr['平均收益%'])}'>{num(pr['平均收益%'],2,sign=True)}%</b>
            ｜ 盈亏比 <b>{num(pl_map.get((pname, phold)), 2)}</b> ｜ 样本 {int(pr['交易次数'])}</div>
            <div class="pk-recent">{r5 or '<span style="color:#999">暂无近期交易</span>'}</div></div>"""
    pick_section = f'<div class="section-title">⭐ 重点策略参考（按3日持有胜率取前3）</div><div class="picks">{pick_cards}</div>' if pick_cards else ""

    recent_days = sorted(trades.loc[trades["持有期"] == RECENT_HORIZON, "信号日"].unique())[-10:]
    recent = trades[(trades["信号日"].isin(set(recent_days))) & (trades["持有期"] == RECENT_HORIZON)] \
        .sort_values(["信号日", "策略"], ascending=[False, True]).head(300)
    recent_rows = ""
    for _, r in recent.iterrows():
        recent_rows += f"""<tr>
            <td class="name">{esc(r['策略'])}</td>
            <td>{r['信号日']}</td>
            <td>{esc(r['代码'])}</td>
            <td class="name">{esc(r['名称'])}</td>
            <td>{int(r['持有期'])}</td>
            <td class="price">{num(r['买入价'])}</td>
            <td class="chg {cls(r['收益%'])}">{num(r['收益%'], 2, sign=True)}%</td>
        </tr>"""

    rules_html = "".join(
        f"<div class='rule-line'><b>{esc(n)}</b>：{esc(e)}</div>" for n, e in STRATEGIES)

    sum_rows = ""
    disp = summary[summary["持有期(交易日)"].isin(DISPLAY_HORIZONS)]
    for _, r in disp.iterrows():
        wr = r["胜率%"]
        wr_cls = "good" if (pd.notna(wr) and wr >= 60) else "bad" if (pd.notna(wr) and wr <= 40) else ""
        key = (r['策略'], int(r['持有期(交易日)']))
        pl = pl_map.get(key)
        tc = today_cnt.get(r['策略'], 0)
        sum_rows += f"""<tr data-wr="{num(r['胜率%'], 1)}">
            <td class="name" title="{esc(rules_map.get(r['策略'], '自定义策略'))}">{esc(r['策略'])}</td>
            <td>{int(r['持有期(交易日)'])}</td>
            <td>{int(r['交易次数'])}</td>
            <td class="win {wr_cls}">{num(r['胜率%'], 1)}%</td>
            <td class="chg {cls(r['平均收益%'])}">{num(r['平均收益%'], 2, sign=True)}%</td>
            <td class="chg {cls(r['中位数收益%'])}">{num(r['中位数收益%'], 2, sign=True)}%</td>
            <td>{pl if pl is not None else '-'}</td>
            <td>{f'<span class="flag carry">✓{tc}</span>' if tc else '<span class="flag normal">-</span>'}</td>
            <td>{num(r['累计净值'], 4)}</td>
            <td>{num(r['最大回撤%'], 2)}</td>
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
    .picks {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
    .pick-card {{ background: #fff; border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-top: 3px solid #f59f00; }}
    .pk-head {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .pk-rule {{ color: #666; font-size: 12.5px; margin: 6px 0; font-family: Consolas, monospace; }}
    .pk-stats {{ font-size: 13px; margin-bottom: 8px; }}
    .pk-recent {{ font-size: 12px; line-height: 2; }}
    .chip {{ background: #f5f6fa; border-radius: 8px; padding: 2px 8px; margin-right: 4px; white-space: nowrap; display: inline-block; }}
    .rule-line {{ font-size: 12.5px; padding: 4px 0; border-bottom: 1px dashed #eee; color: #555; }}
</style>
</head>
<body>
<div class="container">
    <h1>KDJ 三周期信号回测报告</h1>
    <div class="subtitle">数据区间：{first_date} ~ {last_date}（{n_days} 个交易日）｜ 单边交易成本 {cost_pct}% ｜ 生成时间：{now_str}</div>

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
            <th class="sortable" data-col="6" data-type="num">盈亏比</th>
            <th class="sortable" data-col="7" data-type="num">今日信号</th>
            <th class="sortable" data-col="8" data-type="num">累计净值</th>
            <th class="sortable" data-col="9" data-type="num">最大回撤%</th>
        </tr></thead>
        <tbody>{sum_rows}</tbody>
    </table>
    </div>

    <div class="section-title">持有1日净值曲线（每日等权组合）</div>
    {chart_svg}

    {pick_section}

    <div class="section-title">近10个交易日信号（{RECENT_HORIZON}日持有，最新{len(recent)}笔，完整数据请下载CSV）</div>
    <div style="overflow-x: auto;">
    <table>
        <thead><tr>
            <th>策略</th><th>信号日</th><th>代码</th><th>名称</th><th>持有期</th><th>买入价</th><th>收益%</th>
        </tr></thead>
        <tbody>{recent_rows}</tbody>
    </table>
    </div>

    <details style="margin-top:14px;background:#fff;border-radius:10px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,0.08)">
    <summary style="cursor:pointer;font-weight:600;font-size:14px">📖 内置策略规则说明（悬停汇总表中的策略名也可查看）</summary>
    <div style="margin-top:10px">{rules_html}</div>
    </details>

    <div class="footer">
        回测假设：买入=信号日收盘价，卖出=持有期结束日收盘价（统一采用最新前复权序列，跨日可比）；收益已按单边成本 {cost_pct}% 扣减；
        持有期&gt;1天时交易重叠，累计净值仅供参考 ｜ 盈亏比 = 平均盈利 ÷ 平均亏损绝对值，&gt;1 表示赚多亏小<br>
        完整历史数据：<a href="backtest_report_trades.csv" download>下载全部交易明细CSV</a>（含全部持有期 1/2/3/5/10/20/40/60 日，本页仅展示常用档） ｜ 本报告由 backtest.py 生成，仅供研究，不构成投资建议
    </div>
</div>
<script>
(function() {{
    var sumRows = document.querySelectorAll('#sumTable tbody tr');
    var curWR = 0;
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
    ap.add_argument("--horizons", default="1,2,3,5,10,20,40,60", help="持有期(交易日), 逗号分隔")
    ap.add_argument("--cost", type=float, default=0.15, help="单边交易成本%%(佣金+税费+滑点), 默认0.15")
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

        print(f"[{mkt_name}] 重建统一复权价格序列（缓存: {os.path.join(args.outdir, 'kline_cache')}）…")
        px = build_price_map(panel, mkt_name, os.path.join(args.outdir, "kline_cache"))
        print(f"[{mkt_name}] 价格序列就绪: {len(px)}/{panel['代码'].nunique()} 只")

        trades = build_trades(panel, horizons, px, cost_pct=args.cost)
        if trades.empty:
            print(f"[{mkt_name}] 数据不足，跳过")
            continue
        print(f"[{mkt_name}] 共生成 {len(trades)} 条潜在交易记录（单边成本 {args.cost}%）")

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
        html_path = generate_html(summary, all_trades, equity, mkt_outdir, dates_all[0], dates_all[-1], len(dates_all), cost_pct=args.cost)
        all_trades.to_csv(os.path.join(mkt_outdir, "backtest_report_trades.csv"), index=False, encoding="utf-8-sig")
        print(f"[{mkt_name}] HTML报告已生成: {html_path}")

        cols = ["策略", "持有期(交易日)", "交易次数", "胜率%", "平均收益%", "中位数收益%", "累计净值", "最大回撤%"]
        pd.set_option("display.width", 200)
        pd.set_option("display.max_rows", 200)
        print(f"\n--- {mkt_name} 回测结果 ---")
        print(summary[cols].to_string(index=False))

    print(f"\n结果已保存到 {args.outdir}")


if __name__ == "__main__":
    main()
