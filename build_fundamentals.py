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
body{margin:0;background:#f2f4f8;font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;color:#1a1a2e}
.wrap{max-width:760px;margin:0 auto;padding:12px 12px 28px}
.hd{background:linear-gradient(135deg,#141e30,#2d4a6e);color:#fff;border-radius:12px;padding:15px 17px;margin-bottom:12px}
.hd h1{margin:0;font-size:20px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.hd h1 .code{font-size:13px;color:#aab4cf;font-weight:400}
.hd h1 .tag{font-size:11.5px;background:rgba(255,255,255,.15);padding:2px 7px;border-radius:5px;font-weight:400}
.hd .sub{color:#aab4cf;font-size:12px;margin-top:7px;line-height:1.6}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);background:#fff;border-radius:12px;
  box-shadow:0 1px 3px rgba(0,0,0,.06);overflow:hidden;margin-bottom:4px}
.kpis>div{padding:12px 6px;text-align:center;border-right:1px solid #eef1f6}
.kpis>div:last-child{border-right:0}
.kpis b{display:block;font-size:19px;font-variant-numeric:tabular-nums;line-height:1.25}
.kpis span{font-size:11px;color:#8a93a5}
h2{font-size:14px;margin:18px 0 8px;padding-left:9px;border-left:3px solid #2d4a6e;color:#1a1a2e}
table{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums;
  background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid #f2f5f9}
th:first-child,td:first-child{text-align:left;font-weight:600}
th{background:#f7f9fc;color:#6b7488;font-size:11.5px;font-weight:600;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:#fafcff}
.sum{background:#f7f9fc;font-weight:700}
.na{color:#c8ceda}
.up{color:#c92a2a}
.down{color:#2f9e44}
.fn{font-size:11px;color:#98a1b3;line-height:1.7;margin:7px 2px 0}
.verdict{background:#fff;border-radius:10px;padding:12px 15px;font-size:13px;
  box-shadow:0 1px 3px rgba(0,0,0,.06);margin-top:16px;line-height:1.7}
.verdict.ok{color:#2f9e44}
.verdict.warn{color:#e8590c}
.verdict b{font-size:13.5px}
.verdict .kv{color:#6b7488;font-size:12.5px;margin-top:5px}
.ft{color:#a4adbd;font-size:11px;text-align:center;padding:16px 0 4px;line-height:1.8}
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

    # 价格序列（表 2 的「当年股息率」要用年末收盘价）
    _px = d.get("prices") or {}
    dates_all = [x for x in (_px.get("dates") or [])]
    closes_all = [x for x in (_px.get("close") or [])]

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

    # ── 渲染（表格优先：按年 / 按季度，数字对齐、颜色编码）──
    h = []
    h.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">')
    h.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    h.append(f'<title>{name} 基本面</title><style>{CSS}</style></head><body><div class="wrap">')

    # 抬头：一行
    h.append(f'<div class="hd"><h1>{html.escape(name)}<span class="code">{code}</span>'
             f'<span class="tag">{html.escape(str(ind))}</span></h1>')
    h.append(f'<div class="sub">最新价 {last_px} ｜ PE 分位 {pe_pct} ｜ PB 分位 {pb_pct} ｜ '
             f'数据截止 {latest["statDate"] if latest else "—"}'
             f'（公告 {latest["pubDate"] if latest else "—"}）</div></div>')

    # KPI：4 个数字，一屏内先给结论
    fy_year, fy_amt = full_year_dividend(div_by_year)
    ttm = ttm_dividend(d["dividends"])
    yld = (fy_amt / float(last_px) * 100) if fy_amt and last_px else None
    last_payout = next((v for v in reversed(payout) if v is not None), None)
    h.append('<div class="kpis">')
    h.append(f'<div><b>{yld:.2f}%</b><span>股息率（{fy_year} 年度）</span></div>' if yld else
             '<div><b>—</b><span>股息率</span></div>')
    np_yoy = latest.get("npYoy") if latest else None
    h.append(f'<div><b class="{"up" if (np_yoy or 0) > 0 else "down"}">'
             f'{np_yoy:+.1f}%</b><span>净利同比（单季）</span></div>' if np_yoy is not None else
             '<div><b>—</b><span>净利同比</span></div>')
    h.append(f'<div><b>{(latest.get("roe") or 0) * 100:.2f}%</b>'
             f'<span>ROE（{latest["year"]} 累计）</span></div>' if latest else
             '<div><b>—</b><span>ROE</span></div>')
    h.append(f'<div><b>{last_payout:.0f}%</b><span>分红率</span></div>' if last_payout else
             '<div><b>—</b><span>分红率</span></div>')
    h.append('</div>')

    # ── 表 1：季度净利（行=年，列=Q1~Q4）──
    years = sorted({q["year"] for q in qs})[-5:]
    h.append('<h2>季度净利（亿元）</h2>')
    h.append('<table><thead><tr><th>年度</th><th>Q1</th><th>Q2</th><th>Q3</th>'
             '<th>Q4</th><th class="sum">全年</th><th>同比</th></tr></thead><tbody>')
    for y in reversed(years):
        row = [q for q in qs if q["year"] == y]
        cells = []
        for k in (1, 2, 3, 4):
            q = next((x for x in row if x["quarter"] == k), None)
            v = q.get("qNetProfit") if q else None
            cells.append(f'<td>{v / 1e8:,.0f}</td>' if v else '<td class="na">—</td>')
        # 全年 = 最新可得季度的**累计**净利；未走完的年份就是半年累计
        last_q = max(x["quarter"] for x in row) if row else None
        full = next((x["netProfit"] for x in row if x["quarter"] == last_q), None)
        partial = last_q != 4
        # 同比必须**同期累计**比（半年比半年、全年比全年）。曾经拿半年比全年，
        # 2026 算出 −52.9% 这种荒谬值 —— 表格最怕这种"看起来精确的错"。
        prev = next((x["netProfit"] for x in qs if x["year"] == y - 1
                     and x["quarter"] == last_q), None)
        yoy = (full / prev - 1) * 100 if (full and prev) else None
        cls = "up" if (yoy or 0) > 0 else "down"
        yoy_txt = f'<td class="{cls}">{yoy:+.1f}%</td>' if yoy is not None else '<td class="na">—</td>'
        full_txt = f'<td class="sum">{full / 1e8:,.0f}{"*" if partial else ""}</td>'
        h.append(f'<tr><td>{y}</td>' + "".join(cells) + full_txt + yoy_txt + '</tr>')
    h.append('</tbody></table>')
    h.append('<div class="fn">单位亿元。* 该年度尚未走完，为已披露的累计值。'
             '单季值由累计数差分；同比为**同期累计**口径（半年比半年、全年比全年）。</div>')

    # ── 表 2：年度指标（营收 / 净利 / 盈利能力 / 现金回报）──
    h.append('<h2>年度指标</h2>')
    h.append('<table><thead><tr><th>年度</th><th>营收</th><th>净利</th><th>ROE</th>'
             '<th>净利率</th><th>每股分红</th><th>分红率</th><th>股息率</th>'
             '</tr></thead><tbody>')
    for y in reversed(years):
        row = [q for q in qs if q["year"] == y]
        q4 = next((x for x in row if x["quarter"] == 4), None)
        base = q4 or (max(row, key=lambda x: x["quarter"]) if row else None)
        if base is None:
            continue
        rev, npr = base.get("revenue"), base.get("netProfit")
        dps = div_by_year.get(str(y))
        # 分红率：每股分红 × 总股本 ÷ 当年净利
        pay = (dps * share / npr * 100) if (dps and share and npr) else None
        # 当年股息率：每股分红 ÷ 当年末收盘价
        yend = None
        if dps:
            try:
                import bisect
                i = bisect.bisect_right(dates_all, f"{y}-12-31") - 1
                if i >= 0:
                    px_close = closes_all[i]
                    yend = dps / px_close * 100 if px_close else None
            except Exception:
                yend = None
        partial = q4 is None
        h.append(
            f'<tr><td>{y}{"*" if partial else ""}</td>'
            f'<td>{rev / 1e8:,.0f}</td>' if rev else '<tr><td>' + str(y) + '</td><td class="na">—</td>')
        h.append(f'<td>{npr / 1e8:,.0f}</td>' if npr else '<td class="na">—</td>')
        h.append(f'<td>{(base.get("roe") or 0) * 100:.2f}%</td>')
        h.append(f'<td>{(base.get("npMargin") or 0) * 100:.1f}%</td>')
        h.append(f'<td>{dps:.4f}</td>' if dps else '<td class="na">—</td>')
        # 未走完的年度：分红率/股息率是「半年分红 ÷ 半年净利」，与全年不可比，
        # 摆出来会被误读成「分红腰斩」→ 直接留空
        h.append(f'<td>{pay:.0f}%</td>' if (pay and not partial) else '<td class="na">—</td>')
        h.append(f'<td>{yend:.2f}%</td>' if (yend and not partial) else '<td class="na">—</td>')
        h.append('</tr>')
    h.append('</tbody></table>')
    h.append('<div class="fn">单位亿元 / 元。ROE 为加权（累计），Q4 即全年。'
             '股息率 = 该年度每股分红 ÷ 当年末收盘价（含税，未扣红利税）。</div>')

    # 东财一次给 85 季，但图上画 85 根柱子没法看 → 只画近 20 季（5 年）
    cq = qs[-20:]
    labels = [quarter_label(q) for q in cq]

    # ── 图：近 20 季净利趋势（只留一张）──
    h.append('<h2>净利趋势（近 20 季）</h2>')
    h.append(bar_chart(labels,
                       [("单季净利", "#4c6ef5",
                         [(q.get("qNetProfit") or 0) / 1e8 if q.get("qNetProfit") else None
                          for q in cq])],
                       height=200, unit="亿"))

    # ── 结论一行 ──
    bad = [f[1] for f in flags if f[0] in ("bad", "warn")]
    head_txt = "无明显风险信号" if not bad else "需留意：" + "、".join(bad)
    kv = []
    if last_payout:
        kv.append(f"分红率 {last_payout:.0f}%")
    if latest and latest.get("nim") is not None:
        kv.append(f"净息差 {latest['nim']:.2f}%")
    if latest and latest.get("provision") is not None:
        kv.append(f"拨贷比 {latest['provision']:.2f}%")
    h.append(f'<div class="verdict {"ok" if not bad else "warn"}">'
             f'<b>{"✓" if not bad else "!"} {html.escape(head_txt)}</b>')
    if kv:
        h.append(f'<div class="kv">{"　·　".join(kv)}</div>')
    h.append('</div>')

    h.append(f'<div class="ft">数据：东财 datacenter（季频指标 / 分红）· 本地估值序列<br>'
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
