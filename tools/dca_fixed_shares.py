# -*- coding: utf-8 -*-
"""固定股数定投计算器 —— 「每月买 N 股，分红再投，一共投入多少」。

    python tools/dca_fixed_shares.py 601919 --start 2019-01 --shares 100

## 与 dca_batch 的区别

`dca_batch` 算**固定金额**（每月投 1000 元，买零碎股数）。
这里算**固定股数**（每月买 100 股，金额随价格变）。两者回答的问题不同。

## 口径（写出来便于核对）

1. **买入日**：每月**第一个交易日**，价格 = 当日**不复权收盘价**。
   用不复权价是因为分红要单独算 —— 用复权价再叠加分红就是重复计算。
2. **分红再投**：除权日当天，用「**当时**持仓 × 每股分红（税前）」的现金，
   按除权日收盘价买入。
3. **买入与分红必须按时间交错处理**。这是最容易写错的地方：
   如果先把所有买入跑完、再算分红，第一次分红就会按最终持仓发钱 ——
   等于给还没买的股票发分红。第一版就是这么错的，分红总额虚高一倍。
4. **不计费用**：佣金/印花税/过户费未扣。100 股单笔佣金约 0.3 元，
   一年 12 笔 ≈ 4 元，对总额影响可忽略，但要知道没算。
5. **期末价**：最后一个交易日的收盘价。

## 价格来源（按优先级，全部不复权）

1. `_top_raw/{code}.csv` —— 本地日线缓存（2014 起）
2. `output/{日期}/metrics_*.csv` 的「最新价」—— 补最近两周（缓存可能滞后）
3. 东财 push2his（`fqt=0`）→ 4. 腾讯（仅最近 320 根，只用于补尾）
"""
import argparse
import csv
import glob
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(os.path.dirname(BASE), "_top_raw")
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8")


def _load_csv_prices(path):
    out = {}
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                out[r["date"][:10]] = float(r["close"])
            except (KeyError, TypeError, ValueError):
                pass
    return out


def load_prices(code):
    """返回 {日期: 不复权收盘价}，按来源优先级合并。"""
    px = {}
    for p in (os.path.join(RAW_DIR, f"{code}.csv"),
              os.path.join(BASE, "_fin_cache", f"px_{code}.csv")):
        if os.path.exists(p):
            px.update(_load_csv_prices(p))
            break
    # 仓库每日 metrics 的「最新价」补缓存滞后（收盘后采集，即收盘价）
    for f in sorted(glob.glob(os.path.join(BASE, "output", "*", "metrics_*.csv"))):
        day = os.path.basename(f).replace("metrics_", "").replace(".csv", "")
        try:
            with open(f, encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    if str(r.get("代码", "")).zfill(6) != code:
                        continue
                    v = r.get("最新价")
                    if v:
                        px.setdefault(day, float(v))
                    break
        except Exception:
            continue
    return px


def fetch_remote(code, start, end):
    """东财 → 腾讯 兜底。只在本地完全没有数据时用。"""
    import requests
    H = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    p = {"secid": f"{'1' if code[0] == '6' else '0'}.{code}",
         "fields1": "f1,f2", "fields2": "f51,f53", "klt": "101", "fqt": "0",
         "beg": start.replace("-", ""), "end": end.replace("-", ""), "lmt": "4000"}
    try:
        j = requests.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                         params=p, headers=H, timeout=20).json()
        ks = (j.get("data") or {}).get("klines") or []
        if ks:
            return {k.split(",")[0]: float(k.split(",")[2]) for k in ks}
    except Exception:
        pass
    try:
        H2 = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
        pre = "sh" if code[0] == "6" else "sz"
        j = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                         params={"param": f"{pre}{code},day,,,320"},
                         headers=H2, timeout=20).json()
        d = j["data"][f"{pre}{code}"]
        key = "day" if "day" in d else list(d)[0]
        return {r[0]: float(r[2]) for r in d[key]}
    except Exception:
        return {}


def load_actions(code):
    """{除权日: (每股现金分红, 股数放大倍数)}。

    **送转必须处理**，否则不复权价遇到「10 转 7」会算成暴跌 41%：
    价格确实跌了，但股数变成 1.7 倍，市值不变。

    实测池内 117 只（31%）2019 年以来发生过送转，共 229 次 ——
    不处理的话三分之一的结论是错的。中远海控自己就有一次 10 转 3。
    """
    cash = load_divs(code)
    factor = {}
    p = os.path.join(BASE, "_fin_cache", "splits.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if str(r.get("code", "")).zfill(6) != code:
                    continue
                d = (r.get("exDate") or "")[:10]
                if not d:
                    continue
                def _n(v):
                    try:
                        return float(v) if v not in (None, "") else 0.0
                    except ValueError:
                        return 0.0
                b, i = _n(r.get("bonusRatio")), _n(r.get("itRatio"))
                if b + i > 0:
                    factor[d] = 1.0 + (b + i) / 10.0
    return {d: (cash.get(d, 0.0), factor.get(d, 1.0))
            for d in set(cash) | set(factor)}


def load_divs(code):
    """{除权日: 每股分红}。同一除权日多笔合并。"""
    p = os.path.join(BASE, "_fin_cache", "dividends.csv")
    out = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("code", "")).zfill(6) != code:
                continue
            d = (r.get("exDate") or "")[:10]
            v = r.get("cashPs")
            if not d or not v:
                continue
            try:
                out[d] = out.get(d, 0.0) + float(v)
            except ValueError:
                pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="固定股数定投")
    ap.add_argument("code")
    ap.add_argument("--start", default="2019-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--shares", type=int, default=100)
    args = ap.parse_args(argv)

    from datetime import date
    code = args.code.zfill(6)
    start = args.start if len(args.start) == 10 else args.start + "-01"
    end = args.end or date.today().isoformat()

    px = load_prices(code)
    if not px or max(px) < start:
        px.update(fetch_remote(code, start, end))
    px = {d: v for d, v in px.items() if start <= d <= end}
    if not px:
        print("没取到价格")
        return 1
    dates = sorted(px)
    actions = {d: v for d, v in load_actions(code).items() if start <= d <= end}

    # 每月第一个交易日
    by_month = {}
    for d in dates:
        by_month.setdefault(d[:7], d)
    months = sorted(by_month)

    # ── 关键：买入与分红按时间交错 ──
    events = [("买", by_month[m], None) for m in months]
    events += [("息", d, actions[d]) for d in sorted(actions) if d in px]
    events.sort(key=lambda e: (e[1], 0 if e[0] == "买" else 1))

    shares = 0.0
    invested = 0.0
    div_cash = 0.0
    rows = []
    for kind, d, act in events:
        if kind == "买":
            cost = args.shares * px[d]
            shares += args.shares
            invested += cost
            rows.append(dict(kind="买", date=d, px=px[d], qty=args.shares,
                             cash=cost, held=shares))
        else:
            cps, factor = act
            held = shares
            cash = held * cps
            # 先送转（股数放大），再拿现金按除权日价格再投
            shares = held * factor
            add = (cash / px[d]) if cash > 0 else 0.0
            shares += add
            div_cash += cash
            if cash <= 0 and factor == 1.0:
                continue
            rows.append(dict(kind="息", date=d, px=px[d], qty=add,
                             cash=cash, held=held, dps=cps, factor=factor))

    last = dates[-1]
    value = shares * px[last]
    n_buy = sum(1 for r in rows if r["kind"] == "买")

    print(f"\n{'='*68}")
    print(f"{code} 固定股数定投：每月 {args.shares} 股，分红再投")
    print(f"{'='*68}")
    print(f"区间：{dates[0]} ~ {last}（{n_buy} 次买入，{len(months)} 个月）")

    print("\n【一】买入（按年汇总）")
    print(f"  {'年份':<6}{'次':>4}{'股数':>8}{'投入金额':>13}{'均价':>9}")
    yr = {}
    for r in rows:
        if r["kind"] != "买":
            continue
        s = yr.setdefault(r["date"][:4], [0, 0.0, 0.0])
        s[0] += 1
        s[1] += r["qty"]
        s[2] += r["cash"]
    for y in sorted(yr):
        n, q, amt = yr[y]
        print(f"  {y:<6}{n:>4}{q:>8.0f}{amt:>13,.2f}{amt / q:>9.3f}")
    tq = sum(v[1] for v in yr.values())
    print(f"  {'合计':<6}{n_buy:>4}{tq:>8.0f}{invested:>13,.2f}{invested / tq:>9.3f}")

    print("\n【二】分红再投（按时间顺序，持仓是当时的）")
    if not actions:
        print("  区间内没有分红")
    else:
        print(f"  {'除权日':<12}{'每股':>7}{'当时持仓':>10}{'分红现金':>11}"
              f"{'再投价':>8}{'再投股数':>9}")
        for r in rows:
            if r["kind"] != "息":
                continue
            _f = f"  ×{r['factor']:.2f}" if r.get("factor", 1.0) != 1.0 else ""
            print(f"  {r['date']:<12}{r['dps']:>7.2f}{r['held']:>10,.0f}"
                  f"{r['cash']:>11,.2f}{r['px']:>8.2f}{r['qty']:>9.2f}{_f}")
        print(f"  {'合计':<12}{'':>7}{'':>10}{div_cash:>11,.2f}")

    print(f"\n【三】结果（{last} 收盘 {px[last]:.2f} 元）")
    print(f"  现金投入（自掏腰包）  {invested:>15,.2f} 元")
    print(f"  分红再投（不是新钱）  {div_cash:>15,.2f} 元")
    print(f"  期末持股              {shares:>15,.2f} 股")
    print(f"  期末市值              {value:>15,.2f} 元")
    print(f"  账面盈亏              {value - invested:>15,.2f} 元"
          f"（{(value / invested - 1) * 100:+.1f}%）")
    print("\n  注：不计佣金/印花税；分红为税前。")
    print("  价格来源：本地日线缓存 + 仓库每日 metrics（不复权）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
