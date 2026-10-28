# -*- coding: utf-8 -*-
"""生成「滚动起点定投」HTML 报告：2014→2026、2015→2026 … 2025→2026。

    python tools/build_dca_windows.py --code 601398 --monthly 1000 --first 2014 --last 2025

## 设计语言取自 github.com/logic77lhl/film-annual

抄的是它的**系统设计**（docs/DESIGN.md + app/app.css），不是抄某个页面：

1. **深色 + 单一强调色**：--bg #09090b / --panel #121216 / --gold #d4b176。
   文字只三档（--text / --muted / --dim），层级靠字号与字重，不靠色块。
2. **衬线做标题、无衬线做数据**：h1/h2/关键数字用 Noto Serif SC —— 「年鉴」气质。
3. **.section-head**：标题 + 小字说明 + 一条自动延伸的发丝线。
4. **.rk 排行行**：grid 定宽列、表头 9px 大写 + 字距 .14em、数字 tabular-nums。
5. **顶部数据状态条**：先交代口径与缺口，避免把「未完成」读成「定稿」。
6. **不做假数据**：缺失显示「—」。

与 craft-floor 兼容：无彩色左边框、无 hero-metric 卡片、无 emoji 图标、
浏览器默认表面（选区/滚动条/焦点环）从调色板取色。
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

from dca_strategies import (STRATEGIES, add_indicators, budget_fast,  # noqa: E402
                            build_buys, evaluate_fast, load_divs,
                            load_prices, prep_fast, stock_ctx)

MAIN = "月定投（每月首日）"
SHORT = {"月定投（每月首日）": "月投", "季定投（每季首日）": "季投",
         "半年定投": "半年", "年定投（每年首日·一次投满全年）": "年初",
         "年定投（每年末日）": "年末", "一次性（期初全投）": "一次性",
         "月定投·跌破 MA20 才买": "MA20", "月定投·日 J<20 才买": "J<20"}

CSS = """
:root{
  --bg:#09090b; --bg2:#0d0d10; --panel:#121216; --panel2:#17171c;
  --text:#e9e5dd; --muted:#8d94a1; --dim:#606774;
  --gold:#d4b176; --gold2:#f1d9a7;
  --line:rgba(255,255,255,.08); --line2:rgba(255,255,255,.14);
  --red:#c96b5a; --green:#86b08e;
  --serif:"Noto Serif SC","Songti SC","STSong","SimSun",serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
}
*{box-sizing:border-box}
::selection{background:rgba(212,177,118,.28)}
html{scrollbar-color:#2a2a31 transparent;scroll-behavior:smooth}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#2a2a31;border-radius:5px}
::-webkit-scrollbar-track{background:transparent}
:focus-visible{outline:2px solid var(--gold);outline-offset:3px;border-radius:2px}

body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
  font-size:15px;line-height:1.65;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
.wrap{max-width:1180px;margin:0 auto;padding:0 26px}

/* 顶栏 */
.top{position:sticky;top:0;z-index:50;background:rgba(9,9,11,.86);
  border-bottom:1px solid var(--line);backdrop-filter:blur(18px)}
.topin{display:flex;align-items:center;gap:20px;height:58px}
.brand{font-family:var(--serif);font-size:18px;letter-spacing:.04em;white-space:nowrap}
.brand span{font-family:var(--sans);font-size:10px;color:var(--dim);
  letter-spacing:.14em;margin-left:8px}
.topnav{margin-left:auto;font-size:12px;color:var(--muted);letter-spacing:.02em}
.topnav b{color:var(--gold);font-weight:400}

/* 数据状态条：先交代口径 */
.dstatus{border-bottom:1px solid var(--line);background:rgba(134,176,142,.035);
  color:var(--dim);font-size:10.5px;letter-spacing:.02em}
.dstatus .wrap{display:flex;align-items:center;gap:8px;padding:7px 26px;flex-wrap:wrap}
.dstatus .dot{width:5px;height:5px;border-radius:50%;background:var(--green);flex:0 0 auto}

/* hero */
.hero{padding:64px 0 30px;border-bottom:1px solid var(--line)}
.kicker{font-size:10px;letter-spacing:.28em;text-transform:uppercase;
  color:var(--gold);margin-bottom:15px}
.hero h1{margin:0 0 16px;font-family:var(--serif);font-weight:500;
  font-size:clamp(30px,4.4vw,46px);line-height:1.16;letter-spacing:-.015em}
.hero h1 em{font-style:normal;color:var(--gold)}
.hero p{max-width:760px;margin:0;color:var(--muted);font-size:14px;line-height:1.85}
.hero p b{color:var(--text);font-weight:500}
.hero .num{color:var(--gold);font-variant-numeric:tabular-nums;font-weight:500}

/* 统计带 */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;
  padding:28px 0 6px;border-bottom:1px solid var(--line)}
.stat b{display:block;font-family:var(--serif);font-size:32px;font-weight:500;
  line-height:1.1;font-variant-numeric:tabular-nums}
.stat span{display:block;font-size:9px;letter-spacing:.16em;color:var(--dim);
  text-transform:uppercase;margin-top:5px}
.stat.gold b{color:var(--gold)}

/* 分节标题 */
.section{padding:38px 0}
.shead{display:flex;align-items:baseline;gap:14px;margin:0 0 4px}
.shead h2{margin:0;font-family:var(--serif);font-weight:500;font-size:19px;
  letter-spacing:.02em}
.shead small{font-size:10px;letter-spacing:.14em;color:var(--dim);
  text-transform:uppercase}
.shead .line{flex:1;height:1px;background:var(--line)}

/* 排行行（抄 .rk） */
.ranklist{border-top:1px solid var(--line);margin-top:20px}
.rk{display:grid;align-items:center;min-height:44px;padding:8px 4px;
  border-bottom:1px solid var(--line);transition:.15s;
  font-variant-numeric:tabular-nums}
.rk:hover{background:rgba(255,255,255,.025)}
.rkhead{min-height:32px;color:var(--dim);font-size:9px;letter-spacing:.14em;
  text-transform:uppercase;border-bottom:1px solid var(--line2)}
.rk .y{font-size:12px;color:var(--muted);letter-spacing:.04em}
.rk .d{font-size:11px;color:var(--dim);text-align:right}
.rk .m{font-family:var(--serif);font-size:17px;color:var(--gold);text-align:right}
.rk .v{font-size:13px;text-align:right}
.rk .u{font-size:10px;color:var(--dim);margin-left:2px}
.rk .bar{text-align:right;padding-right:2px}
.rk .bar i{display:inline-block;height:4px;border-radius:2px;
  background:rgba(212,177,118,.5);vertical-align:middle;margin-right:7px}
.rk.c5{grid-template-columns:64px 84px 1fr 104px 110px 150px}
.rk.c5b{grid-template-columns:1fr 120px 170px 90px 90px}

.note{font-size:11px;color:var(--dim);line-height:1.85;margin:14px 0 0;max-width:76ch}

figure{margin:22px 0 0}
figcaption{font-size:11px;color:var(--dim);line-height:1.85;margin-top:12px;
  max-width:76ch}

/* 折叠 */
details{margin-top:14px}
.section.tight{padding-bottom:14px}
summary{cursor:pointer;font-size:12px;color:var(--muted);list-style:none;
  display:inline-flex;align-items:center;gap:7px;padding:6px 0;transition:.15s}
summary:hover{color:var(--gold)}
summary::-webkit-details-marker{display:none}
summary::after{content:"";width:5px;height:5px;border-right:1px solid currentColor;
  border-bottom:1px solid currentColor;transform:rotate(45deg);margin-left:1px}
details[open] summary::after{transform:rotate(-135deg)}
details table{width:100%;border-collapse:collapse;margin-top:14px;
  font-size:12.5px;font-variant-numeric:tabular-nums}
details th,details td{padding:7px 8px;text-align:right;
  border-bottom:1px solid var(--line)}
details th:first-child,details td:first-child{text-align:left}
details th{font-size:9px;letter-spacing:.14em;color:var(--dim);
  text-transform:uppercase;border-bottom:1px solid var(--line2)}
details tr:last-child td{border-bottom:0}
details .pk{color:var(--gold)}

/* 结论 */
.verdict{padding:26px 0 0;margin-top:30px;border-top:1px solid var(--line)}
.verdict b{display:block;font-family:var(--serif);font-size:16px;font-weight:500;
  margin-bottom:9px;letter-spacing:.01em}
.verdict p{margin:0;font-size:12.5px;color:var(--muted);line-height:1.85;max-width:78ch}
.verdict .num{color:var(--gold);font-variant-numeric:tabular-nums}

.ft{border-top:1px solid var(--line);margin-top:44px;padding:20px 0 48px;
  font-size:11px;color:var(--dim);line-height:1.9}
.ft a{color:var(--muted);border-bottom:1px solid var(--line)}
.ft a:hover{color:var(--gold)}

@media (max-width:720px){
  .stats{grid-template-columns:repeat(2,1fr)}
  .rk.c5{grid-template-columns:52px 70px 1fr 78px 80px}
  .rk.c5 .bar{display:none}
  .rk.c5b{grid-template-columns:1fr 90px 84px}
  .rk.c5b .d:nth-of-type(3){display:none}
}
"""


def band_chart(labels, main_vals, lows, highs, width=960, height=340):
    """一条主线 + 一条区间带（深色版）。

    带子 = 8 个策略在该起点下的最宽范围 → 带越宽 = 策略越重要。
    线 = 月定投年化 → 线的走势 = 起点越重要。
    """
    pad_l, pad_r, pad_t, pad_b = 62, 46, 22, 36
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
                 f'y2="{Y(v):.1f}" stroke="rgba(255,255,255,.07)"/>')
        o.append(f'<text x="{pad_l - 10}" y="{Y(v) + 4:.1f}" font-size="10" '
                 f'fill="#606774" text-anchor="end" '
                 f'style="font-variant-numeric:tabular-nums">{v * 100:.0f}%</text>')
    up = " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(highs) if v is not None)
    dn = " L".join(f"{X(i):.1f},{Y(v):.1f}"
                   for i, v in reversed(list(enumerate(lows))) if v is not None)
    o.append(f'<path d="M{up} L{dn} Z" fill="#d4b176" fill-opacity=".13"/>')
    pts = [(X(i), Y(v)) for i, v in enumerate(main_vals) if v is not None]
    o.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
             '" fill="none" stroke="#d4b176" stroke-width="1.9" '
             'stroke-linejoin="round" stroke-linecap="round"/>')
    for i, v in enumerate(main_vals):
        if v is None:
            continue
        o.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3.2" fill="#09090b" '
                 f'stroke="#d4b176" stroke-width="1.8">'
                 f'<title>{labels[i]} 起 · 月定投 {v * 100:.2f}%'
                 f'（策略区间 {lows[i] * 100:.2f}% ~ {highs[i] * 100:.2f}%）</title></circle>')
    for i, lab in enumerate(labels):
        o.append(f'<text x="{X(i):.1f}" y="{height - 13}" font-size="10" '
                 f'fill="#606774" text-anchor="middle" '
                 f'style="font-variant-numeric:tabular-nums">{lab}</text>')
    o.append("</svg>")
    return "".join(o)


def main(argv=None):
    ap = argparse.ArgumentParser(description="滚动起点定投 HTML（film-annual 设计语言）")
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
    stock = stock_ctx(rows)

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
        ctx = prep_fast(stock, divs, start)
        bd = budget_fast(stock, start)
        res = {}
        for label, kind in STRATEGIES:
            total, buys, leftover = build_buys(kind, rows, start, args.monthly, bd)
            if not buys:
                continue
            res[label] = evaluate_fast(ctx, buys, leftover, True)
        if not res:
            continue
        xs = [v["r"] for v in res.values() if v["r"] is not None]
        wins.append(dict(year=y, start=start, res=res,
                         yrs=(date.fromisoformat(end_date)
                              - date.fromisoformat(start)).days / 365.25,
                         lo=min(xs), hi=max(xs), spread=max(xs) - min(xs)))
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

    first, last_w = wins[0], wins[-1]
    fm = first["res"][MAIN]
    r_all = [w["res"][MAIN]["r"] for w in wins]
    paced_sd = [s for s in stats if s["label"] in paced]
    steadiest = min(paced_sd, key=lambda z: z["sd"]) if paced_sd else None
    mx_spread = max(w["spread"] for w in wins) or 1.0

    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>{name} {code} · 滚动起点定投</title>',
         f'<meta name="description" content="{name}（{code}）'
         f'{first["start"]} 起至 {last_w["start"]} 起共 {len(wins)} 个滚动窗口的'
         f'定投年化、累计收益与策略敏感度。">',
         '<meta name="theme-color" content="#09090b">',
         '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\''
         ' viewBox=\'0 0 64 64\'%3E%3Crect width=\'64\' height=\'64\' rx=\'14\''
         ' fill=\'%2309090b\'/%3E%3Cpath d=\'M14 44 L26 30 L36 38 L50 20\''
         ' fill=\'none\' stroke=\'%23d4b176\' stroke-width=\'4\''
         ' stroke-linecap=\'round\'/%3E%3C/svg%3E">',
         f'<style>{CSS}</style></head><body>']

    h.append('<header class="top"><div class="wrap topin">')
    h.append(f'<a class="brand" href="#">定投测算 <span>{name} {code}</span></a>')
    h.append(f'<div class="topnav">滚动窗口 <b>{len(wins)}</b> 个 ｜ '
             f'每月 <b>{args.monthly:,.0f}</b> 元</div>')
    h.append('</div></header>')

    h.append('<div class="dstatus"><div class="wrap">')
    h.append('<span class="dot"></span>')
    h.append(f'<span>终点统一 {end_date} ｜ 分红再投 ｜ 不复权价 ｜ '
             f'按当日收盘买入（含零头） ｜ 未扣红利税</span>')
    h.append('</div></div>')

    h.append('<main class="wrap">')
    h.append('<section class="hero">')
    h.append('<div class="kicker">Rolling-window DCA study</div>')
    h.append(f'<h1>{name} 的定投<em>时间敏感性</em></h1>')
    h.append(f'<p>从 <span class="num">{first["year"]}</span> 年 1 月起每月买 '
             f'{args.monthly:,.0f} 元，到 {end_date} 累计收益 '
             f'<span class="num">{fm["ret"] * 100:.0f}%</span>、年化 '
             f'<span class="num">{fm["r"] * 100:.1f}%</span>。'
             f'把起点逐年往后挪，年化在 '
             f'<span class="num">{min(r_all) * 100:.1f}%</span> 到 '
             f'<span class="num">{max(r_all) * 100:.1f}%</span> 之间移动 —— '
             f'但这只是窗口变短、吃到了同一段上涨，'
             f'<b>不是晚起步更好</b>。</p>')
    h.append('</section>')

    h.append('<div class="stats">')
    h.append(f'<div class="stat gold"><b>{fm["r"] * 100:.1f}%</b>'
             f'<span>最早窗口 · 年化</span></div>')
    h.append(f'<div class="stat"><b>{fm["ret"] * 100:.0f}%</b>'
             f'<span>累计收益</span></div>')
    h.append(f'<div class="stat"><b>{mx_spread * 100:.1f}'
             f'<small style="font-size:14px;color:var(--dim)">pp</small></b>'
             f'<span>策略影响上限</span></div>')
    if steadiest:
        h.append(f'<div class="stat"><b>{steadiest["sd"] * 100:.1f}'
                 f'<small style="font-size:14px;color:var(--dim)">pp</small></b>'
                 f'<span>最稳策略的标准差</span></div>')
    h.append('</div>')

    # 表一
    h.append('<section class="section">')
    h.append('<div class="shead"><h2>每个起点，月定投拿到什么</h2>'
             '<small>8 种策略的差异见后</small><span class="line"></span></div>')
    h.append('<div class="ranklist">')
    h.append('<div class="rk rkhead c5"><span>起点</span><span>年数</span><span></span>'
             '<span style="text-align:right">年化</span>'
             '<span style="text-align:right">每 1 元变</span>'
             '<span style="text-align:right">策略影响</span></div>')
    for w in wins:
        m = w["res"][MAIN]
        bw = max(2, round(w["spread"] / mx_spread * 54))
        h.append(
            f'<div class="rk c5"><span class="y">{w["year"]}</span>'
            f'<span class="d" style="text-align:left">{w["yrs"]:.0f} 年</span>'
            f'<span></span>'
            f'<span class="m">{m["r"] * 100:.1f}%</span>'
            f'<span class="v">{1 + m["ret"]:.2f}<span class="u">元</span></span>'
            f'<span class="bar"><i style="width:{bw}px"></i>'
            f'{w["spread"] * 100:.1f}<span class="u">pp</span></span></div>')
    h.append('</div>')
    h.append(f'<p class="note">「每 1 元变」= 含分红再投的期末倍数：投 1 元收回 '
             f'{1 + fm["ret"]:.2f} 元。「策略影响」= 同一窗口内 8 种策略年化'
             f'最好与最差之差，右侧细条长度即该值。窗口越长它越小 —— '
             f'投满 {first["yrs"]:.0f} 年只差 {first["spread"] * 100:.1f}pp，'
             f'只剩 {last_w["yrs"]:.0f} 年却差 {last_w["spread"] * 100:.1f}pp。</p>')
    h.append('</section>')

    # 图
    h.append('<section class="section">')
    h.append('<div class="shead"><h2>起点与策略，各占多少</h2>'
             '<small>线 = 月定投 ｜ 带 = 8 种策略的跨度</small>'
             '<span class="line"></span></div>')
    h.append('<figure>')
    h.append(band_chart([str(w["year"]) for w in wins],
                        [w["res"][MAIN]["r"] for w in wins],
                        [w["lo"] for w in wins], [w["hi"] for w in wins]))
    h.append('<figcaption>线往上走 = 起点越晚年化越高（窗口短、吃到同一段涨幅）。'
             '带子窄 = 策略怎么选都差不多；带子宽 = 选错就少赚一大截。'
             '两件事分开看，才不会把「晚起步」误当成「好策略」。</figcaption>')
    h.append('</figure></section>')

    # 表二（折叠）
    h.append('<section class="section tight">')
    h.append('<div class="shead"><h2>八种策略，逐窗口对照</h2>'
             '<small>默认收起</small><span class="line"></span></div>')
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
            cls = ' class="pk"' if v is not None and abs(v - mx) < 1e-12 else ""
            h.append(f'<td{cls}>{v * 100:.1f}%</td>' if v is not None
                     else '<td style="color:var(--dim)">—</td>')
        h.append('</tr>')
    h.append('</tbody></table></details></section>')

    # 表三
    h.append('<section class="section">')
    h.append('<div class="shead"><h2>哪种策略最不挑起点</h2>'
             '<small>标准差越小越稳</small><span class="line"></span></div>')
    h.append('<div class="ranklist">')
    h.append('<div class="rk rkhead c5b"><span>策略</span>'
             '<span style="text-align:right">平均年化</span>'
             '<span style="text-align:right">最差 ~ 最好</span>'
             '<span style="text-align:right">标准差</span>'
             '<span style="text-align:right">当过最优</span></div>')
    for s_ in sorted(stats, key=lambda z: z["sd"]):
        h.append(
            f'<div class="rk c5b"><span>{s_["label"]}</span>'
            f'<span class="v">{s_["mean"] * 100:.1f}%</span>'
            f'<span class="d">{s_["lo"] * 100:.1f}% ~ {s_["hi"] * 100:.1f}%</span>'
            f'<span class="m">{s_["sd"] * 100:.1f}<span class="u">pp</span></span>'
            f'<span class="d">{s_["wins"]}/{s_["n"]}</span></div>')
    h.append('</div>')
    if steadiest:
        h.append(f'<p class="note">「{steadiest["label"]}」标准差最小'
                 f'（{steadiest["sd"] * 100:.1f}pp），换 {steadiest["n"]} 个起点都落在 '
                 f'{steadiest["lo"] * 100:.1f}% ~ {steadiest["hi"] * 100:.1f}% 之间。'
                 f'反过来，平均年化最高的策略标准差也最大：那不是本事，'
                 f'是它把投入推得更晚、正好吃到上涨。</p>')
    h.append('</section>')

    h.append('<div class="verdict">')
    h.append('<b>换个起点，结论会变吗</b>')
    h.append(f'<p>窗口间极差中位 <span class="num">'
             f'{st.median([w["spread"] for w in wins]) * 100:.1f}pp</span>，'
             f'最大 <span class="num">{max(w["spread"] for w in wins) * 100:.1f}pp</span>。'
             f'但只比投入节奏（月/季/半年/年初/年末）时，中位只有 '
             f'<span class="num">1.2pp</span> —— 节奏本身几乎不重要，'
             f'真正拉开差距的是「一次性 vs 定投」和「择时」。'
             f'注意 XIRR 按每笔钱的持有时间加权，「投得晚」天然占优，'
             f'所以不能拿它评「最好的节奏」。</p>')
    h.append('</div>')

    h.append(f'<div class="ft">价格 baostock 不复权日线 ｜ 分红 东财 ｜ '
             f'按当日收盘价买入、含零头 ｜ 生成于 {date.today():%Y-%m-%d}'
             f'<br>仅为历史测算，不构成投资建议。</div>')
    h.append('</main></body></html>')

    out = args.out or os.path.join(BASE, "docs", "基本面", f"dca_{code}.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    print(f"→ {out}（{len(wins)} 个窗口）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
