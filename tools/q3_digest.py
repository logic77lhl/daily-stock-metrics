# -*- coding: utf-8 -*-
"""Q3 财报季速览：从 _fin_cache 直接算出一页结论。

    python tools/q3_digest.py            # 控制台
    python tools/q3_digest.py --html     # 另存 docs/基本面/Q3财报季.html

## 为什么单独做

个股页有「最新季报」信号，但那是**单只**视角。财报季里最要紧的是横向对比：
多少家变好、多少家变差、哪些行业集体转向、极端值在哪。
这些必须跨股票聚合，个股页做不到。
"""
import argparse
import csv
import os
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8")

import build_fundamentals as bf  # noqa: E402
import verdict  # noqa: E402

CACHE = os.path.join(BASE, "_fin_cache")
POOL = os.path.join(BASE, "_pool_cache")


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


def single(qs, y, q):
    """单季净利 = 当期累计 − 上期累计（Q1 即累计本身）。"""
    cur = next((x for x in qs if x["year"] == y and x["quarter"] == q), None)
    if not cur or cur.get("netProfit") is None:
        return None
    if q == 1:
        return cur["netProfit"]
    prev = next((x for x in qs if x["year"] == y and x["quarter"] == q - 1), None)
    if not prev or prev.get("netProfit") is None:
        return None
    return cur["netProfit"] - prev["netProfit"]


def single_rev(qs, y, q):
    cur = next((x for x in qs if x["year"] == y and x["quarter"] == q), None)
    if not cur or cur.get("revenue") is None:
        return None
    if q == 1:
        return cur["revenue"]
    prev = next((x for x in qs if x["year"] == y and x["quarter"] == q - 1), None)
    if not prev or prev.get("revenue") is None:
        return None
    return cur["revenue"] - prev["revenue"]


def build():
    payloads = bf.load_cache_payloads(CACHE)
    meta = {}
    with open(bf.latest_metrics_path(), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            meta[str(r.get("代码", "")).zfill(6)] = r
    for r in _rows(os.path.join(CACHE, "codes.csv")):
        c = str(r["code"]).zfill(6)
        if c in meta and (r.get("industry") or "").strip():
            meta[c]["行业"] = r["industry"].strip()

    # 全池最新报告期
    latest_q = {}
    for code, d in payloads.items():
        qs = sorted(d["quarters"], key=lambda q: (q["year"], q["quarter"]))
        if qs:
            latest_q[code] = (qs[-1]["year"], qs[-1]["quarter"], qs)

    # 以「出现最多的最新期」为本期
    from collections import Counter
    period = Counter((y, q) for y, q, _ in latest_q.values()).most_common(1)[0][0]
    Y, Q = period

    recs = []
    for code, d in payloads.items():
        if code not in meta:
            continue
        qs = sorted(d["quarters"], key=lambda q: (q["year"], q["quarter"]))
        if not qs:
            continue
        last = qs[-1]
        has_now = (last["year"], last["quarter"]) == (Y, Q)
        sq = single(qs, Y, Q)
        sq_prev = single(qs, Y - 1, Q)
        sr = single_rev(qs, Y, Q)
        sr_prev = single_rev(qs, Y - 1, Q)
        # 符号翻转（扭亏/转亏）时 pct 为 None —— 比值无意义，方向看 note
        yoy, _st, _note = verdict.quarter_change(sq, sq_prev)
        ryoy = (sr / sr_prev - 1) if (sr is not None and sr_prev) else None
        recs.append(dict(
            code=code, name=(meta[code].get("名称") or code),
            ind=(meta[code].get("行业") or "—"),
            has=has_now, sq=sq, yoy=yoy, ryoy=ryoy, note=_note,
            last_label=f"{last['year']}Q{last['quarter']}",
            statDate=(last.get("statDate") or "")[:10]))
    return recs, (Y, Q)


def summarize(recs, period):
    Y, Q = period
    done = [r for r in recs if r["has"]]
    pend = [r for r in recs if not r["has"]]
    ys = sorted(r["yoy"] for r in done if r["yoy"] is not None)
    rs = sorted(r["ryoy"] for r in done if r["ryoy"] is not None)

    print(f"\n{'='*62}")
    print(f"{Y}Q{Q} 财报季 · 池内 {len(recs)} 只，已披露 {len(done)} 只"
          f"（{len(done)/len(recs)*100:.0f}%），待披露 {len(pend)} 只")
    print(f"{'='*62}")

    if ys:
        pos = sum(1 for v in ys if v > 0)
        neg = sum(1 for v in ys if v < 0)
        big_drop = sum(1 for v in ys if v <= -0.3)
        big_up = sum(1 for v in ys if v >= 0.3)
        print(f"\n单季净利同比（{len(ys)} 只有可比数据）")
        print(f"  中位 {st.median(ys)*100:+.1f}%"
              f"　四分位 {ys[len(ys)//4]*100:+.1f}% ~ {ys[3*len(ys)//4]*100:+.1f}%")
        print(f"  同比增长 {pos} 只　下降 {neg} 只"
              f"　（≥+30% {big_up} 只，≤−30% {big_drop} 只）")
    if rs:
        pos = sum(1 for v in rs if v > 0)
        print(f"\n单季营收同比（{len(rs)} 只）")
        print(f"  中位 {st.median(rs)*100:+.1f}%　同比增长 {pos} 只 / 下降 {len(rs)-pos} 只")

    # 行业维度：至少 4 只有数据的行业才排
    by = {}
    for r in done:
        if r["yoy"] is not None:
            by.setdefault(r["ind"], []).append(r["yoy"])
    inds = [(k, st.median(v), len(v)) for k, v in by.items() if len(v) >= 4]
    inds.sort(key=lambda x: -x[1])
    if inds:
        print(f"\n行业单季净利同比中位（≥4 只）")
        print("  最好：" + "　".join(f"{k} {m*100:+.0f}%({n})" for k, m, n in inds[:5]))
        print("  最差：" + "　".join(f"{k} {m*100:+.0f}%({n})" for k, m, n in inds[-5:]))

    def show(title, rows, n=10):
        print(f"\n{title}")
        for r in rows[:n]:
            flag = ""
            if r["yoy"] is not None and r["yoy"] <= -0.3:
                flag = " ⚠"
            print(f"  {r['code']} {r['name'][:8]:<10}{r['ind'][:8]:<9}"
                  f"单季净利 {(r['sq'] or 0)/1e8:>8,.0f}亿  "
                  f"同比 {r['yoy']*100:>+7.1f}%{flag}"
                  if r["yoy"] is not None else "")

    valid = [r for r in done if r["yoy"] is not None]
    show("单季净利同比最好 10 只", sorted(valid, key=lambda r: -r["yoy"]))
    show("单季净利同比最差 10 只", sorted(valid, key=lambda r: r["yoy"]))

    loss = [r for r in done if r["sq"] is not None and r["sq"] < 0]
    if loss:
        print(f"\n单季亏损 {len(loss)} 只：" + "、".join(
            f'{r["name"]}({(r["sq"])/1e8:,.0f}亿)' for r in loss[:12]))

    if pend:
        print(f"\n尚未披露 {len(pend)} 只（最新仍为 Q2）：" +
              "、".join(r["name"] for r in pend[:15]) + ("…" if len(pend) > 15 else ""))
    return done, pend, ys, rs, inds


# ── 网页版 ────────────────────────────────────────────────
def write_html(recs, period, done, pend, ys, rs, inds):
    """生成 docs/基本面/Q3财报季.html —— 财报季的横向速览。"""
    import site_theme
    Y, Q = period
    css = site_theme.page_css()

    def card(name, big, sub, st=""):
        return (f'<div class="stat {st}"><div class="k">'
                f'<span class="dot {st}"></span>{name}</div>'
                f'<div class="v">{big}</div><div class="n">{sub}</div></div>')

    h = [f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>{Y}Q{Q} 财报季</title><style>{css}</style></head><body>',
         '<div class="page">',
         f'<header><div class="title">{Y}Q{Q} 财报季'
         f'<span class="code">{len(done)}/{len(recs)} 已披露</span></div>',
         '<div class="prow"><span class="meta">'
         '单季口径（当期累计 − 上期累计）· 同比对上年同季</span></div></header>',
         site_theme.nav(home="../index.html", current="财报季")]

    # 概览
    pos = sum(1 for v in ys if v > 0)
    neg = sum(1 for v in ys if v < 0)
    med_y = st.median(ys) * 100 if ys else 0
    med_r = st.median(rs) * 100 if rs else 0
    cards = [card("已披露", f'{len(done)}', f'共 {len(recs)} 只 · 待 {len(pend)}', "ok"),
             card("净利同比中位", f'{med_y:+.1f}%',
                  f'增长 {pos} / 下降 {neg}', "ok" if med_y > 0 else "bad"),
             card("营收同比中位", f'{med_r:+.1f}%',
                  f'增长 {sum(1 for v in rs if v > 0)} / 下降 {sum(1 for v in rs if v <= 0)}',
                  "ok" if med_r > 0 else "bad"),
             card("≥+30%", f'{sum(1 for v in ys if v >= 0.3)}', "只", ""),
             card("≤−30%", f'{sum(1 for v in ys if v <= -0.3)}', "只", "bad")]
    h.append('<div class="stats">' + "".join(cards) + '</div>')

    # 一句话结论：增收不增利
    if rs and ys and med_r > 2 and med_y < med_r:
        h.append(f'<div class="sum bad"><span class="mk"></span>'
                 f'增收不增利：营收中位 {med_r:+.1f}%，净利中位 {med_y:+.1f}%'
                 f'—— 收入在涨，利润被成本和价格战吃掉了</div>')
    h.append('<div class="fn">单季 = 当期累计 − 上期累计。<b>扭亏 / 转亏不显示百分比</b>'
             '（比值无意义）：从亏 0.9 亿变成赚 12 亿会算出 −1493%，'
             '但那是巨幅改善。这类按定性 + 绝对金额列在下面。</div>')

    # 行业
    if inds:
        h.append('<h2>行业分化<span class="unit">单季净利同比中位 · ≥4 只的行业</span></h2>')
        h.append('<table><thead><tr><th class="k">方向</th><th>行业</th>'
                 '<th>净利同比中位</th><th>只数</th></tr></thead><tbody>')
        for k, m, n in inds[:6]:
            h.append(f'<tr><td class="k pos">最强</td><td class="ind">{k}</td>'
                     f'<td class="pos">{m * 100:+.0f}%</td><td class="mut">{n}</td></tr>')
        for k, m, n in reversed(inds[-6:]):
            h.append(f'<tr><td class="k neg">最弱</td><td class="ind">{k}</td>'
                     f'<td class="neg">{m * 100:+.0f}%</td><td class="mut">{n}</td></tr>')
        h.append('</tbody></table>')

    # 最好 / 最差
    valid = [r for r in done if r["yoy"] is not None]

    def table(title, rows, cls):
        h.append(f'<h2>{title}<span class="unit">按单季净利同比</span></h2>')
        h.append('<table><thead><tr><th class="k">标的</th><th>行业</th>'
                 '<th>单季净利</th><th>同比</th></tr></thead><tbody>')
        for r in rows:
            h.append(f'<tr><td class="k"><a href="{r["code"]}.html">{r["name"]}</a>'
                     f'<span class="mut"> {r["code"]}</span></td>'
                     f'<td class="ind">{r["ind"]}</td>'
                     f'<td>{(r["sq"] or 0) / 1e8:,.0f} 亿</td>'
                     f'<td class="{cls}">{r["yoy"] * 100:+.0f}%</td></tr>')
        h.append('</tbody></table>')

    table("最好 12 只", sorted(valid, key=lambda r: -r["yoy"])[:12], "pos")
    table("最差 12 只", sorted(valid, key=lambda r: r["yoy"])[:12], "neg")

    # 扭亏 / 转亏
    flip = [r for r in done if r["yoy"] is None and r["note"]]
    if flip:
        h.append('<h2>扭亏 / 转亏<span class="unit">比值无意义，按绝对金额</span></h2>')
        h.append('<table><thead><tr><th class="k">标的</th><th>行业</th>'
                 '<th>单季净利</th><th>情况</th></tr></thead><tbody>')
        for r in sorted(flip, key=lambda r: (r["sq"] or 0)):
            cls = "pos" if (r["sq"] or 0) > 0 else "neg"
            h.append(f'<tr><td class="k"><a href="{r["code"]}.html">{r["name"]}</a>'
                     f'<span class="mut"> {r["code"]}</span></td>'
                     f'<td class="ind">{r["ind"]}</td>'
                     f'<td class="{cls}">{(r["sq"] or 0) / 1e8:,.1f} 亿</td>'
                     f'<td class="{cls}">{r["note"]}</td></tr>')
        h.append('</tbody></table>')

    if pend:
        h.append(f'<h2>待披露 {len(pend)} 只<span class="unit">最新仍为 Q2</span></h2>')
        h.append('<p class="strip">' + "、".join(r["name"] for r in pend) + '</p>')

    h.append('<div class="ft">数据：东财 datacenter 季频指标 · 单季差分自算'
             '　·　仅供参考，不构成投资建议</div>')
    h.append('</div></body></html>')
    out = os.path.join(BASE, "docs", "基本面", "Q3财报季.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    print(f"→ {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Q3 财报季速览")
    ap.add_argument("--html", action="store_true", help="另存网页")
    args = ap.parse_args(argv)
    recs, period = build()
    done, pend, ys, rs, inds = summarize(recs, period)
    if args.html:
        write_html(recs, period, done, pend, ys, rs, inds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
