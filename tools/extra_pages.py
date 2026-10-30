# -*- coding: utf-8 -*-
"""三个「已经算出来但没人看」的页面 —— 把散落的产出接回站点。

    python tools/extra_pages.py

生成：

* `docs/基本面/格雷厄姆.html` —— 七条标准汇总（全过名单 / 逐条通过率）
* `docs/基本面/AH比价.html` —— 16 组同名 A/H 股的估值差
* `docs/基本面/定投回测.html` —— 299 只的定投结果分位与极值

## 为什么单独有这个文件

这些数据早就算出来了（`graham.py`、`tools/ah_compare.py`、`tools/dca_batch.py`），
但只有对应的命令行工具自己读 —— **页面上一个入口都没有**，等于算完就丢了。

这里的三个页面都复用 `build_fundamentals.CSS`，视觉与个股页一致，
不做第二套样式（否则两套会慢慢跑偏）。
"""
import csv
import os
import statistics as st
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.stdout.reconfigure(encoding="utf-8")

import build_fundamentals as bf  # noqa: E402
import graham  # noqa: E402

OUT = os.path.join(BASE_DIR, "docs", "基本面")
POOL = os.path.join(BASE_DIR, "_pool_cache")
CACHE = os.path.join(BASE_DIR, "_fin_cache")


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _stamp(path):
    """CSV 的修改时间 → 「数据生成于 X（N 天前）」。

    A/H 和定投是低频任务（定投要跑 300 次 K 线请求），不跟日更。
    不写日期的话，读者会以为每天都是新的。
    """
    import time
    from datetime import date, datetime
    if not os.path.exists(path):
        return ""
    ts = os.path.getmtime(path)
    d = datetime.fromtimestamp(ts)
    days = (date.today() - d.date()).days
    when = d.strftime("%Y-%m-%d")
    return f"数据生成于 {when}" + (f"（{days} 天前）" if days > 0 else "（今天）")


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _page(title, subtitle, body, out_name):
    """统一外壳。"""
    html = (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>{bf.CSS}</style></head>'
            f'<body><div class="page">'
            f'<h1>{title}<span class="unit">{subtitle}</span></h1>'
            + "".join(body) +
            '<div class="ft">数据：东财 datacenter / 本地估值序列　·　仅供参考，不构成投资建议</div>'
            '</div></body></html>')
    path = os.path.join(OUT, out_name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"  → {path}  ({len(html) / 1024:.0f}KB)")


# ── ① 格雷厄姆七条 ──────────────────────────────────────────
def graham_page():
    payloads = bf.load_cache_payloads(CACHE)
    bal = {}
    for r in _rows(os.path.join(CACHE, "balance.csv")):
        bal.setdefault(str(r["code"]).zfill(6), []).append(r)
    meta = {}
    with open(bf.latest_metrics_path(), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            meta[str(r.get("代码", "")).zfill(6)] = r
    for r in _rows(os.path.join(CACHE, "codes.csv")):
        c = str(r["code"]).zfill(6)
        if c in meta and (r.get("industry") or "").strip():
            meta[c]["行业"] = r["industry"].strip()

    res, per = [], {i: {"pass": 0, "fail": 0, "na": 0, "nodata": 0} for i in range(1, 8)}
    for code, d in payloads.items():
        m = meta.get(code)
        if not m:
            continue
        rows, summ = graham.check(d.get("quarters") or [], d.get("dividends") or [],
                                  bal.get(code, []), m)
        if not rows:
            continue
        for r in rows:
            per[r["no"]][r["status"]] += 1
        res.append(dict(code=code, name=m.get("名称") or code,
                        ind=m.get("行业") or "", summ=summ, rows=rows))
    res.sort(key=lambda r: (r["summ"]["fail"], r["summ"]["nodata"], r["name"]))

    full = [r for r in res if r["summ"]["fail"] == 0 and r["summ"]["nodata"] == 0]
    names = {r["no"]: r["name"] for r in res[0]["rows"]} if res else {}

    body = []
    body.append('<p class="strip">《聪明的投资者》1973 第 4 版 · 第 14 章 · '
                f'全池 {len(res)} 只里 <b>{len(full)} 只</b>七条全过</p>')

    # 偏差警告放最前 —— 不读它就会误读下面的名单
    fin = sum(1 for r in full if graham._is_fin(r["ind"]))
    body.append('<div class="fn" style="border-left:3px solid var(--warn);'
                'margin:14px 0 22px;background:rgba(255,210,30,.06);border-radius:0 8px 8px 0;'
                'padding:13px 15px;color:var(--dim)">'
                f'⚠ <b>读这份名单前必须知道一件事</b>：七条全过的 {len(full)} 只里，'
                f'<b>{fin} 只是金融股</b>（银行/保险/券商）。不是因为它们更符合格雷厄姆，'
                '而是<b>第 2 条「财务状况」对金融业判「不适用」—— 等于少考了一门</b>。'
                '银行没有流动资产/流动负债科目，格雷厄姆在《证券分析》里另有标准。'
                '它们 PE/PB 低、分红长，剩下六条容易过。<br>'
                '要看「按工业口径完整考过七条」的，请用下面的筛选条件排除金融股。'
                '</div>')

    body.append('<h2>七条全过<span class="cnt">'
                f'{len(full)} 只</span><span class="hint">按行业，金融股已标注</span></h2>')
    body.append('<table><thead><tr><th class="k">标的</th><th>行业</th>'
                '<th>PE(3年均)</th><th>PB</th><th>说明</th></tr></thead><tbody>')
    for r in full:
        rows = {x["no"]: x for x in r["rows"]}
        pe = rows[6]["actual"].replace("PE(3 年平均) ", "")
        pb = rows[7]["actual"].split("　")[0].replace("PB ", "")
        tag = ('<span style="color:var(--bad)">金融 · 少考第 2 条</span>'
               if graham._is_fin(r["ind"]) else '<span style="color:var(--ok)">完整七条</span>')
        body.append(f'<tr><td class="k"><a href="{r["code"]}.html">{r["name"]}</a>'
                    f'<span class="mut"> {r["code"]}</span></td>'
                    f'<td class="ind">{r["ind"]}</td><td>{pe}</td><td>{pb}</td>'
                    f'<td>{tag}</td></tr>')
    body.append('</tbody></table>')

    body.append('<h2>逐条通过率<span class="cnt">全池</span></h2>')
    body.append('<table><thead><tr><th class="k">标准</th><th>通过</th><th>不通过</th>'
                '<th>不适用</th><th>数据不足</th><th>通过率</th></tr></thead><tbody>')
    for no in range(1, 8):
        c = per[no]
        tot = sum(c.values())
        rate = c["pass"] / tot * 100 if tot else 0
        body.append(f'<tr><td class="k">{no}. {names.get(no, "")}</td>'
                    f'<td class="pos">{c["pass"]}</td><td class="neg">{c["fail"]}</td>'
                    f'<td class="mut">{c["na"]}</td><td class="mut">{c["nodata"]}</td>'
                    f'<td class="mut">{rate:.0f}%</td></tr>')
    body.append('</tbody></table>')

    body.append('<details><summary>不通过的都卡在哪一条（按标准分组）</summary>')
    for no in range(1, 8):
        bad = [r for r in res if any(x["no"] == no and x["status"] == graham.FAIL
                                    for x in r["rows"])]
        if not bad:
            continue
        body.append(f'<p class="strip"><b>{no}. {names.get(no, "")}</b> —— '
                    f'{len(bad)} 只不通过</p><p class="fn" style="line-height:2">'
                    + "、".join(f'{r["name"]}' for r in bad[:40])
                    + ('…' if len(bad) > 40 else '') + '</p>')
    body.append('</details>')

    _page("格雷厄姆七条", "第 14 章原文口径 · 逐条判定", body, "格雷厄姆.html")
    return len(full)


# ── ② A/H 比价 ──────────────────────────────────────────────
def ah_page():
    rows = _rows(os.path.join(POOL, "ah_compare.csv"))
    body = []
    if not rows:
        body.append('<p class="fn">还没有数据，先跑 <code>python tools/ah_compare.py</code></p>')
        _page("A/H 比价", "同一家公司，两个价格", body, "AH比价.html")
        return 0
    for r in rows:
        r["_prem"] = _num(r.get("pe_premium"))
    rows.sort(key=lambda r: (r["_prem"] is None, -(r["_prem"] or 0)))
    pos = [r for r in rows if (r["_prem"] or 0) > 0]
    med = st.median([r["_prem"] for r in pos]) if pos else 0

    body.append(f'<p class="strip muted" style="color:var(--dim2);font-size:12px">'
                f'{_stamp(os.path.join(POOL, "ah_compare.csv"))}</p>')
    body.append(f'<p class="strip">{len(rows)} 组同名 A/H 股里，'
                f'<b>{len(pos)} 组 A 股更贵</b>，PE 溢价中位 <b>{med:+.0f}%</b></p>')
    body.append('<div class="fn" style="margin:12px 0 20px">'
                '⚠ 按<b>公司名称</b>启发式匹配（去掉「股份/集团/控股/-R」等后缀），'
                '不是权威 A/H 名单：同名不等于同一公司，跨市场用不同名字的会漏掉。'
                '要做准得接港交所 A/H 名单或按 ISIN 匹配。</div>')
    body.append('<table><thead><tr><th class="k">公司</th><th>A 股</th><th>H 股</th>'
                '<th>PE(A)</th><th>PE(H)</th><th>溢价</th><th>股息(A)</th><th>股息(H)</th>'
                '</tr></thead><tbody>')
    for r in rows:
        def f(v, nd=1, suf=""):
            x = _num(v)
            return f"{x:.{nd}f}{suf}" if x is not None else "—"
        pr = f"{r['_prem']:+.0f}%" if r["_prem"] is not None else "—"
        cls = "neg" if (r["_prem"] or 0) > 20 else ""
        body.append(f'<tr><td class="k">{r.get("name", "")}</td>'
                    f'<td><a href="{r.get("code_a")}.html">{r.get("code_a")}</a></td>'
                    f'<td><a href="{r.get("code_hk")}.html">{r.get("code_hk")}</a></td>'
                    f'<td>{f(r.get("pe_a"))}</td><td>{f(r.get("pe_hk"))}</td>'
                    f'<td class="{cls}">{pr}</td>'
                    f'<td>{f(r.get("yld_a"), 2, "%")}</td>'
                    f'<td>{f(r.get("yld_hk"), 2, "%")}</td></tr>')
    body.append('</tbody></table>')
    _page("A/H 比价", "同名股 · 同一家公司两个价格", body, "AH比价.html")
    return len(rows)


# ── ③ 定投回测 ──────────────────────────────────────────────
def dca_page():
    rows = _rows(os.path.join(POOL, "dca_results.csv"))
    body = []
    if not rows:
        body.append('<p class="fn">还没有数据，先跑 <code>python tools/dca_batch.py</code></p>')
        _page("定投回测", "A 股长期定投的真实结果", body, "定投回测.html")
        return 0
    for r in rows:
        r["_x"] = _num(r.get("xirr"))
        r["_y"] = _num(r.get("years"))
        r["_d"] = _num(r.get("maxdd"))
    ok = [r for r in rows if r["_x"] is not None]
    long = [r for r in ok if (r["_y"] or 0) >= 8]

    def stats(rs, label):
        xs = sorted(r["_x"] for r in rs)
        ds = [r["_d"] for r in rs if r["_d"] is not None]
        if not xs:
            return
        body.append(f'<p class="strip"><b>{label}</b>（{len(rs)} 只）'
                    f'　XIRR 中位 <b>{st.median(xs):.2f}%</b>'
                    f'　四分位 {xs[len(xs) // 4]:.2f}% ~ {xs[3 * len(xs) // 4]:.2f}%'
                    f'　最大回撤中位 {st.median(ds):.1f}%</p>')

    body.append(f'<p class="strip" style="color:var(--dim2);font-size:12px">'
                f'{_stamp(os.path.join(POOL, "dca_results.csv"))}</p>')
    body.append('<p class="strip">每月 1,000 元、分红再投、<b>不复权价 + 分红单列</b>，'
                '2014-01 起</p>')
    body.append('<div class="fn" style="margin:10px 0 18px">'
                '⚠ 上市晚的股票，「2014 起」其实等于「上市起」，窗口太短时 XIRR 是噪声'
                '（实测有上市一年算出 −47% 的）。所以分两组看，<b>看 ≥8 年那组</b>。'
                '基准是「用同样现金流定投沪深300 再算 XIRR」—— 不是拿累计收益比指数涨幅，'
                '那两者口径不同。</div>')
    stats(ok, "全部")
    stats(long, "窗口 ≥8 年（可比）")

    body.append('<h2>最好 10 只<span class="cnt">按 XIRR</span></h2>')
    body.append('<table><thead><tr><th class="k">标的</th><th>XIRR</th><th>年数</th>'
                '<th>投入</th><th>期末</th><th>分红累计</th><th>最大回撤</th>'
                '</tr></thead><tbody>')
    for r in sorted(long or ok, key=lambda x: -x["_x"])[:10]:
        body.append(f'<tr><td class="k"><a href="{r["code"]}.html">{r.get("name","")}</a>'
                    f'<span class="mut"> {r["code"]}</span></td>'
                    f'<td class="pos">{r["_x"]:.1f}%</td><td class="mut">{r["_y"] or "—"}</td>'
                    f'<td>{_num(r.get("invested")) or 0:,.0f}</td>'
                    f'<td>{_num(r.get("value")) or 0:,.0f}</td>'
                    f'<td>{_num(r.get("div")) or 0:,.0f}</td>'
                    f'<td class="neg">{r["_d"]:.0f}%</td></tr>')
    body.append('</tbody></table>')

    body.append('<h2>最差 10 只<span class="cnt">按 XIRR</span></h2>')
    body.append('<table><thead><tr><th class="k">标的</th><th>XIRR</th><th>年数</th>'
                '<th>投入</th><th>期末</th><th>分红累计</th><th>最大回撤</th>'
                '</tr></thead><tbody>')
    for r in sorted(long or ok, key=lambda x: x["_x"])[:10]:
        body.append(f'<tr><td class="k"><a href="{r["code"]}.html">{r.get("name","")}</a>'
                    f'<span class="mut"> {r["code"]}</span></td>'
                    f'<td class="neg">{r["_x"]:.1f}%</td><td class="mut">{r["_y"] or "—"}</td>'
                    f'<td>{_num(r.get("invested")) or 0:,.0f}</td>'
                    f'<td>{_num(r.get("value")) or 0:,.0f}</td>'
                    f'<td>{_num(r.get("div")) or 0:,.0f}</td>'
                    f'<td class="neg">{r["_d"]:.0f}%</td></tr>')
    body.append('</tbody></table>')
    _page("定投回测", "每月 1,000 元 · 分红再投 · 不复权价", body, "定投回测.html")
    return len(ok)



def hk_page():
    """港股 100 只 —— 之前是孤岛：页面存在但没有任何入口能找到它们。

    港股**没有估值分位**（缺日线历史，算不出 PE 在自身历史上的位置），
    所以进不了「四信号榜单」。这里把它们本来有的列出来，并明确标注缺什么。
    """
    import hk_payload
    pl = hk_payload.load_hk_payloads(CACHE)
    sel = {str(r["code"]).zfill(5): r for r in _rows(os.path.join(POOL, "selected_hk.csv"))}
    recs = []
    for code, d in pl.items():
        qs = sorted([q for q in d["quarters"] if q.get("quarter") == 4],
                    key=lambda q: q["year"])
        if not qs:
            continue
        roe_first = next((q["roe"] for q in qs if q.get("roe")), None)
        roe_last = qs[-1].get("roe")
        # 最新单季净利同比
        lq = q3 = None
        allq = sorted(d["quarters"], key=lambda q: (q["year"], q["quarter"]))
        if allq:
            last = allq[-1]
            def single(y, q):
                """区间净利。

                港股只有中报（Q2）和年报（Q4），**没有 Q1**。
                所以「当期累计 − 上期累计」在 Q2 上没有上期可减 ——
                这时累计值本身就是这半年的数，直接用。
                （A 股的季度序列是齐的，那边走 verdict._single 的减法。）
                """
                cur = next((x for x in allq if x["year"] == y and x["quarter"] == q), None)
                if not cur or cur.get("netProfit") is None:
                    return None
                if q == 1:
                    return cur["netProfit"]
                prev = next((x for x in allq if x["year"] == y and x["quarter"] == q - 1), None)
                if not prev or prev.get("netProfit") is None:
                    return cur["netProfit"]     # 没有上期（港股中报）→ 累计即本期
                return cur["netProfit"] - prev["netProfit"]
            if last["quarter"] != 4:
                a, b = single(last["year"], last["quarter"]), single(last["year"] - 1, last["quarter"])
                if a is not None and b:
                    lq = a / b - 1
            q3 = last
        recs.append(dict(code=code, name=d.get("name") or code,
                         ind=(sel.get(code) or {}).get("ind") or "—",
                         pe=_num((sel.get(code) or {}).get("pe")),
                         pb=_num((sel.get(code) or {}).get("pb")),
                         yld=d.get("yld_now"), roe_first=roe_first, roe_last=roe_last,
                         lq=lq, last=q3))
    recs.sort(key=lambda r: -(r["roe_last"] or 0))

    body = []
    body.append(f'<p class="strip">港股 <b>{len(recs)} 只</b>（策展池）· '
                '按最近年报 ROE 排序</p>')
    body.append('<div class="fn" style="border-left:3px solid var(--warn);padding:13px 15px;'
                'margin:14px 0 22px;background:rgba(255,210,30,.06);border-radius:0 8px 8px 0;color:var(--dim)">'
                '⚠ <b>港股进不了「四信号榜单」</b>：没有港股日线，'
                '算不出 PE 在自身历史上的分位，所以「估值」这一信号缺失。<br>'
                '另外<b>港股分红历史拿不到可信序列</b>（`DPS_HKD` 与 `DIVIDEND_RATE` '
                '在所有报告期都是同一个快照值，`DIVI_RATIO` 在不同股票上含义不一致），'
                '所以「现金回报」这一信号也缺失。<br>'
                '这里只列<b>确实有</b>的：ROE 水平与趋势、PE/PB 绝对值、最新季报同比。'
                '</div>')
    body.append('<table><thead><tr><th class="k">标的</th><th>行业</th>'
                '<th>PE</th><th>PB</th><th>股息率</th><th>ROE(近一年)</th>'
                '<th>ROE 变化</th><th>最新季报</th></tr></thead><tbody>')
    for r in recs:
        d = (r["roe_last"] - r["roe_first"]) if (r["roe_last"] is not None
                                                 and r["roe_first"] is not None) else None
        dcls = "pos" if (d or 0) > 0 else "neg"
        if r["lq"] is None:
            lq = '<span class="na">—</span>'
        else:
            c = "pos" if r["lq"] >= 0.3 else ("neg" if r["lq"] <= -0.3 else "mut")
            lq = f'<span class="{c}">{r["lq"] * 100:+.0f}%</span>'
        pe_t = f'{r["pe"]:.1f}' if r["pe"] else "—"
        pb_t = f'{r["pb"]:.2f}' if r["pb"] else "—"
        yl_t = f'{r["yld"]:.2f}%' if r["yld"] else "—"
        roe_t = f'{r["roe_last"] * 100:.2f}%' if r["roe_last"] is not None else "—"
        dt = f'{d * 100:+.1f}pp' if d is not None else "—"
        body.append(f'<tr><td class="k"><a href="{r["code"]}.html">{r["name"]}</a>'
                    f'<span class="mut"> {r["code"]}</span></td>'
                    f'<td class="ind">{r["ind"]}</td><td>{pe_t}</td><td>{pb_t}</td>'
                    f'<td>{yl_t}</td><td>{roe_t}</td>'
                    f'<td class="{dcls}">{dt}</td><td>{lq}</td></tr>')
    body.append('</tbody></table>')
    _page("港股名单", "100 只策展池 · 缺估值分位与分红历史", body, "港股名单.html")
    return len(recs)


def main():
    os.makedirs(OUT, exist_ok=True)
    print("生成三个补充页面：")
    n1 = graham_page()
    n2 = ah_page()
    n3 = dca_page()
    n4 = hk_page()
    print(f"\n格雷厄姆全过 {n1} 只 · A/H {n2} 组 · 定投 {n3} 只 · 港股 {n4} 只")
    return 0



if __name__ == "__main__":
    sys.exit(main())
