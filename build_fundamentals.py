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
*{box-sizing:border-box;-webkit-text-size-adjust:100%}
body{margin:0;background:#fff;color:#101828;
  font:14px/1.5 -apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif}
.page{max-width:960px;margin:0 auto;padding:20px 16px 40px}

/* 抬头：白底、无渐变、左对齐 */
header{border-bottom:2px solid #101828;padding-bottom:12px;margin-bottom:22px}
.title{font-size:22px;font-weight:700;letter-spacing:-.2px}
.title .code{font-size:14px;color:#667085;font-weight:400;margin-left:6px}
.title .ind{font-size:12px;background:#f2f4f7;color:#475467;padding:2px 7px;
  border-radius:4px;margin-left:8px;font-weight:400;vertical-align:2px}
.prow{display:flex;align-items:baseline;gap:10px;margin-top:8px;flex-wrap:wrap}
.prow .px{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.prow .chg{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums}
.prow .meta{font-size:12px;color:#667085}

/* 区块标题 */
h2{font-size:15px;font-weight:700;margin:26px 0 10px;display:flex;
  align-items:baseline;justify-content:space-between;gap:12px}
h2 .unit{font-size:11.5px;color:#98a2b3;font-weight:400}

/* 财务表：行=指标，列=期间 */
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;
  font-size:13.5px}
thead th{position:sticky;top:0;background:#fff;z-index:1}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid #f0f2f5;
  white-space:nowrap}
th.k,td.k{text-align:left;font-weight:500;color:#101828;position:sticky;left:0;
  background:#fff;z-index:2}
thead tr:first-child th{border-bottom:0;font-size:12.5px;font-weight:700;color:#101828}
thead tr.sub th{border-bottom:1.5px solid #d0d5dd;font-size:11px;font-weight:400;
  color:#98a2b3;padding-top:0}
tbody tr.main td{font-weight:600}
tbody tr.subrow td{font-size:12px;color:#667085;padding-top:0;border-bottom:1px solid #f7f8fa}
tbody tr.subrow td.k{color:#98a2b3;padding-left:22px;font-size:11.5px}
tbody tr.gap td{border-bottom:1px solid #eaecf0}
.up{color:#d92d20}
.down{color:#079455}
.na{color:#d0d5dd}
.fn{font-size:11px;color:#98a2b3;margin:8px 0 0;line-height:1.7}

/* 结论 */
.verdict{border-left:3px solid #d0d5dd;padding:10px 0 10px 14px;margin-top:26px;
  font-size:13.5px}
.verdict.warn{border-left-color:#f79009}
.verdict.ok{border-left-color:#12b76a}
.verdict b{display:block;margin-bottom:4px}
.verdict .kv{font-size:12.5px;color:#667085}
.ft{color:#98a2b3;font-size:11px;text-align:center;margin-top:34px;line-height:1.9}
/* 估值位置条 */
.rb{display:flex;align-items:center;gap:10px;padding:7px 0;font-size:13px;
  font-variant-numeric:tabular-nums}
.rb .rl{width:34px;color:#667085;font-size:12px}
.rb .rv{width:52px;font-weight:700;text-align:right}
.rb .rt{position:relative;flex:1;height:5px;background:#eef1f6;border-radius:3px}
.rb .rt i{position:absolute;top:-4px;width:3px;height:13px;background:#d92d20;
  border-radius:2px;transform:translateX(-1.5px)}
.rb .rt em{position:absolute;top:9px;font-size:10px;color:#b0b8c7;font-style:normal}
.rb .rt .rlo{left:0}.rb .rt .rhi{right:0}
.rb .rp{width:66px;text-align:right;color:#667085;font-size:12px}

/* 横向滚动容器：8 年 + TTM 共 11 列，窄屏横滚，指标列固定 */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -2px}
.scroll table{min-width:880px}
/* J 值刻度条 0~100 */
.jrow{display:flex;align-items:center;gap:10px;padding:6px 0;font-size:13px;
  font-variant-numeric:tabular-nums}
.jrow .jl{width:52px;color:#667085;font-size:12px}
.jrow .jv{width:44px;font-weight:700;text-align:right}
.jrow .jt{position:relative;flex:1;height:6px;border-radius:3px;
  background:linear-gradient(90deg,#dbeafe 0%,#dbeafe 28.6%,#f2f4f7 28.6%,
  #f2f4f7 71.4%,#fee4e2 71.4%,#fee4e2 100%)}
.jrow .jt i{position:absolute;top:-5px;width:3px;height:16px;border-radius:2px;
  background:#101828;transform:translateX(-1.5px)}
.jrow .js{width:74px;text-align:right;font-size:11.5px;color:#667085}
/* 当前位置：两列小卡 */
.nowbox{display:grid;grid-template-columns:1fr 1fr;gap:0 26px;
  border-top:1px solid #f2f4f7;border-bottom:1px solid #f2f4f7;padding:10px 0}
.nowbox .col{min-width:0}
.nowbox .kv{display:flex;justify-content:space-between;font-size:12.5px;
  padding:4px 0;border-bottom:1px dotted #f2f4f7;font-variant-numeric:tabular-nums}
.nowbox .kv:last-child{border-bottom:0}
.nowbox .kv span:first-child{color:#667085}
.nowbox .kv b{font-weight:600}
.nowbox .cap{font-size:11px;color:#98a2b3;margin:2px 0 6px}
.link{display:inline-block;margin-top:10px;font-size:12.5px;color:#175cd3;
  text-decoration:none;border-bottom:1px solid #d1e0ff}
/* 公式提示 */
td.k abbr{text-decoration:none;border-bottom:1px dotted #d0d5dd;cursor:help}
td.k abbr i.q{font-style:normal;font-size:9px;color:#b0b8c7;margin-left:3px;
  vertical-align:super}
/* 决策速览 */
.dec{border:1px solid #eaecf0;border-radius:10px;overflow:hidden;margin-bottom:4px}
.dec .row{display:flex;align-items:flex-start;gap:10px;padding:9px 13px;
  border-bottom:1px solid #f7f8fa;font-size:13px}
.dec .row:last-child{border-bottom:0}
.dec .row .n{width:58px;color:#667085;font-size:12px;flex-shrink:0;white-space:nowrap}
.dec .row .dot{width:7px;height:7px;border-radius:50%;margin-top:6px;flex-shrink:0}
.dec .row .dot.ok{background:#12b76a}.dec .row .dot.warn{background:#f79009}
.dec .row .dot.bad{background:#d92d20}
.dec .row .txt{flex:1;line-height:1.6}
.dec .row .txt b{font-weight:600;margin-right:8px}
.dec .row .txt em{font-style:normal;color:#98a2b3;font-size:11.5px;margin-left:4px}
.dec .sum{background:#f7f9fc;padding:11px 13px;font-size:13px;font-weight:600}
/* 同业表 */
table.peer th.me,table.peer td.me{background:#f7f9fc;font-weight:700}
table.peer th,table.peer td{text-align:right;font-size:13px}
/* 估值条容器 */
.valbox{border-top:1px solid #f2f4f7;border-bottom:1px solid #f2f4f7;padding:4px 0}
/* sparkline */
.sp{display:block;margin-left:auto}
td.trend{padding-top:6px;padding-bottom:6px}
/* 折叠 */
details{margin-top:10px;border:1px solid #eaecf0;border-radius:8px;padding:0 12px}
details summary{cursor:pointer;padding:10px 0;font-size:13px;color:#475467;
  list-style:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";color:#98a2b3}
details[open] summary::before{content:"▾ "}
details table{margin-bottom:12px}

"""


# ─────────────────────────── SVG 基础件 ───────────────────────────
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


def sparkline(values, width=62, height=18, color="#2d4a6e"):
    """迷你趋势线：一行指标一个，几乎不占宽度，但趋势一眼可见。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return '<span class="na">—</span>'
    lo, hi = min(vals), max(vals)
    step = width / (len(vals) - 1)
    # 幅度小于 1% 就画水平线：分红率常年 30.0~30.2%，归一化后会变成一条满幅斜线，
    # 看起来像"分红率在大幅上升"，这是纯粹的视觉欺骗。
    if hi == lo or (hi - lo) <= abs(hi) * 0.01:
        pts = " ".join(f"{i * step:.1f},{height / 2:.1f}" for i in range(len(vals)))
    else:
        pts = " ".join(f"{i * step:.1f},{height - (v - lo) / (hi - lo) * height:.1f}"
                       for i, v in enumerate(vals))
    tip = f"{vals[0]:.2f} → {vals[-1]:.2f}"
    return (f'<svg class="sp" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}"><polyline points="{pts}" fill="none" '
            f'stroke="{color}" stroke-width="1.4" stroke-linejoin="round" '
            f'stroke-linecap="round"/><title>{tip}</title></svg>')


def range_bar(label, cur, lo, hi, pct, fmt="{:.2f}"):
    """一条横轴 + 游标：一眼看出当前值在历史区间的位置。"""
    if cur is None or lo is None or hi is None or hi <= lo:
        return ""
    pos = max(0.0, min(100.0, (cur - lo) / (hi - lo) * 100))
    pct_txt = f"分位 {pct:.0f}%" if pct is not None else ""
    return (f'<div class="rb"><span class="rl">{label}</span>'
            f'<span class="rv">{fmt.format(cur)}</span>'
            f'<span class="rt"><i style="left:{pos:.1f}%"></i>'
            f'<em class="rlo">{fmt.format(lo)}</em><em class="rhi">{fmt.format(hi)}</em></span>'
            f'<span class="rp">{pct_txt}</span></div>')


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
                       f'y2="{y:.1f}" stroke="#f2f4f7"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 3.5:.1f}" font-size="10" fill="#9aa3b5" '
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
                       f'fill="#9aa3b5" text-anchor="middle">{html.escape(lab)}</text>')
    if legend:
        lx = width - pad_r
        for name, color in reversed(legend):
            w_ = 20 + len(name) * 10
            lx -= w_
            out.append(f'<rect x="{lx}" y="6" width="8" height="8" fill="{color}" rx="1.5"/>')
            out.append(f'<text x="{lx + 12}" y="13.5" font-size="10" fill="#7a8397">'
                       f'{html.escape(name)}</text>')
    out.append("</svg>")
    return "".join(out)


def line_chart(labels, series, height=210, unit="%", width=780, zero_line=True, legend=None):
    """折线图。series = [(名字, 颜色, [值...]), ...]"""
    pad_l, pad_r, pad_t, pad_b = 52, 12, 14, 34
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [v for _, _, arr in series for v in arr if v is not None]
    if not vals:
        return '<p class="note">（无数据）</p>'
    lo, hi = min(vals), max(vals)
    if zero_line:
        lo, hi = min(0, lo), max(0, hi)
    ticks = _nice_ticks(lo, hi if hi > lo else lo + 1, 4)
    lo, hi = min(ticks), max(ticks)
    n = len(labels)
    step = pw / max(n - 1, 1)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in ticks:
        y = pad_t + ph - (t - lo) / (hi - lo) * ph
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
                   f'stroke="#eef1f6"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 3.5:.1f}" font-size="10" fill="#9aa3b5" '
                   f'text-anchor="end">{t:g}</text>')
    for name, color, arr in series:
        pts = [(pad_l + step * i, pad_t + ph - (v - lo) / (hi - lo) * ph)
               for i, v in enumerate(arr) if v is not None]
        if len(pts) > 1:
            d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8"/>')
        for i, v in enumerate(arr):
            if v is None:
                continue
            x, y = pad_l + step * i, pad_t + ph - (v - lo) / (hi - lo) * ph
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{color}">'
                       f'<title>{html.escape(labels[i])} {name}: {v:.2f}{unit}</title></circle>')
    lab_step = max(1, -(-n // 8))     # 最多 8 个 x 轴标签，避免挤成一团
    for i, lab in enumerate(labels):
        if i % lab_step == 0:
            out.append(f'<text x="{pad_l + step * i:.1f}" y="{height - 12}" font-size="9.5" '
                       f'fill="#9aa3b5" text-anchor="middle">{html.escape(lab)}</text>')
    if legend:
        lx = width - pad_r
        for name, color in reversed(legend):
            w_ = 20 + len(name) * 10
            lx -= w_
            out.append(f'<rect x="{lx}" y="6" width="8" height="8" fill="{color}" rx="1.5"/>')
            out.append(f'<text x="{lx + 12}" y="13.5" font-size="10" fill="#7a8397">'
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
            payout=(dv * share / npf * 100) if (dv and share and npf) else None,
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
    last_px = meta.get("最新价") or (d["prices"]["close"][-1] if d["prices"]["close"] else None)
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
    _px = d.get("prices") or {}
    dates_all = [x for x in (_px.get("dates") or [])]
    closes_all = [x for x in (_px.get("close") or [])]
    _yr = {}
    for _d, _c in zip(dates_all, closes_all):
        if _d and _c:
            _yr.setdefault(_d[:4], []).append(_c)
    yr_avg = {y: sum(v) / len(v) for y, v in _yr.items()}
    yr_lo = {y: min(v) for y, v in _yr.items()}
    yr_hi = {y: max(v) for y, v in _yr.items()}

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
    h.append(f'<span class="meta">'
             + (f'股息率 <b style="color:#101828">{yld_now:.2f}%</b>'
                f'<span style="color:#98a2b3">（{_fy_y} 年度 ÷ 现价）</span> ｜ '
                if yld_now else '')
             + f'PE 分位 {pe_pct} ｜ PB 分位 {pb_pct} ｜ '
             f'最新数据 {latest["statDate"] if latest else "—"}'
             f'（公告 {latest["pubDate"] if latest else "—"}）</span>')
    h.append('</div></header>')

    # ── 决策速览：四个信号灯，把「贵不贵 / 好不好 / 给不给钱 / 什么位置」合成一句 ──
    _pp, _pbp = _pct(pe_pct), _pct(pb_pct)
    _roe = [(y, (qof(y, 4) or {}).get("roe") * 100) for y in annual_chrono[-5:]
            if (qof(y, 4) or {}).get("roe") is not None]
    _ylds = [v for v in (_yield(None, "annual", y) for y in annual_chrono) if v is not None]
    _yld_med = sorted(_ylds)[len(_ylds) // 2] if _ylds else None
    _js = [v for v in (jd, jw, jm) if v is not None]

    sig = []
    if _pp is not None:
        st = "bad" if _pp > 70 else ("ok" if _pp < 30 else "warn")
        sig.append(("估值", st,
                    f'PE 分位 {_pp:.0f}%' + (f'　PB 分位 {_pbp:.0f}%' if _pbp is not None else ''),
                    "相对自身历史偏贵" if _pp > 70 else ("相对自身历史便宜" if _pp < 30 else "中性")))
    if len(_roe) >= 2:
        _droe = _roe[-1][1] - _roe[0][1]
        st = "bad" if _droe < -1 else ("ok" if _droe > 1 else "warn")
        sig.append(("盈利", st,
                    f'ROE {_roe[0][1]:.2f}%（{_roe[0][0]}）→ {_roe[-1][1]:.2f}%（{_roe[-1][0]}）',
                    f'{_droe:+.2f}pp，'
                    f'{"持续下滑" if _droe < -1 else ("在改善" if _droe > 1 else "基本持平")}'))
    if _yld_med and yld_now:
        st = "ok" if yld_now >= _yld_med else "warn"
        sig.append(("现金回报", st,
                    f'股息率 {yld_now:.2f}%　历史中位 {_yld_med:.2f}%',
                    "高于自身历史中位" if st == "ok" else "低于自身历史中位"))
    if _js:
        m = sum(_js) / len(_js)
        st = "bad" if m > 80 else ("ok" if m < 20 else "warn")
        sig.append(("技术位置", st,
                    f'日 J {jd:.0f}　周 J {jw:.0f}　月 J {jm:.0f}' if all(
                        v is not None for v in (jd, jw, jm)) else f'三周期 J 均值 {m:.0f}',
                    "三周期都在超买区" if m > 80 else ("三周期都在超卖区" if m < 20 else "在中性区")))

    if sig:
        nbad = sum(1 for s in sig if s[1] == "bad")
        nok = sum(1 for s in sig if s[1] == "ok")
        if nbad >= 2:
            concl = "多项偏高位 / 走弱 —— 不是好的买入位置"
        elif nok >= 2 and nbad == 0:
            concl = "多项偏低位 / 改善 —— 值得进一步看"
        else:
            concl = "信号混合 —— 没有明显方向"
        h.append('<div class="dec">')
        for name, st, val, note in sig:
            h.append(f'<div class="row"><span class="n">{name}</span>'
                     f'<span class="dot {st}"></span>'
                     f'<span class="txt"><b>{val}</b><em>{note}</em></span></div>')
        h.append(f'<div class="sum">{concl}</div>')
        h.append('</div>')
        h.append('<div class="fn">这四项都是<b>位置描述</b>，不是预测。'
                 '独立回测显示这类技术信号对未来收益无预测力，请只当作"现在处在什么位置"来看。</div>')

    # ── 当前位置：把技术面（日J/周J/月J/均线/量比）和估值放在一起 ──
    # 数据全部来自仓库的 metrics CSV，不需要额外抓取 —— 这就是「和现有系统联动」。
    J_MIN, J_MAX = -20.0, 120.0     # J 没有上下界（工行周 J 曾到 -16.8、月 J 到 113）

    def jbar(label, j):
        if j is None:
            return ""
        pos = (max(J_MIN, min(J_MAX, j)) - J_MIN) / (J_MAX - J_MIN) * 100
        if j < 20:
            state = "超卖"
        elif j > 80:
            state = "超买"
        else:
            state = "中性"
        return (f'<div class="jrow"><span class="jl">{label}</span>'
                f'<span class="jv">{j:.1f}</span>'
                f'<span class="jt"><i style="left:{pos:.1f}%"></i></span>'
                f'<span class="js">{state}</span></div>')

    if any(v is not None for v in (jd, jw, jm)):
        h.append('<h2>当前位置<span class="unit">技术面数据来自仓库每日 metrics CSV</span></h2>')
        h.append('<div class="jrow" style="padding-bottom:2px"><span class="jl"></span>'
                 '<span class="jv"></span><span class="jt" style="height:0;background:none">'
                 '<i style="left:20%;background:none"></i></span>'
                 '<span class="js"></span></div>')
        h.append(jbar("日线 J", jd))
        h.append(jbar("周线 J", jw))
        h.append(jbar("月线 J", jm))
        h.append('<div class="fn">刻度 −20 ~ 120。浅蓝 = 超卖（J&lt;20），浅红 = 超买（J&gt;80）。J 没有上下界，超出会被夹在端点。</div>')

        ma20, gap20, bull = _f("MA20"), _f("价距MA20%"), meta.get("双均线多头")
        vr, vr30 = _f("量比"), _f("量比30")
        h.append('<div class="nowbox" style="margin-top:12px">')
        h.append('<div class="col"><div class="cap">均线 / 量能</div>')
        if ma20 is not None:
            h.append(f'<div class="kv"><span>MA20</span><b>{ma20:.2f}</b></div>')
        if gap20 is not None:
            h.append(f'<div class="kv"><span>价距 MA20</span>'
                     f'<b class="{"up" if gap20 > 0 else "down"}">{gap20:+.2f}%</b></div>')
        if bull not in (None, ""):
            txt = {"1": "是", "1.0": "是", "True": "是", "true": "是",
                   "0": "否", "0.0": "否", "False": "否", "false": "否"}.get(
                       str(bull).strip(), str(bull))
            h.append(f'<div class="kv"><span>双均线多头</span><b>{txt}</b></div>')
        if vr is not None:
            h.append(f'<div class="kv"><span>量比</span><b>{vr:.2f}</b></div>')
        if vr30 is not None:
            h.append(f'<div class="kv"><span>30 日量比</span><b>{vr30:.2f}</b></div>')
        h.append('</div><div class="col"><div class="cap">估值 / 位置</div>')
        if pe_now is not None:
            h.append(f'<div class="kv"><span>PE_TTM</span><b>{pe_now:.2f}</b></div>')
        if _pct(pe_pct) is not None:
            h.append(f'<div class="kv"><span>PE 分位</span><b>{_pct(pe_pct):.0f}%</b></div>')
        if pb_now is not None:
            h.append(f'<div class="kv"><span>PB</span><b>{pb_now:.2f}</b></div>')
        if _pct(pb_pct) is not None:
            h.append(f'<div class="kv"><span>PB 分位</span><b>{_pct(pb_pct):.0f}%</b></div>')
        h.append('</div></div>')

        # 联动：指向当天的技术面报告
        _day = ""
        if args.metrics:
            import re as _re
            mt = _re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(args.metrics))
            _day = mt.group(1) if mt else ""
        if _day:
            h.append(f'<a class="link" href="https://logic77lhl.github.io/daily-stock-metrics/'
                     f'{_day}/a.html">查看 {_day} 的完整技术面报告 →</a>')

    # ── 估值位置：当前值落在历史区间的哪里 ──
    h.append('<h2>估值位置<span class="unit">区间 = 有数据以来最低~最高</span></h2>')
    h.append('<div class="valbox">')
    h.append(range_bar("PE", pe_now, pe_lo, pe_hi, _pct(pe_pct)))
    h.append(range_bar("PB", pb_now, pb_lo, pb_hi, _pct(pb_pct)))
    h.append('</div>')

    # ── 主表：行=指标，列=期间 ──
    h.append('<h2>核心指标<span class="unit">单位：亿元 / %</span></h2>')
    h.append('<div class="scroll"><table><thead><tr><th class="k">指标</th>'
             '<th class="ttmh">TTM</th>')
    for kind, y, q in periods:
        lab = f"{y}中报" if (kind == "latest" and q == 2) else (
            f"{y}Q{q}" if kind == "latest" else f"{y}年报")
        h.append(f'<th>{lab}</th>')
    h.append('<th class="trendh">趋势</th>')
    h.append('</tr><tr class="sub"><th class="k"></th><th>近 12 月</th>')
    for kind, y, q in periods:
        node = qof(y, q)
        h.append(f'<th>{node["pubDate"][5:] if node and node.get("pubDate") else "—"}</th>')
    h.append('<th></th></tr></thead><tbody>')

    # 趋势线的数据点：只用完整年度，按时间正序（左旧右新）

    def metric(label, getter, fmt="{:.0f}", gap=False, ttm=None, tip=None):
        lab = (f'<abbr title="{html.escape(tip)}">{label}<i class="q">?</i></abbr>'
               if tip else label)
        h.append(f'<tr class="{"main gap" if gap else "main"}"><td class="k">{lab}</td>')
        h.append(f'<td class="ttm">{fmt.format(ttm)}</td>' if ttm is not None
                 else '<td class="na">—</td>')
        for kind, y, q in periods:
            v = getter(qof(y, q), kind, y)
            h.append(f'<td>{fmt.format(v)}</td>' if v is not None
                     else '<td class="na">—</td>')
        series = [getter(qof(y, 4), "annual", y) for y in annual_chrono]
        h.append(f'<td class="trend">{sparkline(series)}</td>')
        h.append('</tr>')

    def yoy_row(key, label="同比"):
        h.append(f'<tr class="subrow"><td class="k">{label}</td><td></td>')
        for kind, y, q in periods:
            v = (qof(y, q) or {}).get(key)
            h.append(f'<td class="{"up" if (v or 0) > 0 else "down"}">{v:+.1f}%</td>'
                     if v is not None else '<td class="na">—</td>')
        h.append('<td></td></tr>')

    def yi(x, key):
        v = (x or {}).get(key)
        return v / 1e8 if v else None

    metric("营业收入", lambda x, k, y: yi(x, "revenue"), "{:,.0f}",
           ttm=(ttm_rev / 1e8 if ttm_rev else None),
           tip="利润表「营业收入」，年内累计。数据源：东财 RPT_F10_FINANCE_MAINFINADATA / TOTALOPERATEREVE")
    yoy_row("yoyRev")
    metric("归母净利", lambda x, k, y: yi(x, "netProfit"), "{:,.0f}",
           ttm=(ttm_np / 1e8 if ttm_np else None),
           tip="归属于母公司股东的净利润，年内累计。字段 PARENTNETPROFIT（不含少数股东损益）")
    yoy_row("yoyNi")
    metric("ROE（加权）",
           lambda x, k, y: ((x or {}).get("roe") or 0) * 100
           if (x or {}).get("roe") is not None else None, "{:.2f}%", gap=True,
           ttm=ttm_roe,
           tip="归母净利 ÷ 加权平均净资产（官方披露口径 ROEJQ）。TTM 列为年化值")
    # 净利率统一用**归母净利 ÷ 营收**。数据源的 XSJLL 是「全部净利 ÷ 营收」
    # （含少数股东损益），与上面的「归母净利」行不同口径 —— 工行 2026 中报
    # 1737/4659 = 37.3%，而 XSJLL 给 37.9%，读者一除就会发现对不上。
    def _margin(x, k, y):
        rv, npf = (x or {}).get("revenue"), (x or {}).get("netProfit")
        return npf / rv * 100 if (rv and npf) else None

    metric("净利率", _margin, "{:.1f}%", ttm=ttm_margin,
           tip="归母净利 ÷ 营业收入。注意：数据源自带的 XSJLL 是「全部净利 ÷ 营收」（含少数股东），本表不用它")
    metric("每股分红", lambda x, k, y: div_by_year.get(str(y)) if k == "annual" else None,
           "{:.4f}", gap=True,
           tip="该财务年度的每股现金分红合计（税前）。一年两次分红的年份会把中报+年报两笔相加")
    metric("分红率",
           lambda x, k, y: (div_by_year.get(str(y)) * share
                            / (qof(y, 4) or {}).get("netProfit") * 100)
           if (k == "annual" and div_by_year.get(str(y)) and share
               and (qof(y, 4) or {}).get("netProfit")) else None, "{:.0f}%",
           tip="每股分红 × 总股本 ÷ 归母净利。>100% 说明分红超过当年利润，不可持续")


    metric("股息率", _yield, "{:.2f}%",
           tip="每股分红 ÷ 当年**日均价**（不是年末价）。一年内股价有高低，所以这是个代表值，实际在区间内浮动")
    h.append('</tbody></table></div>')
    h.append('<div class="fn">年报为全年累计；<b>TTM = 最近 12 个月</b>'
             '（最新累计 + 上年全年 − 上年同期累计），因此与年报天然可比 —— '
             '这也是主表不放「中报」列的原因：半年 vs 全年并排，营收和 ROE 会看起来像断崖。'
             '净利率 = <b>归母净利 ÷ 营业收入</b>。<br>'
             '<b>股息率有三种算法，本表用第二种</b>：① 按年末价（年末那天只是个抽样点，'
             '偏高或偏低）；② <b>按当年日均价</b>（本表，代表那年的典型买入成本）；'
             '③ 按当前价（表头那个数，代表「现在买能拿多少」）。'
             '一年之内股价有高低，实际股息率会在区间内浮动 —— 所以这是一个代表值，不是唯一值。'
             '分红均为税前，未扣红利税。</div>')

    # ── 年度串联：业绩 ↔ 分红 ↔ 回购 ──
    _ar = annual_rows(d, years=10)
    if len(_ar) >= 3:
        h.append('<h2>年度：业绩 ↔ 分红 ↔ 回购'
                 '<span class="unit">同一财务年度一行</span></h2>')
        h.append('<table><thead><tr><th class="k">年度</th><th>营收(亿)</th>'
                 '<th>同比</th><th>归母净利</th><th>同比</th><th>ROE</th>'
                 '<th>净利率</th><th>每股分红</th><th>分红率</th><th>股息率</th>'
                 '<th>回购</th></tr></thead><tbody>')
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
            h.append(f'<tr><td class="k">{x["year"]}{mark}</td>')
            h.append(f'<td>{x["revenue"]:,.0f}</td>' if x["revenue"] else '<td class="na">—</td>')
            h.append(_p(x["yoyRev"], sign=True))
            h.append(f'<td>{x["netProfit"]:,.0f}</td>' if x["netProfit"] else '<td class="na">—</td>')
            h.append(_p(x["yoyNi"], sign=True))
            h.append(_p(x["roe"], nd=2))
            h.append(_p(x["margin"]))
            h.append(f'<td>{x["dps"]:.4f}</td>' if x["dps"] else '<td class="na">—</td>')
            h.append(_p(x["payout"], nd=0))
            h.append(_p(x["yld"], nd=2))
            h.append(f'<td class="{"na" if not (x["bk_cancel"] or x["bk_prog"]) else ""}">{bk}</td></tr>')
        h.append('</tbody></table>')
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
        h.append(f'<div class="fn">{"　·　".join(obs)}<br>'
                 '业绩取年报（年内累计）。每股分红按 <b>财务年度</b> 归集（年报+中报相加），'
                 '不按除权日。分红率 = 每股分红 × 总股本 ÷ 归母净利；'
                 '股息率 = 每股分红 ÷ 当年日均价。回购按公告日年份归集，'
                 '格式为「注销式 / 激励式」—— 只有注销式才真正提高每股价值。<br>'
                 '⚠ 回购列只覆盖公告接口可取到的最近约 300 条公告（通常 2~4 年），'
                 '更早年份显示「—」<b>不代表当年没有回购</b>。'
                 '* 该年度未走完，分红率与股息率不计算。</div>')

    # ── 同业对比：孤立的数字没有参照，横着比才有意义 ──
    peers = d.get("peers") or []
    if peers:
        fy_year2, fy_amt2 = full_year_dividend(div_by_year)
        yld_self = (fy_amt2 / float(last_px) * 100) if (fy_amt2 and last_px) else None
        cols = [("ROE", (latest or {}).get("roe") and (latest["roe"] * 100), "{:.2f}%"),
                ("净利率", (latest or {}).get("npMargin") and (latest["npMargin"] * 100), "{:.1f}%"),
                ("股息率", yld_self, "{:.2f}%"),
                ("PB", _pct(meta.get("PB_MRQ")), "{:.2f}"),
                ("PE", _pct(meta.get("PE_TTM")), "{:.1f}")]
        peer_cols = {"ROE": "roe", "净利率": "margin", "股息率": "yld", "PB": "pb", "PE": "pe"}
        h.append('<h2>同业对比<span class="unit">2026 中报同期 / 最新价</span></h2>')
        h.append('<table class="peer"><thead><tr><th class="k"></th>')
        h.append(f'<th class="me">{html.escape(name)}</th>')
        for pr in peers:
            h.append(f'<th>{html.escape(str(pr.get("name") or pr.get("code")))}</th>')
        h.append('</tr></thead><tbody>')
        for label, self_v, fmt in cols:
            h.append(f'<tr><td class="k">{label}</td>')
            h.append(f'<td class="me">{fmt.format(self_v)}</td>' if self_v is not None
                     else '<td class="na">—</td>')
            for pr in peers:
                v = pr.get(peer_cols[label])
                h.append(f'<td>{fmt.format(v)}</td>' if v is not None
                         else '<td class="na">—</td>')
            h.append('</tr>')
        h.append('</tbody></table>')
        h.append('<div class="fn">招行的 ROE 明显高于四大行、股息率也更高 —— '
                 '这种差距只有横着比才看得出来。</div>')

    # ── 季度表：行=年，列=Q1~Q4 ──
    h.append('<h2>单季净利<span class="unit">单位：亿元</span></h2>')
    h.append('<table><thead><tr><th class="k">年度</th><th>Q1</th><th>Q2</th><th>Q3</th>'
             '<th>Q4</th><th>全年</th><th>同比</th></tr></thead><tbody>')
    for y in reversed(years_all[-8:]):
        rowq = [q for q in qs if q["year"] == y]
        lq = max(x["quarter"] for x in rowq) if rowq else None
        h.append(f'<tr class="main"><td class="k">{y}</td>')
        for k in (1, 2, 3, 4):
            node = next((x for x in rowq if x["quarter"] == k), None)
            v = node.get("qNetProfit") if node else None
            h.append(f'<td>{v / 1e8:,.0f}</td>' if v else '<td class="na">—</td>')
        cum = next((x["netProfit"] for x in rowq if x["quarter"] == lq), None)
        pcum = next((x["netProfit"] for x in qs if x["year"] == y - 1
                     and x["quarter"] == lq), None)
        mark = "*" if lq != 4 else ""
        h.append(f'<td><b>{cum / 1e8:,.0f}{mark}</b></td>' if cum else '<td class="na">—</td>')
        h.append(yoy_cell(cum, pcum))
        h.append('</tr>')
    h.append('</tbody></table>')
    h.append('<div class="fn">单季 = 累计差分。* 该年度未走完，为已披露累计值；'
             '同比为同期累计口径。</div>')

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

    # ── 图：只留一张，细柱无网格 ──
    cq = qs[-20:]
    labels = [quarter_label(q) for q in cq]
    h.append('<h2>单季净利趋势<span class="unit">近 20 季</span></h2>')
    h.append(bar_chart(labels, [("单季净利", "#2d4a6e",
                                 [(q.get("qNetProfit") or 0) / 1e8 if q.get("qNetProfit")
                                  else None for q in cq])], height=180, unit="亿"))

    # ── 结论 ──
    last_payout = next((v for v in reversed(payout) if v is not None), None)
    bad = [f[1] for f in flags if f[0] in ("bad", "warn")]
    kv = []
    if last_payout:
        kv.append(f"分红率 {last_payout:.0f}%")
    _bk = d.get("buybacks") or []
    if _bk:
        _n_cancel = sum(1 for x in _bk if x.get("kind") == "注销")
        kv.append(f"回购 {len(_bk)} 条公告"
                  + (f"（注销式 {_n_cancel} 条）" if _n_cancel else "（激励式，股本不变）"))
    elif d.get("code"):
        kv.append("回购：无")
    if latest and latest.get("nim") is not None:
        kv.append(f"净息差 {latest['nim']:.2f}%")
    if latest and latest.get("provision") is not None:
        kv.append(f"拨贷比 {latest['provision']:.2f}%")
    h.append(f'<div class="verdict {"warn" if bad else "ok"}">'
             f'<b>{"! 需留意：" + "、".join(bad) if bad else "✓ 无明显风险信号"}</b>')
    if kv:
        h.append(f'<div class="kv">{"　·　".join(kv)}</div>')
    h.append('</div>')

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
                 f'<td style="text-align:left;white-space:normal;color:#475467">{fm}</td></tr>')
    h.append('</tbody></table></details>')

    h.append(f'<div class="ft">数据：东财 datacenter（季频指标 / 分红）· 本地估值序列<br>'
             f'生成于 {datetime.now():%Y-%m-%d %H:%M}　·　仅供参考，不构成投资建议</div>')
    h.append('</div></body></html>')

    out = d.get("_out") or args.out
    if not out:
        raise SystemExit("缺少输出路径（--out）")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
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
         '<h2>按红灯数排序<span class="unit">红 = 估值偏高 / ROE 走弱 / 周J 超买</span></h2>',
         '<div class="scroll"><table><thead><tr><th class="k">标的</th><th>行业</th>'
         '<th>最新价</th><th>PE分位</th><th>PB分位</th><th>股息率</th><th>ROE</th>'
         '<th>ROE 4年</th><th>净利同比</th><th>周J</th><th>灯</th></tr></thead><tbody>']
    for r in rows:
        dots = ("<span style='color:#d92d20'>●</span>" * r["red"]
                + "<span style='color:#d0d5dd'>○</span>" * (3 - r["red"]))
        pe_txt = f'{r["pe_pct"]:.0f}%' if r["pe_pct"] is not None else "—"
        pb_txt = f'{r["pb_pct"]:.0f}%' if r["pb_pct"] is not None else "—"
        yld_txt = f'{r["yld"]:.2f}%' if r["yld"] else "—"
        roe_txt = f'{r["roe"]:.2f}%' if r["roe"] else "—"
        d_txt = f'{r["roe_delta"]:+.2f}pp' if r["roe_delta"] is not None else "—"
        d_cls = "down" if (r["roe_delta"] or 0) < 0 else "up"
        n_txt = f'{r["np_yoy"]:+.1f}%' if r["np_yoy"] is not None else "—"
        j_txt = f'{r["jw"]:.0f}' if r["jw"] is not None else "—"
        h.append(
            f'<tr><td class="k"><a href="{r["code"]}.html" style="color:#175cd3;'
            f'text-decoration:none">{html.escape(str(r["name"]))}'
            f'<span style="color:#98a2b3;font-size:11px"> {r["code"]}</span></a></td>'
            f'<td style="text-align:left;color:#667085;font-size:12px">'
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

