# -*- coding: utf-8 -*-
"""生成「个股基本面档案页」：业绩趋势 / 分红历史 / 盈利能力 / 估值位置 / 财务排雷。

设计约束（与本仓库既有风格一致）：
  * **零外部依赖**：只用标准库，图表是内联 SVG（不引 ECharts/Chart.js、不引 CDN）；
  * **零 JS**：hover 提示靠 SVG 的 `<title>`，浏览器原生就有；
  * **每个数字都带公告日**：季报有滞后，不标 `pubDate` 会误导；
  * **数据异常要显式标出来**，不能画一个「负债率 0.9%」的图（baostock 真有这种脏点）。

用法::

    python build_fundamentals.py --json _fin_cache/601398.json --out 基本面/601398.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CSS = """
:root{
  --bg:#0a0e15; --bg2:#0e141d; --panel:#131b26; --panel2:#17202d;
  --line:#22303f; --line2:#2d3d4f;
  --tx:#e8eef7; --dim:#8fa3ba; --dim2:#7b8fa5;
  --acc:#ffd21e; --acc2:#f7b500;
  --ok:#34d399; --warn:#fbbf24; --bad:#fb7185;
  --blue:#7dd3fc; --violet:#a78bfa;
  --mono:ui-monospace,"Cascadia Code",Consolas,"SF Mono",monospace;
}
*{box-sizing:border-box;-webkit-text-size-adjust:100%}
body{margin:0;color:var(--tx);
  background:
    radial-gradient(1100px 500px at 12% -10%,rgba(255,210,30,.07),transparent 60%),
    radial-gradient(900px 480px at 92% 0%,rgba(125,211,252,.06),transparent 60%),
    var(--bg);
  font:14.5px/1.6 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;padding-bottom:40px}
a{color:var(--blue);text-decoration:none}
.page{max-width:1020px;margin:0 auto;padding:0 18px 40px}

/* ── 抬头：粘顶 + 毛玻璃 ── */
header{position:sticky;top:0;z-index:20;backdrop-filter:blur(14px);
  background:rgba(10,14,21,.86);border-bottom:1px solid var(--line);
  padding:14px 0 12px;margin-bottom:20px}
.title{font-size:21px;font-weight:700;letter-spacing:-.2px;display:flex;
  align-items:baseline;gap:8px;flex-wrap:wrap}
.title .code{font:600 13px var(--mono);color:var(--acc);letter-spacing:.5px}
.title .ind{font-size:11px;color:var(--dim);background:var(--panel);
  border:1px solid var(--line);padding:3px 9px;border-radius:999px;font-weight:500}
.prow{display:flex;align-items:baseline;gap:12px;margin-top:7px;flex-wrap:wrap}
.prow .px{font:700 23px/1.1 var(--mono);color:var(--tx)}
.prow .chg{font:600 14px/1 var(--mono)}
.prow .meta{font-size:11.5px;color:var(--dim2);letter-spacing:.2px}

/* ── 区块标题 ── */
h2{font-size:15px;font-weight:700;margin:26px 0 12px;display:flex;
  align-items:baseline;gap:9px;flex-wrap:wrap}
h2 .unit{font-size:11.5px;color:var(--dim2);font-weight:400}
h2 .cnt{font:700 11px var(--mono);color:var(--acc);background:rgba(255,210,30,.1);
  border:1px solid rgba(255,210,30,.25);padding:3px 8px;border-radius:6px}

/* ── 信号卡片：首屏唯一主角 ── */
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin:16px 0 0}
.stat{background:linear-gradient(180deg,var(--panel),var(--bg2));
  border:1px solid var(--line);border-radius:12px;padding:13px 14px;min-width:0}
.stat .k{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--dim2);
  letter-spacing:.4px;white-space:nowrap}
.stat .k .dot{width:6px;height:6px;border-radius:50%;flex:none}
.stat .v{font:800 21px/1.15 var(--mono);margin-top:9px;letter-spacing:-.5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat .n{font-size:11px;color:var(--dim);margin-top:6px;line-height:1.45;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.stat.ok .v{color:var(--ok)}
.stat.warn .v{color:var(--warn)}
.stat.bad .v{color:var(--bad)}
.stat.na .v{color:var(--dim2)}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}
.dot.bad{background:var(--bad)}.dot.na{background:var(--dim2)}

/* 结论条 */
.sum{margin-top:12px;padding:13px 16px;border-radius:12px;font-size:14px;
  font-weight:660;border:1px solid var(--line2);background:var(--panel2);
  display:flex;align-items:center;gap:10px}
.sum .mk{width:3px;align-self:stretch;border-radius:2px;background:var(--dim2)}
.sum.ok .mk{background:var(--ok)}.sum.bad .mk{background:var(--bad)}

/* ── 财务表 ── */
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;
  font-size:13px}
thead th{position:sticky;top:64px;background:var(--bg);z-index:1}
th,td{padding:8px 9px;text-align:right;border-bottom:1px solid var(--line);
  white-space:nowrap}
th{font-size:11.5px;font-weight:600;color:var(--dim2);letter-spacing:.3px}
th.k,td.k{text-align:left;font-weight:500;color:var(--tx);position:sticky;left:0;
  background:var(--bg);z-index:2}
td{font-family:var(--mono);font-size:12.5px}
td.k{font-family:inherit;font-size:13px}
tbody tr:hover td{background:rgba(255,255,255,.018)}
/* 语义：pos/neg 是「基本面好坏」，up/down 是「涨跌」（A 股红涨绿跌） */
.pos{color:var(--ok)}
.neg{color:var(--bad)}
.up{color:var(--bad)}
.down{color:var(--ok)}
.na,.mut{color:var(--dim2)}
.fn{font-size:11px;color:var(--dim2);margin:9px 0 0;line-height:1.75}
.fn b{color:var(--dim)}

/* 结论块（旧版兼容） */
.verdict{border-left:3px solid var(--line2);padding:10px 0 10px 14px;
  margin-top:26px;font-size:13.5px}
.verdict.warn{border-left-color:var(--warn)}
.verdict.ok{border-left-color:var(--ok)}
.verdict b{display:block;margin-bottom:4px}
.verdict .kv{font-size:12.5px;color:var(--dim)}
.ft{color:var(--dim2);font-size:11px;text-align:center;margin-top:34px;line-height:1.9}

/* ── 折叠 ── */
details{margin-top:9px;border:1px solid var(--line);border-radius:10px;
  padding:0 14px;background:rgba(255,255,255,.012)}
details summary{cursor:pointer;padding:11px 0;font-size:12.5px;color:var(--dim);
  list-style:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";color:var(--dim2)}
details[open] summary::before{content:"▾ "}
details table{margin-bottom:12px}

/* ── 格雷厄姆七条 ── */
.gtab{table-layout:fixed}
.gtab th:nth-child(1),.gtab td:nth-child(1){width:150px}
.gtab th:nth-child(2),.gtab td:nth-child(2){width:auto;text-align:left}
.gtab th:nth-child(3),.gtab td:nth-child(3){width:72px;text-align:right;
  white-space:nowrap}
.gtab td{font-size:12.5px;padding:9px 10px;vertical-align:top}
.gtab td:nth-child(2){font-family:inherit}
.gtab .gn{color:var(--tx)}
.gtab .gth{display:block;color:var(--dim2);font-size:10.5px;margin-top:3px;
  line-height:1.5;font-family:inherit}
.gtab .pos{font-weight:700}
.gtab .neg{font-weight:700}
.gtab .gnote{display:block;color:var(--dim2);font-size:10px;font-weight:400;
  margin-top:2px;line-height:1.45;font-family:inherit}

/* 年度表趋势行 */
.trow td{background:rgba(255,210,30,.045);color:var(--dim);font-size:11.5px;
  font-weight:600;border-top:1px solid var(--line2);border-bottom:0}
.trow td.k{color:var(--dim2);background:rgba(255,210,30,.045)}

/* 技术面 / TTM 各一行 */
.strip{margin:8px 0 0;font-size:12px;color:var(--dim);line-height:1.85;
  font-variant-numeric:tabular-nums}
.strip .strip-k{display:inline-block;min-width:44px;color:var(--dim2);font-size:10.5px;
  letter-spacing:.06em;margin-right:9px}
.strip .strip-d{color:var(--dim2);font-size:10.5px;margin-left:10px}

/* 站内导航胶囊 */
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 6px}
.nav a{font-size:12px;color:var(--dim);border:1px solid var(--line);
  border-radius:999px;padding:5px 13px;background:var(--panel);transition:.16s}
.nav a:hover{color:var(--acc);border-color:rgba(255,210,30,.35)}

@media(max-width:860px){
  .stats{grid-template-columns:repeat(2,1fr)}
  .stat .v{font-size:18px}
}
@media(max-width:520px){
  .stats{grid-template-columns:1fr 1fr}
  .page{padding:0 12px 30px}
}
"""


# ─────────────────────────── SVG 基础件 ───────────────────────────
def trend_word(vals, tol=0.0):
    """一串序列的方向词：↑8/9 年 / ↓7/8 年 / → 基本持平。

    只统计**相邻两期**的变化，不做回归 —— 读者能自己复核：
    「9 次变化里 8 次在涨」是可以数出来的。
    """
    seq = [v for v in vals if v is not None]
    if len(seq) < 2:
        return ""
    ups = sum(1 for a, b in zip(seq, seq[1:]) if b - a > tol)
    downs = sum(1 for a, b in zip(seq, seq[1:]) if b - a < -tol)
    n = len(seq) - 1
    if ups == 0 and downs == 0:
        return "→ 持平"
    if ups >= n * 0.75:
        return f'↑{ups}/{n}'
    if downs >= n * 0.75:
        return f'↓{downs}/{n}'
    if ups > downs:
        return f'↑{ups} ↓{downs}'
    if downs > ups:
        return f'↓{downs} ↑{ups}'
    return f'↑{ups} ↓{downs}'


def spark(vals, w=62, h=20, color="#7dd3fc"):
    """迷你折线：只表达方向，不带坐标轴。

    刻意不画网格、不标数值 —— 数值已经在左边用大字写着了，
    这里唯一的职责是让「在涨还是在跌」一眼可见。
    """
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 2:
        return ""
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    if hi - lo < 1e-12:
        hi = lo + 1.0
    n = len(vals)

    def X(i):
        return 1.5 + (i / (n - 1)) * (w - 3)

    def Y(v):
        return h - 2.5 - (v - lo) / (hi - lo) * (h - 5)

    d = "M" + " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in pts)
    lx, ly = X(pts[-1][0]), Y(pts[-1][1])
    return (f'<svg class="spk" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'aria-hidden="true"><path d="{d}" fill="none" stroke="{color}" '
            f'stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="1.9" fill="{color}"/></svg>')


def posbar(lo, hi, cur, w=62, h=14):
    """位置条：当前值落在 [lo, hi] 的哪里。

    用于估值 —— 我们没有 PE 的历史时间序列（缓存里只有区间上下沿），
    但「现在贴着上沿还是下沿」本身就是最有用的证据。
    """
    try:
        lo, hi, cur = float(lo), float(hi), float(cur)
    except (TypeError, ValueError):
        return ""
    if hi <= lo:
        return ""
    t = max(0.0, min(1.0, (cur - lo) / (hi - lo)))
    y = h / 2
    x = 1.5 + t * (w - 3)
    return (f'<svg class="pos" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'aria-hidden="true">'
            f'<line x1="1.5" y1="{y}" x2="{w - 1.5}" y2="{y}" stroke="#22303f" '
            f'stroke-width="3" stroke-linecap="round"/>'
            f'<line x1="1.5" y1="{y}" x2="{x:.1f}" y2="{y}" stroke="#ffd21e" '
            f'stroke-width="3" stroke-linecap="round"/>'
            f'<circle cx="{x:.1f}" cy="{y}" r="2.7" fill="#fff" stroke="#ffd21e" '
            f'stroke-width="1.6"/></svg>')


def _fmt(v, nd=1):
    if v is None:
        return "—"
    if abs(v) >= 10000:
        return f"{v / 10000:,.{nd}f}万"
    return f"{v:,.{nd}f}"


def _nice_ticks(lo, hi, n=4):
    """给 [lo,hi] 生成 n+1 个好看的刻度。"""
    if lo == hi:
        hi = lo + 1
    span = hi - lo
    raw = span / n
    mag = 10 ** (len(str(int(abs(raw)))) - 1) if raw >= 1 else 10 ** -1
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if step >= raw:
            break
    start = (int(lo / step)) * step
    ticks = []
    v = start
    while v <= hi + step * 0.5:
        ticks.append(round(v, 6))
        v += step
    return ticks


def _scale(v, lo, hi, px, py, pw, ph):
    x = px + (v - lo) / (hi - lo) * pw if hi > lo else px
    return x


def bar_chart(labels, series, height=230, unit="亿", width=780, legend=None,
              grid=True, bar_gap=0.28):
    """分组柱状图。series = [(名字, 颜色, [值...]), ...]"""
    pad_l, pad_r, pad_t, pad_b = 52, 12, 14, 34
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [v for _, _, arr in series for v in arr if v is not None]
    if not vals:
        return '<p class="note">（无数据）</p>'
    lo, hi = min(0, min(vals)), max(vals)
    ticks = _nice_ticks(lo, hi * 1.08, 4)
    lo, hi = min(ticks), max(ticks)
    n = len(labels)
    slot = pw / max(n, 1)
    bw = slot * (1 - bar_gap) / max(len(series), 1)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in ticks:
        y = pad_t + ph - (t - lo) / (hi - lo) * ph
        if grid:
            out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                       f'y2="{y:.1f}" stroke="#1c2634"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 3.5:.1f}" font-size="10" fill="#7b8fa5" '
                   f'text-anchor="end">{_fmt(t, 0)}</text>')
    for i, lab in enumerate(labels):
        cx = pad_l + slot * i
        for j, (name, color, arr) in enumerate(series):
            v = arr[i] if i < len(arr) else None
            if v is None:
                continue
            y0 = pad_t + ph - (0 - lo) / (hi - lo) * ph
            y1 = pad_t + ph - (v - lo) / (hi - lo) * ph
            x = cx + slot * bar_gap / 2 + bw * j
            out.append(f'<rect x="{x:.1f}" y="{min(y0, y1):.1f}" width="{bw * 0.92:.1f}" '
                       f'height="{abs(y1 - y0):.1f}" fill="{color}" rx="1.5">'
                       f'<title>{html.escape(lab)} {name}: {_fmt(v)} {unit}</title></rect>')
        if n <= 14 or i % 2 == 0:
            out.append(f'<text x="{cx + slot / 2:.1f}" y="{height - 12}" font-size="9.5" '
                       f'fill="#7b8fa5" text-anchor="middle">{html.escape(lab)}</text>')
    if legend:
        lx = width - pad_r
        for name, color in reversed(legend):
            w_ = 20 + len(name) * 10
            lx -= w_
            out.append(f'<rect x="{lx}" y="6" width="8" height="8" fill="{color}" rx="1.5"/>')
            out.append(f'<text x="{lx + 12}" y="13.5" font-size="10" fill="#8fa3ba">'
                       f'{html.escape(name)}</text>')
    out.append("</svg>")
    return "".join(out)


# ─────────────────────────── 数据加工 ───────────────────────────
def quarter_label(q):
    return f"{str(q['year'])[2:]}Q{q['quarter']}"


def yearly_dividends(divs):
    """按**财务年度**归集每股现金分红，同一期去重。

    东财 `RPT_SHAREBONUS_DET` 直接给 `REPORT_DATE`（所属报告期），所以归集不用猜：
    2025-12-31（年报）+ 2025-06-30（中报）= 2025 年度。

    baostock 版没有这个字段，只能按公告日启发式（1~4 月公告的属上一年度）——
    保留为兜底：工行 2025 年转一年两次时，按除权日归集会把三次派息挤进同一年，
    算出 5.29% 的假股息率。
    """
    seen, by_year = set(), {}
    for d in divs:
        ex = d.get("exDate") or d.get("date") or ""
        key = (ex, d["cashPs"])
        if key in seen:
            continue
        seen.add(key)
        rd = d.get("reportDate")
        if rd:
            y = int(rd[:4])
        else:
            ann = d.get("announce") or ex
            try:
                y = int(ann[:4])
                if int(ann[5:7]) <= 4:
                    y -= 1
            except (TypeError, ValueError):
                y = int(ex[:4])
        by_year.setdefault(y, []).append(d["cashPs"])
    return {str(y): round(sum(v), 4) for y, v in sorted(by_year.items())}


def full_year_dividend(by_year):
    """最后一个**完整**年度的每股分红合计 → (年度, 金额)。

    为什么不用「近 12 个月」：工行 2025 年起改一年两次（年度 + 中期），过渡期里
    滚动 12 个月会挤进三次派息（2025-07 年度 + 2025-12 中期 + 2026-05 年度
    = 0.4749），据此算出的股息率 5.59% 明显高估；按完整年度算约 0.306（3.6%）。

    规则（启发式，因为聚合后已看不到派息次数）：最新年度合计若明显低于上一年
    （<80%），说明那一年还没派完，退到上一年。
    """
    years = sorted(by_year, key=int)
    if not years:
        return None, None
    last = years[-1]
    if len(years) >= 2 and by_year[last] < by_year[years[-2]] * 0.8:
        return years[-2], by_year[years[-2]]
    return last, by_year[last]


def ttm_dividend(divs):
    """最近 12 个月**已实施**的每股分红合计（仅用于交叉核对，不作主口径）。"""
    seen, rows = set(), []
    for d in divs:
        ex = d.get("exDate") or d.get("date") or ""
        key = (ex, d["cashPs"])
        if key in seen:
            continue
        seen.add(key)
        if ex:
            rows.append(dict(date=ex, cashPs=d["cashPs"]))
    if not rows:
        return None
    last = max(r["date"] for r in rows)
    cutoff = (datetime.strptime(last, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    return round(sum(r["cashPs"] for r in rows if r["date"] > cutoff), 4)


def consecutive_years(by_year):
    """从最新分红年度往前数，连续有分红的年数。"""
    years = sorted(int(y) for y in by_year)
    if not years:
        return 0, None, None
    n = 1
    for i in range(len(years) - 1, 0, -1):
        if years[i] - years[i - 1] == 1:
            n += 1
        else:
            break
    return n, years[-n], years[-1]


def annual_rows(d, years=10):
    """业绩 ↔ 分红 ↔ 回购，同一财务年度一行。

    三个数据源的时间键不同（季报按 statDate、分红按 reportDate 的财务年度、
    回购按公告日），分开看永远看不出「业绩变了，分红跟着变了吗」。
    """
    qs = d.get("quarters") or []
    annual = {}
    latest = {}
    for q in qs:
        y, k = q["year"], q["quarter"]
        if k == 4:
            annual[y] = q
        if y not in latest or k > latest[y]["quarter"]:
            latest[y] = q

    dps = {}
    for r in d.get("dividends") or []:
        rd = (r.get("reportDate") or "")[:10]
        v = r.get("cashPs")
        if rd and v:
            try:
                y = int(rd[:4])
            except ValueError:
                continue
            dps[y] = dps.get(y, 0.0) + float(v)

    bk = {}
    for r in d.get("buybacks") or []:
        dd = (r.get("date") or "")[:10]
        if not dd:
            continue
        try:
            y = int(dd[:4])
        except ValueError:
            continue
        slot = bk.setdefault(y, {"注销": 0, "进展": 0})
        slot["注销" if r.get("kind") == "注销" else "进展"] += 1

    yr_avg = d.get("yr_avg") or {}
    out = []
    for y in sorted(annual):
        a = annual[y]
        rev, npf = a.get("revenue"), a.get("netProfit")
        share = a.get("totalShare")
        dv = dps.get(y)
        out.append(dict(
            year=y, complete=True,
            revenue=rev / 1e8 if rev else None,
            netProfit=npf / 1e8 if npf else None,
            yoyRev=a.get("yoyRev"), yoyNi=a.get("yoyNi"),
            roe=(a.get("roe") or 0) * 100 if a.get("roe") is not None else None,
            margin=(npf / rev * 100) if (rev and npf) else None,
            dps=dv,
            # 港股没有每股分红，只有源给的派息率（DIVI_RATIO）—— 优先用它
            payout=((a.get("payout") or 0) * 100) if a.get("payout") is not None
            else ((dv * share / npf * 100) if (dv and share and npf) else None),
            yld=(dv / yr_avg[str(y)] * 100) if (dv and yr_avg.get(str(y))) else None,
            bk_cancel=bk.get(y, {}).get("注销", 0),
            bk_prog=bk.get(y, {}).get("进展", 0)))
    for y in sorted(latest):
        if y in annual:
            continue
        r = latest[y]
        rev, npf = r.get("revenue"), r.get("netProfit")
        out.append(dict(
            year=y, complete=False,
            revenue=rev / 1e8 if rev else None,
            netProfit=npf / 1e8 if npf else None,
            yoyRev=r.get("yoyRev"), yoyNi=r.get("yoyNi"),
            roe=(r.get("roe") or 0) * 100 if r.get("roe") is not None else None,
            margin=(npf / rev * 100) if (rev and npf) else None,
            dps=dps.get(y), payout=None, yld=None,
            bk_cancel=bk.get(y, {}).get("注销", 0),
            bk_prog=bk.get(y, {}).get("进展", 0)))
    out.sort(key=lambda x: x["year"])

    # 自动异常标记 —— 这类错不该靠肉眼发现。
    # 实例：五粮液 2025 营收 −54.6%、净利 −71.9%，但每股分红没降、
    # 分红率算出来 224%（分红超过净利），而次年一季度单季营收就超过前一年的半年。
    # 标记「分红超过净利」—— 这是**可持续性**信号，不是数据错误。
    # 曾经把它标成「数据可疑」，是错的：五粮液 2025（营收 −55%、净利 −72%、
    # 分红率 224%）与中石油 2020（净利 −58%、分红率 168%）都是**真实的**
    # 业绩崩塌下维持分红，序列内部完全自洽。误判成脏数据会误导读者去核对
    # 一个本来没问题的年报。
    for x in out:
        if x["payout"] is not None and x["payout"] > 100:
            x["flag"] = f'分红 {x["payout"]:.0f}% 超过当年净利，需看能否持续'
        else:
            x["flag"] = ""
    return out[-years:]


def main(argv=None, payload=None):
    ap = argparse.ArgumentParser(description="生成个股基本面档案页")
    ap.add_argument("--json", default=None, help="季频财务 JSON（单只调试用）")
    ap.add_argument("--out", default=None, help="输出 HTML 路径（--all 时是目录）")
    ap.add_argument("--metrics", default=None, help="仓库的 metrics CSV")
    ap.add_argument("--all", action="store_true", help="批量：池子每只一页 + 索引页")
    ap.add_argument("--quiet", action="store_true", help="批量时不逐只打印")
    args = ap.parse_args(argv)

    if args.all:
        return build_all(args)
    if payload is not None:
        d = payload
    else:
        if not args.json:
            ap.error("需要 --json 或 --all")
        with open(args.json, encoding="utf-8") as fh:
            d = json.load(fh)

    code = d["code"]
    qs = d["quarters"]
    meta = d.get("_meta") or {}
    if not meta and args.metrics and os.path.exists(args.metrics):
        import csv
        with open(args.metrics, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("代码", "")).zfill(6) == code.zfill(6):
                    meta = row
                    break

    name = meta.get("名称") or code
    ind = meta.get("行业") or "—"
    # 三档兜底：metrics 的「最新价」→ 单只 JSON 的日线末值 → None。
    # 第三档是必须的：策展池里还没纳入日更的股票两者都没有，
    # 直接 d["prices"] 会 KeyError，263 只页面全渲染失败。
    _px_src = d.get("prices") or {}
    _px_close = _px_src.get("close") or []
    last_px = meta.get("最新价") or (_px_close[-1] if _px_close else None)
    pe_pct = f'{meta["PE历史分位%"]}%' if meta.get("PE历史分位%") else "—"
    pb_pct = f'{meta["PB历史分位%"]}%' if meta.get("PB历史分位%") else "—"
    latest = qs[-1] if qs else None

    # 营收：Q1/Q3 缺失时用 净利 ÷ 净利率 反推
    for q in qs:
        if not q.get("revenue") and q.get("netProfit") and q.get("npMargin"):
            q["revenue"] = q["netProfit"] / q["npMargin"]
            q["revenueImplied"] = True
    for q in qs:
        prev = next((x for x in qs if x["year"] == q["year"] and x["quarter"] == q["quarter"] - 1), None)
        if q["quarter"] == 1:
            q["qRevenue"], q["qNetProfit"] = q["revenue"], q["netProfit"]
        elif prev is None:
            # 窗口内第一个非 Q1 季度（缺上季累计）→ 无法差分，置空。
            # 曾经这里退化成用累计值，导致 21Q3 出现一根 7121 亿的假柱子，
            # 把整张图的纵轴撑到 8000、其余柱子全被压扁。
            q["qRevenue"] = q["qNetProfit"] = None
        else:
            q["qRevenue"] = (q["revenue"] - prev["revenue"]) if q["revenue"] and prev.get("revenue") else None
            q["qNetProfit"] = (q["netProfit"] - prev["netProfit"]) if q["netProfit"] and prev.get("netProfit") else None
    for q in qs:
        same = next((x for x in qs if x["year"] == q["year"] - 1 and x["quarter"] == q["quarter"]), None)
        q["revYoy"] = (q["qRevenue"] / same["qRevenue"] - 1) * 100 if same and same.get("qRevenue") and q.get("qRevenue") else None
        q["npYoy"] = (q["qNetProfit"] / same["qNetProfit"] - 1) * 100 if same and same.get("qNetProfit") and q.get("qNetProfit") else None
    # 东财直接给「单季同比」（DJD_TOI_YOY / DJD_DPNP_YOY），优先用它 ——
    # 这样同比不依赖我自己差分的正确性，差分只用于画柱子的绝对值。
    for q in qs:
        if q.get("qRevYoy") is not None:
            q["revYoy"] = q["qRevYoy"]
        if q.get("qNpYoy") is not None:
            q["npYoy"] = q["qNpYoy"]
    # ROE 是年内累计 → 差分出**单季 ROE**。不差分曲线是锯齿（每季从 0 重来），
    # 看起来像"波动很大"，其实只是累计口径。
    for q in qs:
        prev = next((x for x in qs if x["year"] == q["year"] and x["quarter"] == q["quarter"] - 1), None)
        if q["quarter"] == 1:
            q["qRoe"] = q["roe"]
        elif prev is None or q.get("roe") is None or prev.get("roe") is None:
            q["qRoe"] = None
        else:
            q["qRoe"] = q["roe"] - prev["roe"]

    # 价格序列 + **当年日均价**
    # 股息率为什么用均价而不是年末价：一年之内股价有高有低，年末那一天的收盘价
    # 只是个抽样点。用全年日均价，代表「那一年买入的典型成本」。
    # 两种 payload 都支持：
    #   批量（CSV 缓存）→ d["yr_avg"] 已经是 {年: 年均价}
    #   单只（JSON）    → d["prices"] 有完整日线，现算
    # 这里曾经只剩 else 分支，于是批量模式下 yr_avg 恒为空 ——
    # 结果核心指标表的「股息率」列 116 只全是「—」，决策卡也少了「现金回报」信号，
    # 而且不报错。是个静默回归。
    if d.get("yr_avg"):
        yr_avg = {str(k): v for k, v in d["yr_avg"].items() if v}
    else:
        _px = d.get("prices") or {}
        _yr = {}
        for _d, _c in zip(_px.get("dates") or [], _px.get("close") or []):
            if _d and _c:
                _yr.setdefault(str(_d)[:4], []).append(_c)
        yr_avg = {y: sum(v) / len(v) for y, v in _yr.items()}

    # 分红
    div_by_year = yearly_dividends(d["dividends"])
    # 最后一个分红年度如果还没有年报（Q4），说明这一年没走完 —— 柱子只有半年、
    # 矮一半，读者会误以为分红下降。直接不进图，在注释里说明。
    _years = list(div_by_year)
    _partial = False
    if _years:                      # 次新股可能一次都没分过红，_years 为空
        _partial = not any(str(x["year"]) == _years[-1] and x["quarter"] == 4
                           for x in qs)
        if _partial:
            _years = _years[:-1]
    div_years = _years[-12:]
    div_ps = [div_by_year[y] for y in div_years]
    share = latest.get("totalShare") if latest else None
    payout = []
    for y in div_years:
        np_year = next((x["netProfit"] for x in qs if str(x["year"]) == y and x["quarter"] == 4), None)
        if np_year and share and div_by_year.get(y):
            payout.append(div_by_year[y] * share / np_year * 100)
        else:
            payout.append(None)

    # ── 财务排雷 ──
    flags = []
    last4 = qs[-4:]
    neg_np = [q for q in last4 if q.get("npYoy") is not None and q["npYoy"] < 0]
    neg_rev = [q for q in last4 if q.get("revYoy") is not None and q["revYoy"] < 0]
    flags.append(("bad" if len(neg_np) >= 2 else ("warn" if neg_np else "ok"),
                  "净利润同比",
                  f"最近 4 季中 {len(neg_np)} 季同比为负" if neg_np else "最近 4 季同比均为正"))
    flags.append(("bad" if len(neg_rev) >= 2 else ("warn" if neg_rev else "ok"),
                  "营收同比",
                  f"最近 4 季中 {len(neg_rev)} 季同比为负" if neg_rev else "最近 4 季同比均为正"))
    # 净利率用**同比**比（银行净利率季节性很强，跨年比会误报）
    same_q = next((x for x in qs if latest and x["year"] == latest["year"] - 1
                   and x["quarter"] == latest["quarter"]), None)
    if latest and same_q and latest.get("npMargin") and same_q.get("npMargin"):
        a, b = same_q["npMargin"] * 100, latest["npMargin"] * 100
        drop = b - a
        flags.append(("warn" if drop < -1 else "ok", "净利率（同比）",
                      f"{latest['year']}Q{latest['quarter']} {b:.1f}% vs 去年同期 {a:.1f}%"
                      f"（{drop:+.1f}pp）"))
    # 负债率异常（baostock 有脏点：工行 2024Q2~2025Q4 返回 0.9%）
    bad_liab = [q for q in qs if q.get("liabilityToAsset") is not None
                and q["liabilityToAsset"] < 0.2]
    if bad_liab:
        flags.append(("na", "负债率数据异常",
                      f"{len(bad_liab)} 个季度返回 <20%（真实应在 90% 以上）→ 已在下表中标灰，不参与判断"))
    # 分红可持续性
    if payout and payout[-1] is not None:
        p = payout[-1]
        flags.append(("bad" if p > 100 else ("warn" if p > 70 else "ok"), "分红率（payout）",
                      f"最近年度 {p:.0f}%（>100% 不可持续，>70% 需留意）"))
    # CFO/净利：银行不适用
    flags.append(("na", "经营现金流 ÷ 净利润", "对银行不适用（存贷款业务现金流口径不同），已跳过"))

    # ── 渲染（对齐 StockAnalysis / 专业 F10 的排版：行=指标、列=期间）──
    def cell(v, fmt="{:.0f}", cls="", na="—"):
        return (f'<td class="{cls}">{fmt.format(v)}</td>' if v is not None
                else f'<td class="na">{na}</td>')

    def yoy_cell(cur, prev, pct=True):
        if cur is None or prev in (None, 0):
            return '<td class="na">—</td>'
        d = (cur / prev - 1) * 100
        return f'<td class="{"up" if d > 0 else "down"}">{d:+.1f}%</td>'

    # 期间列：最新一期（若不是年报）+ 最近 4 个完整年度
    years_all = sorted({q["year"] for q in qs})
    complete = [y for y in years_all
                if any(x["year"] == y and x["quarter"] == 4 for x in qs)]
    last_q = max(x["quarter"] for x in qs if x["year"] == years_all[-1])
    # 主表**只放年报 + TTM**。不把「中报」列摆进去 —— 半年 vs 全年并排，
    # 营收 4,659 vs 8,383、ROE 4.32% vs 9.45%，看起来像断崖式下滑，其实是口径不同。
    # StockAnalysis 也是这么做的：TTM | FY2025 | FY2024 …，没有"半年"列。
    periods = [("annual", y, 4) for y in reversed(complete[-8:])]

    annual_chrono = list(reversed([y for k, y, q in periods if k == "annual"]))

    def _yield(x, k, y):
        """当年股息率 = 该年度每股分红 ÷ 当年**日均价**。

        不用年末价：一年之内有高有低，年末那天只是抽样点。以工行 2025 年为例，
        年内价格 6.5~8.2，股息率实际在 3.8%~4.8% 之间；用年均价 7.37 得 4.21%，
        用年末价 7.94 得 3.91% —— 后者偏低。均价更能代表「那一年买入的典型水平」。
        """
        dps = div_by_year.get(str(y))
        if not dps or k != "annual":
            return None
        px = yr_avg.get(str(y))
        return dps / px * 100 if px else None

    def qof(y, q):
        return next((x for x in qs if x["year"] == y and x["quarter"] == q), None)

    def _pct(s):
        try:
            return float(str(s).replace("%", ""))
        except (TypeError, ValueError):
            return None

    def _f(key):
        v = meta.get(key)
        try:
            return float(str(v).replace("%", ""))
        except (TypeError, ValueError):
            return None

    jd, jw, jm = _f("日线J"), _f("周线J"), _f("月线J")

    # TTM（滚动 12 个月）= 最新累计 + 上年全年 − 上年同期累计。
    # 为什么需要它：中报是半年累计，和右边年报放一起，净利率会显示 37.9% vs 44.2%，
    # 像"利润率腰斩"，其实是口径不可比。TTM 才与年报可比。
    _cum = qof(years_all[-1], last_q) or {}
    _fy = qof(years_all[-1] - 1, 4) or {}
    _cp = qof(years_all[-1] - 1, last_q) or {}

    def _ttm(key):
        a, b, c = _cum.get(key), _fy.get(key), _cp.get(key)
        return (a + b - c) if None not in (a, b, c) else None

    ttm_rev, ttm_np = _ttm("revenue"), _ttm("netProfit")
    ttm_margin = (ttm_np / ttm_rev * 100) if (ttm_rev and ttm_np) else None  # 归母 ÷ 营收
    ttm_roe = (_cum.get("roe") or 0) * 400 / last_q if _cum.get("roe") else None

    # 估值区间（PE/PB 的历史最低~最高）
    _pe = [v for v in (d.get("prices", {}).get("pe") or []) if v]
    _pb = [v for v in (d.get("prices", {}).get("pb") or []) if v]
    # 当前值取 metrics CSV 的（与表头同一天）；区间仍用估值序列的历史
    pe_now = _pct(meta.get("PE_TTM")) or (_pe[-1] if _pe else None)
    pb_now = _pct(meta.get("PB_MRQ")) or (_pb[-1] if _pb else None)
    pe_lo, pe_hi = (min(_pe) if _pe else None, max(_pe) if _pe else None)
    pb_lo, pb_hi = (min(_pb) if _pb else None, max(_pb) if _pb else None)


    h = []
    h.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">')
    h.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    h.append(f'<title>{name} 基本面</title><style>{CSS}</style></head><body><div class="page">')

    # 抬头
    h.append('<header>')
    h.append(f'<div class="title">{html.escape(name)}<span class="code">{code}</span>'
             f'<span class="ind">{html.escape(str(ind))}</span></div>')
    chg = meta.get("涨跌幅")
    h.append('<div class="prow">')
    h.append(f'<span class="px">{last_px}</span>')
    if chg not in (None, ""):
        try:
            c = float(chg)
            h.append(f'<span class="chg {"up" if c > 0 else "down"}">{c:+.2f}%</span>')
        except ValueError:
            pass
    _fy_y, _fy_a = full_year_dividend(div_by_year)
    # 用**完整年度**分红 ÷ 现价，不用滚动 12 个月：一年两次分红的过渡期里，
    # 滚动窗口会挤进三次派息（0.1414+0.1689+0.1511=0.4614 → 5.43%，高估）。
    yld_now = (_fy_a / float(last_px) * 100) if (_fy_a and last_px) else None
    if yld_now is None and d.get("yld_now"):
        yld_now = d["yld_now"]          # 港股：源直接给当前股息率
    h.append(f'<span class="meta">'
             + (f'股息率 <b style="color:var(--acc)">{yld_now:.2f}%</b>'
                f'<span style="color:var(--dim2)">（{_fy_y} 年度 ÷ 现价）</span> ｜ '
                if yld_now else '')
             + f'PE 分位 {pe_pct} ｜ PB 分位 {pb_pct} ｜ '
             f'最新数据 {latest["statDate"] if latest else "—"}'
             f'（公告 {latest["pubDate"] if latest else "—"}）</span>')
    h.append('</div></header>')

    # ── 决策速览 ──
    # **必须走 verdict.decide()**，不能在这里再写一份。
    # 这里曾经是内联的四信号逻辑，而 verdict.py 后来加了第 5 信号「最新季报」——
    # 结果榜单把平安判成「中性」，个股页还写着「值得进一步看」，两套逻辑跑偏。
    # 现在唯一实现只有 verdict.decide()，页面只负责渲染。
    import verdict as _vd
    _div_by_year = yearly_dividends(d["dividends"])
    sig, _verdict, _detail = _vd.decide(qs, _div_by_year, yr_avg, meta)
    _pp = _detail.get("pe_pct")
    _pbp = _detail.get("pb_pct")
    yld_now = _detail.get("yld") if _detail.get("yld") is not None else yld_now
    _yld_med = _detail.get("yld_med")

    if sig:
        _c = {"值得看": ("ok", "多项偏低位 / 改善 —— 值得进一步看"),
              "高位": ("bad", "多项偏高位 / 走弱 —— 不是好的买入位置"),
              "中性": ("", "信号混合 —— 没有明显方向")}
        _vk, concl = _c.get(_verdict, ("", "信号混合 —— 没有明显方向"))

        # 每张卡片：一个大数字 + 一行小字。大数字取自 _detail 的原始值，
        # 小字补上「另一个维度」，避免和数字重复。
        _d = _detail.get("roe_delta")
        _roe = _detail.get("roe_now")
        _yld = _detail.get("yld")
        _med = _detail.get("yld_med")
        _jw = _detail.get("jw")
        _javg = _detail.get("j")
        _qy = _detail.get("q_yoy")
        _lastq = ""
        for _n, _st, _val, _note, *_rest in sig:
            if _n == "最新季报":
                _lastq = _val
                break

        def _card(name, big, sub, st):
            if big is None:
                big, st = "—", "na"
            return (f'<div class="stat {st}"><div class="k">'
                    f'<span class="dot {st}"></span>{name}</div>'
                    f'<div class="v">{big}</div>'
                    f'<div class="n">{sub}</div></div>')

        _cards = []
        _cards.append(_card("估值",
                            f'{_pp:.0f}%' if _pp is not None else None,
                            f'PE 分位' + (f'　PB {_pbp:.0f}%' if _pbp is not None else ''),
                            next((s[1] for s in sig if s[0] == "估值"), "")))
        _cards.append(_card("盈利",
                            f'{_d:+.1f}pp' if _d is not None else None,
                            f'ROE {_roe:.2f}%' if _roe is not None else 'ROE 趋势',
                            next((s[1] for s in sig if s[0] == "盈利"), "")))
        _cards.append(_card("现金回报",
                            f'{_yld:.2f}%' if _yld else None,
                            f'历史中位 {_med:.2f}%' if _med else '股息率',
                            next((s[1] for s in sig if s[0] == "现金回报"), "")))
        _cards.append(_card("技术位置",
                            f'{_jw:.0f}' if _jw is not None else None,
                            (f'周J　均 {_javg:.0f}' if _javg is not None else '周J'),
                            next((s[1] for s in sig if s[0] == "技术位置"), "")))
        _cards.append(_card("最新季报",
                            f'{_qy * 100:+.0f}%' if _qy is not None else None,
                            _lastq.replace("单季", "单季").split("（")[0] or "季报同比",
                            next((s[1] for s in sig if s[0] == "最新季报"), "")))
        h.append('<div class="stats">' + "".join(_cards) + '</div>')
        h.append(f'<div class="sum {_vk}"><span class="mk"></span>{concl}</div>')
        h.append('<div class="fn">这些都是<b>位置描述</b>，不是预测。'
                 '独立回测显示技术类信号对未来收益无预测力，请只当作'
                 '"现在处在什么位置"来看。</div>')

    # ── 技术面 / TTM：各一行，不再占独立区块 ──
    # 技术面本来就每天更新、财务是季报节奏，两者必须分开标日期，
    # 否则读者会以为整页是同一天的数据。
    _bits = []
    if jd is not None:
        _bits.append(f'日J {jd:.0f}')
    if jw is not None:
        _bits.append(f'周J {jw:.0f}')
    if jm is not None:
        _bits.append(f'月J {jm:.0f}')
    if _pp is not None:
        _bits.append(f'PE 分位 {_pp:.1f}%')
    if _pbp is not None:
        _bits.append(f'PB 分位 {_pbp:.1f}%')
    _ma = _f("MA20")
    if _ma is not None:
        _bits.append(f'MA20 {_ma:.2f}')
    _vr = _f("量比")
    if _vr is not None:
        _bits.append(f'量比 {_vr:.2f}')
    _md = (meta.get("数据日期") or "")[:10]
    if meta.get("_no_metrics"):
        h.append('<p class="strip"><span class="strip-k">技术面</span>'
                 '<span style="color:var(--dim2)">未纳入日更采集 —— '
                 '本页只有财务数据（年度串联、ROE 趋势完整可用），'
                 '估值分位与技术位置待当日行情补齐</span></p>')
    if _bits:
        h.append(f'<p class="strip"><span class="strip-k">技术面</span>'
                 f'{"　·　".join(_bits)}'
                 + (f'<span class="strip-d">{_md}</span>' if _md else '') + '</p>')

    _tb = []
    if ttm_rev:
        _tb.append(f'营收 {ttm_rev / 1e8:,.0f} 亿')
    if ttm_np:
        _tb.append(f'归母净利 {ttm_np / 1e8:,.0f} 亿')
    if ttm_margin is not None:
        _tb.append(f'净利率 {ttm_margin:.1f}%')
    if ttm_roe is not None:
        _tb.append(f'ROE(年化) {ttm_roe:.2f}%')
    if _tb:
        h.append(f'<p class="strip"><span class="strip-k">TTM</span>'
                 f'{"　·　".join(_tb)}'
                 + (f'<span class="strip-d">财务截至 '
                    f'{latest["statDate"] if latest else "—"}</span>') + '</p>')

    # ── 年度串联：业绩 ↔ 分红 ↔ 回购 ──
    _ar = annual_rows(d, years=10)
    if len(_ar) >= 3:
        h.append('<h2>年度：业绩 ↔ 分红 ↔ 回购'
                 '<span class="unit">同一财务年度一行</span></h2>')
        h.append('<table><thead><tr><th class="k">年度</th><th>营收(亿)</th>'
                 '<th>归母净利(亿)</th><th>ROE</th><th>每股分红</th>'
                 '<th>分红率</th><th>股息率</th><th>回购</th>'
                 '</tr></thead><tbody>')
        for x in _ar:
            mark = "" if x["complete"] else "*"
            cls = ' class="na"' if not x["complete"] else ""

            def _p(v, nd=1, sign=False):
                if v is None:
                    return '<td class="na">—</td>'
                c = ""
                if sign:
                    c = ' class="up"' if v > 0 else ' class="down"'
                return f'<td{c}>{v:+.{nd}f}%</td>' if sign else f'<td>{v:.{nd}f}%</td>'

            bk = (f'{x["bk_cancel"]} / {x["bk_prog"]}'
                  if (x["bk_cancel"] or x["bk_prog"]) else "—")
            warn = (f'<span title="{html.escape(x["flag"])}" '
                    f'style="color:var(--warn);cursor:help">⚠</span>') if x.get("flag") else ""
            h.append(f'<tr><td class="k">{x["year"]}{mark} {warn}</td>')
            h.append(f'<td>{x["revenue"]:,.0f}</td>' if x["revenue"] else '<td class="na">—</td>')
            h.append(f'<td>{x["netProfit"]:,.0f}</td>' if x["netProfit"] else '<td class="na">—</td>')
            h.append(_p(x["roe"], nd=2))
            h.append(f'<td>{x["dps"]:.4f}</td>' if x["dps"] else '<td class="na">—</td>')
            h.append(_p(x["payout"], nd=0))
            h.append(_p(x["yld"], nd=2))
            h.append(f'<td class="{"na" if not (x["bk_cancel"] or x["bk_prog"]) else ""}">{bk}</td></tr>')
        h.append('</tbody>')

        # 趋势行：把 10 行数字扫成一句可读的方向。
        # 折线已经在决策卡里了，这里只给文字结论 —— 读者能自己数着复核。
        _cv = [x for x in _ar if x["complete"]]
        if len(_cv) >= 3:
            _pay = [x["payout"] for x in _cv if x["payout"] is not None]
            _pay_txt = ""
            if _pay:
                _spread = max(_pay) - min(_pay)
                _pay_txt = (f'稳定在 {_pay[-1]:.0f}%' if _spread < 2
                            else f'{_pay[0]:.0f}% → {_pay[-1]:.0f}%')
            h.append('<tfoot><tr class="trow"><td class="k">趋势</td>'
                     f'<td>{trend_word([x["revenue"] for x in _cv])}</td>'
                     f'<td>{trend_word([x["netProfit"] for x in _cv])}</td>'
                     f'<td>{trend_word([x["roe"] for x in _cv], tol=0.05)}</td>'
                     f'<td>{trend_word([x["dps"] for x in _cv], tol=0.0001)}</td>'
                     f'<td>{_pay_txt}</td>'
                     f'<td>{trend_word([x["yld"] for x in _cv], tol=0.01)}</td>'
                     f'<td>{(str(sum(x["bk_cancel"] + x["bk_prog"] for x in _ar)) + " 条") if any(x["bk_cancel"] + x["bk_prog"] for x in _ar) else "—"}</td>'
                     '</tr></tfoot>')
        h.append('</table>')
        # 一句话观察
        _c = [x for x in _ar if x["complete"] and x["payout"] is not None]
        obs = []
        if len(_c) >= 3:
            r0, r1 = _c[0]["roe"], _c[-1]["roe"]
            if r0 is not None and r1 is not None:
                obs.append(f'ROE {r0:.1f}% → {r1:.1f}%（{r1 - r0:+.1f}pp）')
            p0, p1 = _c[0]["payout"], _c[-1]["payout"]
            obs.append(f'分红率稳定在 {p1:.0f}%' if abs(p1 - p0) < 1
                       else f'分红率 {p0:.0f}% → {p1:.0f}%')
        _tot = sum(x["bk_cancel"] + x["bk_prog"] for x in _ar)
        obs.append(f'回购公告 {_tot} 条' if _tot else '无回购')
        _bad = [x for x in _ar if x.get("flag")]
        if _bad:
            obs.append("⚠ " + "；".join(
                f'{x["year"]} {x["flag"]}' for x in _bad))
        # 同比与净利率折起来：净利率与 ROE 高度相关，同比看趋势用不着每年两列
        h.append('<details><summary>展开营收/净利同比与净利率</summary>')
        h.append('<table><thead><tr><th class="k">年度</th><th>营收同比</th>'
                 '<th>净利同比</th><th>净利率</th></tr></thead><tbody>')
        for x in _ar:
            mk = "" if x["complete"] else "*"
            h.append(f'<tr><td class="k">{x["year"]}{mk}</td>')
            h.append(_p(x["yoyRev"], sign=True))
            h.append(_p(x["yoyNi"], sign=True))
            h.append(_p(x["margin"]))
            h.append('</tr>')
        h.append('</tbody></table></details>')
        _hk_note = (
            '⚠ <b>港股的分红历史源不提供可靠序列</b>：`DPS_HKD` 与 `DIVIDEND_RATE` '
            '在所有报告期都是同一个快照值，`DIVI_RATIO` 在不同股票上含义不一致'
            '（新华保险 2017 给 1.63、南山铝业 2021 给 21390519）。'
            '所以「每股分红 / 分红率 / 股息率」三列对港股一律显示「—」，'
            '这是拿不到，不是没有。当前股息率见页头。<br>'
            if d.get("market") == "HK" else '')
        h.append(f'<div class="fn">{"　·　".join(obs)}</div>')
        h.append('<details><summary>这一年的数字怎么算的</summary>'
                 '<div class="fn" style="margin-top:8px">' + _hk_note +
                 '业绩取年报（年内累计）。每股分红按 <b>财务年度</b> 归集（年报+中报相加），'
                 '不按除权日。分红率 = 每股分红 × 总股本 ÷ 归母净利；'
                 '股息率 = 每股分红 ÷ 当年日均价。回购按公告日年份归集，'
                 '格式为「注销式 / 激励式」—— 只有注销式才真正提高每股价值。<br>'
                 '⚠ 回购列只覆盖公告接口可取到的最近约 300 条公告（通常 2~4 年），'
                 '更早年份显示「—」<b>不代表当年没有回购</b>。'
                 '* 该年度未走完，分红率与股息率不计算。</div></details>')

    # ── 格雷厄姆「防御型投资者」7 条标准（第 14 章原文口径）──
    try:
        import graham as _g
        _bal = []
        _bp = os.path.join(BASE_DIR, "_fin_cache", "balance.csv")
        if os.path.exists(_bp):
            for _r in _read_csv(_bp):
                if str(_r.get("code", "")).zfill(6) == code:
                    _bal.append(_r)
        _grows, _gsum = _g.check(d.get("quarters") or [], d.get("dividends") or [],
                                 _bal, meta, meta.get("行业"))
        if _grows:
            h.append('<h2>格雷厄姆七条标准'
                     '<span class="unit">《聪明的投资者》第 14 章 · '
                     f'{_gsum["verdict"]}</span></h2>')
            h.append('<table class="gtab"><thead><tr><th class="k">标准</th>'
                     '<th>实际 ／ 门槛</th><th>判定</th></tr></thead><tbody>')
            for _r in _grows:
                # pass 用 pos（绿），fail 用 neg（红）。
                # 不能用 up/down —— 那两个是「红涨绿跌」的行情语义，
                # 用在「通过/不通过」上会让「通过」显示成红色。
                _cls = {"pass": "pos", "fail": "neg", "na": "na",
                        "nodata": "na"}.get(_r["status"], "")
                _txt = _g.label(_r["status"])
                _extra = ""
                if _r["status"] in ("na", "nodata") and _r.get("note"):
                    _extra = f'<span class="gnote">{_r["note"]}</span>'
                h.append(f'<tr><td class="k">{_r["no"]}. {_r["name"]}</td>'
                         f'<td><span class="gn">{_r["actual"]}</span>'
                         f'<span class="gth">{_r["threshold"]}</span>{_extra}</td>'
                         f'<td class="{_cls}">{_txt}</td></tr>')
            h.append('</tbody></table>')
            _cnt = _gsum
            h.append('<div class="fn">'
                     f'通过 {_cnt["pass"]}、不通过 {_cnt["fail"]}、'
                     f'不适用 {_cnt["na"]}、数据不足 {_cnt["nodata"]}'
                     f'（共 7 项）</div>')
            h.append('<details><summary>七条的门槛与判定口径</summary>'
                     '<div class="fn" style="margin-top:8px">'
                     '取自原书第 14 章。规模门槛按 1 亿美元 × 7.2 折人民币（<b>这是假设</b>）。'
                     '「财务状况」工业股要求流动比 ≥ 2 <b>且</b>长期债务 ≤ 营运资本；'
                     '公用事业改为负债 ≤ 2 倍账面股权。<br>'
                     '<b>金融业该条判「不适用」</b> —— 银行没有流动资产/流动负债科目，'
                     '格雷厄姆在《证券分析》里另有标准，判成「不合格」是错的。<br>'
                     '<b>「数据不足」与「不通过」严格区分</b>：分红记录不足 20 年时，'
                     '上市也不足 20 年判不适用；上市够久但我们的数据没回溯那么远，判数据不足。'
                     '</div></details>')
    except Exception as _exc:
        print(f"[warn] 格雷厄姆判定失败 {code}: {type(_exc).__name__}: {_exc}")

    # ── 季度表（折叠：需要的人再展开，不占首屏）──
    h.append('<details><summary>展开单季净利（按年 × 季度）</summary>')
    h.append('<table><thead><tr><th class="k">年度</th><th>Q1</th><th>Q2</th><th>Q3</th>'
             '<th>Q4</th><th>全年</th><th>同比</th></tr></thead><tbody>')
    for y in reversed(years_all[-8:]):
        rowq = [q for q in qs if q["year"] == y]
        lq = max(x["quarter"] for x in rowq) if rowq else None
        h.append(f'<tr><td class="k">{y}</td>')
        for k in (1, 2, 3, 4):
            node = next((x for x in rowq if x["quarter"] == k), None)
            v = node.get("qNetProfit") if node else None
            h.append(f'<td>{v / 1e8:,.0f}</td>' if v else '<td class="na">—</td>')
        cum = next((x["netProfit"] for x in rowq if x["quarter"] == lq), None)
        pcum = next((x["netProfit"] for x in qs if x["year"] == y - 1
                     and x["quarter"] == lq), None)
        mark = "*" if lq != 4 else ""
        h.append(f'<td><b>{cum / 1e8:,.0f}{mark}</b></td>' if cum
                 else '<td class="na">—</td>')
        h.append(yoy_cell(cum, pcum))
        h.append('</tr>')
    h.append('</tbody></table>')
    h.append('<div class="fn">单季 = 累计差分。* 该年度未走完，为已披露累计值；'
             '同比为同期累计口径。</div></details>')

    # ── 折叠：全部历史季度（想深挖的人再展开，默认不占地方）──
    h.append(f'<details><summary>展开全部 {len(qs)} 个季度的明细</summary>')
    h.append('<table><thead><tr><th class="k">报告期</th><th>公告日</th>'
             '<th>营收</th><th>净利</th><th>净利率</th><th>ROE</th></tr></thead><tbody>')
    for q in reversed(qs):
        h.append(f'<tr><td class="k">{q["year"]}Q{q["quarter"]}</td>'
                 f'<td>{q.get("pubDate") or "—"}</td>'
                 f'<td>{(q.get("revenue") or 0) / 1e8:,.0f}</td>'
                 f'<td>{(q.get("netProfit") or 0) / 1e8:,.0f}</td>'
                 f'<td>{(q.get("npMargin") or 0) * 100:.1f}%</td>'
                 f'<td>{(q.get("roe") or 0) * 100:.2f}%</td></tr>')
    h.append('</tbody></table></details>')

    # ── 趋势图（折叠）──
    cq = qs[-20:]
    labels = [quarter_label(q) for q in cq]
    h.append('<details><summary>展开单季净利趋势（近 20 季）</summary>')
    h.append(bar_chart(labels, [("单季净利", "#7dd3fc",
                                 [(q.get("qNetProfit") or 0) / 1e8
                                  if q.get("qNetProfit") else None for q in cq])],
                       height=180, unit="亿"))
    h.append('</details>')

    # ── 公式说明：每个数字怎么来的，可逐条核对 ──
    FORMULAS = [
        ("营业收入", "利润表「营业收入」，年内累计（字段 TOTALOPERATEREVE）"),
        ("归母净利", "归属于母公司股东的净利润，年内累计（PARENTNETPROFIT，不含少数股东损益）"),
        ("ROE（加权）", "归母净利 ÷ 加权平均净资产（ROEJQ，官方披露口径）"),
        ("净利率", "归母净利 ÷ 营业收入（本表自算；数据源自带的 XSJLL 含少数股东，不用）"),
        ("每股分红", "该财务年度的每股现金分红合计（税前）。一年两次分红的年份把中报 + 年报两笔相加"),
        ("分红率", "每股分红 × 总股本 ÷ 归母净利"),
        ("股息率（年度）", "每股分红 ÷ <b>当年日均价</b>。不用年末价：年末那天只是抽样点"),
        ("股息率（表头）", "最近一个<b>完整年度</b>的每股分红 ÷ 当前价。不用滚动 12 个月："
                     "一年两次分红的过渡期里会挤进三次派息，高估"),
        ("TTM", "最新累计 + 上年全年 − 上年同期累计。这样才与年报可比"),
        ("PE / PB 分位", "当前 PE_TTM / PB_MRQ 在有数据以来的分位（来自仓库每日 metrics CSV）"),
        ("日/周/月 J", "KDJ 的 J 值（N=9, M1=3, M2=3），周/月由日线聚合后计算。"
                  "J 无上下界，可小于 0 或大于 100"),
        ("数据来源", "东财 datacenter：RPT_F10_FINANCE_MAINFINADATA（季频指标）、"
                 "RPT_SHAREBONUS_DET（分红）、RPT_VALUEANALYSIS_DET（估值序列）"),
    ]
    h.append('<details><summary>各项指标怎么算的（口径与数据来源）</summary>')
    h.append('<table><thead><tr><th class="k">指标</th>'
             '<th style="text-align:left">公式 / 口径</th></tr></thead><tbody>')
    for nm, fm in FORMULAS:
        h.append(f'<tr><td class="k">{nm}</td>'
                 f'<td style="text-align:left;white-space:normal;color:var(--dim)">{fm}</td></tr>')
    h.append('</tbody></table></details>')

    h.append(f'<div class="ft">数据：东财 datacenter（季频指标 / 分红）· 本地估值序列<br>'
             f'生成于 {datetime.now():%Y-%m-%d %H:%M}　·　仅供参考，不构成投资建议</div>')
    h.append('</div></body></html>')

    out = d.get("_out") or args.out
    if not out:
        raise SystemExit("缺少输出路径（--out）")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    # 折叠项保持单列。
    # 试过用正则把相邻 details 包成两列网格，结果 `.*?` 跨过了整张格雷厄姆表，
    # 把表格当成 grid 的第二个格子挤到右半边 —— 页面直接坏了。
    # 两列只是好看一点，不值得用脆弱的后处理换正确性，所以撤掉了。
    _html = "\n".join(h)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(_html)
    if not getattr(args, "quiet", False):
        print(f"已生成 {out}  ({len(''.join(h)) / 1024:.0f}KB)")
    return 0




# ─────────────────────────── 批量模式 ───────────────────────────
def _read_csv(path):
    import csv
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _num(v):
    try:
        return float(v) if v not in (None, "", "nan", "None") else None
    except (TypeError, ValueError):
        return None


def load_cache_payloads(cache_dir):
    """把 _fin_cache 的 CSV 还原成「每只一个 payload」。"""
    payloads = {}
    for r in _read_csv(os.path.join(cache_dir, "fundamentals.csv")):
        code = str(r["code"]).zfill(6)
        pl = payloads.setdefault(code, dict(code=code, quarters=[], dividends=[],
                                            predicts=[], yr_avg={}))
        pl["quarters"].append(dict(
            year=int(r["year"]), quarter=int(r["quarter"]),
            statDate=r.get("statDate") or "", pubDate=r.get("pubDate") or "",
            reportName=r.get("reportName") or "",
            revenue=_num(r.get("revenue")), netProfit=_num(r.get("netProfit")),
            roe=_num(r.get("roe")), npMargin=_num(r.get("npMargin")),
            gpMargin=_num(r.get("gpMargin")),
            liabilityToAsset=_num(r.get("liabilityToAsset")),
            cfoToNp=_num(r.get("cfoToNp")), totalShare=_num(r.get("totalShare")),
            yoyRev=_num(r.get("yoyRev")), yoyNi=_num(r.get("yoyNi")),
            qRevYoy=_num(r.get("qRevYoy")), qNpYoy=_num(r.get("qNpYoy")),
            nim=_num(r.get("nim")), npl=_num(r.get("npl")),
            provision=_num(r.get("provision")),
        ))
    for r in _read_csv(os.path.join(cache_dir, "dividends.csv")):
        pl = payloads.get(str(r["code"]).zfill(6))
        if pl is not None:
            pl["dividends"].append(dict(reportDate=r.get("reportDate") or "",
                                        cashPs=_num(r.get("cashPs")) or 0.0,
                                        exDate=r.get("exDate") or "",
                                        yieldAtEx=_num(r.get("yieldAtEx"))))
    for r in _read_csv(os.path.join(cache_dir, "predicts.csv")):
        pl = payloads.get(str(r["code"]).zfill(6))
        if pl is not None:
            pl["predicts"].append(dict(reportDate=r.get("reportDate") or "",
                                       notice=r.get("notice") or "",
                                       type=r.get("type") or "",
                                       content=r.get("content") or ""))
    for r in _read_csv(os.path.join(cache_dir, "prices_year.csv")):
        pl = payloads.get(str(r["code"]).zfill(6))
        if pl is not None:
            pl["yr_avg"][str(r["year"])] = _num(r.get("avgClose"))
    for r in _read_csv(os.path.join(cache_dir, "valu.csv")):
        pl = payloads.get(str(r["code"]).zfill(6))
        if pl is not None:
            pl["pe_range"] = (_num(r.get("peLo")), _num(r.get("peHi")))
            pl["pb_range"] = (_num(r.get("pbLo")), _num(r.get("pbHi")))
    for r in _read_csv(os.path.join(cache_dir, "buybacks.csv")):
        pl = payloads.get(str(r["code"]).zfill(6))
        if pl is not None:
            pl.setdefault("buybacks", []).append(dict(
                date=r.get("date") or "", title=r.get("title") or "",
                kind=r.get("kind") or ""))
    for pl in payloads.values():
        pl["quarters"].sort(key=lambda x: (x["year"], x["quarter"]))
    return payloads


def latest_metrics_path(out_dir="output"):
    """最新的 metrics CSV。注意它在 output/{日期}/ 子目录里，不是 output/ 根下。"""
    d = os.path.join(BASE_DIR, out_dir)
    if not os.path.isdir(d):
        return None
    for name in sorted(os.listdir(d), reverse=True):
        sub = os.path.join(d, name)
        if not os.path.isdir(sub):
            continue
        cands = sorted(x for x in os.listdir(sub)
                       if x.startswith("metrics_") and x.endswith(".csv"))
        if cands:
            return os.path.join(sub, cands[-1])
    return None


def _summary_row(pl, meta):
    """索引页一行：最新季 + 三个红灯。"""
    qs = pl["quarters"]
    latest = qs[-1] if qs else {}
    div_by_year = yearly_dividends(pl["dividends"])
    fy_year, fy_amt = full_year_dividend(div_by_year)
    px = _num(meta.get("最新价"))
    yld = (fy_amt / px * 100) if (fy_amt and px) else None
    roe_pts = [(q["year"], q["roe"]) for q in qs if q["quarter"] == 4 and q.get("roe")][-4:]
    roe_delta = (roe_pts[-1][1] - roe_pts[0][1]) * 100 if len(roe_pts) >= 2 else None
    pe_pct, jw = _num(meta.get("PE历史分位%")), _num(meta.get("周线J"))
    red = 0
    if pe_pct is not None and pe_pct > 70:
        red += 1
    if roe_delta is not None and roe_delta < -1:
        red += 1
    if jw is not None and jw > 80:
        red += 1
    return dict(code=pl["code"], name=meta.get("名称") or pl["code"],
                ind=meta.get("行业") or "—", px=px, pe_pct=pe_pct,
                pb_pct=_num(meta.get("PB历史分位%")), yld=yld,
                roe=(latest.get("roe") or 0) * 100 if latest.get("roe") else None,
                roe_delta=roe_delta, jw=jw, np_yoy=latest.get("qNpYoy"), red=red,
                statDate=latest.get("statDate") or "")


def _write_index(out_dir, rows, metrics):
    rows = sorted(rows, key=lambda r: (-r["red"], -(r["yld"] or 0)))
    date = os.path.basename(metrics).replace("metrics_", "").replace(".csv", "")
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>基本面榜单 {date}</title><style>{CSS}</style></head><body>',
         '<div class="page">',
         f'<header><div class="title">基本面榜单<span class="code">{len(rows)} 只</span></div>'
         f'<div class="prow"><span class="meta">财务 _fin_cache ｜ 技术面与估值分位 {date}'
         f' ｜ 点名字进个股页</span></div></header>',
         # 站内导航 —— 之前没有入口，用户只能靠记住文件名
         '<div class="nav">'
         '<a href="榜单.html">四信号分组榜单</a>'
         '<a href="格雷厄姆.html">格雷厄姆七条</a>'
         '<a href="AH比价.html">A/H 比价</a>'
         '<a href="定投回测.html">定投回测</a>'
         '<a href="港股名单.html">港股 100 只</a>'
         '</div>',
         '<h2>按红灯数排序<span class="unit">红 = 估值偏高 / ROE 走弱 / 周J 超买</span></h2>',
         '<div class="scroll"><table><thead><tr><th class="k">标的</th><th>行业</th>'
         '<th>最新价</th><th>PE分位</th><th>PB分位</th><th>股息率</th><th>ROE</th>'
         '<th>ROE 4年</th><th>净利同比</th><th>周J</th><th>灯</th></tr></thead><tbody>']
    for r in rows:
        dots = ("<span class='neg'>●</span>" * r["red"]
                + "<span class='na'>○</span>" * (3 - r["red"]))
        pe_txt = f'{r["pe_pct"]:.0f}%' if r["pe_pct"] is not None else "—"
        pb_txt = f'{r["pb_pct"]:.0f}%' if r["pb_pct"] is not None else "—"
        yld_txt = f'{r["yld"]:.2f}%' if r["yld"] else "—"
        roe_txt = f'{r["roe"]:.2f}%' if r["roe"] else "—"
        d_txt = f'{r["roe_delta"]:+.2f}pp' if r["roe_delta"] is not None else "—"
        d_cls = "down" if (r["roe_delta"] or 0) < 0 else "up"
        n_txt = f'{r["np_yoy"]:+.1f}%' if r["np_yoy"] is not None else "—"
        j_txt = f'{r["jw"]:.0f}' if r["jw"] is not None else "—"
        h.append(
            f'<tr><td class="k"><a href="{r["code"]}.html" style="color:var(--blue);'
            f'text-decoration:none">{html.escape(str(r["name"]))}'
            f'<span style="color:var(--dim2);font-size:11px"> {r["code"]}</span></a></td>'
            f'<td style="text-align:left;color:var(--dim);font-size:12px">'
            f'{html.escape(str(r["ind"]))}</td>'
            f'<td>{r["px"] if r["px"] else "—"}</td><td>{pe_txt}</td><td>{pb_txt}</td>'
            f'<td>{yld_txt}</td><td>{roe_txt}</td><td class="{d_cls}">{d_txt}</td>'
            f'<td>{n_txt}</td><td>{j_txt}</td><td>{dots}</td></tr>')
    h.append('</tbody></table></div>')
    h.append('<div class="fn">红灯数只是位置描述，不是买卖建议。'
             '排序 = 红灯数降序、同灯数按股息率降序。</div>')
    h.append(f'<div class="ft">生成于 {datetime.now():%Y-%m-%d %H:%M}　·　'
             f'仅供参考，不构成投资建议</div></div></body></html>')
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))


def build_all_simple(metrics=None, out_dir=None) -> int:
    """给 build_pages 调用的入口：不用构造 argv。"""
    ns = argparse.Namespace(json=None, out=out_dir, metrics=metrics, all=True, quiet=True)
    return build_all(ns)


def build_all(args) -> int:
    """池子里每只一页 + 索引页。"""
    cache_dir = os.path.join(BASE_DIR, "_fin_cache")
    payloads = load_cache_payloads(cache_dir)
    if not payloads:
        print("_fin_cache 为空，先跑 fetch_fundamentals.py --init")
        return 0

    metrics = args.metrics or latest_metrics_path()
    if not metrics or not os.path.exists(metrics):
        print("找不到 metrics CSV，跳过基本面页面")
        return 0

    import csv
    meta_by_code = {}
    with open(metrics, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            meta_by_code[str(row.get("代码", "")).zfill(6)] = row

    # 名称/行业以 _fin_cache/codes.csv 为准：metrics CSV 的行业列在 top100 抓取
    # 降级（只剩代码+名称的历史名单）时会是空的，页面不该跟着一起空。
    for r in _read_csv(os.path.join(cache_dir, "codes.csv")):
        code = str(r.get("code", "")).zfill(6)
        if code in meta_by_code:
            if (r.get("name") or "").strip():
                meta_by_code[code]["名称"] = r["name"].strip()
            if (r.get("industry") or "").strip():
                meta_by_code[code]["行业"] = r["industry"].strip()

    out_dir = args.out or os.path.join(BASE_DIR, "docs", "基本面")
    os.makedirs(out_dir, exist_ok=True)

    # ── 港股：从 hk_fundamentals.csv 适配成同一形状（字段映射在 hk_payload.py）──
    try:
        import hk_payload
        hk = hk_payload.load_hk_payloads(cache_dir)
        if hk:
            payloads.update(hk)
            print(f"  港股 payload {len(hk)} 只")
    except Exception as exc:
        print(f"[warn] 港股 payload 加载失败：{type(exc).__name__}: {exc}")

    # ── 没有当日 metrics 的股票：用选股缓存兜底 ──
    # 策展池扩到 300 只后，日更还没覆盖到它们（run_daily 只抓 watchlist 展开清单，
    # 且盘中会拒绝采集）。这些股票先建「只有财务数据」的页面 ——
    # 年度串联表和 ROE 趋势是完整可用的，只是缺技术面与估值分位。
    # 页面会明确标注，不假装数据齐全。
    fallback = {}
    sel = os.path.join(BASE_DIR, "_pool_cache", "selected_a.csv")
    if os.path.exists(sel):
        for r in _read_csv(sel):
            c = str(r.get("code", "")).zfill(6)
            fallback[c] = {"代码": c, "名称": (r.get("name") or "").strip(),
                           "行业": (r.get("ind") or "").strip(),
                           "PE_TTM": r.get("pe"), "PB_MRQ": r.get("pb"),
                           "_no_metrics": True}
    # 港股：代码 5 位，不在 A 股 metrics 里 —— 用选股缓存 + payload 自身的 PE/PB 兜底
    sel_hk = os.path.join(BASE_DIR, "_pool_cache", "selected_hk.csv")
    if os.path.exists(sel_hk):
        for r in _read_csv(sel_hk):
            c = str(r.get("code", "")).zfill(5)
            if c in payloads and c not in meta_by_code:
                meta_by_code[c] = {"代码": c, "名称": (r.get("name") or "").strip(),
                                   "行业": (r.get("ind") or "").strip(),
                                   "PE_TTM": r.get("pe"), "PB_MRQ": r.get("pb"),
                                   "市场": "港股", "_no_metrics": True}

    filled = 0
    for c, m in fallback.items():
        if c in payloads and c not in meta_by_code:
            meta_by_code[c] = m
            filled += 1
    if filled:
        print(f"  {filled} 只无当日 metrics，用选股缓存兜底（页面会标注）")

    codes = sorted(c for c in meta_by_code if c in payloads)
    summary, ok, fail = [], 0, 0
    for code in codes:
        pl = payloads[code]
        pl["_meta"] = meta_by_code[code]
        pl["_out"] = os.path.join(out_dir, f"{code}.html")
        try:
            main(["--quiet"], payload=pl)
            ok += 1
            summary.append(_summary_row(pl, meta_by_code[code]))
        except Exception as exc:
            fail += 1
            print(f"  {code} 渲染失败 {type(exc).__name__}: {exc}")
        finally:
            pl.pop("_meta", None)
            pl.pop("_out", None)

    _write_index(out_dir, summary, metrics)
    print(f"基本面页面：{ok} 只成功、{fail} 只失败 → {out_dir}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

