# -*- coding: utf-8 -*-
"""定投策略对比：**总投入相同**，只改投入时机。

    python tools/dca_strategies.py --code 601398 --start 2012-01-01 --monthly 1000

## 为什么这样比才公平

* **总投入固定**：所有策略在同一时间窗口内投出去的钱一样多（= 月数 × 每月金额）。
* **同一结束日**：期末同一天估值。
* **用 XIRR 排序**：定投、年投、一次性的现金流时间点完全不同，
  比"总收益率"会把"钱在场时间长短"混进来；XIRR 是资金加权年化，才可比。
* **未投出的钱**：择时策略可能一直不满足条件，剩下的现金按 0 收益计入终值 —— 不能假装它赚了。

## 口径

* 用**不复权**价格（分红才单独可见）；分红按除权日结算，可选再投。
* 每次买入用当日收盘价；A 股最小 100 股 —— 这里按金额买入并允许碎股，
  是为了让"总投入相同"严格成立（实盘会有零头差异，量级可忽略）。
"""
import argparse
import csv
import os
import sys
from datetime import date

sys.path.insert(0, r"C:\Users\user\.dsh\ungrouped\_probe_libs")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import http_util  # noqa: E402


# ─────────────────────────── 数据 ───────────────────────────
def load_prices(code, start, end):
    """baostock 不复权日线（含 high/low，J 要用）。"""
    import baostock as bs
    code_bs = f"{'sh' if str(code)[0] == '6' else 'sz'}.{code}"
    bs.login()
    rs = bs.query_history_k_data_plus(code_bs, "date,open,high,low,close",
                                      start_date=start.replace("-", "-"),
                                      end_date=end, frequency="d", adjustflag="3")
    rows = []
    while rs.error_code == "0" and rs.next():
        d, o, h, l, c = rs.get_row_data()
        if c:
            rows.append(dict(date=d, open=float(o), high=float(h),
                             low=float(l), close=float(c)))
    bs.logout()
    return rows


def load_divs(code):
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "_fin_cache", "dividends.csv")
    out = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if str(r["code"]).zfill(6) == str(code).zfill(6):
                    d, v = (r.get("exDate") or "")[:10], r.get("cashPs")
                    if d and v:
                        out[d] = out.get(d, 0.0) + float(v)
    return dict(sorted(out.items()))


def add_indicators(rows):
    """MA20 + KDJ 的 J（N=9, M1=3, M2=3）。"""
    closes = [r["close"] for r in rows]
    k = d = 50.0
    for i, r in enumerate(rows):
        r["ma20"] = sum(closes[max(0, i - 19):i + 1]) / len(closes[max(0, i - 19):i + 1]) \
            if i >= 19 else None
        win = rows[max(0, i - 8):i + 1]
        hi = max(x["high"] for x in win)
        lo = min(x["low"] for x in win)
        rsv = (r["close"] - lo) / (hi - lo) * 100 if hi > lo else 50.0
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
        r["j"] = 3 * k - 2 * d
    return rows


# ─────────────────────────── 预算日程 ───────────────────────────
def budget_days(rows, start):
    """返回 [(月/季/年 分组键, 该组首个交易日, 该组末个交易日)]"""
    months, quarters, years = {}, {}, {}
    order = []
    for r in rows:
        d = r["date"]
        if d < start:
            continue
        ym, yq, y = d[:7], f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}", d[:4]
        for bucket, key in ((months, ym), (quarters, yq), (years, y)):
            if key not in bucket:
                bucket[key] = [d, d]
                if bucket is months:
                    order.append(key)
            else:
                bucket[key][1] = d
    return months, quarters, years


def build_buys(kind, rows, start, M):
    """按策略生成 [(buy_date, amount)]，以及期末未投出的现金。"""
    months, quarters, years = budget_days(rows, start)
    n_month = len(months)
    total = n_month * M
    by_date = {r["date"]: r for r in rows}
    buys = []

    if kind == "monthly":
        for ym, (first, _last) in months.items():
            buys.append((first, M))
    elif kind == "quarterly":
        for _, (first, _last) in quarters.items():
            buys.append((first, 3 * M))
    elif kind == "semiannual":
        for y in sorted({k[:4] for k in months}):
            for half in ("01", "07"):
                days = [k for k in months if k[:4] == y and
                        ((half == "01" and k[5:7] <= "06") or (half == "07" and k[5:7] >= "07"))]
                if days:
                    buys.append((months[sorted(days)[0]][0], 6 * M))
    elif kind == "annual":
        for _, (first, _last) in years.items():
            buys.append((first, 12 * M))
    elif kind == "annual_end":
        for _, (_first, last) in years.items():
            buys.append((last, 12 * M))
    elif kind == "lump":
        buys.append((rows[0]["date"] if rows[0]["date"] >= start else
                     min(d for d in months.values())[0], total))
    elif kind in ("dip_ma20", "dip_j20"):
        pending = 0.0
        for ym in sorted(months):
            pending += M
            d = months[ym][0]
            r = by_date[d]
            cond = False
            if kind == "dip_ma20":
                cond = r["ma20"] is not None and r["close"] < r["ma20"]
            else:
                cond = r["j"] is not None and r["j"] < 20
            if cond and pending > 0:
                buys.append((d, pending))
                pending = 0.0
        return total, buys, pending
    else:
        raise ValueError(kind)

    spent = sum(a for _, a in buys)
    return total, buys, max(0.0, total - spent)


# ─────────────────────────── 评估 ───────────────────────────
def xirr(flows):
    if not flows:
        return None
    def npv(r):
        t0 = flows[0][0]
        return sum(a / (1 + r) ** ((d - t0).days / 365.0) for d, a in flows)
    if npv(-0.9999) < 0 and npv(3.0) < 0:
        return None
    lo, hi = -0.9999, 3.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def evaluate(rows, divs, buys, leftover, reinvest, start):
    """按 buys 买入，期间分红（可选再投），期末估值。"""
    by_date = {r["date"]: r for r in rows}
    buy_map = {}
    for d, a in buys:
        buy_map[d] = buy_map.get(d, 0.0) + a
    keys = [k for k in divs if k >= start]
    di = 0
    shares = 0.0          # 总股数（含分红再投买的）
    bought = 0.0          # 只用现金买的股数（算买入均价用）
    cost = 0.0
    cash_div = 0.0
    flows = []
    for r in rows:
        d = r["date"]
        if d < start:
            continue
        while di < len(keys) and keys[di] <= d:
            ex = keys[di]
            if shares > 0:
                amt = shares * divs[ex]
                cash_div += amt
                # 只有「收现金」时分红才是真正的现金流流入。
                # 再投的话它已经变成股票、体现在终值里了，再记一次就是双重计算。
                if not reinvest:
                    flows.append((date.fromisoformat(ex), amt))
                else:
                    shares += amt / r["close"]
            di += 1
        if d in buy_map:
            amt = buy_map.pop(d)
            add = amt / r["close"]
            shares += add
            bought += add
            cost += amt
            flows.append((date.fromisoformat(d), -amt))
    last = rows[-1]
    value = shares * last["close"]
    # 未投出的现金按 0 收益计入终值
    terminal = value + leftover + (0 if reinvest else cash_div)
    if reinvest:
        terminal = value + leftover
    flows.append((date.fromisoformat(last["date"]), value + leftover))
    profit = terminal - cost
    return dict(cost=cost, shares=shares, bought=bought, value=value, div=cash_div,
                leftover=leftover, terminal=terminal, profit=profit,
                ret=(terminal / cost - 1) if cost else 0.0,
                r=xirr(flows), n_buy=len(buys))


STRATEGIES = [
    ("月定投（每月首日）", "monthly"),
    ("季定投（每季首日）", "quarterly"),
    ("半年定投", "semiannual"),
    ("年定投（每年首日·一次投满全年）", "annual"),
    ("年定投（每年末日）", "annual_end"),
    ("一次性（期初全投）", "lump"),
    ("月定投·跌破 MA20 才买", "dip_ma20"),
    ("月定投·日 J<20 才买", "dip_j20"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="定投策略对比（总投入相同）")
    ap.add_argument("--code", default="601398")
    ap.add_argument("--start", default="2012-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--monthly", type=float, default=1000, help="每月金额（其余策略按此缩放）")
    ap.add_argument("--no-reinvest", action="store_true", help="分红收现金（不再投）")
    args = ap.parse_args(argv)

    reinvest = not args.no_reinvest
    print(f"抓 {args.code} 不复权日线（含 high/low）…")
    rows = add_indicators(load_prices(args.code, "2011-12-01", args.end))
    divs = load_divs(args.code)
    print(f"  {len(rows)} 个交易日，{len(divs)} 条分红；"
          f"分红{'再投' if reinvest else '收现金'}")

    out = []
    for label, kind in STRATEGIES:
        total, buys, leftover = build_buys(kind, rows, args.start, args.monthly)
        m = evaluate(rows, divs, buys, leftover, reinvest, args.start)
        out.append((label, total, m))

    print(f"\n{'策略':<30}{'投入':>10}{'次数':>6}{'平均成本':>10}"
          f"{'终值':>12}{'收益':>12}{'收益率':>9}{'XIRR':>8}{'未投出':>9}")
    print("-" * 108)
    for label, total, m in sorted(out, key=lambda x: -(x[2]["r"] or -9)):
        avg = m["cost"] / m["bought"] if m["bought"] else 0
        print(f"{label:<30}{total:>10,.0f}{m['n_buy']:>6}{avg:>10.3f}"
              f"{m['terminal']:>12,.0f}{m['profit']:>12,.0f}"
              f"{m['ret']:>8.1%}{(m['r'] or 0):>8.2%}{m['leftover']:>9,.0f}")
    print("-" * 108)
    base = next(x for x in out if x[0].startswith("月定投（"))
    best = max(out, key=lambda x: x[2]["r"] or -9)
    print(f"基准（月定投）XIRR {base[2]['r']:.2%}；最优 {best[0]} {best[2]['r']:.2%}"
          f"（差 {(best[2]['r'] - base[2]['r']) * 100:+.2f}pp）")
    print("\n注：端值＝期末市值＋未投出现金；收益＝端值−投入。"
          "一次性策略把全部资金放在期初，XIRR 是资金加权年化，才可与定投直接比。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
