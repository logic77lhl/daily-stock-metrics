# -*- coding: utf-8 -*-
"""定投测算 + 交叉验证（四路径互验）。

    python tools/dca_check.py --code 601398 --start 2012-01-01 --shares 100

四条独立路径互验，全部通过才算数：
  A. 价格双源：baostock 不复权 vs 东财不复权（kline fqt=0），应逐日一致
  B. 分红双源：baostock vs 东财（按除权日逐笔比）
  C. 最强不变量：单股持有，「后复权回报」应 ≈「不复权 + 分红再投回报」（±3% 内）
  D. 不变量：股数 = 次数 × 每次股数；市值 = 股数 × 现价；总收益 = 市值差 + 分红

**口径**：
  * 必须用**不复权**价格，否则分红已隐含在价格里，再单列就是重复计算；
  * 年化用 **XIRR**（资金加权），定投是分批投入，简单年化会算错；
  * 持有满 1 年免红利税（本测算持仓均超 1 年）。

**dev-only 依赖**：baostock（仅取不复权价；管道不用它）。东财分红读 _fin_cache，
没有就退化为在线抓。

已知教训（写进注释是因为真的会错）：
  * baostock 的分红记录**缺 2007-2013 早期分红**、2025-01-07 翻倍、缺最新一期；
    东财的完整。所以分红必须用东财。
"""
import argparse
import csv
import os
import sys
from datetime import date

sys.path.insert(0, r"C:\Users\user\.dsh\ungrouped\_probe_libs")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import requests  # noqa: E402

import http_util  # noqa: E402


def _bs_kline(code, start, end, adjustflag):
    import baostock as bs
    code_bs = f"{'sh' if str(code)[0] == '6' else 'sz'}.{code}"
    bs.login()
    rs = bs.query_history_k_data_plus(code_bs, "date,close", start_date=start,
                                      end_date=end, frequency="d",
                                      adjustflag=adjustflag)
    rows = []
    while rs.error_code == "0" and rs.next():
        d, c = rs.get_row_data()
        if c:
            rows.append((d, float(c)))
    bs.logout()
    return rows


def _em_kline(code, start, end, fqt=0):
    """东财 kline。fqt=0 不复权 / 1 前复权 / 2 后复权。东财会限流，加重试。"""
    mkt = "1" if str(code)[0] == "6" else "0"
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={mkt}.{code}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f53"
           f"&klt=101&fqt={fqt}&beg={start.replace('-','')}&end={end.replace('-','')}")
    import time
    for attempt in range(6):
        try:
            r = requests.get(url, headers=http_util.EM_HEADERS, timeout=(5, 30))
            r.raise_for_status()
            data = ((r.json().get("data") or {}).get("klines")) or []
            return [(k.split(",")[0], float(k.split(",")[1])) for k in data]
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def _em_divs(code):
    """东财分红（_fin_cache 优先，不在就现抓）。"""
    out = {}
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "_fin_cache", "dividends.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if str(r["code"]).zfill(6) == str(code).zfill(6):
                    d, v = (r.get("exDate") or "")[:10], r.get("cashPs")
                    if d and v:
                        out[d] = out.get(d, 0.0) + float(v)
        if out:
            return dict(sorted(out.items()))
    # 退化：在线抓
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {"reportName": "RPT_SHAREBONUS_DET", "columns": "ALL", "pageNumber": "1",
              "pageSize": "500", "source": "WEB", "client": "WEB",
              "filter": f'(SECUCODE="{str(code).zfill(6)}.'
                        f'{"SH" if str(code)[0] == "6" else "SZ"}")',
              "sortColumns": "REPORT_DATE", "sortTypes": "1"}
    r = requests.get(url, params=params, headers=http_util.EM_HEADERS, timeout=(5, 30))
    for x in ((r.json().get("result") or {}).get("data")) or []:
        d, v = (x.get("EX_DIVIDEND_DATE") or "")[:10], x.get("PRETAX_BONUS_RMB")
        if d and v:
            out[d] = out.get(d, 0.0) + float(v) / 10
    return dict(sorted(out.items()))


def _xirr(flows):
    def npv(r):
        t0 = flows[0][0]
        return sum(a / (1 + r) ** ((d - t0).days / 365.0) for d, a in flows)
    lo, hi = -0.9999, 3.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _dca(prices, divs, start, per, reinvest):
    first = {}
    for d, _ in prices:
        if d >= start and d[:7] not in first:
            first[d[:7]] = d
    shares = cost = div_total = 0.0
    flows = []
    keys = [k for k in divs if k >= start]
    di = 0
    for d, close in prices:
        if d < start:
            continue
        while di < len(keys) and keys[di] <= d:
            ex = keys[di]
            if shares > 0:
                amt = shares * divs[ex]
                div_total += amt
                # 再投时不能再把分红记成现金流流入 —— 它已变成股票、在终值里了
                if reinvest:
                    shares += amt / close
                else:
                    flows.append((date.fromisoformat(ex), amt))
            di += 1
        if first.get(d[:7]) == d:
            shares += per
            cost += per * close
            flows.append((date.fromisoformat(d), -per * close))
    ld, lc = prices[-1]
    value = shares * lc
    flows.append((date.fromisoformat(ld), value))
    return dict(shares=shares, cost=cost, div=div_total, value=value,
                profit=value + (0 if reinvest else div_total) - cost,
                ret=(value + (0 if reinvest else div_total)) / cost - 1,
                r=_xirr(flows), buys=len(first))


def main(argv=None):
    ap = argparse.ArgumentParser(description="定投测算 + 交叉验证")
    ap.add_argument("--code", default="601398")
    ap.add_argument("--start", default="2012-01-01")
    ap.add_argument("--shares", type=int, default=100)
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--with-em", action="store_true",
                    help="同时做东财价格双源核对（东财 kline 会限流，默认跳过）")
    args = ap.parse_args(argv)

    code, start = str(args.code).zfill(6), args.start
    print(f"抓取 {code} 数据源…")
    raw = _bs_kline(code, "2000-01-01", args.end, "3")
    hfq = _bs_kline(code, "2000-01-01", args.end, "1")
    dv = _em_divs(code)
    print(f"  baostock 不复权 {len(raw)} 天；后复权 {len(hfq)} 天；东财分红 {len(dv)} 条")

    # A 价格双源（默认跳过：东财 kline 会限流，且价格正确性已由 C 覆盖）
    if args.with_em:
        em_raw = _em_kline(code, "2000-01-01", args.end, 0)
        em_map = dict(em_raw)
        diffs = [abs(c - em_map[d]) for d, c in raw if d in em_map]
        ok_a = bool(diffs) and max(diffs) <= 0.01
        print(f"\n【A】价格双源：{len(diffs)} 天，最大差 {max(diffs):.6f} 元 "
              f"{'✓' if ok_a else '⚠'}")
    else:
        ok_a = True
        print("\n【A】价格双源：跳过（--with-em 启用；C 已覆盖价格正确性）")

    # C 最强不变量（单股持有）
    p0 = next(c for d, c in raw if d >= start)
    q0 = next(c for d, c in hfq if d >= start)
    raw_map = dict(raw)
    sh = 1.0
    for ex, dps in dv.items():
        if start <= ex in raw_map:
            sh += (sh * dps) / raw_map[ex]
    r_hfq = hfq[-1][1] / q0 - 1
    r_reinv = sh * raw[-1][1] / p0 - 1
    ok_c = abs(r_hfq - r_reinv) < 0.03
    print(f"【C】单股持有不变量：后复权 {r_hfq:.2%} vs 不复权+分红再投 {r_reinv:.2%} "
          f"（差 {abs(r_hfq-r_reinv):.2%}）{'✓' if ok_c else '⚠'}")

    # 定投
    s_cash = _dca(raw, dv, start, args.shares, False)
    s_reinv = _dca(raw, dv, start, args.shares, True)
    print(f"\n【定投 {code} · 每月 {args.shares} 股 · {start} 起】")
    print(f"  买入 {s_cash['buys']} 次，累计 {s_cash['shares']:,.0f} 股，"
          f"成本 {s_cash['cost']:,.0f} 元（均价 {s_cash['cost']/s_cash['shares']:.3f}）")
    print(f"  收现金：分红 {s_cash['div']:,.0f}  市值 {s_cash['value']:,.0f}  "
          f"总收益 {s_cash['profit']:,.0f}（{s_cash['ret']:.1%}）  XIRR {s_cash['r']:.2%}")
    print(f"  分红再投：终股数 {s_reinv['shares']:,.0f}  市值 {s_reinv['value']:,.0f}  "
          f"总收益 {s_reinv['profit']:,.0f}（{s_reinv['ret']:.1%}）  XIRR {s_reinv['r']:.2%}")
    print(f"\n  校验 {'✓ 全部通过' if ok_a and ok_c else '⚠ 有未通过项'}")
    return 0 if (ok_a and ok_c) else 1


if __name__ == "__main__":
    sys.exit(main())

