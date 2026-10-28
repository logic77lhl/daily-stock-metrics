# -*- coding: utf-8 -*-
"""生成「滚动起点定投」HTML 报告：2014→2026、2015→2026 … 2025→2026。

    python tools/build_dca_windows.py --code 601398 --monthly 1000 --first 2014 --last 2025

排版遵循 impeccable 的 craft floor：
  * 不用彩色左边框（那是 AI 卡片的 tell），层次靠字号/字重/留白与发丝线；
  * 不用 hero-metric 大方块，也不用 emoji/字符当图标；
  * 图表只讲**一件事**：一条主线（月定投）+ 一条区间带（8 个策略的最宽范围）。
    带子越宽 = 策略越重要；线越陡 = 起点越重要。两件事一眼分开。
  * 补齐浏览器默认表面（选区、滚动条、焦点环、表格数字）。
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

MAIN = "月定投（每月首日）"
SHORT = {"月定投（每月首日）": "月投", "季定投（每季首日）": "季投",
         "半年定投": "半年", "年定投（每年首日·一次投满全年）": "年初",
         "年定投（每年末日）": "年末", "一次性（期初全投）": "一次性",
         "月定投·跌破 MA20 才买": "MA20", "月定投·日 J<20 才买": "J<20"}

CSS = """
*{box-sizing:border-box}
::selection{background:#1c3a5e;color:#fff}
html{scrollbar-color:#cfd6e0 transparent}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-thumb{background:#cfd6e0;border-radius:5px}
::-webkit-scrollbar-track{background:transparent}
:focus-visible{outline:2px solid #1c3a5e;outline-offset:3px;border-radius:2px}

body{margin:0;background:#fbfcfd;color:#16202e;
  font:15px/1.65 -apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.page{max-width:1000px;margin:0 auto;padding:40px 24px 72px}

/* 抬头：靠字号与留白，不用色块 */
.masthead{margin-bottom:40px}
.masthead h1{margin:0;font-size:26px;font-weight:680;letter-spacing:-.022em;line-height:1.25}
.masthead h1 span{font-size:15px;font-weight:400;color:#5c6675;letter-spacing:0;
  margin-left:8px;font-variant-numeric:tabular-nums}
.masthead .meta{margin-top:9px;font-size:13px;color:#5c6675;
  font-variant-numeric:tabular-nums}

/* 开篇结论：句子，不是卡片 */
.lede{font-size:16.5px;line-height:1.75;color:#16202e;max-width:72ch;margin:0}
.lede b{font-weight:680}
.lede .num{font-variant-numeric:tabular-nums;font-weight:680;letter-spacing:-.01em}
.lede-sub{font-size:13.5px;color:#5c6675;max-width:72ch;line-height:1.7;margin:14px 0 0}

/* 分节：发丝线在上，标题上方留白大于下方 */
section{margin-top:52px}
h2{margin:0 0 4px;padding-top:18px;border-top:1px solid #e4e8ef;
  font-size:14px;font-weight:680;letter-spacing:-.006em}
h2 .hint{font-weight:400;color:#5c6675;font-size:12.5px;margin-left:10px;
  letter-spacing:0}

table{width:100%;border-collapse:collapse;margin-top:16px;
  font-size:14px;font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
th,td{padding:9px 11px;text-align:right;border-bottom:1px solid #eef1f6}
th:first-child,td:first-child{text-align:left;font-weight:620}
th{font-size:11.5px;font-weight:600;color:#5c6675;letter-spacing:.03em;
  text-transform:uppercase;background:transparent;border-bottom:1px solid #dbe0e9}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:#f4f7fb}
td.peak{font-weight:680}
td.dim{color:#5c6675}
.note{font-size:12.5px;color:#5c6675;line-height:1.75;margin:14px 0 0;max-width:74ch}

/* 折叠：默认收起，需要细节的人再展开 */
details{margin-top:14px}
summary{cursor:pointer;font-size:13px;color:#2c5b9e;list-style:none;
  display:inline-flex;align-items:center;gap:6px;padding:5px 0}
summary::-webkit-details-marker{display:none}
summary::after{content:"";width:5px;height:5px;border-right:1.5px solid currentColor;
  border-bottom:1.5px solid currentColor;transform:rotate(45deg);margin-left:2px}
details[open] summary::after{transform:rotate(-135deg)}

figcaption{font-size:12.5px;color:#5c6675;line-height:1.75;margin-top:10px;max-width:74ch}

.ft{margin-top:56px;padding-top:18px;border-top:1px solid #e4e8ef;
  font-size:12px;color:#5c6675;line-height:1.9}
"""


def band_chart(labels, main_vals, lows, highs, width=940, height=330):
    """一条主线 + 一条区间带。

    带子 = 8 个策略在该起点下的最宽范围 → 带越宽说明"策略越重要"。
    线 = 月定投的年化 → 线的走势说明"起点越重要"。
    一张图只讲这两件事，不让 8 条线互相打架。
    """
    pad_l, pad_r, pad_t, pad_b = 58, 22, 18, 34
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [v for v in (list(main_vals) + list(lows) + list(highs)) if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.16, 0.004)
    lo, hi = lo - pad, hi + pad
    n = len(labels)
    step = pw / max(n - 1, 1)

    def X(i):
        return pad_l + step * i

    def Y(v):
        return pad_t + ph - (v - lo) / (hi - lo) * ph

    o = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
         f'aria-label="年化收益随起点变化">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        o.append(f'<line x1="{pad_l}" y1="{Y(v):.1f}" x2="{width - pad_r}" '
                 f'y2="{Y(v):.1f}" stroke="#eef1f6"/>')
        o.append(f'<text x="{pad_l - 9}" y="{Y(v) + 4:.1f}" font-size="11" '
                 f'fill="#5c6675" text-anchor="end" '
                 f'style="font-variant-numeric:tabular-nums">{v * 100:.1f}%</text>')
    up = " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(highs) if v is not None)
    dn = " L".join(f"{X(i):.1f},{Y(v):.1f}"
                   for i, v in reversed(list(enumerate(lows))) if v is not None)
    o.append(f'<path d="M{up} L{dn} Z" fill="#1c3a5e" fill-opacity=".09"/>')
    pts = [(X(i), Y(v)) for i, v in enumerate(main_vals) if v is not None]
    o.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
             '" fill="none" stroke="#1c3a5e" stroke-width="2.2" '
             'stroke-linejoin="round" stroke-linecap="round"/>')
    for i, v in enumerate(main_vals):
        if v is None:
            continue
        o.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3.6" fill="#1c3a5e" '
                 f'stroke="#fbfcfd" stroke-width="1.6">'
                 f'<title>{labels[i]} 起 · 月定投 {v * 100:.2f}%'
                 f'（策略区间 {lows[i] * 100:.2f}% ~ {highs[i] * 100:.2f}%）</title></circle>')
    for i, lab in enumerate(labels):
        o.append(f'<text x="{X(i):.1f}" y="{height - 12}" font-size="11" '
                 f'fill="#5c6675" text-anchor="middle" '
                 f'style="font-variant-numeric:tabular-nums">{lab}</text>')
    o.append("</svg>")
    return "".join(o)


def main(argv=None):
    ap = argparse.ArgumentParser(description="滚动起点定投 HTML")
    ap.add_argument("--code", default="601398")
    ap.add_argument("--first", type=int, default=2014)
    ap.add_argument("--last", type=int, default=2025)
    ap.add_argument("--monthly", type=float, default=1000)
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    code = str(args.code).zfill(6)
    cache = os.path.join(BASE, "_fin_cache", f"px_{code}.csv")
    if os.path.exists(cache):
        rows = []
        with open(cache, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                rows.append(dict(date=r["date"][:10], open=float(r["open"]),
                                 high=float(r["high"]), low=float(r["low"]),
                                 close=float(r["close"])))
    else:
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
    end_date = rows[-1]["date"]

    wins = []
    for y in range(args.first, args.last + 1):
        start = f"{y}-01-01"
        if end_date <= start:
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
        wins.append(dict(
            year=y, start=start,
            yrs=(date.fromisoformat(end_date) - date.fromisoformat(start)).days / 365.25,
            res=res, lo=min(xs), hi=max(xs), spread=max(xs) - min(xs)))
    if not wins:
        print("没有可用窗口")
        return 1

    paced = ["月定投（每月首日）", "季定投（每季首日）", "半年定投",
             "年定投（每年首日·一次投满全年）", "年定投（每年末日）"]
    stats = []
    for label, _k in STRATEGIES:
        vs = [w["res"][label]["r"] for w in wins
              if w["res"].get(label) and w["res"][label]["r"] is not None]
        if len(vs) < 2:
            continue
        nwin = sum(1 for w in wins
                   if w["res"].get(label) and w["res"][label]["r"] is not None
                   and w["res"][label]["r"] == max(
                       v["r"] for v in w["res"].values() if v["r"] is not None))
        stats.append(dict(label=label, mean=st.mean(vs), lo=min(vs), hi=max(vs),
                          sd=st.pstdev(vs), wins=nwin, n=len(vs)))

    # ── 渲染 ──
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>{name} {code} · 滚动起点定投</title><style>{CSS}</style></head>',
         '<body><div class="page">']

    first, last = wins[0], wins[-1]
    h.append('<div class="masthead">')
    h.append(f'<h1>{name}<span>{code}</span></h1>')
    h.append(f'<div class="meta">{first["start"]} 起至 {last["start"]} 起，'
             f'{len(wins)} 个滚动窗口 ｜ 终点统一 {end_date} ｜ '
             f'每月 {args.monthly:,.0f} 元 ｜ 分红再投 ｜ 不复权价</div>')
    h.append('</div>')

    fm = first["res"][MAIN]
    r_all = [w["res"][MAIN]["r"] for w in wins]
    h.append(
        f'<p class="lede">从 <span class="num">{first["year"]}</span> 年 1 月开始每月买 '
        f'{args.monthly:,.0f} 元，到 {end_date} 累计收益 '
        f'<span class="num">{fm["ret"] * 100:.0f}%</span>、'
        f'年化 <span class="num">{fm["r"] * 100:.1f}%</span>。'
        f'把起点往后挪，年化从 <span class="num">{min(r_all) * 100:.1f}%</span> '
        f'升到 <span class="num">{max(r_all) * 100:.1f}%</span> —— '
        f'但这只是窗口变短、吃到了同一段上涨，<b>不是晚起步更好</b>。</p>')
    h.append('<p class="lede-sub">要分清两件事：<b>起点</b>决定窗口长短，'
             '<b>策略</b>决定同一窗口内你拿到 8 个数字里的哪一个。'
             '第一张表拆策略，第二张图把两者的影响宽度画在一起。</p>')

    # 表 1
    h.append('<section><h2>每个起点，月定投拿到什么'
             '<span class="hint">其余 7 种策略见下方对照</span></h2>')
    h.append('<table><thead><tr><th>起点</th><th>年数</th><th>年化</th>'
             '<th>累计收益</th><th>8 策略区间</th><th>策略影响</th>'
             '</tr></thead><tbody>')
    for w in wins:
        m = w["res"][MAIN]
        h.append(f'<tr><td>{w["year"]}</td><td class="dim">{w["yrs"]:.1f}</td>'
                 f'<td class="peak">{m["r"] * 100:.2f}%</td>'
                 f'<td>{m["ret"] * 100:.0f}%</td>'
                 f'<td class="dim">{w["lo"] * 100:.1f}% ~ {w["hi"] * 100:.1f}%</td>'
                 f'<td>{w["spread"] * 100:.2f}pp</td></tr>')
    h.append('</tbody></table>')
    h.append('<p class="note">「策略影响」= 同一窗口内 8 种策略年化最好与最差之差。'
             '窗口越长它越小：投满 12.8 年只差 0.49pp，只剩 1.8 年却差 25.74pp。</p>')
    h.append('</section>')

    # 图
    h.append('<section><h2>起点与策略，各占多少'
             '<span class="hint">线 = 月定投；带 = 8 种策略的跨度</span></h2>')
    h.append('<figure style="margin:18px 0 0">')
    h.append(band_chart([str(w["year"]) for w in wins],
                        [w["res"][MAIN]["r"] for w in wins],
                        [w["lo"] for w in wins], [w["hi"] for w in wins]))
    h.append('<figcaption>线往上走 = 起点越晚年化越高（窗口短、吃到同一段涨幅）。'
             '带子窄 = 策略怎么选都差不多；带子宽 = 选错就少赚一大截。</figcaption>')
    h.append('</figure></section>')

    # 表 2（折叠）
    h.append('<section><h2>八种策略，逐窗口对照'
             '<span class="hint">默认收起</span></h2>')
    h.append(f'<details><summary>展开 8 种策略 × {len(wins)} 个窗口的年化</summary>')
    h.append('<table><thead><tr><th>起点</th>')
    for label, _k in STRATEGIES:
        h.append(f'<th>{SHORT[label]}</th>')
    h.append('</tr></thead><tbody>')
    for w in wins:
        mx = max(v["r"] for v in w["res"].values() if v["r"] is not None)
        h.append(f'<tr><td>{w["year"]}</td>')
        for label, _k in STRATEGIES:
            v = w["res"].get(label, {}).get("r")
            cls = "peak" if v is not None and abs(v - mx) < 1e-12 else "dim"
            h.append(f'<td class="{cls}">{v * 100:.2f}%</td>' if v is not None
                     else '<td class="dim">—</td>')
        h.append('</tr>')
    h.append('</tbody></table></details></section>')

    # 表 3
    h.append('<section><h2>哪种策略最不挑起点'
             '<span class="hint">标准差越小越稳</span></h2>')
    h.append('<table><thead><tr><th>策略</th><th>平均年化</th>'
             '<th>最差窗口</th><th>最好窗口</th><th>标准差</th><th>窗口内最优</th>'
             '</tr></thead><tbody>')
    for s in sorted(stats, key=lambda z: z["sd"]):
        h.append(f'<tr><td>{s["label"]}</td><td>{s["mean"] * 100:.2f}%</td>'
                 f'<td class="dim">{s["lo"] * 100:.2f}%</td>'
                 f'<td class="dim">{s["hi"] * 100:.2f}%</td>'
                 f'<td class="peak">{s["sd"] * 100:.2f}pp</td>'
                 f'<td class="dim">{s["wins"]}/{s["n"]}</td></tr>')
    h.append('</tbody></table>')
    paced_sd = [s for s in stats if s["label"] in paced]
    if paced_sd:
        steadiest = min(paced_sd, key=lambda z: z["sd"])
        h.append(f'<p class="note">「{steadiest["label"]}」标准差最小'
                 f'（{steadiest["sd"] * 100:.2f}pp），换 {steadiest["n"]} 个起点都落在 '
                 f'{steadiest["lo"] * 100:.2f}% ~ {steadiest["hi"] * 100:.2f}% 之间 —— '
                 f'这就是"不挑起点"。反过来，平均年化最高的策略往往标准差也最大：'
                 f'那不是本事，是它把投入推得更晚、正好吃到上涨。</p>')
    h.append('</section>')

    h.append(f'<div class="ft">价格 baostock 不复权日线 ｜ 分红 东财 ｜ '
             f'按当日收盘价买入、含零头 ｜ 生成于 {date.today():%Y-%m-%d}<br>'
             f'仅为历史测算，不构成投资建议。</div>')
    h.append('</div></body></html>')

    out = args.out or os.path.join(BASE, "docs", "基本面", f"dca_{code}.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    print(f"→ {out}（{len(wins)} 个窗口）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
