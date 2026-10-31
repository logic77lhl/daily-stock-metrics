# -*- coding: utf-8 -*-
"""行业页 —— 路径二「我想了解某行业」的落点。

    python tools/industry_page.py

生成 `docs/基本面/行业.html`：

1. 行业排行表（可排序）：只数 / Q3 净利同比中位 / PE 分位中位 / 股息率中位 /
   值得看数 / 单季亏损数
2. 每个行业一个折叠块，里面是该行业的成员表（代码 / 名称 / 信号 / 关键数字）

## 为什么必须有这一页

之前首页有「行业板块温度榜」、Q3 页有「行业分化」，但**都点不进去** ——
看到一个行业异动，没有任何路径能看到它由哪些股票构成。这是「杂糅」的
最典型症状：数据聚出来了，但没有下一跳。

个股页的行业标签现在会链到这里（`行业.html#ind-<行业名>`）。
"""
import argparse
import csv
import os
import statistics as st
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

import build_fundamentals as bf  # noqa: E402
import site_theme  # noqa: E402
import verdict  # noqa: E402
import q3_digest  # noqa: E402

CACHE = os.path.join(BASE, "_fin_cache")
OUT = os.path.join(BASE, "docs", "基本面", "行业.html")


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def collect():
    """每只股票：行业、Q3 同比、PE 分位、股息率、信号。"""
    payloads = bf.load_cache_payloads(CACHE)
    meta = {}
    with open(bf.latest_metrics_path(), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            meta[str(r.get("代码", "")).zfill(6)] = r
    for r in _rows(os.path.join(CACHE, "codes.csv")):
        c = str(r["code"]).zfill(6)
        if c in meta and (r.get("industry") or "").strip():
            meta[c]["行业"] = r["industry"].strip()

    # Q3 单季同比（与财报季页同一套逻辑，符号翻转返回 None）
    q3recs, period = q3_digest.build()
    q3map = {r["code"]: r for r in q3recs}

    out = []
    for code, d in payloads.items():
        m = meta.get(code)
        if not m:
            continue
        qs = sorted(d["quarters"], key=lambda q: (q["year"], q["quarter"]))
        if not qs:
            continue
        sig, vd, det = verdict.decide(
            qs, bf.yearly_dividends(d.get("dividends") or []),
            d.get("yr_avg") or {}, m)
        q3 = q3map.get(code) or {}
        out.append(dict(
            code=code, name=(m.get("名称") or code),
            ind=(m.get("行业") or "未分类"),
            verdict=vd or "中性",
            pe_pct=det.get("pe_pct"), yld=det.get("yld"),
            yoy=q3.get("yoy"), sq=q3.get("sq"), note=q3.get("note") or "",
            has_q3=q3.get("has", False),
            jw=det.get("jw")))
    return out, period


def _med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def build(recs, period):
    Y, Q = period
    by = defaultdict(list)
    for r in recs:
        by[r["ind"]].append(r)

    rows = []
    for ind, members in by.items():
        yoy = _med([m["yoy"] for m in members])
        rows.append(dict(
            ind=ind, n=len(members),
            yoy=yoy,
            pe=_med([m["pe_pct"] for m in members]),
            yld=_med([m["yld"] for m in members]),
            watch=sum(1 for m in members if m["verdict"] == "值得看"),
            high=sum(1 for m in members if m["verdict"] == "高位"),
            loss=sum(1 for m in members if m["sq"] is not None and m["sq"] < 0),
            members=sorted(members, key=lambda m: (m["yoy"] is None, -(m["yoy"] or 0)))))
    # 默认按 Q3 净利同比降序；没有 Q3 的排最后
    rows.sort(key=lambda r: (r["yoy"] is None, -(r["yoy"] or 0)))
    return rows


def write_html(rows, period):
    Y, Q = period
    css = site_theme.page_css()
    n_stock = sum(r["n"] for r in rows)

    h = [f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>行业排行 · {Y}Q{Q}</title><style>{css}</style></head><body>',
         '<div class="page">',
         f'<header><div class="title">行业排行'
         f'<span class="code">{len(rows)} 个行业 · {n_stock} 只</span></div>',
         f'<div class="prow"><span class="meta">{Y}Q{Q} 单季净利同比 · '
         f'点表头可排序 · 点行业名展开成员</span></div></header>',
         site_theme.nav(home="../index.html", current="看行业")]

    h.append('<h2>行业排行<span class="unit">按 Q3 净利同比中位 · 可点表头排序</span></h2>')
    h.append('<table data-sort><thead><tr>'
             '<th class="k">行业</th><th>只数</th><th>Q3 净利同比</th>'
             '<th>PE 分位</th><th>股息率</th><th>值得看</th><th>高位</th>'
             '<th>单季亏损</th></tr></thead><tbody>')
    for r in rows:
        anchor = f'ind-{r["ind"]}'
        yoy_t = f'{r["yoy"] * 100:+.0f}%' if r["yoy"] is not None else "—"
        ycls = "" if r["yoy"] is None else ("pos" if r["yoy"] > 0 else "neg")
        pe_t = f'{r["pe"]:.0f}%' if r["pe"] is not None else "—"
        yld_t = f'{r["yld"]:.2f}%' if r["yld"] else "—"
        h.append(
            f'<tr><td class="k"><a href="#{anchor}">{r["ind"]}</a></td>'
            f'<td data-v="{r["n"]}">{r["n"]}</td>'
            f'<td data-v="{r["yoy"] if r["yoy"] is not None else ""}" class="{ycls}">{yoy_t}</td>'
            f'<td data-v="{r["pe"] if r["pe"] is not None else ""}">{pe_t}</td>'
            f'<td data-v="{r["yld"] if r["yld"] else ""}">{yld_t}</td>'
            f'<td data-v="{r["watch"]}">{r["watch"]}</td>'
            f'<td data-v="{r["high"]}">{r["high"]}</td>'
            f'<td data-v="{r["loss"]}" class="{"neg" if r["loss"] else "mut"}">{r["loss"] or "—"}</td>'
            f'</tr>')
    h.append('</tbody></table>')
    h.append('<div class="fn">「值得看 / 高位」是四信号分组的结果，与'
             '<a href="榜单.html">榜单</a>同一套规则。'
             'Q3 净利同比为单季口径，<b>扭亏/转亏不显示百分比</b>（比值无意义）。</div>')

    h.append('<h2>按行业看成员<span class="unit">点行业展开</span></h2>')
    for r in rows:
        h.append(f'<details id="ind-{r["ind"]}"><summary>'
                 f'<b>{r["ind"]}</b>　{r["n"]} 只'
                 + (f'　Q3 {r["yoy"] * 100:+.0f}%' if r["yoy"] is not None else "")
                 + f'　值得看 {r["watch"]}</summary>')
        h.append('<table><thead><tr><th class="k">标的</th><th>Q3 单季</th>'
                 '<th>同比</th><th>PE 分位</th><th>股息率</th><th>周J</th>'
                 '<th>信号</th></tr></thead><tbody>')
        for m in r["members"]:
            sq_t = (f'{m["sq"] / 1e8:,.1f} 亿' if m["sq"] is not None else "—")
            if m["yoy"] is not None:
                yoy_t = f'{m["yoy"] * 100:+.0f}%'
                ycls = "pos" if m["yoy"] > 0 else "neg"
            else:
                yoy_t = m["note"] or "—"
                ycls = "pos" if (m["sq"] or 0) > 0 else "neg"
            pe_t = f'{m["pe_pct"]:.0f}%' if m["pe_pct"] is not None else "—"
            yld_t = f'{m["yld"]:.2f}%' if m["yld"] else "—"
            jw_t = f'{m["jw"]:.0f}' if m["jw"] is not None else "—"
            vcls = {"值得看": "pos", "高位": "neg"}.get(m["verdict"], "mut")
            h.append(
                f'<tr><td class="k"><a href="{m["code"]}.html">{m["name"]}</a>'
                f'<span class="mut"> {m["code"]}</span></td>'
                f'<td>{sq_t}</td><td class="{ycls}">{yoy_t}</td>'
                f'<td>{pe_t}</td><td>{yld_t}</td><td>{jw_t}</td>'
                f'<td class="{vcls}">{m["verdict"]}</td></tr>')
        h.append('</tbody></table></details>')

    h.append('<div class="ft">数据：东财 datacenter 季频指标 + 仓库每日 metrics'
             '　·　仅供参考，不构成投资建议</div>')
    h.append('</div>' + site_theme.SORT_JS + '</body></html>')

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    print(f"→ {OUT}  ({len(rows)} 个行业)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="行业页")
    ap.parse_args(argv)
    recs, period = collect()
    rows = build(recs, period)
    write_html(rows, period)
    return 0


if __name__ == "__main__":
    sys.exit(main())
