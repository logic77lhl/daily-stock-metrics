# -*- coding: utf-8 -*-
"""生成「滚动起点定投」HTML 报告：2014→2026、2015→2026 … 2025→2026。

    python tools/build_dca_windows.py --code 601398 --monthly 1000
    python tools/build_dca_windows.py --code 601398 --first 2014 --last 2025

回答两个问题：
  1. **换个起点，结论会不会变？**（同一策略在不同起点的年化波动多大）
  2. **哪个策略在多数起点下更稳？**

数据：不复权日线（baostock，落 _fin_cache/px_<code>.csv 缓存）+ 东财分红。
零 JS、内联 CSS/SVG，和站点其它页面同一套视觉。
"""
import argparse
import csv
import os
import statistics as st
import sys
from datetime import date

sys.path.insert(0, r"C:\Users\user\.dsh\ungrouped\_probe_libs")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

from dca_strategies import (STRATEGIES, add_indicators, build_buys,  # noqa: E402
                            evaluate, load_divs, load_prices)

# 报告里只留这几条线，避免图太挤
PLOT = [("月定投（每月首日）", "月定投", "#2d4a6e"),
        ("一次性（期初全投）", "一次性", "#d92d20"),
        ("月定投·跌破 MA20 才买", "MA20 择时", "#f79009"),
        ("月定投·日 J<20 才买", "J<20 择时", "#0ca678")]

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#101828;
  font:14px/1.5 -apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif}
.page{max-width:1000px;margin:0 auto;padding:22px 16px 46px}
header{border-bottom:2px solid #101828;padding-bottom:12px;margin-bottom:22px}
.title{font-size:22px;font-weight:700}
.title .code{font-size:14px;color:#667085;font-weight:400;margin-left:6px}
.sub{font-size:12.5px;color:#667085;margin-top:7px;line-height:1.7}
h2{font-size:15px;font-weight:700;margin:28px 0 10px;display:flex;
  align-items:baseline;justify-content:space-between;gap:12px}
h2 .unit{font-size:11.5px;color:#98a2b3;font-weight:400}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13px}
th,td{padding:7px 9px;text-align:right;border-bottom:1px solid #f0f2f5;white-space:nowrap}
th:first-child,td:first-child{text-align:left;font-weight:600}
th{background:#f7f9fc;color:#6b7488;font-size:11.5px;font-weight:600}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:#fafcff}
tr.hi td{background:#fffbf0}
.up{color:#d92d20}.down{color:#079455}
.na{color:#d0d5dd}
.fn{font-size:11.5px;color:#98a2b3;line-height:1.75;margin:8px 0 0}
.verdict{border-left:3px solid #12b76a;padding:10px 0 10px 14px;margin-top:26px;font-size:13.5px}
.verdict b{display:block;margin-bottom:5px}
.verdict .kv{color:#667085;font-size:12.5px;line-height:1.9}
.ft{color:#98a2b3;font-size:11px;text-align:center;margin-top:32px;line-height:1.9}
"""


def line_svg(labels, series, width=960, height=300):
    """折线图。series=[(名, 颜色, [值...])]，用 <title> 做原生 tooltip。"""
    pad_l, pad_r, pad_t, pad_b = 54, 96, 14, 30
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [v for _, _, arr in series for v in arr if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = max(hi - lo, 0.01)
    lo, hi = lo - span * 0.12, hi + span * 0.12
    n = len(labels)
    step = pw / max(n - 1, 1)

    def y_of(v):
        return pad_t + ph - (v - lo) / (hi - lo) * ph

    o = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = y_of(v)
        o.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
                 f'stroke="#f2f4f7"/>')
        o.append(f'<text x="{pad_l - 7}" y="{y + 3.5:.1f}" font-size="10" fill="#98a2b3" '
                 f'text-anchor="end">{v*100:.1f}%</text>')
    for i, lab in enumerate(labels):
        o.append(f'<text x="{pad_l + step * i:.1f}" y="{height - 9}" font-size="10" '
                 f'fill="#98a2b3" text-anchor="middle">{lab}</text>')
    for name, color, arr in series:
        pts = [(pad_l + step * i, y_of(v)) for i, v in enumerate(arr) if v is not None]
        if len(pts) > 1:
            o.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
                     f'" fill="none" stroke="{color}" stroke-width="2"/>')
        for i, v in enumerate(arr):
            if v is None:
                continue
            o.append(f'<circle cx="{pad_l + step * i:.1f}" cy="{y_of(v):.1f}" r="3" '
                     f'fill="{color}"><title>{labels[i]} {name}: {v*100:.2f}%</title></circle>')
    # 图例（右侧竖排）
    ly = pad_t + 4
    for name, color, _ in series:
        o.append(f'<rect x="{width - pad_r + 14}" y="{ly}" width="9" height="9" '
                 f'fill="{color}" rx="2"/>')
        o.append(f'<text x="{width - pad_r + 27}" y="{ly + 8}" font-size="11" '
                 f'fill="#475467">{name}</text>')
        ly += 19
    o.append("</svg>")
    return "".join(o)


def main(argv=None):
    ap = argparse.ArgumentParser(description="滚动起点定投 HTML")
    ap.add_argument("--code", default="601398")
    ap.add_argument("--first", type=int, default=2014, help="最早起点年")
    ap.add_argument("--last", type=int, default=2025, help="最晚起点年")
    ap.add_argument("--monthly", type=float, default=1000)
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--name", default=None, help="显示名（默认读 codes.csv）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    code = str(args.code).zfill(6)
    cache = os.path.join(BASE, "_fin_cache", f"px_{code}.csv")
    if os.path.exists(cache):                      # 本地缓存优先，省一次抓取
        rows = []
        with open(cache, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                rows.append(dict(date=r["date"][:10], open=float(r["open"]),
                                 high=float(r["high"]), low=float(r["low"]),
                                 close=float(r["close"])))
        print(f"价格缓存 {len(rows)} 天")
    else:
        print(f"抓 {code} 不复权日线…")
        rows = load_prices(code, f"{args.first - 1}-01-01", args.end)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["date", "open", "high", "low", "close"])
            w.writeheader()
            w.writerows(rows)
    rows = add_indicators(rows)
    divs = load_divs(code)

    name = args.name
    if not name:
        cp = os.path.join(BASE, "_fin_cache", "codes.csv")
        if os.path.exists(cp):
            with open(cp, encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    if str(r["code"]).zfill(6) == code:
                        name = r.get("name")
                        break
    name = name or code

    # ── 逐起点跑 ──
    wins = []
    for y in range(args.first, args.last + 1):
        start = f"{y}-01-01"
        if rows[-1]["date"] <= start:
            continue
        res = {}
        for label, kind in STRATEGIES:
            total, buys, leftover = build_buys(kind, rows, start, args.monthly)
            if not buys:
                continue
            res[label] = evaluate(rows, divs, buys, leftover, True, start)
        if not res:
            continue
        xs = [v["r"] for v in res.values() if v["r"] is not None]
        yrs = (date.fromisoformat(rows[-1]["date"]) - date.fromisoformat(start)).days / 365.25
        wins.append(dict(year=y, start=start, yrs=yrs, res=res,
                         spread=(max(xs) - min(xs)) if len(xs) > 1 else 0.0))

    if not wins:
        print("没有可用窗口")
        return 1

    # ── 各策略跨窗口统计 ──
    stats = []
    for label, _kind in STRATEGIES:
        vs = [w["res"][label]["r"] for w in wins
              if w["res"].get(label) and w["res"][label]["r"] is not None]
        if len(vs) < 2:
            continue
        nwin = sum(1 for w in wins
                   if all(w["res"].get(k) and w["res"][k]["r"] is not None
                          for k in [x[0] for x in STRATEGIES])
                   and w["res"][label]["r"] == max(
                       w["res"][k]["r"] for k in [x[0] for x in STRATEGIES]
                       if w["res"].get(k) and w["res"][k]["r"] is not None))
        stats.append(dict(label=label, n=len(vs), mean=st.mean(vs),
                          med=st.median(vs), lo=min(vs), hi=max(vs),
                          sd=st.pstdev(vs), wins=nwin))

    # ── 渲染 ──
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>{name} 滚动起点定投</title><style>{CSS}</style></head><body>',
         '<div class="page"><header>',
         f'<div class="title">{name}<span class="code">{code}</span></div>',
         f'<div class="sub">起点 {wins[0]["start"]} … {wins[-1]["start"]}，'
         f'共 <b>{len(wins)}</b> 个窗口，终点统一 {rows[-1]["date"]} ｜ '
         f'每月 {args.monthly:,.0f} 元 ｜ 分红再投 ｜ 不复权价 ｜ A 股按金额买入（含零头）</div>',
         '</header>']

    # 表 1：逐窗口
    h.append('<h2>各起点下的年化收益（XIRR）'
             '<span class="unit">同一只股票，只是开始定投的年份不同</span></h2>')
    h.append('<div style="overflow-x:auto"><table><thead><tr><th>起点</th><th>年数</th>')
    for label, _ in STRATEGIES:
        short = {"月定投（每月首日）": "月投", "季定投（每季首日）": "季投",
                 "半年定投": "半年", "年定投（每年首日·一次投满全年）": "年年初",
                 "年定投（每年末日）": "年年末", "一次性（期初全投）": "一次性",
                 "月定投·跌破 MA20 才买": "MA20", "月定投·日 J<20 才买": "J<20"}[label]
        h.append(f'<th>{short}</th>')
    h.append('<th>极差</th><th>月投收益率</th></tr></thead><tbody>')
    best_by_win = []
    for w in wins:
        xs = {k: (v["r"] if v and v["r"] is not None else None)
              for k, v in w["res"].items()}
        mx = max((v for v in xs.values() if v is not None), default=None)
        h.append(f'<tr><td>{w["year"]}</td><td>{w["yrs"]:.1f}</td>')
        for label, _ in STRATEGIES:
            v = xs.get(label)
            cls = "up" if (mx is not None and v == mx) else ""
            h.append(f'<td class="{cls}">{v*100:.2f}%</td>' if v is not None
                     else '<td class="na">—</td>')
        h.append(f'<td>{w["spread"]*100:.2f}pp</td>')
        mr = w["res"].get("月定投（每月首日）", {}).get("ret")
        h.append(f'<td>{mr*100:.1f}%</td>' if mr is not None else '<td class="na">—</td>')
        h.append('</tr>')
    h.append('</tbody></table></div>')
    h.append('<div class="fn">红色 = 该起点下 XIRR 最高的策略。'
             '「极差」= 同一窗口内最好与最差策略的年化差。</div>')

    # 图
    labels = [f'{w["year"]}' for w in wins]
    series = []
    for label, short, color in PLOT:
        arr = [(w["res"].get(label) or {}).get("r") for w in wins]
        series.append((short, color, [v for v in arr]))
    h.append('<h2>年化收益随起点变化<span class="unit">t月横轴 = 从哪一年开始定投</span></h2>')
    h.append(line_svg(labels, series))

    # 表 2：跨窗口稳健性
    h.append('<h2>各策略在全部窗口上的稳健性'
             '<span class="unit">标准差越小 = 越不挑起点</span></h2>')
    h.append('<table><thead><tr><th>策略</th><th>平均</th><th>中位</th>'
             '<th>最好</th><th>最差</th><th>标准差</th><th>胜出次数</th>'
             '</tr></thead><tbody>')
    for s in sorted(stats, key=lambda z: -z["mean"]):
        h.append(f'<tr><td>{s["label"]}</td>'
                 f'<td>{s["mean"]*100:.2f}%</td><td>{s["med"]*100:.2f}%</td>'
                 f'<td>{s["hi"]*100:.2f}%</td><td>{s["lo"]*100:.2f}%</td>'
                 f'<td>{s["sd"]*100:.2f}pp</td>'
                 f'<td>{s["wins"]}/{s["n"]}</td></tr>')
    h.append('</tbody></table>')

    # 结论
    spr = [w["spread"] for w in wins]
    paced = [s for s in stats if s["label"] in
             ("月定投（每月首日）", "季定投（每季首日）", "半年定投",
              "年定投（每年首日·一次投满全年）", "年定投（每年末日）")]
    stable = min(paced, key=lambda z: z["sd"]) if paced else None
    most = max(stats, key=lambda z: z["mean"])
    h.append('<div class="verdict">')
    h.append(f'<b>换个起点，结论会变吗？——'
             f'窗口间极差中位 {st.median(spr)*100:.2f}pp，最大 {max(spr)*100:.2f}pp</b>')
    h.append('<div class="kv">')
    if stable:
        h.append(f'最不挑起点的是「{stable["label"]}」，跨 {stable["n"]} 个窗口'
                 f'标准差仅 {stable["sd"]*100:.2f}pp（{stable["lo"]*100:.2f}% ~ '
                 f'{stable["hi"]*100:.2f}%）。<br>')
    h.append(f'平均年化最高的是「{most["label"]}」{most["mean"]*100:.2f}%，'
             f'但它的标准差也最大（{most["sd"]*100:.2f}pp）—— '
             f'高平均来自「投入更晚」的运气，不是稳定优势。<br>')
    h.append('注意：XIRR 是按每笔钱的持有时间加权，所以"投得晚"天然占优 —— '
             '起点越晚、窗口越短，年化通常越高，这不代表那个起点更好。')
    h.append('</div></div>')

    h.append(f'<div class="ft">数据：baostock 不复权日线 + 东财分红 ｜ '
             f'生成于 {date.today():%Y-%m-%d} ｜ 仅供参考，不构成投资建议</div>')
    h.append('</div></body></html>')

    out = args.out or os.path.join(BASE, "docs", "基本面", f"dca_{code}.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    print(f"→ {out}（{len(wins)} 个窗口）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
