# -*- coding: utf-8 -*-
"""把「每月买 100 股」的批量结果渲染成一张可排序的完整表格。

    python tools/dca_shares_page.py

数据来自 `_pool_cache/dca_shares_100.csv`（由 dca_shares_batch.py 生成）。

## 为什么加稳定性指标

只看收益率会误导：茅台 7 年投 1455 万、收益 −0.6%，但中途从 2600 跌到 1200，
对持有人是灾难。收益必须和「过程有多颠」一起看。

三个指标：
* **最大回撤** —— 组合市值从峰值的最大回落
* **年化波动** —— 月度净值收益率（剔除新增投入）的标准差 × √12
* **收益/回撤** —— Calmar 式的性价比：每承受 1% 回撤换多少收益

## 一个必须过滤的坑

上市不足 3 年的股票只有几个月的净值序列，波动率算出来 200%~400%，
纯粹是样本太少的假象。表格里标「样本不足」，不参与排序。
"""
import csv
import os
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8")

import site_theme  # noqa: E402

SRC = os.path.join(BASE, "_pool_cache", "dca_shares_100.csv")
OUT = os.path.join(BASE, "docs", "基本面", "定投固定股数.html")
MIN_YEARS = 3.0        # 少于 3 年的不参与排序（样本太短，波动率无意义）


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load():
    with open(SRC, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k in ("invested", "div_cash", "div_ratio", "shares", "value",
                  "ret", "mdd", "vol", "years", "pos_years", "n_years",
                  "buys", "last_px"):
            r[k] = _f(r.get(k))
        r["calmar"] = (r["ret"] / (r["mdd"] * 100)) if r["mdd"] > 0 else None
        r["short"] = r["years"] < MIN_YEARS
    return rows


def main(argv=None):
    rows = load()
    css = site_theme.page_css()
    full = [r for r in rows if not r["short"]]
    short = [r for r in rows if r["short"]]

    def med(key, rs=None):
        xs = [r[key] for r in (rs or full) if r[key] is not None]
        return st.median(xs) if xs else 0.0

    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>定投：每月 100 股</title><style>{css}</style></head><body>',
         '<div class="page">',
         f'<header><div class="title">定投：每月 100 股'
         f'<span class="code">{len(rows)} 只</span></div>',
         '<div class="prow"><span class="meta">2019-01 起 · 分红再投 · 送转已处理 · '
         '不复权价 · 点表头排序</span></div></header>',
         site_theme.nav(home="../index.html", current="定投")]

    # 概览
    def card(name, big, sub, st_=""):
        return (f'<div class="stat {st_}"><div class="k">'
                f'<span class="dot {st_}"></span>{name}</div>'
                f'<div class="v">{big}</div><div class="n">{sub}</div></div>')

    pos = sum(1 for r in full if r["ret"] > 0)
    h.append('<div class="stats">'
             + card("可比标的", f'{len(full)}', f'另有 {len(short)} 只上市不足 '
                    f'{MIN_YEARS:.0f} 年', "")
             + card("收益率中位", f'{med("ret"):+.1f}%',
                    f'盈利 {pos} / 亏损 {len(full) - pos}',
                    "ok" if med("ret") > 0 else "bad")
             + card("最大回撤中位", f'−{med("mdd") * 100:.1f}%',
                    f'最差 −{max(r["mdd"] for r in full) * 100:.1f}%', "bad")
             + card("年化波动中位", f'{med("vol") * 100:.1f}%',
                    "月度净值收益率的标准差", "warn")
             + card("收益/回撤 中位", f'{med("calmar"):.2f}',
                    "每承受 1% 回撤换多少收益", "")
             + '</div>')

    h.append('<div class="sum"><span class="mk"></span>'
             '定投的收益不取决于公司好坏，取决于买入时的价格 —— '
             '茅台投 1,455 万收益 −0.6%，工行投 5.3 万收益 +95%</div>')
    h.append('<div class="fn">口径：每月<b>第一个交易日</b>买 100 股（不复权收盘价）；'
             '分红按<b>当时持仓</b>在除权日再投；<b>送转按比例放大股数</b>'
             '（池内 117 只发生过送转，不处理会把「10 转 7」算成暴跌）；'
             '不计佣金印花税，分红税前。'
             '最大回撤 = 组合市值从峰值的最大回落；'
             '年化波动 = 月度净值收益率（<b>剔除当月新增投入</b>）的标准差 × √12。</div>')

    # 完整表
    h.append(f'<h2>完整表格<span class="cnt">{len(rows)} 只</span>'
             f'<span class="hint">点表头排序 · 标「样本不足」的不参与排序</span></h2>')
    h.append('<table data-sort><thead><tr>'
             '<th class="k">标的</th><th>行业</th><th>年数</th>'
             '<th>现金投入</th><th>分红占比</th><th>期末市值</th>'
             '<th>收益率</th><th>最大回撤</th><th>年化波动</th>'
             '<th>收益/回撤</th><th>正收益年</th></tr></thead><tbody>')
    # 默认按 收益/回撤 降序，样本不足排最后
    for r in sorted(rows, key=lambda x: (x["short"],
                                         -(x["calmar"] if x["calmar"] is not None
                                           else -999))):
        cls_ret = "pos" if r["ret"] > 0 else "neg"
        calmar = f'{r["calmar"]:.2f}' if r["calmar"] is not None else "—"
        if r["short"]:
            h.append(f'<tr><td class="k"><a href="{r["code"]}.html">{r["name"]}</a>'
                     f'<span class="mut"> {r["code"]}</span></td>'
                     f'<td class="ind">{r["ind"]}</td>'
                     f'<td data-v="{r["years"]}" class="mut">{r["years"]}</td>'
                     f'<td colspan="7" class="mut">样本不足 {MIN_YEARS:.0f} 年，'
                     f'不参与排序（收益 {r["ret"]:+.1f}%）</td></tr>')
            continue
        h.append(
            f'<tr><td class="k"><a href="{r["code"]}.html">{r["name"]}</a>'
            f'<span class="mut"> {r["code"]}</span></td>'
            f'<td class="ind">{r["ind"]}</td>'
            f'<td data-v="{r["years"]}" class="mut">{r["years"]}</td>'
            f'<td data-v="{r["invested"]}">{r["invested"]:,.0f}</td>'
            f'<td data-v="{r["div_ratio"]}" class="mut">{r["div_ratio"]:.1f}%</td>'
            f'<td data-v="{r["value"]}">{r["value"]:,.0f}</td>'
            f'<td data-v="{r["ret"]}" class="{cls_ret}">{r["ret"]:+.1f}%</td>'
            f'<td data-v="{r["mdd"]}" class="neg">−{r["mdd"] * 100:.1f}%</td>'
            f'<td data-v="{r["vol"]}" class="mut">{r["vol"] * 100:.1f}%</td>'
            f'<td data-v="{r["calmar"] if r["calmar"] is not None else ""}">{calmar}</td>'
            f'<td data-v="{r["pos_years"]}" class="mut">'
            f'{r["pos_years"]}/{r["n_years"]}</td></tr>')
    h.append('</tbody></table>')

    h.append('<div class="ft">数据：东财 datacenter 估值序列（不复权收盘价）+ '
             '仓库每日 metrics　·　仅供参考，不构成投资建议</div>')
    h.append('</div>' + site_theme.SORT_JS + '</body></html>')

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    print(f"→ {OUT}  （{len(rows)} 只，可比 {len(full)}）")

    # 控制台也给一份排名
    ranked = sorted([r for r in full if r["calmar"] is not None],
                    key=lambda x: -x["calmar"])
    print(f"\n收益/回撤 最好的 12 只（每承受 1% 回撤换多少收益）")
    print(f"  {'代码':<8}{'名称':<10}{'年数':>5}{'投入':>11}{'收益':>8}{'回撤':>8}"
          f"{'波动':>8}{'收益/回撤':>10}")
    for r in ranked[:12]:
        print(f"  {r['code']:<8}{r['name'][:9]:<10}{r['years']:>5.1f}"
              f"{r['invested']:>11,.0f}{r['ret']:>+7.1f}%{r['mdd'] * 100:>7.1f}%"
              f"{r['vol'] * 100:>7.1f}%{r['calmar']:>10.2f}")
    print(f"\n收益/回撤 最差的 8 只")
    for r in ranked[-8:]:
        print(f"  {r['code']:<8}{r['name'][:9]:<10}{r['years']:>5.1f}"
              f"{r['invested']:>11,.0f}{r['ret']:>+7.1f}%{r['mdd'] * 100:>7.1f}%"
              f"{r['vol'] * 100:>7.1f}%{r['calmar']:>10.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
