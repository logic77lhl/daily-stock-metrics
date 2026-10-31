# -*- coding: utf-8 -*-
"""批量：池内每只算「2019-01 起每月买 100 股、分红再投」。

    python tools/dca_shares_batch.py                # 全部 380 只
    python tools/dca_shares_batch.py --limit 20     # 先试 20 只

## 价格来源：东财 datacenter 的估值序列

`RPT_VALUEANALYSIS_DET` 里带 `CLOSE_PRICE`（每日收盘价）。实测：
* 601919 从 2019-01-02 起 1897 个交易日，`pageSize=2000` **一次请求拿全**
* 与本地日线缓存重叠 1887 天，**0 处不一致** → 确认是不复权收盘价

为什么不用 K 线接口：`push2his` 当前不可用（ConnectionError），
而 datacenter 正常。腾讯只给最近 320 根，拿不到 2019。

## 与 dca_fixed_shares 共用一套口径

买入/分红按时间交错、不复权价、分红按当时持仓 —— 逻辑在
`dca_fixed_shares` 里，这里只做批量循环和汇总。
"""
import argparse
import csv
import os
import statistics as st
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

import requests  # noqa: E402
import dca_fixed_shares as fs  # noqa: E402

U = "https://datacenter-web.eastmoney.com/api/data/v1/get"
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
OUT = os.path.join(BASE, "_pool_cache", "dca_shares_100.csv")


def fetch_prices(code, start):
    """datacenter 估值序列里的每日收盘价（不复权）。"""
    p = {"reportName": "RPT_VALUEANALYSIS_DET",
         "columns": "SECURITY_CODE,TRADE_DATE,CLOSE_PRICE",
         "pageNumber": "1", "pageSize": "2000", "source": "WEB", "client": "WEB",
         "filter": f'(SECURITY_CODE="{code}")(TRADE_DATE>=\'{start}\')',
         "sortColumns": "TRADE_DATE", "sortTypes": "1"}
    for _ in range(3):
        try:
            j = requests.get(U, params=p, headers=H, timeout=25).json()
            d = (j.get("result") or {}).get("data") or []
            return {x["TRADE_DATE"][:10]: float(x["CLOSE_PRICE"])
                    for x in d if x.get("CLOSE_PRICE")}
        except Exception:
            continue
    return {}


def simulate(px, actions, shares_per_month, start, end):
    """买入与分红按时间交错 —— 见 dca_fixed_shares 的说明。"""
    dates = sorted(d for d in px if start <= d <= end)
    if not dates:
        return None
    by_month = {}
    for d in dates:
        by_month.setdefault(d[:7], d)
    month_end = {}
    for d in dates:
        month_end[d[:7]] = d          # 升序，最后写入即该月最后交易日
    events = [("买", by_month[m], None) for m in sorted(by_month)]
    events += [("息", d, actions[d]) for d in sorted(actions)
               if start <= d <= end and d in px]
    events.sort(key=lambda e: (e[1], 0 if e[0] == "买" else 1))

    sh = invested = div_cash = 0.0
    n_buy = 0
    # 逐日推进，月末记一次快照 —— 用来算回撤和波动。
    # 只算终点收益率会误导：茅台 7 年投 1455 万、收益 -0.6%，
    # 但中途从 2600 跌到 1200 的过程对持有人是灾难。
    snaps = []
    ei = 0
    contrib_month = {}
    for d in dates:
        while ei < len(events) and events[ei][1] == d:
            kind, _, act = events[ei]
            if kind == "买":
                cost = shares_per_month * px[d]
                sh += shares_per_month
                invested += cost
                n_buy += 1
                contrib_month[d[:7]] = contrib_month.get(d[:7], 0.0) + cost
            else:
                cps, factor = act
                held = sh
                cash = held * cps
                sh = held * factor          # 送转：股数放大
                if cash > 0:
                    sh += cash / px[d]      # 分红再投（不是新钱）
                    div_cash += cash
            ei += 1
        if month_end.get(d[:7]) == d:
            snaps.append((d[:7], sh * px[d], invested,
                          contrib_month.get(d[:7], 0.0)))
    last = dates[-1]

    # 最大回撤：组合市值从峰值的回落
    peak = 0.0
    mdd = 0.0
    for _, v, _, _ in snaps:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, 1 - v / peak)

    # 年化波动：月度**剔除新增投入后**的净值收益率
    rets = []
    for i in range(1, len(snaps)):
        v0 = snaps[i - 1][1]
        v1 = snaps[i][1]
        c = snaps[i][3]
        if v0 > 0:
            rets.append((v1 - c) / v0 - 1)
    vol = 0.0
    if len(rets) >= 6:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        vol = (var ** 0.5) * (12 ** 0.5)

    # 正收益年份数（按年末快照的年度净值收益）
    yr_ret = {}
    for i in range(1, len(snaps)):
        y = snaps[i][0][:4]
        v0, v1, c = snaps[i - 1][1], snaps[i][1], snaps[i][3]
        if v0 > 0:
            r = (v1 - c) / v0 - 1
            yr_ret[y] = (yr_ret.get(y, 1.0)) * (1 + r)
    pos_years = sum(1 for v in yr_ret.values() if v > 1)

    return dict(buys=n_buy, invested=invested, div_cash=div_cash,
                shares=sh, last_px=px[last], last_date=last,
                value=sh * px[last], mdd=mdd, vol=vol,
                years=round(len(snaps) / 12, 1),
                pos_years=pos_years, n_years=len(yr_ret),
                div_ratio=(div_cash / invested * 100) if invested else 0.0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="批量固定股数定投")
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--shares", type=int, default=100)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    from datetime import date
    end = args.end or date.today().isoformat()

    # 池内代码 + 名称 + 行业
    meta = {}
    for r in csv.DictReader(open(os.path.join(BASE, "_fin_cache", "codes.csv"),
                                encoding="utf-8-sig")):
        meta[str(r["code"]).zfill(6)] = ((r.get("name") or "").strip(),
                                         (r.get("industry") or "").strip())
    codes = sorted(meta)
    if args.limit:
        codes = codes[:args.limit]
    print(f"批量定投：{len(codes)} 只，{args.start} ~ {end}，每月 {args.shares} 股")

    rows = []
    for i, code in enumerate(codes, 1):
        px = fetch_prices(code, args.start)
        if not px:
            print(f"  [{i}/{len(codes)}] {code} 无价格，跳过")
            continue
        r = simulate(px, fs.load_actions(code), args.shares, args.start, end)
        if not r:
            continue
        name, ind = meta.get(code, (code, ""))
        rows.append(dict(code=code, name=name, ind=ind, **r))
        if i % 40 == 0:
            print(f"  [{i}/{len(codes)}] …")

    if not rows:
        print("没有结果")
        return 1

    for r in rows:
        r["ret"] = (r["value"] / r["invested"] - 1) * 100 if r["invested"] else None

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cols = ["code", "name", "ind", "buys", "years", "invested", "div_cash",
            "div_ratio", "shares", "last_px", "last_date", "value",
            "ret", "mdd", "vol", "pos_years", "n_years"]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["ret"] is not None]
    rets = sorted(r["ret"] for r in ok)
    print(f"\n完成 {len(rows)} 只（{rows[0]['last_date']} 收盘价估值）")
    print(f"  收益率中位 {st.median(rets):+.1f}%"
          f"　四分位 {rets[len(rets)//4]:+.1f}% ~ {rets[3*len(rets)//4]:+.1f}%")
    print(f"  盈利 {sum(1 for v in rets if v > 0)} 只 / 亏损 {sum(1 for v in rets if v <= 0)} 只")
    inv = [r["invested"] for r in rows]
    print(f"  累计现金投入 {sum(inv):,.0f} 元（单只中位 {st.median(inv):,.0f}）")
    print(f"  累计分红再投 {sum(r['div_cash'] for r in rows):,.0f} 元")
    mdd = sorted(r["mdd"] * 100 for r in rows)
    vol = sorted(r["vol"] * 100 for r in rows if r["vol"])
    print(f"  最大回撤中位 {st.median(mdd):.1f}%　最差 {max(mdd):.1f}%"
          f"　≤-40% 的 {sum(1 for v in mdd if v >= 40)} 只")
    print(f"  年化波动中位 {st.median(vol):.1f}%　最高 {max(vol):.1f}%")

    print(f"\n最好 10 只（按收益率）")
    for r in sorted(ok, key=lambda x: -x["ret"])[:10]:
        print(f"  {r['code']} {r['name'][:8]:<10}投入 {r['invested']:>9,.0f}  "
              f"期末 {r['value']:>9,.0f}  {r['ret']:>+7.1f}%")
    print(f"\n最差 10 只")
    for r in sorted(ok, key=lambda x: x["ret"])[:10]:
        print(f"  {r['code']} {r['name'][:8]:<10}投入 {r['invested']:>9,.0f}  "
              f"期末 {r['value']:>9,.0f}  {r['ret']:>+7.1f}%")
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
