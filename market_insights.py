# -*- coding: utf-8 -*-
"""市场洞察模块（纯本地聚合，不新增抓取）。

提供三项能力：
1. sector_temperature()  —— 行业板块温度榜（基于 A股 metrics_*.csv 的「行业」列聚合）
2. market_breadth_dashboard() —— 大盘宽度仪表盘（从 market_breadth_*.csv + metrics 池合成）
3. opportunity_board() —— 超跌/超买机会榜（从今日 A股 metrics 按策略过滤出 TOP30）

输出格式统一：{"html": str, "md": str, "data": pd.DataFrame | dict}
仅在数据不足时 data 可能为空，html/md 永远返回可直接嵌入的占位文本。
"""

import html
import os
import sys

import numpy as np
import pandas as pd

import signals

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _esc(val):
    """把第三方数据（股票/行业名称等）安全地写进 HTML。"""
    if val is None or pd.isna(val):
        return "-"
    return html.escape(str(val), quote=True)


def _md_esc(val):
    if val is None or pd.isna(val):
        return "-"
    return str(val).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def _fnum(v, digits=1, suffix="", sign=False, comma=False):
    """数字格式化：NaN/缺失一律渲染成 '-'。

    否则 `f"{np.nan:.1f}%"` 会在页面上印出字面量 `nan%` —— 一个看起来
    像真实读数的字符串，比留空更糟。
    """
    try:
        if v is None or pd.isna(v):
            return "-"
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if pd.isna(f):
        return "-"
    if comma:
        body = f"{f:+,.{digits}f}" if sign else f"{f:,.{digits}f}"
    else:
        body = f"{f:+.{digits}f}" if sign else f"{f:.{digits}f}"
    return body + suffix


def _inum(v):
    """整数计数格式化：缺失渲染成 '-'，不抛异常（抛了会被上层裸 except 吞掉）。"""
    try:
        if v is None or pd.isna(v):
            return "-"
        return str(int(v))
    except (TypeError, ValueError):
        return "-"


def _to_numeric(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# --------------------------- 1. 行业板块温度 ---------------------------

SECTOR_MIN_N = 3   # 少于 N 只标的的行业不单独展示，并入「其他」
SECTOR_MAX_SHOW = 18   # 邮件/卡片最多展示行业数

# 三周期信号 chip 的样式：原来写成 `<style scoped>` 塞在 <body> 里。
# `scoped` 早已从 HTML 规范中删除、所有浏览器都不支持，于是 .chip2 会全局
# 泄漏到聚合站点与邮件正文。改成逐元素内联样式：无泄漏，也不依赖 <style>。
_CHIP_STYLE = ("display:inline-block;padding:3px 9px;border-radius:99px;"
               "font-size:11.5px;background:#f1f3f9;color:#495057;border:1px solid #e5e8f0")
# chip 展示顺序与短标签（沿用洞察页既有文案，保持视觉不变）
_CHIP_ORDER = [
    ("resonance_strong", "三周期偏强"),
    ("overbought_resonance", "三周期超买"),
    ("resonance_weak", "三周期偏弱"),
    ("oversold_resonance", "三周期超卖"),
    ("newlow_resonance", "三周期新低"),
]
_DIVERGENCE_KEYS = ("divergence_dw", "divergence_wd", "partial")


def _temp_label(t):
    """0-100 温度 → 口语化档位（与综合温度口径一致，沿用 fetch_market_breadth._temp_band）。"""
    if pd.isna(t):
        return "-"
    if t < 20:
        return "冰点"
    if t < 40:
        return "低迷"
    if t < 60:
        return "温和"
    if t < 80:
        return "偏热"
    return "过热"


def _temp_color(t):
    if pd.isna(t):
        return "#bfc5d2"
    # 从冰蓝 → 浅绿 → 橙红渐变
    if t < 20:
        return "#339af0"
    if t < 40:
        return "#22b8cf"
    if t < 60:
        return "#51cf66"
    if t < 80:
        return "#ff922b"
    return "#fa5252"


def sector_temperature(metrics_csv, market="A"):
    """行业板块温度榜。

    metrics_csv: output/<date>/metrics_<date>.csv（必须含「行业」列，A股/港股适用，ETF 行业为空会 fallback 到空面板）

    注意：`market` 参数在当前实现里并未被读取（口径对所有市场一致）。
    保留它是因为 build_pages.py / run_daily.py 都以关键字形式传入
    `market="A"`，而那两个文件不由本模块维护。
    """
    if not os.path.exists(metrics_csv):
        return {"html": "", "md": "", "data": pd.DataFrame()}
    df = pd.read_csv(metrics_csv, dtype={"代码": str})
    if df.empty or "行业" not in df.columns:
        return {"html": "", "md": "", "data": pd.DataFrame()}

    df = _to_numeric(df, ["日线J", "周线J", "月线J", "PE历史分位%", "PB历史分位%",
                          "涨跌幅", "双均线多头"])
    df["行业"] = df["行业"].astype(str).str.strip()
    df.loc[df["行业"].isin(["", "nan", "None", "-"]), "行业"] = "其他"

    def _combine(g):
        n = len(g)
        d = {
            "标的数": n,
            "平均涨跌幅%": g["涨跌幅"].mean() if "涨跌幅" in g else np.nan,
            "平均日线J": g["日线J"].mean() if "日线J" in g else np.nan,
            "平均周线J": g["周线J"].mean() if "周线J" in g else np.nan,
            "平均PE分位%": g["PE历史分位%"].mean() if "PE历史分位%" in g else np.nan,
            "平均PB分位%": g["PB历史分位%"].mean() if "PB历史分位%" in g else np.nan,
            "均线多头占比%": g["双均线多头"].mean() * 100 if "双均线多头" in g else np.nan,
        }
        # 合成温度：60% 三J位置 + 20% 估值便宜度 + 20% 均线趋势。
        # 缺失分项按「缺失」处理，只在可用分项上重新归一 —— 旧实现在 nanmean
        # 的参数表里塞了一个字面量 50，于是 j_place 永远不是 NaN，一个
        # J/PE/PB/MA 全缺的行业会自信地显示 `50.0 · 温和`，旁边还挂着自相
        # 矛盾的 `J:-｜估值:-%｜多头:-`。
        j_terms = [(d["平均日线J"] + 100) / 2, (d["平均周线J"] + 100) / 2]
        j_place = np.nanmean(j_terms) if any(pd.notna(t) for t in j_terms) else np.nan
        val_cheap = (100 - np.nanmean([d["平均PE分位%"], d["平均PB分位%"]])
                     if pd.notna(d["平均PE分位%"]) or pd.notna(d["平均PB分位%"]) else np.nan)
        trend = d["均线多头占比%"] if pd.notna(d["均线多头占比%"]) else np.nan

        avail = [(v, w) for v, w in ((j_place, 0.6), (val_cheap, 0.2), (trend, 0.2))
                 if pd.notna(v)]
        if not avail:
            # 全部缺失 —— 明确标记「无数据」，绝不编造一个像模像样的中值
            d["板块温度"] = np.nan
            d["档位"] = "无数据"
            return pd.Series(d)
        temp = sum(v * w for v, w in avail) / sum(w for _, w in avail)
        temp = max(0.0, min(100.0, float(temp)))
        d["板块温度"] = round(temp, 1)
        d["档位"] = _temp_label(temp)
        return pd.Series(d)

    # 小行业并到「其他」
    counts = df["行业"].value_counts()
    small = counts[counts < SECTOR_MIN_N].index.tolist()
    df.loc[df["行业"].isin(small), "行业"] = "其他"

    g = df.groupby("行业", dropna=False).apply(_combine, include_groups=False).reset_index()
    g = g.sort_values(["板块温度", "标的数"], ascending=[True, False],
                      na_position="last").reset_index(drop=True)

    # 冷/热榜单：最冷 TOP5 便宜行业 与 最热 TOP5 过热行业（用于邮件卡片精选）。
    # 温度缺失的行业既不是「低景气」也不是「高景气」，不进冷/热榜；
    # 但仍保留在完整明细表里（页面上显示「无数据」）。
    g_ranked = g[g["板块温度"].notna()]
    cool = g_ranked.head(min(5, len(g_ranked)))
    hot = g_ranked.tail(min(5, len(g_ranked))).iloc[::-1]
    top_n = min(SECTOR_MAX_SHOW, len(g))
    table = g.head(top_n).copy()

    def _v(x, suffix="", digits=1):
        if pd.isna(x):
            return "-"
        return f"{x:.{digits}f}{suffix}"

    # --------- HTML（卡片式，冷/热分两列） ---------
    def _sector_row(r):
        temp_v = r["板块温度"]
        color = _temp_color(temp_v)
        badge = "无数据" if pd.isna(temp_v) else f"{_v(temp_v)}·{_esc(r['档位'])}"
        return (f'<div class="sec-row" style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:6px 2px;border-bottom:1px dashed #edf0f7">'
                f'<div style="flex:1;min-width:0">'
                f'<div style="font-weight:600;font-size:13px">{_esc(r["行业"])} <span style="color:#8791a8;font-weight:400;font-size:11px">({_inum(r["标的数"])}只)</span></div>'
                f'<div style="font-size:11.5px;color:#657289;margin-top:2px">'
                f'J:{_v(r["平均日线J"])}｜估值:{_v(r["平均PE分位%"])}%｜多头:{_v(r["均线多头占比%"], "%", 0)}'
                f'</div></div>'
                f'<div style="text-align:right;margin-left:10px">'
                f'<div style="display:inline-block;border-radius:99px;padding:3px 10px;color:#fff;font-size:12px;font-weight:600;'
                f'background:{color}">{badge}</div>'
                f'<div style="font-size:11px;color:#657289;margin-top:3px">{_v(r["平均涨跌幅%"], "%", 2)}</div>'
                f'</div></div>')

    cold_html = "".join(_sector_row(r) for _, r in cool.iterrows()) if len(cool) else ""
    hot_html = "".join(_sector_row(r) for _, r in hot.iterrows()) if len(hot) else ""
    cols_html = ""
    if cold_html:
        cols_html += (f'<div class="dsm-sec-col">'
                      f'<div style="font-size:12px;font-weight:700;color:#1971c2;margin-bottom:4px">🧊 低估/低景气 TOP{len(cool)}</div>'
                      f'{cold_html}</div>')
    if hot_html:
        cols_html += (f'<div class="dsm-sec-col" style="margin-left:14px">'
                      f'<div style="font-size:12px;font-weight:700;color:#d9480f;margin-bottom:4px">🔥 高位/高景气 TOP{len(hot)}</div>'
                      f'{hot_html}</div>')

    fallback = '<div style="color:#8791a8">行业数据不足</div>'
    cols_wrap = cols_html if cols_html else fallback
    # 无数据行业不进冷/热榜（它既不是低景气也不是高景气），但必须在页面上留痕：
    # 「消失了」和「数据不足」是两件完全不同的事，读者有权区分。
    no_data = g[g["板块温度"].isna()]
    no_data_note = ""
    if len(no_data):
        names = "、".join(_esc(x) for x in no_data["行业"].head(6))
        more = "" if len(no_data) <= 6 else f" 等{len(no_data)}个"
        no_data_note = (f'<div style="margin-top:8px;font-size:11.5px;color:#8791a8">'
                        f'⚠️ {names}{more} 数据不足，未计入冷/热榜</div>')
    html = ("<div style=\"background:#fff;border-radius:10px;padding:14px 18px;margin-bottom:16px;"
            "box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:14px;line-height:1.7\">"
            # 390px 手机上原来两列各约 170px，明细行要折 3~4 行 —— 窄屏改为纵向堆叠。
            # 这里的 <style> 必须留在最外层 div 内：build_pages._strip_outer_div
            # 依赖最外层是 `<div style="...">`。
            "<style>"
            ".dsm-sec-cols{display:flex;flex-wrap:wrap;gap:6px}"
            ".dsm-sec-col{flex:1 1 300px;min-width:0}"
            "@media (max-width:640px){.dsm-sec-cols{flex-direction:column;gap:2px}"
            ".dsm-sec-col{flex:1 1 auto;margin-left:0 !important}}"
            "</style>"
            "<div style=\"font-weight:700;color:#1a1a2e;margin-bottom:10px\">🏭 行业板块温度榜</div>"
            f'<div class="dsm-sec-cols">{cols_wrap}</div>'
            + no_data_note
            + "</div>")

    # --------- Markdown（摘要） ---------
    def _md_row(r):
        temp_v = r["板块温度"]
        temp_str = "无数据" if pd.isna(temp_v) else f"{_v(temp_v)}·{_md_esc(r['档位'])}"
        return (f"- **{_md_esc(r['行业'])}**（{_inum(r['标的数'])}只）：{temp_str}｜"
                f"PE分位{_v(r['平均PE分位%'])}%｜均价{_v(r['平均涨跌幅%'], '%', 2)}")
    md = "## 🏭 行业板块温度榜\n\n"
    if len(cool):
        md += "### 🧊 低估/低景气\n" + "\n".join(_md_row(r) for _, r in cool.iterrows()) + "\n\n"
    if len(hot):
        md += "### 🔥 高位/高景气\n" + "\n".join(_md_row(r) for _, r in hot.iterrows()) + "\n"
    if len(no_data):
        md += (f"\n> ⚠️ {'、'.join(_md_esc(x) for x in no_data['行业'])} 数据不足，未计入冷/热榜\n")

    return {"html": html, "md": md, "data": table}


# --------------------------- 2. 大盘宽度仪表盘 ---------------------------

def market_breadth_dashboard(market_breadth_csv, metrics_csv=None, market="A"):
    """大盘宽度仪表盘。

    - 从 market_breadth_<date>.csv 取：上涨/下跌/平盘/涨停/跌停、上涨占比、量能/宽度/综合温度、成交额
    - 从今日 metrics_<date>.csv（A股 Top100+追踪池）派生：三周期共振分布、MA20以上占比、J<0/J>100 数
    """
    rows = []
    if market_breadth_csv and os.path.exists(market_breadth_csv):
        mdf = pd.read_csv(market_breadth_csv)
        if not mdf.empty:
            r = mdf.iloc[0]
            rows.extend([
                ("📈 上涨/下跌/平盘",
                 f"{_inum(r['上涨家数'])} / {_inum(r['下跌家数'])} / {_inum(r['平盘家数'])}",
                 "上涨占比", _fnum(r["上涨占比%"], 1, "%")),
                ("🚀 涨停 / 跌停",
                 f"{_inum(r['涨停家数'])} / {_inum(r['跌停家数'])}",
                 "中位涨跌幅", _fnum(r["中位涨跌幅%"], 2, "%", sign=True)),
                ("💰 全市场成交额", _fnum(r["成交额_亿"], 0, " 亿", comma=True),
                 "加权换手率", _fnum(r["加权换手率%"], 2, "%")),
            ])
            # 温度卡（复用 fetch_market_breadth 既有的三温）
            t_vol, t_brd, t_cmp = r["量能温度"], r["宽度温度"], r["综合温度"]
            t_band = r.get("温度档位", _temp_label(t_cmp))
            rows.append(("🌡️  量能/宽度/综合温度",
                         f"{_fnum(t_vol)} / {_fnum(t_brd)} / "
                         f"<b style='color:{_temp_color(t_cmp)}'>{_fnum(t_cmp)}·{_esc(t_band)}</b>",
                         "活跃市值", _fnum(r["活跃市值_亿"], 0, " 亿", comma=True)))

    pool_stats_html = ""
    if metrics_csv and os.path.exists(metrics_csv) and market == "A":
        df = pd.read_csv(metrics_csv, dtype={"代码": str})
        if not df.empty:
            df = _to_numeric(df, ["日线J", "周线J", "月线J", "双均线多头", "涨跌幅", "PE历史分位%", "PB历史分位%"])
            n = len(df)
            # 信号分布 —— 口径一律来自 signals.classify（唯一权威实现）。
            # 这里原来手抄了一份判定逻辑，还把 d<0 排在 d<20 前面，
            # 于是同一只股票在日报里是「三周期共振超卖」、在洞察页里却是「三周期新低」。
            sig_counts = {}
            for _, srow in df.iterrows():
                key = signals.classify_row(srow)[1]
                if key == "insufficient":
                    continue
                sig_counts[key] = sig_counts.get(key, 0) + 1
            sig_items = [
                f'<span class="chip2" style="{_CHIP_STYLE}">{label} {sig_counts[key]}</span>'
                for key, label in _CHIP_ORDER if sig_counts.get(key, 0) > 0
            ]
            div_cnt = sum(sig_counts.get(k, 0) for k in _DIVERGENCE_KEYS)
            if div_cnt > 0:
                sig_items.append(f'<span class="chip2" style="{_CHIP_STYLE}">分化 {div_cnt}</span>')
            if sig_items:
                pool_stats_html = (f'<div style="margin-top:10px;padding-top:10px;border-top:1px dashed #edf0f7">'
                                   f'<div style="font-size:12px;font-weight:700;color:#495057;margin-bottom:6px">'
                                   f'📚 Top{n}池：三周期信号分布</div>'
                                   f'<div style="display:flex;flex-wrap:wrap;gap:6px">'
                                   + "".join(sig_items) + "</div></div>")
            # MA20 以上占比 + 极端J数量
            if "双均线多头" in df.columns and "日线J" in df.columns:
                bull = df["双均线多头"].dropna()
                # 整列全 NaN 时旧代码算出 np.nan，再被 f"{ab:.0f}%" 印成字面量 `nan%`
                ab = float(bull.mean()) * 100 if len(bull) else np.nan
                jlow = int((df["日线J"].dropna() < 0).sum())
                jhigh = int((df["日线J"].dropna() > 100).sum())
                pch = (_fnum(df["涨跌幅"].dropna().mean(), 2, "%", sign=True)
                       if "涨跌幅" in df.columns and df["涨跌幅"].notna().any() else "-")
                rows.append((f"📚 Top{n}池 均线多头", _fnum(ab, 0, "%"),
                             "池均涨跌幅", pch))
                rows.append((f"💧 池内 日线J<0 新低", f"{jlow} 家",
                             "💥 池内 日线J>100 新高", f"{jhigh} 家"))

    # 2xN 网格渲染
    def _cell(label, value):
        return (f'<div style="background:#f7f9fc;border-radius:8px;padding:10px 12px;min-height:58px;'
                f'border:1px solid #eef1f7">'
                f'<div style="font-size:11px;color:#8791a8">{_esc(label)}</div>'
                f'<div style="font-size:14.5px;font-weight:700;color:#1c2333;margin-top:4px;line-height:1.3">{value}</div>'
                f'</div>')

    grid_items = ""
    for l1, v1, l2, v2 in rows:
        grid_items += (f'<div class="dsm-brd-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">'
                       f'{_cell(l1, v1)}{_cell(l2, v2)}</div>')

    html = ("<div style=\"background:#fff;border-radius:10px;padding:14px 18px;margin-bottom:16px;"
            "box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:14px;line-height:1.7\">"
            # 窄屏（390px）下两列各只剩 ~163px，长读数要折 3 行 —— 直接纵向堆叠。
            # 用 !important 覆盖内联样式：万一客户端丢弃 <style>，退化成原样而不是坏掉。
            "<style>@media (max-width:420px){.dsm-brd-grid{grid-template-columns:1fr !important}}</style>"
            "<div style=\"font-weight:700;color:#1a1a2e;margin-bottom:10px\">📡 大盘宽度仪表盘</div>"
            + (grid_items or '<div style="color:#8791a8">暂无可展示数据</div>')
            + pool_stats_html
            + "</div>")

    md = "## 📡 大盘宽度仪表盘\n\n"
    for l1, v1, l2, v2 in rows:
        md += f"- {l1}: {v1}　｜　{l2}: {v2}\n"
    return {"html": html, "md": md, "data": rows}


# --------------------------- 3. 超跌/超买机会榜 ---------------------------

OPP_COLS_DISP = ["排名", "代码", "名称", "涨跌幅", "最新价",
                 "日线J", "周线J", "月线J", "PE_TTM", "PE历史分位%", "PB_MRQ", "PB历史分位%",
                 "价距MA20%", "双均线多头"]


def _cond_oversold(df):
    """超跌候选池。"""
    cols = {c: pd.to_numeric(df[c], errors="coerce") for c in ["日线J", "周线J", "月线J", "PE历史分位%", "PB_MRQ", "双均线多头", "价距MA20%"] if c in df.columns}
    dj = cols.get("日线J"); wj = cols.get("周线J"); mj = cols.get("月线J")
    pep = cols.get("PE历史分位%"); pb = cols.get("PB_MRQ")
    bull = cols.get("双均线多头"); gap = cols.get("价距MA20%")

    # 三周期共振超卖/新低
    reso = (dj < 20) & (wj < 20) & (mj < 20) if dj is not None else pd.Series(False, index=df.index)
    newlow = (dj < 0) & (wj < 0) & (mj < 0) if dj is not None else pd.Series(False, index=df.index)
    # 估值便宜 + 日线超卖（PE5年<30% 或 PB<1.5 兜底）
    value_oversold = (dj < 20) if dj is not None else pd.Series(False, index=df.index)
    if pep is not None:
        value_oversold = value_oversold & (pep < 30)
    if pb is not None:
        value_oversold = value_oversold & (pb < 4)  # 至少不贵
    # 缩量回踩：双均线多头 + 价距MA20%在 [-5%, 0%] 区间且 日线J < 40（回踩支撑位）
    pullback = (dj < 40) if dj is not None else pd.Series(False, index=df.index)
    if bull is not None:
        pullback = pullback & (bull > 0.5)
    if gap is not None:
        pullback = pullback & (gap >= -5) & (gap <= 0)
    return (reso | newlow | value_oversold | pullback).fillna(False)


def _cond_overbought(df):
    """超买候选池。"""
    cols = {c: pd.to_numeric(df[c], errors="coerce") for c in ["日线J", "周线J", "月线J", "PE历史分位%", "PB历史分位%"] if c in df.columns}
    dj = cols.get("日线J"); wj = cols.get("周线J"); mj = cols.get("月线J")
    pep = cols.get("PE历史分位%"); pbp = cols.get("PB历史分位%")

    reso = (dj > 80) & (wj > 80) & (mj > 80) if dj is not None else pd.Series(False, index=df.index)
    high_val = (dj > 80) & ((pep > 75) | (pbp > 75)) if dj is not None else pd.Series(False, index=df.index)
    if pep is None and pbp is None:
        high_val = pd.Series(False, index=df.index)
    return (reso | high_val).fillna(False)


def _tag_oversold(r):
    tags = []
    d, pep, bull, gap = (r.get(x) for x in ["日线J", "PE历史分位%", "双均线多头", "价距MA20%"])
    # 桶位判定一律走 signals.classify —— 这里曾是第三份手抄副本
    sig_key = signals.classify_row(r)[1]
    if sig_key == "newlow_resonance":
        tags.append("三周期新低")
    elif sig_key == "oversold_resonance":
        tags.append("三周期共振超卖")
    if pd.notna(pep) and pep < 30 and pd.notna(d) and d < 20:
        tags.append("估值+超卖双低")
    if pd.notna(bull) and bull > 0.5 and pd.notna(gap) and -5 <= gap <= 0 and pd.notna(d) and d < 40:
        tags.append("多头回踩MA20")
    return " / ".join(tags) if tags else "超跌候选"


def _tag_overbought(r):
    d, pep, pbp = (r.get(x) for x in ["日线J", "PE历史分位%", "PB历史分位%"])
    tags = []
    if signals.classify_row(r)[1] == "overbought_resonance":
        tags.append("三周期共振超买")
    if pd.notna(d) and d > 80 and ((pd.notna(pep) and pep > 75) or (pd.notna(pbp) and pbp > 75)):
        tags.append("高位+估值顶区")
    return " / ".join(tags) if tags else "超买候选"


def opportunity_board(metrics_csv, market="A", top_n=30, email_picks=5):
    """机会榜。

    返回 {"oversold_html", "overbought_html", "oversold_md", "overbought_md",
            "email_picks_html", "email_picks_md",
            "oversold_df", "overbought_df"}
    全部 html 含 30 行完整表格，可直接嵌 Pages；email_picks 仅含精选前 5 只小卡片（邮件正文末尾精选）。
    """
    empty = {"oversold_html": "", "overbought_html": "", "oversold_md": "", "overbought_md": "",
             "html": "", "md": "",
             "email_picks_html": "", "email_picks_md": "",
             "oversold_df": pd.DataFrame(), "overbought_df": pd.DataFrame()}
    if not os.path.exists(metrics_csv):
        return empty
    df = pd.read_csv(metrics_csv, dtype={"代码": str})
    if df.empty:
        return empty
    df = _to_numeric(df, ["日线J", "周线J", "月线J", "PE历史分位%", "PB_MRQ", "PB历史分位%",
                          "双均线多头", "价距MA20%", "涨跌幅", "最新价", "排名"])

    # ---- 超跌榜 ----
    mask_sell = _cond_oversold(df)
    df_s = df.loc[mask_sell].copy()
    # 便宜 + J 低优先：合成得分
    if not df_s.empty:
        def _score_s(r):
            s = 0.0
            for c in ["日线J", "周线J", "月线J"]:
                if pd.notna(r.get(c)):
                    s += max(0.0, 20.0 - r[c]) / 20.0  # J 越低得分越高
            if pd.notna(r.get("PE历史分位%")):
                s += (100.0 - r["PE历史分位%"]) / 100.0
            if pd.notna(r.get("PB_MRQ")) and r["PB_MRQ"] > 0:
                s += max(0.0, min(1.0, (5.0 - r["PB_MRQ"]) / 4.0))
            if pd.notna(r.get("双均线多头")) and r["双均线多头"] > 0.5:
                s += 0.5  # 多头趋势下的超跌更安全
            return round(s, 3)
        df_s["_score"] = df_s.apply(_score_s, axis=1)
        df_s = df_s.sort_values("_score", ascending=False).reset_index(drop=True)
    df_s_top = df_s.head(top_n).copy()
    df_s_top["入选标签"] = df_s_top.apply(_tag_oversold, axis=1)

    # ---- 超买榜 ----
    mask_buy = _cond_overbought(df)
    df_b = df.loc[mask_buy].copy()
    if not df_b.empty:
        def _score_b(r):
            s = 0.0
            for c in ["日线J", "周线J", "月线J"]:
                if pd.notna(r.get(c)):
                    s += max(0.0, r[c] - 80.0) / 20.0
            if pd.notna(r.get("PE历史分位%")):
                s += r["PE历史分位%"] / 100.0
            if pd.notna(r.get("PB历史分位%")):
                s += r["PB历史分位%"] / 100.0
            return round(s, 3)
        df_b["_score"] = df_b.apply(_score_b, axis=1)
        df_b = df_b.sort_values("_score", ascending=False).reset_index(drop=True)
    df_b_top = df_b.head(top_n).copy()
    df_b_top["入选标签"] = df_b_top.apply(_tag_overbought, axis=1)

    def _fmt(v, digits=2, suffix=""):
        if pd.isna(v):
            return "-"
        if isinstance(v, (int, np.integer)) or (isinstance(v, float) and float(v).is_integer() and digits == 0):
            return f"{int(v)}{suffix}"
        return f"{float(v):.{digits}f}{suffix}"

    def _chg(v):
        if pd.isna(v):
            return '<span style="color:#adb5bd">-</span>'
        vv = float(v)
        color = "#fa5252" if vv > 0 else "#2f9e44" if vv < 0 else "#adb5bd"
        return f'<span style="color:{color};font-weight:600">{vv:+.2f}%</span>'

    def _j_cell(v):
        if pd.isna(v):
            return "-"
        vv = float(v)
        if vv < 0 or vv < 20:
            cls, color = "oversold", "#1971c2"
        elif vv > 100 or vv > 80:
            cls, color = "overbought", "#d9480f"
        else:
            cls, color = "normal", "#495057"
        return f'<span style="color:{color};font-weight:600">{vv:.1f}</span>'

    def _table(title, sub_df):
        if sub_df.empty:
            return ""
        header = "<tr>" + "".join(f'<th style="border-bottom:2px solid #eee;padding:7px 8px;font-size:12px;color:#8791a8;font-weight:600">{h}</th>'
                                 for h in ["#", "代码", "名称", "入选标签", "涨跌幅", "日线J", "周线J", "月线J",
                                           "PE/PB分位%", "最新价"]) + "</tr>"
        rows_out = ""
        for i, (_, r) in enumerate(sub_df.iterrows(), 1):
            pe_s = f"{_fmt(r.get('PE历史分位%'), 0)}" if pd.notna(r.get("PE历史分位%")) else "-"
            pb_s = f"{_fmt(r.get('PB历史分位%'), 0)}" if pd.notna(r.get("PB历史分位%")) else "-"
            # 名称/代码来自第三方接口，必须转义后再进 HTML
            name_cell = _esc(r.get("名称", ""))
            code_cell = _esc(str(r.get("代码", "")).zfill(6 if market != "HK" else 5))
            rows_out += (f'<tr style="border-bottom:1px solid #f4f6fa">'
                         f'<td style="padding:6px 8px;color:#8791a8;font-size:12px">{i}</td>'
                         f'<td style="padding:6px 8px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;color:#495057">{code_cell}</td>'
                         f'<td style="padding:6px 8px;font-weight:600;color:#1c2333">{name_cell}</td>'
                         f'<td style="padding:6px 8px;font-size:11.5px;color:#5f3dc4">{str(r["入选标签"])}</td>'
                         f'<td style="padding:6px 8px;text-align:right">{_chg(r.get("涨跌幅"))}</td>'
                         f'<td style="padding:6px 8px;text-align:right">{_j_cell(r.get("日线J"))}</td>'
                         f'<td style="padding:6px 8px;text-align:right">{_j_cell(r.get("周线J"))}</td>'
                         f'<td style="padding:6px 8px;text-align:right">{_j_cell(r.get("月线J"))}</td>'
                         f'<td style="padding:6px 8px;text-align:right;font-size:12px;color:#495057">{pe_s} / {pb_s}</td>'
                         f'<td style="padding:6px 8px;text-align:right;font-size:12.5px">{_fmt(r.get("最新价"), 2)}</td>'
                         f'</tr>')
        title_bar = f'<div style="font-weight:700;color:#1a1a2e;margin-bottom:10px">{title}</div>'
        return title_bar + ('<div style="overflow:auto;border:1px solid #eceef4;border-radius:10px">'
                            + f'<table style="width:100%;border-collapse:collapse;font-size:13px">{header}{rows_out}</table></div>')

    s_html = _table("🧊 超跌/低吸机会榜（TOP " + str(len(df_s_top)) + "）", df_s_top)
    b_html = _table("🔥 超买/高抛观察榜（TOP " + str(len(df_b_top)) + "）", df_b_top)

    html = ""
    if s_html:
        html += ("<div style=\"background:#fff;border-radius:10px;padding:14px 18px;margin-bottom:16px;"
                 "box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:14px;line-height:1.7\">" + s_html + "</div>")
    if b_html:
        html += ("<div style=\"background:#fff;border-radius:10px;padding:14px 18px;margin-bottom:16px;"
                 "box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:14px;line-height:1.7\">" + b_html + "</div>")

    # Markdown
    def _md_table(df_in, title_md):
        if df_in.empty:
            return ""
        lines = [f"### {title_md}",
                 "| # | 代码 | 名称 | 标签 | 涨跌幅 | 日J | 周J | 月J | PE/PB分位% |",
                 "|---|------|------|------|--------|------|------|------|------------|"]
        for i, (_, r) in enumerate(df_in.head(15).iterrows(), 1):
            pe_s = _fmt(r.get("PE历史分位%"), 0, "%") if pd.notna(r.get("PE历史分位%")) else "-"
            pb_s = _fmt(r.get("PB历史分位%"), 0, "%") if pd.notna(r.get("PB历史分位%")) else "-"
            lines.append(f"| {i} | {_md_esc(str(r.get('代码','')).zfill(6 if market!='HK' else 5))} "
                         f"| {_md_esc(r.get('名称',''))} | {_md_esc(r['入选标签'])} "
                         f"| {_fmt(r.get('涨跌幅'), 2, '%') if pd.notna(r.get('涨跌幅')) else '-'} "
                         f"| {_fmt(r.get('日线J'),1)} | {_fmt(r.get('周线J'),1)} | {_fmt(r.get('月线J'),1)} "
                         f"| {pe_s}/{pb_s} |")
        return "\n".join(lines) + "\n"

    md = "## 🎯 机会榜\n\n"
    md += _md_table(df_s_top, "🧊 超跌/低吸机会")
    md += "\n"
    md += _md_table(df_b_top, "🔥 超买/高抛观察")

    # 邮件底部精选（只取超跌前 email_picks 只，更实用）
    picks_html = ""
    picks_md = ""
    if not df_s_top.empty:
        p = df_s_top.head(email_picks).copy()
        cards = ""
        for _, r in p.iterrows():
            label = _esc(r["入选标签"])
            tag_style = "display:inline-block;background:#e7f5ff;color:#1864ab;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;margin-left:6px"
            cards += (f'<div style="background:linear-gradient(180deg,#fff,#f8f9ff);border:1px solid #e5e8f0;border-radius:10px;padding:10px 12px">'
                      f'<div style="display:flex;justify-content:space-between;align-items:center">'
                      f'<div><b style="color:#1c2333;font-size:14.5px">{_esc(r.get("名称",""))}</b>'
                      f'<span style="{tag_style}">{label}</span></div>'
                      f'<div style="text-align:right">'
                      f'<div style="font-size:15px;font-weight:700;color:#1c2333">{_fmt(r.get("最新价"), 2)}</div>'
                      f'<div>{_chg(r.get("涨跌幅"))}</div></div></div>'
                      f'<div style="margin-top:6px;font-size:12px;color:#495057;display:flex;gap:10px;flex-wrap:wrap">'
                      f'<span>日J {_j_cell(r.get("日线J"))}</span>'
                      f'<span>周J {_j_cell(r.get("周线J"))}</span>'
                      f'<span>月J {_j_cell(r.get("月线J"))}</span>'
                      f'<span>PE分位 {_fmt(r.get("PE历史分位%"),0,"%") if pd.notna(r.get("PE历史分位%")) else "-"}</span>'
                      f'<span>PB_MRQ {_fmt(r.get("PB_MRQ"),2) if pd.notna(r.get("PB_MRQ")) else "-"}</span>'
                      f'<span>距MA20 {_fmt(r.get("价距MA20%"),2,"%") if pd.notna(r.get("价距MA20%")) else "-"}</span>'
                      f'</div></div>')
        picks_html = ("<div style=\"background:#fff;border-radius:10px;padding:14px 18px;margin-bottom:16px;"
                      "box-shadow:0 1px 3px rgba(0,0,0,0.08);font-size:14px;line-height:1.7\">"
                      f'<div style="font-weight:700;color:#1a1a2e;margin-bottom:10px">📌 超跌精选 {len(p)} 只（邮件速览）</div>'
                      f'<div style="display:grid;gap:8px">{cards}</div></div>')

        md_lines = [f"### 📌 超跌精选 {len(p)} 只"]
        for i, (_, r) in enumerate(p.iterrows(), 1):
            md_lines.append(
                f"{i}. **{_md_esc(r.get('名称',''))}**（{_md_esc(str(r.get('代码','')).zfill(6 if market!='HK' else 5))}｜{_md_esc(r['入选标签'])}）"
                f"｜价 {_fmt(r.get('最新价'),2)}｜{_fmt(r.get('涨跌幅'),2,'%') if pd.notna(r.get('涨跌幅')) else '-'}｜"
                f"日J {_fmt(r.get('日线J'),1)}｜PE分位 {_fmt(r.get('PE历史分位%'),0,'%') if pd.notna(r.get('PE历史分位%')) else '-'}"
            )
        picks_md = "\n".join(md_lines) + "\n"

    return {
        "oversold_html": s_html,
        "overbought_html": b_html,
        "html": html,
        "oversold_md": _md_table(df_s_top, "🧊 超跌/低吸机会"),
        "overbought_md": _md_table(df_b_top, "🔥 超买/高抛观察"),
        "md": md,
        "email_picks_html": picks_html,
        "email_picks_md": picks_md,
        "oversold_df": df_s_top.drop(columns=["_score"], errors="ignore"),
        "overbought_df": df_b_top.drop(columns=["_score"], errors="ignore"),
    }
