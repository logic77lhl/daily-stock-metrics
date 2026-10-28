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
    """按策略生成 [(buy_date, amount)] 与期末未投出现金。

    **关键约束**：每一期的金额 = 该期实际包含的月数 × M。
    不能一律用 12×M / 3×M —— 部分年份上市的股票会被超投，
    「总投入相同」就不成立了，比较也就失去意义。
    """
    months, quarters, years = budget_days(rows, start)
    total = len(months) * M
    by_date = {r["date"]: r for r in rows}
    buys = []

    if kind == "monthly":
        for ym in sorted(months):
            buys.append((months[ym][0], M))

    elif kind == "quarterly":
        for key in sorted(quarters):
            n = sum(1 for ym in months if ym[:4] == key[:4] and
                    (int(ym[5:7]) - 1) // 3 + 1 == int(key[-1]))
            buys.append((quarters[key][0], n * M))

    elif kind == "semiannual":
        for y in sorted({ym[:4] for ym in months}):
            for lo, hi in (("01", "06"), ("07", "12")):
                grp = [ym for ym in months if ym[:4] == y and lo <= ym[5:7] <= hi]
                if grp:
                    buys.append((months[sorted(grp)[0]][0], len(grp) * M))

    elif kind in ("annual", "annual_end"):
        for y in sorted(years):
            n = sum(1 for ym in months if ym[:4] == y)
            d = years[y][0] if kind == "annual" else years[y][1]
            buys.append((d, n * M))

    elif kind == "lump":
        buys.append((min(months[ym][0] for ym in months), total))

    elif kind in ("dip_ma20", "dip_j20"):
        pending = 0.0
        for ym in sorted(months):
            pending += M
            r = by_date[months[ym][0]]
            cond = (r["ma20"] is not None and r["close"] < r["ma20"]) \
                if kind == "dip_ma20" else (r["j"] is not None and r["j"] < 20)
            if cond and pending > 0:
                buys.append((months[ym][0], pending))
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
    r = (lo + hi) / 2
    # 撞到边界 = 无解（例如全部资金在最后一天投入、期间价值没变）。
    # 不能把 -99.99% 当成"年化收益"，那是求解器的下界，不是结果。
    if r <= -0.99 or r >= 2.99:
        return None
    scale = sum(abs(a) for _, a in flows) or 1.0
    if abs(npv(r)) > 0.02 * scale:
        return None
    return r


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



# ─────────────────────────── 批量模式 ───────────────────────────
def load_prices_dir(path):
    """从缓存目录读 <code>.csv → {code: rows}"""
    out = {}
    for fn in sorted(os.listdir(path)):
        if not fn.endswith(".csv") or fn.startswith("_"):
            continue
        code = fn[:-4]
        if not (len(code) == 6 and code.isdigit()):
            continue
        rows = []
        # utf-8-sig：缓存文件带 BOM，用 utf-8 读会让表头变成 \ufeffdate
        with open(os.path.join(path, fn), encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                try:
                    rows.append(dict(date=r["date"][:10], open=float(r["open"]),
                                     high=float(r["high"]), low=float(r["low"]),
                                     close=float(r["close"])))
                except (KeyError, ValueError):
                    continue
        if len(rows) > 60:
            out[code] = rows
    return out


def load_names():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "_fin_cache", "codes.csv")
    out = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                out[str(r["code"]).zfill(6)] = r.get("name") or ""
    return out


def batch(args):
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    pdir = args.price_dir or os.path.join(os.path.dirname(base), "_top_raw")
    prices = load_prices_dir(pdir)
    names = load_names()
    if not prices:
        print(f"价格缓存为空：{pdir}")
        return 1
    print(f"价格缓存 {len(prices)} 只（{pdir}）")

    # 每只自己的起点：尽量用统一起点，没有就退到其最早交易日
    rows_out = []
    for code, rows in prices.items():
        rows = add_indicators(rows)
        first = rows[0]["date"]
        start = args.start if args.start >= first else first
        yrs = (date.fromisoformat(rows[-1]["date"])
               - date.fromisoformat(start)).days / 365.25
        if yrs < args.min_years:      # 史太短，各策略不可比
            continue
        if args.start < first:
            pass                      # 已在最早
        divs = load_divs(code)
        res = {}
        for label, kind in STRATEGIES:
            total, buys, leftover = build_buys(kind, rows, start, args.monthly)
            if not buys:
                continue
            res[label] = evaluate(rows, divs, buys, leftover, not args.no_reinvest, start)
        if len(res) < 4:
            continue
        xs = {k: (v["r"] or -9) for k, v in res.items()}
        best = max(xs, key=xs.get)
        rows_out.append(dict(
            code=code, name=names.get(code) or code, start=start,
            months=int((len(rows) - sum(1 for r in rows if r["date"] < start)) / 21),
            x=res, best=best, spread=max(xs.values()) - min(xs.values()),
        ))

    if not rows_out:
        print("没有可评估的标的")
        return 1

    # 明细表
    cols = [("月定投（每月首日）", "月投"), ("季定投（每季首日）", "季投"),
            ("半年定投", "半年"), ("年定投（每年首日·一次投满全年）", "年初"),
            ("年定投（每年末日）", "年末"), ("一次性（期初全投）", "一次性"),
            ("月定投·跌破 MA20 才买", "MA20"), ("月定投·日 J<20 才买", "J<20")]
    hdr = f"{'代码':<8}{'名称':<10}{'起点':<12}"
    hdr += "".join(f"{c[1]:>9}" for c in cols) + f"{'最优':>8}{'极差':>8}"
    print("\n" + hdr)
    print("-" * len(hdr))
    order = sorted(rows_out, key=lambda z: z["spread"], reverse=(args.sort == "desc"))
    for r in order[:args.top]:
        line = f"{r['code']:<8}{(r['name'] or '')[:8]:<10}{r['start']:<12}"
        for key, _short in cols:
            v = r["x"].get(key)
            line += f"{(v['r'] or 0):>8.2%} " if v else f"{'—':>9}"
        line += f"{r['best'].split('（')[0][:6]:>8}{r['spread']*100:>7.1f}pp"
        print(line)

    # ── 统计 ──
    import statistics as st
    sp = [r["spread"] for r in rows_out]
    qs_ = sorted(sp)

    def _q(f):
        return qs_[min(len(qs_) - 1, int(len(qs_) * f))] * 100

    print(f"\n【统计】{len(rows_out)} 只，起点跨度 "
          f"{min(r['start'] for r in rows_out)} ~ {max(r['start'] for r in rows_out)}")

    # 1. 极差分布（同一只股票，最好 vs 最差策略的 XIRR 差）
    print(f"  策略极差分布：min {qs_[0]*100:.2f}pp ｜ 25% {_q(.25):.2f}pp ｜ "
          f"中位 {_q(.5):.2f}pp ｜ 75% {_q(.75):.2f}pp ｜ max {qs_[-1]*100:.2f}pp")

    # 2. 只比投入节奏
    DCA_ONLY = ["月定投（每月首日）", "季定投（每季首日）", "半年定投",
                "年定投（每年首日·一次投满全年）", "年定投（每年末日）"]
    sp_dca = []
    for r in rows_out:
        xs = [r["x"][k]["r"] for k in DCA_ONLY
              if r["x"].get(k) and r["x"][k]["r"] is not None]
        if len(xs) >= 4:
            sp_dca.append(max(xs) - min(xs))
    if sp_dca:
        print(f"  只比投入节奏（月/季/半年/年年初/年末）的极差："
              f"中位 {st.median(sp_dca)*100:.2f}pp ｜ 平均 {st.mean(sp_dca)*100:.2f}pp "
              f"｜ 最大 {max(sp_dca)*100:.2f}pp")

    # 3. 按策略敏感度分组
    def _avg(rs, key):
        vs = [r["x"][key]["r"] for r in rs
              if r["x"].get(key) and r["x"][key]["r"] is not None]
        return st.mean(vs) if vs else None

    BUCKETS = [("<2pp 策略无关", 0, 0.02), ("2~5pp", 0.02, 0.05),
               ("5~10pp", 0.05, 0.10), (">10pp 策略敏感", 0.10, 9.0)]
    print(f"\n  【按策略敏感度分组】")
    print(f"    {'组别':<18}{'只数':>5}{'占比':>7}{'平均极差':>10}"
          f"{'月投XIRR':>10}{'一次性XIRR':>11}{'一次性终值胜':>13}")
    for label, lo, hi in BUCKETS:
        rs = [r for r in rows_out if lo <= r["spread"] < hi]
        if not rs:
            continue
        m0 = _avg(rs, "月定投（每月首日）")
        m1 = _avg(rs, "一次性（期初全投）")
        win = sum(1 for r in rs
                  if r["x"].get("一次性（期初全投）") and r["x"].get("月定投（每月首日）")
                  and r["x"]["一次性（期初全投）"]["terminal"]
                  > r["x"]["月定投（每月首日）"]["terminal"])
        print(f"    {label:<18}{len(rs):>5}{len(rs)/len(rows_out):>6.0%}"
              f"{st.mean([r['spread'] for r in rs])*100:>9.2f}pp"
              f"{(m0 or 0)*100:>9.2f}%{(m1 or 0)*100:>10.2f}%"
              f"{f'{win}/{len(rs)}':>13}")

    # 4. 谁赢（两个维度分别看）
    wins = {}
    for r in rows_out:
        wins[r["best"]] = wins.get(r["best"], 0) + 1
    tv = {}
    for r in rows_out:
        best = max((k for k in r["x"] if r["x"][k]["terminal"]),
                   key=lambda k: r["x"][k]["terminal"])
        tv[best] = tv.get(best, 0) + 1

    print(f"\n  【各策略胜出次数】")
    print(f"    {'策略':<32}{'按XIRR':>10}{'按终值':>10}")
    allk = set(wins) | set(tv)
    for k in sorted(allk, key=lambda z: -(wins.get(z, 0) + tv.get(z, 0))):
        w, t = wins.get(k, 0), tv.get(k, 0)
        print(f"    {k:<32}{w:>5}（{w/len(rows_out):>3.0%}）{t:>5}（{t/len(rows_out):>3.0%}）")

    a = sum(1 for r in rows_out
            if (r["x"].get("一次性（期初全投）", {}).get("r") or -9)
            > (r["x"].get("月定投（每月首日）", {}).get("r") or -9))
    b = sum(1 for r in rows_out if r["x"].get("一次性（期初全投）")
            and r["x"].get("月定投（每月首日）"))
    if b:
        print(f"\n  一次性 XIRR 高于月定投：{a}/{b} 只（{a/b:.0%}）")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="定投策略对比（总投入相同）")
    ap.add_argument("--code", default="601398")
    ap.add_argument("--start", default="2012-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--monthly", type=float, default=1000, help="每月金额（其余策略按此缩放）")
    ap.add_argument("--no-reinvest", action="store_true", help="分红收现金（不再投）")
    ap.add_argument("--batch", action="store_true", help="整池批量跑（读本地价格缓存）")
    ap.add_argument("--price-dir", default=None, help="价格缓存目录（<code>.csv）")
    ap.add_argument("--top", type=int, default=20, help="明细表显示前 N 只（按极差排序）")
    ap.add_argument("--sort", choices=("desc", "asc"), default="desc",
                    help="按极差排序：desc=最敏感在前，asc=最稳定在前")
    ap.add_argument("--min-years", type=float, default=5.0,
                    help="最短历史年数，太短的次新股不进统计（默认 5）")
    args = ap.parse_args(argv)

    reinvest = not args.no_reinvest
    if args.batch:
        return batch(args)

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
