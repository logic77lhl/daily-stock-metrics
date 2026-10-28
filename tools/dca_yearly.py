# -*- coding: utf-8 -*-
"""长期定投年度回测：逐年一行，含回撤与 HS300 对照。

    python tools/dca_yearly.py --code 601398 --start 2012-01-01 --monthly 1000

## 口径（固定）

* **每月首个交易日**买入 `--monthly` 元，**不复权价**成交（分红才可单独拆出）。
* 分红按除权日结算；两条线：`--no-reinvest` 切换「收现金」。
* **当年收益** 用 **TWR**（时间加权）而不是「市值变化」——
  定投每月有新钱进来，直接看市值变化会把「投入」算成「收益」。
  TWR 把现金流剥离：`r_t = (V_t − flow_t) / V_{t−1}`，逐年连乘。
* **最大回撤** 也基于 TWR 净值曲线（同样剔除投入的影响）。
* **HS300 对照** 用同期指数收盘涨幅（同起止日）。
* XIRR 按每笔现金流日期加权（定投的正确年化口径）。

## 数据

个股与基准都缓存到 `_fin_cache/`，缺失时用 baostock 补抓（adjustflag=3 不复权）。
"""
import argparse
import csv
import os
import sys
from datetime import date

sys.path.insert(0, r"C:\Users\user\.dsh\ungrouped\_probe_libs")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

CACHE = os.path.join(BASE, "_fin_cache")


# ─────────────────────────── 数据 ───────────────────────────
def _bs_fetch(bs_code, start, end):
    import baostock as bs
    bs.login()
    rs = bs.query_history_k_data_plus(bs_code, "date,open,high,low,close",
                                      start_date=start, end_date=end,
                                      frequency="d", adjustflag="3")
    rows = []
    while rs.error_code == "0" and rs.next():
        d, o, h, l, c = rs.get_row_data()
        if c:
            rows.append(dict(date=d, open=float(o), high=float(h),
                             low=float(l), close=float(c)))
    bs.logout()
    return rows


def load_series(path, bs_code, start, end, log=print):
    """优先读缓存；不足则补抓并覆盖。"""
    if os.path.exists(path):
        rows = []
        with open(path, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                try:
                    rows.append(dict(date=r["date"][:10], open=float(r["open"]),
                                     high=float(r["high"]), low=float(r["low"]),
                                     close=float(r["close"])))
                except (KeyError, ValueError):
                    continue
        if rows and rows[0]["date"] <= start:
            log(f"  缓存 {os.path.basename(path)}：{len(rows)} 天"
                f"（{rows[0]['date']} ~ {rows[-1]['date']}）")
            return rows
    log(f"  抓 {bs_code} …")
    rows = _bs_fetch(bs_code, start, end)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "open", "high", "low", "close"])
        w.writeheader()
        w.writerows(rows)
    log(f"  已抓 {len(rows)} 天（{rows[0]['date']} ~ {rows[-1]['date']}）")
    return rows


def load_divs(code):
    out = {}
    p = os.path.join(CACHE, "dividends.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if str(r["code"]).zfill(6) == str(code).zfill(6):
                    d, v = (r.get("exDate") or "")[:10], r.get("cashPs")
                    if d and v:
                        out[d] = out.get(d, 0.0) + float(v)
    return dict(sorted(out.items()))


def xirr(flows):
    if len(flows) < 2:
        return None
    import numpy as np
    t0 = flows[0][0]
    t = np.fromiter(((d - t0).days / 365.0 for d, _ in flows), float, len(flows))
    a = np.fromiter((x for _, x in flows), float, len(flows))

    def npv(r):
        return float((a / (1.0 + r) ** t).sum())

    if npv(-0.9999) < 0 and npv(3.0) < 0:
        return None
    lo, hi = -0.9999, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    r = (lo + hi) / 2
    if r <= -0.99 or r >= 2.99:
        return None
    if abs(npv(r)) > 0.02 * (float(np.abs(a).sum()) or 1.0):
        return None
    return r


# ─────────────────────────── 模拟 ───────────────────────────
def simulate(rows, divs, start, monthly, reinvest=True):
    """逐日推进，返回每日记录 + 现金流。"""
    import bisect
    dates = [r["date"] for r in rows]
    px = {r["date"]: r["close"] for r in rows}
    i0 = bisect.bisect_left(dates, start)
    days = dates[i0:]

    # 除权日顺延到下一个交易日
    ev = {}
    for d in sorted(divs):
        if d < start:
            continue
        j = bisect.bisect_left(days, d)
        if j < len(days):
            ev[days[j]] = ev.get(days[j], 0.0) + divs[d]

    first_of_month = {}
    for d in days:
        first_of_month.setdefault(d[:7], d)

    shares = cash = invested = div_total = 0.0
    flows = []
    recs = []
    for d in days:
        flow = 0.0
        # 先分红（当天买入的不享当天分红），再买入
        if d in ev and shares > 0:
            amt = shares * ev[d]
            div_total += amt
            if reinvest:
                shares += amt / px[d]
            else:
                cash += amt
                flows.append((date.fromisoformat(d), amt))
        if first_of_month.get(d[:7]) == d:
            flow = monthly
            shares += flow / px[d]
            invested += flow
            flows.append((date.fromisoformat(d), -flow))
        value = shares * px[d] + cash
        recs.append(dict(date=d, invested=invested, shares=shares, cash=cash,
                         value=value, flow=flow, div_amt=(shares * ev[d]) if (d in ev and shares) else 0.0))

    # TWR 净值：剥离现金流
    prev = None
    idx = 1.0
    peak = 1.0
    for r in recs:
        v = r["value"]
        if prev is not None and prev > 0:
            idx *= (v - r["flow"]) / prev
        r["idx"] = idx
        peak = max(peak, idx)
        r["dd"] = idx / peak - 1.0
        prev = v

    flows.append((date.fromisoformat(recs[-1]["date"]), recs[-1]["value"]))
    return recs, flows, div_total


def yearly(recs, bench, monthly):
    """按年聚合。"""
    import bisect
    bdates = [b["date"] for b in bench]
    bpx = {b["date"]: b["close"] for b in bench}
    out = []
    prev_idx = 1.0
    prev_inv = 0.0
    years = sorted({r["date"][:4] for r in recs})
    for y in years:
        yr = [r for r in recs if r["date"][:4] == y]
        if not yr:
            continue
        last = yr[-1]
        inv_year = last["invested"] - prev_inv
        div_year = sum(r["div_amt"] for r in yr)
        ret = last["idx"] / prev_idx - 1.0
        # 当年最大回撤（只在本年内）
        pk = None
        mdd = 0.0
        for r in yr:
            pk = r["idx"] if pk is None else max(pk, r["idx"])
            mdd = min(mdd, r["idx"] / pk - 1.0)
        # 基准同期
        b0 = yr[0]["date"]
        b1 = last["date"]
        j0 = bisect.bisect_left(bdates, b0)
        j1 = bisect.bisect_right(bdates, b1) - 1
        b_ret = None
        if 0 <= j0 < len(bdates) and 0 <= j1 < len(bdates) and bdates[j0] != bdates[j1]:
            b_ret = bpx[bdates[j1]] / bpx[bdates[j0]] - 1.0
        out.append(dict(
            year=y, inv_year=inv_year, invested=last["invested"],
            shares=last["shares"], div_year=div_year, value=last["value"],
            ret=ret, cum_ret=last["value"] / last["invested"] - 1.0 if last["invested"] else None,
            mdd=mdd, bench=b_ret,
            excess=(ret - b_ret) if b_ret is not None else None))
        prev_idx = last["idx"]
        prev_inv = last["invested"]
    return out


# ─────────────────────────── HTML ───────────────────────────
CSS = """
*{box-sizing:border-box}
::selection{background:#24425c;color:#fff}
html{scrollbar-color:#c9ced6 transparent}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#c9ced6;border-radius:5px}
:focus-visible{outline:2px solid #24425c;outline-offset:3px}
body{margin:0;background:#fdfdfc;color:#16181d;
  font:15px/1.62 -apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:44px 26px 72px}
h1{margin:0 0 6px;font-size:27px;font-weight:660;letter-spacing:-.022em}
h1 small{font-size:14px;font-weight:400;color:#6b7280;margin-left:8px;
  letter-spacing:0;font-variant-numeric:tabular-nums}
.meta{font-size:12.5px;color:#6b7280;font-variant-numeric:tabular-nums}
.lede{margin:22px 0 0;font-size:16px;line-height:1.72;max-width:74ch}
.lede b{font-weight:660}
.lede .n{font-variant-numeric:tabular-nums;font-weight:660}
h2{margin:46px 0 0;padding-top:16px;border-top:1px solid #e6e8eb;
  font-size:14px;font-weight:660;letter-spacing:-.004em}
h2 span{font-weight:400;color:#8b919b;font-size:12.5px;margin-left:10px;letter-spacing:0}
table{width:100%;border-collapse:collapse;margin-top:16px;
  font-size:13.5px;font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid #eff1f4}
th:first-child,td:first-child{text-align:left;font-weight:620}
th{font-size:11px;font-weight:600;color:#6b7280;letter-spacing:.04em;
  border-bottom:1px solid #dcdfe4;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:#f6f8fa}
td.pos{font-weight:660}
td.neg{color:#9aa0aa}
td.mut{color:#9aa0aa}
figure{margin:20px 0 0}
figcaption{font-size:12px;color:#8b919b;line-height:1.75;margin-top:10px;max-width:76ch}
.note{font-size:12px;color:#8b919b;line-height:1.8;margin:14px 0 0;max-width:76ch}
.ft{margin-top:52px;padding-top:16px;border-top:1px solid #e6e8eb;
  font-size:11.5px;color:#9aa0aa;line-height:1.9}
"""


def _svg(recs, bench, width=1040, height=392):
    """上：定投 TWR 净值 vs HS300（同起点归一为 1）；下：回撤独立面板。"""
    import bisect
    bdates = [b["date"] for b in bench]
    bpx = {b["date"]: b["close"] for b in bench}
    j0 = bisect.bisect_left(bdates, recs[0]["date"])
    if j0 >= len(bdates):
        return ""
    b0 = bpx[bdates[j0]]
    step = max(1, len(recs) // 280)
    pts = recs[::step]
    if pts[-1] is not recs[-1]:
        pts.append(recs[-1])
    series = []
    for r in pts:
        j = bisect.bisect_right(bdates, r["date"]) - 1
        b = (bpx[bdates[j]] / b0) if 0 <= j < len(bdates) else None
        series.append((r["date"], r["idx"], b, r["dd"]))

    pl, pr = 58, 16
    pw = width - pl - pr
    top_h, dd_h, gap = 250, 56, 30
    top_y, dd_y = 14, 14 + top_h + gap

    vals = [x[1] for x in series] + [x[2] for x in series if x[2] is not None]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.10 or 0.1
    lo, hi = max(0.0, lo - pad), hi + pad
    n = len(series)
    st = pw / max(n - 1, 1)

    def X(i):
        return pl + st * i

    def Yt(v):
        return top_y + top_h - (v - lo) / (hi - lo) * top_h

    o = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
         f'aria-label="定投净值与沪深300对照">']
    # 主面板网格
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        o.append(f'<line x1="{pl}" y1="{Yt(v):.1f}" x2="{width - pr}" y2="{Yt(v):.1f}" '
                 f'stroke="#eff1f4"/>')
        o.append(f'<text x="{pl - 9}" y="{Yt(v) + 4:.1f}" font-size="10" fill="#9aa0aa" '
                 f'text-anchor="end" style="font-variant-numeric:tabular-nums">'
                 f'{v:.1f}x</text>')
    # 1.0 基准线
    if lo <= 1.0 <= hi:
        o.append(f'<line x1="{pl}" y1="{Yt(1.0):.1f}" x2="{width - pr}" '
                 f'y2="{Yt(1.0):.1f}" stroke="#d6dae0" stroke-dasharray="3 3"/>')
    # HS300
    bp = [(X(i), Yt(x[2])) for i, x in enumerate(series) if x[2] is not None]
    if len(bp) > 1:
        o.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in bp) +
                 '" fill="none" stroke="#c2c7cf" stroke-width="1.6"/>')
    # DCA
    dp = [(X(i), Yt(x[1])) for i, x in enumerate(series)]
    o.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in dp) +
             '" fill="none" stroke="#24425c" stroke-width="2" '
             'stroke-linejoin="round"/>')

    # 回撤面板（独立）
    mdd = min(x[3] for x in series) or -0.01
    o.append(f'<line x1="{pl}" y1="{dd_y}" x2="{width - pr}" y2="{dd_y}" '
             f'stroke="#e6e8eb"/>')
    ddpts = [(X(i), dd_y + (x[3] / mdd) * dd_h) for i, x in enumerate(series)]
    o.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in ddpts) +
             f' L{X(n - 1):.1f},{dd_y} L{pl:.1f},{dd_y} Z" '
             f'fill="#9c3b2e" fill-opacity=".16"/>')
    o.append(f'<text x="{pl - 9}" y="{dd_y + 9}" font-size="9.5" fill="#b0b6bf" '
             f'text-anchor="end">0</text>')
    o.append(f'<text x="{pl - 9}" y="{dd_y + dd_h + 2}" font-size="9.5" '
             f'fill="#b0b6bf" text-anchor="end">{mdd * 100:.0f}%</text>')


    # x 轴：每年第一个点
    seen = set()
    for i, x in enumerate(series):
        y = x[0][:4]
        if y in seen:
            continue
        seen.add(y)
        o.append(f'<text x="{X(i):.1f}" y="{height - 8}" font-size="10" '
                 f'fill="#9aa0aa" text-anchor="middle" '
                 f'style="font-variant-numeric:tabular-nums">{y}</text>')
    o.append("</svg>")
    return "".join(o)


def write_html(path, name, code, args, ys, recs, bench, div_total, r,
               total_inv, total_val, total_profit, worst, reinvest):
    import statistics as st
    first, last = ys[0], ys[-1]
    exc = [x["excess"] for x in ys if x["excess"] is not None]
    win = sum(1 for e in exc if e > 0)
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>{name} {code} · 长期定投年度回测</title>',
         f'<style>{CSS}</style></head><body><div class="wrap">']
    h.append(f'<h1>{name}<small>{code}</small></h1>')
    h.append(f'<div class="meta">{args.start} 起至 {recs[-1]["date"]} ｜ '
             f'每月 {args.monthly:,.0f} 元 ｜ '
             f'{"分红再投" if reinvest else "分红收现金"} ｜ 不复权价 ｜ 对照 沪深300</div>')
    h.append(f'<p class="lede">每月投 <span class="n">{args.monthly:,.0f}</span> 元、'
             f'连续 {len(ys)} 年，累计投入 <span class="n">{total_inv:,.0f}</span> 元，'
             f'期末市值 <span class="n">{total_val:,.0f}</span> 元，'
             f'总收益 <span class="n">{total_profit:,.0f}</span> 元'
             f'（<span class="n">{total_profit/total_inv*100:.0f}%</span>），'
             f'年化 <span class="n">{r*100:.2f}%</span>。'
             f'同期沪深300 有 <span class="n">{win}</span>/{len(exc)} 年跑输。'
             f'最大回撤 <span class="n">{worst["dd"]*100:.1f}%</span>'
             f'（{worst["date"]}）。</p>')

    h.append('<h2>净值与基准<span>定投净值已剥离每月投入，可与指数直接比</span></h2>')
    h.append('<figure>' + _svg(recs, bench))
    h.append('<figcaption>深色线 = 定投的时间加权净值（起点归一为 1）；'
             '灰线 = 沪深300 同期；底部红带 = 定投回撤。'
             '净值剥离了每月新增投入，所以它衡量的是「这段时间市场给了多少」，'
             '而不是「账户里有多少钱」。</figcaption></figure>')

    h.append('<h2>逐年明细<span>当年收益为时间加权，累计为市值÷累计投入</span></h2>')
    h.append('<table><thead><tr><th>年份</th><th>当年投入</th><th>累计投入</th>'
             '<th>年末持股</th><th>当年分红</th><th>年末市值</th>'
             '<th>当年收益</th><th>累计</th><th>当年回撤</th>'
             '<th>沪深300</th><th>超额</th></tr></thead><tbody>')
    for x in ys:
        e = x["excess"]
        e_cls = "pos" if (e is not None and e > 0) else "neg"
        e_txt = f"{e*100:+.1f}pp" if e is not None else "—"
        b_txt = f"{x['bench']*100:+.1f}%" if x["bench"] is not None else "—"
        h.append(f'<tr><td>{x["year"]}</td>'
                 f'<td class="mut">{x["inv_year"]:,.0f}</td>'
                 f'<td>{x["invested"]:,.0f}</td>'
                 f'<td class="mut">{x["shares"]:,.0f}</td>'
                 f'<td>{x["div_year"]:,.0f}</td>'
                 f'<td>{x["value"]:,.0f}</td>'
                 f'<td>{x["ret"]*100:+.1f}%</td>'
                 f'<td>{x["cum_ret"]*100:.0f}%</td>'
                 f'<td class="mut">{x["mdd"]*100:.1f}%</td>'
                 f'<td class="mut">{b_txt}</td>'
                 f'<td class="{e_cls}">{e_txt}</td></tr>')
    h.append('</tbody></table>')
    h.append(f'<p class="note">「当年收益」是时间加权（TWR）：把每月新投入剥离后，'
             f'市场当年给你的回报。所以它不等于「市值变化 ÷ 投入」——'
             f'后者会把新钱算成收益。「累计」= 年末市值 ÷ 累计投入。'
             f'「当年回撤」只统计该年内的峰值回撤。'
             f'超额为正 = 跑赢沪深300（粗体）。</p>')
    h.append(f'<div class="ft">价格 baostock 不复权日线 ｜ 分红 东财 ｜ '
             f'基准 沪深300(sh.000300) ｜ 未扣红利税<br>'
             f'仅为历史测算，不构成投资建议。</div>')
    h.append('</div></body></html>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="长期定投年度回测")
    ap.add_argument("--code", default="601398")
    ap.add_argument("--start", default="2012-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--monthly", type=float, default=1000)
    ap.add_argument("--no-reinvest", action="store_true")
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default=None, help="CSV 输出路径")
    ap.add_argument("--html", default=None, help="HTML 输出路径")
    args = ap.parse_args(argv)

    code = str(args.code).zfill(6)
    bs_code = f"{'sh' if code[0] == '6' else 'sz'}.{code}"
    reinvest = not args.no_reinvest

    print(f"加载数据（起点 {args.start}）")
    rows = load_series(os.path.join(CACHE, f"px_{code}.csv"), bs_code,
                       args.start, args.end)
    bench = load_series(os.path.join(CACHE, "px_hs300.csv"), "sh.000300",
                        args.start, args.end)
    divs = load_divs(code)
    print(f"  分红 {len(divs)} 条")

    recs, flows, div_total = simulate(rows, divs, args.start, args.monthly, reinvest)
    ys = yearly(recs, bench, args.monthly)

    name = args.name
    if not name:
        p = os.path.join(CACHE, "codes.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    if str(r["code"]).zfill(6) == code:
                        name = r.get("name")
                        break
    name = name or code

    r = xirr(flows)
    total_inv = recs[-1]["invested"]
    total_val = recs[-1]["value"]
    total_profit = total_val + (0 if reinvest else div_total) - total_inv
    worst = min(recs, key=lambda z: z["dd"])

    print(f"\n{'='*76}")
    print(f"{name} {code} · 每月 {args.monthly:,.0f} 元 · "
          f"{'分红再投' if reinvest else '分红收现金'}")
    print(f"{'='*76}")
    print(f"{'年份':<6}{'当年投入':>11}{'累计投入':>12}{'年末持股':>11}"
          f"{'当年分红':>11}{'年末市值':>12}{'当年收益':>10}{'累计':>9}"
          f"{'回撤':>8}{'HS300':>9}{'超额':>9}")
    print("-" * 76)
    for x in ys:
        b = f"{x['bench']*100:>8.1f}%" if x["bench"] is not None else f"{'—':>9}"
        e = f"{x['excess']*100:>+8.1f}pp" if x["excess"] is not None else f"{'—':>9}"
        print(f"{x['year']:<6}{x['inv_year']:>11,.0f}{x['invested']:>12,.0f}"
              f"{x['shares']:>11,.0f}{x['div_year']:>11,.0f}{x['value']:>12,.0f}"
              f"{x['ret']*100:>9.1f}%{x['cum_ret']*100:>8.0f}%"
              f"{x['mdd']*100:>7.1f}%{b}{e}")
    print("-" * 76)
    print(f"累计投入 {total_inv:,.0f} 元 ｜ 累计分红 {div_total:,.0f} 元 ｜ "
          f"期末市值 {total_val:,.0f} 元")
    print(f"总收益 {total_profit:,.0f} 元（{total_profit/total_inv*100:.1f}%）｜ "
          f"XIRR {r*100 if r else 0:.2f}%")
    print(f"最大回撤 {worst['dd']*100:.1f}%（{worst['date']}）")

    out = args.out or os.path.join(CACHE, f"dca_year_{code}.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["年份", "当年投入", "累计投入", "年末持股", "当年分红",
                    "年末市值", "当年收益", "累计收益", "当年最大回撤",
                    "HS300同期", "超额"])
        for x in ys:
            w.writerow([x["year"], f"{x['inv_year']:.0f}", f"{x['invested']:.0f}",
                        f"{x['shares']:.0f}", f"{x['div_year']:.0f}",
                        f"{x['value']:.0f}", f"{x['ret']*100:.2f}%",
                        f"{x['cum_ret']*100:.2f}%" if x["cum_ret"] is not None else "—",
                        f"{x['mdd']*100:.2f}%",
                        f"{x['bench']*100:.2f}%" if x["bench"] is not None else "—",
                        f"{x['excess']*100:+.2f}pp" if x["excess"] is not None else "—"])
    print(f"\n→ {out}")

    hp = args.html or os.path.join(BASE, "docs", "基本面", f"dca_year_{code}.html")
    os.makedirs(os.path.dirname(os.path.abspath(hp)), exist_ok=True)
    write_html(hp, name, code, args, ys, recs, bench, div_total, r,
               total_inv, total_val, total_profit, worst, reinvest)
    print(f"→ {hp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
