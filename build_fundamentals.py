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
*{box-sizing:border-box}
body{margin:0;background:#f0f2f5;font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;color:#1a1a2e}
.wrap{max-width:860px;margin:0 auto;padding:14px}
.card{background:#fff;border-radius:12px;padding:16px 18px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.hd{background:linear-gradient(135deg,#141e30,#2d4a6e);color:#fff}
.hd h1{margin:0;font-size:21px}
.hd .sub{color:#aab4cf;font-size:12.5px;margin-top:6px;line-height:1.7}
h2{font-size:15px;margin:0 0 4px}
.note{font-size:11.5px;color:#8a93a5;line-height:1.7;margin:2px 0 10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:12px}
.tile{background:#f7f9fc;border-radius:9px;padding:10px 12px}
.tile .k{font-size:11.5px;color:#7a8397}
.tile .v{font-size:19px;font-weight:700;margin-top:3px}
.tile .s{font-size:11px;color:#9aa3b5;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid #eef1f6;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:#7a8397;font-weight:600;background:#fafbfd}
tr:hover td{background:#f7f9fc}
.flag{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid #eef1f6;font-size:13px;align-items:flex-start}
.flag:last-child{border:0}
.flag .ic{font-size:15px;line-height:1.3}
.flag .t{flex:1}
.flag .t b{display:block;font-size:13px;margin-bottom:2px}
.flag .t span{color:#7a8397;font-size:12px;line-height:1.6}
.ok{color:#2f9e44}.warn{color:#e8590c}.bad{color:#c92a2a}.na{color:#adb5bd}
.ft{color:#98a1b3;font-size:11.5px;text-align:center;padding:6px 0 22px;line-height:1.8}
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


def bar_chart(labels, series, height=230, unit="亿", width=780, legend=None):
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
    bw = slot * 0.72 / max(len(series), 1)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in ticks:
        y = pad_t + ph - (t - lo) / (hi - lo) * ph
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
                   f'stroke="#eef1f6"/>')
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
            x = cx + slot * 0.14 + bw * j
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成个股基本面档案页")
    ap.add_argument("--json", required=True, help="季频财务 JSON（_fin_fetch.py 的产物）")
    ap.add_argument("--out", required=True, help="输出 HTML 路径")
    ap.add_argument("--metrics", default=None, help="仓库的 metrics CSV（取名称/行业/估值分位）")
    args = ap.parse_args(argv)

    with open(args.json, encoding="utf-8") as fh:
        d = json.load(fh)

    code = d["code"]
    qs = d["quarters"]
    meta = {}
    if args.metrics and os.path.exists(args.metrics):
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

    # 分红
    div_by_year = yearly_dividends(d["dividends"])
    # 最后一个分红年度如果还没有年报（Q4），说明这一年没走完 —— 柱子只有半年、
    # 矮一半，读者会误以为分红下降。直接不进图，在注释里说明。
    _years = list(div_by_year)
    _partial = not any(str(x["year"]) == _years[-1] and x["quarter"] == 4 for x in qs)
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

    # 估值序列（降采样到 ~120 点）
    px = d["prices"]
    pe = [v for v in px["pe"]]
    idx = list(range(len(pe)))[::max(1, len(pe) // 120)]
    pe_vals = [pe[i] for i in idx if pe[i] is not None]
    pe_labels = [px["dates"][i][2:7] for i in idx]
    pe_series = [pe[i] for i in idx]
    cur_pe = pe_vals[-1] if pe_vals else None
    p_lo = sorted(pe_vals)[int(len(pe_vals) * 0.2)] if pe_vals else None
    p_hi = sorted(pe_vals)[int(len(pe_vals) * 0.8)] if pe_vals else None

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

    # ── 渲染 ──
    h = []
    h.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">')
    h.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    h.append(f'<title>{html.escape(name)} 基本面档案</title><style>{CSS}</style></head><body>')
    h.append('<div class="wrap">')

    # 抬头
    h.append('<div class="card hd">')
    h.append(f'<h1>{html.escape(name)} <span style="font-size:14px;color:#aab4cf">{code}</span></h1>')
    h.append(f'<div class="sub">{html.escape(str(ind))} ｜ 最新价 {last_px} ｜ '
             f'PE 分位 {pe_pct} ｜ PB 分位 {pb_pct}<br>'
             f'数据截止 {latest["statDate"] if latest else "—"}（公告于 '
             f'{latest["pubDate"] if latest else "—"}）</div>')
    h.append('</div>')

    # 三个结论数字
    h.append('<div class="card"><div class="tiles">')
    dy = div_ps[-1] if div_ps else None
    fy_year, fy_amt = full_year_dividend(div_by_year)
    ttm = ttm_dividend(d["dividends"])
    yld = (fy_amt / float(last_px) * 100) if fy_amt and last_px else None
    n_year, y_from, y_to = consecutive_years(div_by_year)
    h.append(f'<div class="tile"><div class="k">{fy_year} 年度每股分红</div>'
             f'<div class="v">{fy_amt if fy_amt else "—"}</div>'
             f'<div class="s">股息率约 {yld:.2f}%</div></div>' if yld else
             f'<div class="tile"><div class="k">最近完整年度分红</div>'
             f'<div class="v">{fy_amt if fy_amt else "—"}</div><div class="s">—</div></div>')
    h.append(f'<div class="tile"><div class="k">连续分红（数据窗口内）</div>'
             f'<div class="v">{n_year} 年</div>'
             f'<div class="s">{y_from} ~ {y_to}</div></div>')
    h.append(f'<div class="tile"><div class="k">最新单季净利</div>'
             f'<div class="v">{(latest.get("qNetProfit") or 0) / 1e8:,.0f}亿</div>'
             f'<div class="s">同比 {latest.get("npYoy") or 0:+.1f}%</div></div>')
    h.append(f'<div class="tile"><div class="k">最新 ROE（累计）</div>'
             f'<div class="v">{(latest.get("roe") or 0) * 100:.2f}%</div>'
             f'<div class="s">净利率 {(latest.get("npMargin") or 0) * 100:.1f}%</div></div>')
    h.append('</div></div>')

    # 银行专属指标（东财独有字段，baostock 拿不到）
    if latest and (latest.get("nim") is not None or latest.get("provision") is not None):
        h.append('<div class="card"><h2>银行专属指标</h2>')
        h.append('<div class="note">只有银行口径才有这几项 —— 这也是为什么「换数据源」'
                 '不只是省请求，而是能多出判断维度。</div><div class="tiles">')
        if latest.get("nim") is not None:
            h.append(f'<div class="tile"><div class="k">净息差 NIM</div>'
                     f'<div class="v">{latest["nim"]:.2f}%</div>'
                     f'<div class="s">利息净收入 ÷ 生息资产</div></div>')
        if latest.get("npl") is not None:
            h.append(f'<div class="tile"><div class="k">不良贷款余额</div>'
                     f'<div class="v">{latest["npl"] / 1e8:,.0f}亿</div>'
                     f'<div class="s">余额（不是比率）</div></div>')
        if latest.get("provision") is not None:
            h.append(f'<div class="tile"><div class="k">拨贷比</div>'
                     f'<div class="v">{latest["provision"]:.2f}%</div>'
                     f'<div class="s">贷款损失准备 ÷ 贷款总额</div></div>')
        h.append('</div></div>')

    # ① 业绩趋势
    # 东财一次给 85 季，但图上画 85 根柱子没法看 → 只画近 20 季（5 年）。
    cq = qs[-20:]
    labels = [quarter_label(q) for q in cq]
    implied = any(q.get("revenueImplied") for q in qs)
    h.append('<div class="card"><h2>① 业绩趋势</h2>')
    h.append('<div class="note">单季绝对值由累计数差分；同比优先用数据源直接给的**单季同比**。'
             + ('营收的 Q1/Q3 由「净利 ÷ 净利率」反推（该源只在半年报/年报给营收）。'
                if implied else '营收每季都有，无需反推。') + '</div>')
    h.append(bar_chart(labels,
                       [("单季营收", "#4c6ef5", [(q.get("qRevenue") or 0) / 1e8 for q in cq]),
                        ("单季净利", "#12b886", [(q.get("qNetProfit") or 0) / 1e8 for q in cq])],
                       height=240, unit="亿",
                       legend=[("单季营收", "#4c6ef5"), ("单季净利", "#12b886")]))
    h.append(line_chart(labels,
                        [("净利同比", "#e8590c", [q.get("npYoy") for q in cq]),
                         ("营收同比", "#7048e8", [q.get("revYoy") for q in cq])],
                        height=200, unit="%",
                        legend=[("净利同比", "#e8590c"), ("营收同比", "#7048e8")]))
    h.append('</div>')

    # ② 分红历史
    h.append('<div class="card"><h2>② 分红历史</h2>')
    h.append('<div class="note">按**财务年度**归集每股现金分红（税前，同一除权日已去重）。'
             '2025 年起改为一年两次（年报 + 中报），所以最近两个年度各有两笔。'
             f'滚动 12 个月实际已派 {ttm if ttm else "—"} 元 —— 那含过渡期的三次，'
             f'**主口径用完整年度**。'
             + (f'{_years[-1] if _years else ""} 年之后若还没派完，那一年的柱子不进图。'
                if _partial else '')
             + '</div>')
    h.append(bar_chart(div_years, [("每股分红", "#f08c00", div_ps)], height=220, unit="元"))
    pay_years = [y for y, p in zip(div_years, payout) if p is not None]
    pay_vals = [p for p in payout if p is not None]
    if pay_years:
        h.append(line_chart(pay_years, [("分红率 payout", "#c92a2a", pay_vals)],
                            height=170, unit="%",
                            legend=[("分红率 payout", "#c92a2a")]))
        h.append('<div class="note">分红率 = 每股分红 × 总股本 ÷ 当年净利；只在有年报净利的年度可算。</div>')
    h.append('</div>')

    # ③ 盈利能力（ROE 与净利率量级差 10 倍，拆成两张图，别挤一个轴）
    h.append('<div class="card"><h2>③ 盈利能力</h2>')
    h.append('<div class="note">两张图分开画（ROE 约 2~10%、净利率约 38~45%，共用一个轴会把 ROE 压扁）。'
             'ROE 已由年内累计**差分**成单季，否则曲线会呈锯齿状、看起来像波动很大。</div>')
    h.append(line_chart(labels, [("单季 ROE", "#1971c2", [(q.get("qRoe") or 0) * 100 if q.get("qRoe") is not None else None for q in cq])],
                        height=170, unit="%", legend=[("单季 ROE", "#1971c2")]))
    h.append(line_chart(labels, [("净利率", "#0ca678", [(q.get("npMargin") or 0) * 100 for q in cq])],
                        height=170, unit="%", legend=[("净利率", "#0ca678")]))
    h.append('</div>')

    # ④ 估值位置
    h.append('<div class="card"><h2>④ 估值位置</h2>')
    h.append(f'<div class="note">PE_TTM 走势（近 3 年降采样）。当前 {cur_pe:.1f}'
             f'，20% 分位 {p_lo:.1f}、80% 分位 {p_hi:.1f}。</div>' if cur_pe else '')
    h.append(line_chart(pe_labels, [("PE_TTM", "#5f3dc4", pe_series)], height=200, unit="",
                        zero_line=False))
    h.append('</div>')

    # 业绩预告（东财 RPT_PUBLIC_OP_NEWPREDICT）
    _cutoff = str((latest or {}).get("year", 9999) - 1)
    preds = [x for x in (d.get("predicts") or [])
             if (x.get("reportDate") or "")[:4] >= _cutoff]
    if preds:
        h.append('<div class="card"><h2>业绩预告</h2>')
        h.append('<div class="note">公司自己披露的业绩区间，比正式报表早 1~3 个月 —— '
                 '这是唯一能"提前"知道结果的合法信息。</div>')
        h.append('<table><thead><tr><th>报告期</th><th>公告日</th><th>类型</th>'
                 '<th style="text-align:left">内容</th></tr></thead><tbody>')
        for p in preds[:5]:
            h.append(f'<tr><td>{p.get("reportDate") or "—"}</td>'
                     f'<td>{p.get("notice") or "—"}</td>'
                     f'<td>{html.escape(str(p.get("type") or "—"))}</td>'
                     f'<td style="text-align:left;white-space:normal">'
                     f'{html.escape(p.get("content") or "—")}</td></tr>')
        h.append('</tbody></table></div>')

    # ⑤ 财务排雷
    icons = {"ok": ("✓", "ok"), "warn": ("!", "warn"), "bad": ("✕", "bad"), "na": ("–", "na")}
    h.append('<div class="card"><h2>⑤ 财务排雷</h2>')
    h.append('<div class="note">这一块是「判断层」—— 数字之外，还要看它靠不靠得住。</div>')
    for state, title, desc in flags:
        ic, cls = icons[state]
        h.append(f'<div class="flag"><div class="ic {cls}">{ic}</div>'
                 f'<div class="t"><b>{html.escape(title)}</b>'
                 f'<span>{html.escape(desc)}</span></div></div>')
    h.append('</div>')

    # 明细表
    h.append('<div class="card"><h2>季度明细（近 12 季）</h2>')
    h.append('<table><thead><tr><th>报告期</th><th>公告日</th><th>单季营收(亿)</th>'
             '<th>单季净利(亿)</th><th>净利同比</th><th>ROE</th><th>净利率</th>'
             '<th>负债率</th></tr></thead><tbody>')
    for q in qs[-12:][::-1]:
        liab = q.get("liabilityToAsset")
        liab_txt = "—" if liab is None else (
            f'<span class="na">{liab * 100:.1f}%*</span>' if liab < 0.2 else f"{liab * 100:.1f}%")
        yoy = q.get("npYoy")
        yoy_txt = "—" if yoy is None else f'<span class="{"bad" if yoy < 0 else "ok"}">{yoy:+.1f}%</span>'
        h.append(f'<tr><td>{q["year"]}Q{q["quarter"]}</td><td>{q["pubDate"]}</td>'
                 f'<td>{(q.get("qRevenue") or 0) / 1e8:,.0f}</td>'
                 f'<td>{(q.get("qNetProfit") or 0) / 1e8:,.0f}</td>'
                 f'<td>{yoy_txt}</td>'
                 f'<td>{(q.get("roe") or 0) * 100:.2f}%</td>'
                 f'<td>{(q.get("npMargin") or 0) * 100:.1f}%</td>'
                 f'<td>{liab_txt}</td></tr>')
    h.append('</tbody></table>')
    if bad_liab:
        h.append('<div class="note">* 数据源异常值（&lt;20%），不参与判断。</div>')
    h.append('</div>')

    h.append(f'<div class="ft">数据：东财 datacenter（季频指标 / 分红 / 业绩预告）'
             f'· 本地估值序列<br>'
             f'生成于 {datetime.now():%Y-%m-%d %H:%M}｜仅供参考，不构成投资建议</div>')
    h.append('</div></body></html>')

    out = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    print(f"已生成 {out}  ({len(''.join(h)) / 1024:.0f}KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
