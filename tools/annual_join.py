# -*- coding: utf-8 -*-
"""年度串联表：业绩 ↔ 分红 ↔ 回购，一年一行。

    python tools/annual_join.py --code 601398

## 为什么要有这一张表

营收/净利/ROE 在 `fundamentals.csv`，每股分红在 `dividends.csv`，
回购公告在 `buybacks.csv` —— 三个文件、三种口径、三个时间键。
分开看永远看不出「**业绩变了，分红跟着变了吗？**」

把同一财务年度放进一行，才能一眼看见：
  * 工行 ROE 从 13.8% 一路降到 9.5%，但分红率死死钉在 30%；
  * 美的在哪些年既分红又回购。

## 口径

* 业绩取**年报**（Q4，年内累计）；没有年报的年份跳过并标注。
* 每股分红按 `reportDate` 的**财务年度**归集（年报+中报相加），不是按除权日。
* 分红率 = 每股分红 × 总股本 ÷ 归母净利。
* 股息率 = 每股分红 ÷ **当年日均价**（不是年末价，见 dca_check 的说明）。
* 回购按**公告日年份**归集，区分「注销式」（真减少股本）与「激励式」。
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "_fin_cache")
sys.stdout.reconfigure(encoding="utf-8")


def _read(name):
    p = os.path.join(CACHE, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _num(v):
    try:
        return float(v) if v not in (None, "", "nan", "None") else None
    except (TypeError, ValueError):
        return None


def build_rows(code):
    code = str(code).zfill(6)
    fund = [r for r in _read("fundamentals.csv") if str(r["code"]).zfill(6) == code]
    divs = [r for r in _read("dividends.csv") if str(r["code"]).zfill(6) == code]
    bks = [r for r in _read("buybacks.csv") if str(r["code"]).zfill(6) == code]
    pys = {str(r["year"]): _num(r.get("avgClose"))
           for r in _read("prices_year.csv") if str(r["code"]).zfill(6) == code}

    # 年报（Q4）
    annual = {}
    for r in fund:
        if int(r["quarter"]) == 4:
            annual[int(r["year"])] = r
    # 最新一期（用于没有年报的当前年）
    latest = {}
    for r in fund:
        y = int(r["year"])
        if y not in latest or int(r["quarter"]) > int(latest[y]["quarter"]):
            latest[y] = r

    # 每股分红（按财务年度）
    dps = defaultdict(float)
    for r in divs:
        rd, v = (r.get("reportDate") or "")[:10], _num(r.get("cashPs"))
        if rd and v:
            dps[int(rd[:4])] += v

    # 回购（按公告年）
    bk = defaultdict(lambda: {"注销": 0, "进展": 0})
    for r in bks:
        d = (r.get("date") or "")[:10]
        if d:
            bk[int(d[:4])][r.get("kind") or "进展"] += 1

    rows = []
    for y in sorted(annual):
        a = annual[y]
        rev, npf = _num(a.get("revenue")), _num(a.get("netProfit"))
        share = _num(a.get("totalShare"))
        d = dps.get(y)
        rows.append(dict(
            year=y, complete=True,
            revenue=rev / 1e8 if rev else None,
            netProfit=npf / 1e8 if npf else None,
            yoyRev=_num(a.get("yoyRev")), yoyNi=_num(a.get("yoyNi")),
            roe=(_num(a.get("roe")) or 0) * 100 if a.get("roe") else None,
            margin=(npf / rev * 100) if (rev and npf) else None,
            dps=d,
            payout=(d * share / npf * 100) if (d and share and npf) else None,
            yld=(d / pys[str(y)] * 100) if (d and pys.get(str(y))) else None,
            bk_cancel=bk[y]["注销"], bk_prog=bk[y]["进展"],
        ))
    # 未走完的当年（有数据但无年报）
    for y in sorted(latest):
        if y in annual:
            continue
        r = latest[y]
        rev, npf = _num(r.get("revenue")), _num(r.get("netProfit"))
        rows.append(dict(
            year=y, complete=False,
            revenue=rev / 1e8 if rev else None,
            netProfit=npf / 1e8 if npf else None,
            yoyRev=_num(r.get("yoyRev")), yoyNi=_num(r.get("yoyNi")),
            roe=(_num(r.get("roe")) or 0) * 100 if r.get("roe") else None,
            margin=(npf / rev * 100) if (rev and npf) else None,
            dps=dps.get(y), payout=None, yld=None,
            bk_cancel=bk[y]["注销"], bk_prog=bk[y]["进展"],
        ))
    rows.sort(key=lambda x: x["year"])
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="年度串联表：业绩 ↔ 分红 ↔ 回购")
    ap.add_argument("--code", default="601398")
    ap.add_argument("--years", type=int, default=12, help="显示最近 N 年")
    ap.add_argument("--out", default=None, help="CSV 输出")
    args = ap.parse_args(argv)

    code = str(args.code).zfill(6)
    rows = build_rows(code)
    if not rows:
        print("没有数据")
        return 1
    rows = rows[-args.years:]

    name = code
    for r in _read("codes.csv"):
        if str(r["code"]).zfill(6) == code:
            name = r.get("name") or code
            break

    print(f"\n{'='*104}")
    print(f"{name} {code} · 业绩 ↔ 分红 ↔ 回购（同一财务年度一行）")
    print(f"{'='*104}")
    print(f"{'年份':<6}{'营收(亿)':>11}{'同比':>8}{'归母净利':>11}{'同比':>8}"
          f"{'ROE':>8}{'净利率':>8}{'每股分红':>10}{'分红率':>8}{'股息率':>8}"
          f"{'回购(注销/激励)':>16}")
    print("-" * 104)

    def f(v, nd=0, pct=False, sign=False):
        if v is None:
            return f"{'—':>8}"
        if pct:
            return f"{v:>+7.1f}%" if sign else f"{v:>7.1f}%"
        return f"{v:>8,.{nd}f}"

    for r in rows:
        mark = "" if r["complete"] else "*"
        bk = (f"{r['bk_cancel']} / {r['bk_prog']}" if (r["bk_cancel"] or r["bk_prog"])
              else "—")
        print(f"{str(r['year'])+mark:<6}"
              f"{f(r['revenue'],0):>11}{f(r['yoyRev'],pct=True,sign=True)}"
              f"{f(r['netProfit'],0):>11}{f(r['yoyNi'],pct=True,sign=True)}"
              f"{f(r['roe'],pct=True):>8}{f(r['margin'],pct=True):>8}"
              f"{(f'{r['dps']:.4f}' if r['dps'] else '—'):>10}"
              f"{f(r['payout'],pct=True):>8}{f(r['yld'],pct=True):>8}"
              f"{bk:>16}")
    print("-" * 104)
    print("* 该年度尚未走完，业绩为已披露累计值；分红率/股息率不计算。")

    # 一句话观察
    complete = [r for r in rows if r["complete"] and r["payout"] is not None]
    if len(complete) >= 3:
        p0, p1 = complete[0]["payout"], complete[-1]["payout"]
        r0, r1 = complete[0]["roe"], complete[-1]["roe"]
        parts = []
        if r0 is not None and r1 is not None:
            parts.append(f"ROE {r0:.1f}% → {r1:.1f}%（{r1-r0:+.1f}pp）")
        if abs(p1 - p0) < 1:
            parts.append(f"分红率稳定在 {p1:.0f}%")
        else:
            parts.append(f"分红率 {p0:.0f}% → {p1:.0f}%")
        tot = sum(r["bk_cancel"] + r["bk_prog"] for r in rows)
        parts.append(f"回购公告 {tot} 条" if tot else "无回购")
        print("观察：" + " ｜ ".join(parts))

    out = args.out or os.path.join(CACHE, f"annual_{code}.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["年份", "营收(亿)", "营收同比", "归母净利(亿)", "净利同比",
                    "ROE", "净利率", "每股分红", "分红率", "股息率",
                    "回购注销", "回购激励", "完整年度"])
        for r in rows:
            w.writerow([r["year"], f"{r['revenue']:.0f}" if r["revenue"] else "",
                        f"{r['yoyRev']:+.1f}%" if r["yoyRev"] is not None else "",
                        f"{r['netProfit']:.0f}" if r["netProfit"] else "",
                        f"{r['yoyNi']:+.1f}%" if r["yoyNi"] is not None else "",
                        f"{r['roe']:.2f}%" if r["roe"] is not None else "",
                        f"{r['margin']:.1f}%" if r["margin"] is not None else "",
                        f"{r['dps']:.4f}" if r["dps"] else "",
                        f"{r['payout']:.0f}%" if r["payout"] is not None else "",
                        f"{r['yld']:.2f}%" if r["yld"] is not None else "",
                        r["bk_cancel"], r["bk_prog"],
                        "是" if r["complete"] else "否"])
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
