# -*- coding: utf-8 -*-
"""定投回测批量：对选中的 A 股逐只跑「2014 起月度定投」，只存结果。

    python tools/dca_batch.py --limit 60        # 先跑 60 只验证
    python tools/dca_batch.py                   # 全 300 只（约 15 分钟）

## 关键设计：日线不落盘

300 只 × 3100 个交易日 ≈ **34MB** 的日线缓存。存进 git 会把仓库撑爆，
而它只在计算时需要。所以：**临时抓 → 算完丢弃 → 只存结果**。

结果每只约 2KB（XIRR / 终值 / 回撤 / 年度序列），300 只约 600KB。

已有 `_top_raw/*.csv` 的直接读缓存，没有的现抓（baostock，不复权）。

## 为什么必须用不复权价

分红要单独拆出来。用前/后复权价再单列分红 = 重复计算。
详见 `信息架构.md` 的口径表。

## 输出

`_pool_cache/dca_results.csv` —— 每只一行：

    代码 / 名称 / 起点 / 月数 / 累计投入 / 期末市值 / 累计分红
    XIRR / 总收益% / 最大回撤 / HS300 同期 / 超额

外加控制台的分位数汇总（XIRR 中位、跑赢基准比例等）。
"""
import argparse
import csv
import glob
import os
import statistics as st
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

import dca_yearly as dy  # noqa: E402
import requests  # noqa: E402

KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
KHEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_SESS = requests.Session()


def _secid(code):
    """东财 secid：沪市 1.、深市 0.、指数 1.000300。"""
    if code == "000300":
        return "1.000300"
    return f"{'1' if code[0] == '6' else '0'}.{code}"


def fetch_kline(code, start, end):
    """东财日线，fqt=0 = **不复权**。

    为什么不用 baostock：实测每只 2 分钟（登录+查询 3100 根），
    5 只就要 10 分钟以上；东财同一个查询 **0.95 秒**，快 100 倍。
    """
    p = {"secid": _secid(code), "fields1": "f1,f2,f3,f4,f5,f6",
         "fields2": "f51,f52,f53,f54,f55,f56,f57", "klt": "101", "fqt": "0",
         "beg": start.replace("-", ""), "end": end.replace("-", ""), "lmt": "5000"}
    try:
        j = _SESS.get(KLINE, params=p, headers=KHEAD, timeout=(6, 25)).json()
    except Exception:
        return []
    ks = ((j.get("data") or {}).get("klines") or [])
    out = []
    for line in ks:
        f = line.split(",")
        if len(f) < 5:
            continue
        try:
            # 字段顺序是 date,open,close,high,low —— 不是 OHLC
            out.append(dict(date=f[0], open=float(f[1]), close=float(f[2]),
                            high=float(f[3]), low=float(f[4])))
        except ValueError:
            continue
    return out

CACHE = os.path.join(BASE_DIR, "_fin_cache")
POOL = os.path.join(BASE_DIR, "_pool_cache")
RAW_DIR = os.path.join(os.path.dirname(BASE_DIR), "_top_raw")
OUT = os.path.join(POOL, "dca_results.csv")


def load_divs():
    """{code: {除权日: 每股现金分红}}。

    原来批量里给 simulate 传的是空字典 —— **完全没算分红**，
    于是 XIRR 中位只有 0.95%（股息率 3~5% 的股票全被抹掉了）。
    """
    out = {}
    p = os.path.join(CACHE, "dividends.csv")
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            c = str(r.get("code", "")).zfill(6)
            d = (r.get("exDate") or "")[:10]
            v = r.get("cashPs")
            if not d or not v:
                continue
            try:
                out.setdefault(c, {})
                out[c][d] = out[c].get(d, 0.0) + float(v)
            except (TypeError, ValueError):
                continue
    return out


def _read_raw(path):
    rows = []
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append(dict(date=r["date"][:10], open=float(r["open"]),
                                 high=float(r["high"]), low=float(r["low"]),
                                 close=float(r["close"])))
            except (KeyError, ValueError):
                continue
    return rows


def load_prices(code, start, end, allow_fetch=True):
    """优先读缓存；没有再抓（不落盘）。"""
    for p in (os.path.join(CACHE, f"px_{code}.csv"),
              os.path.join(RAW_DIR, f"{code}.csv")):
        if os.path.exists(p):
            rows = _read_raw(p)
            if rows and rows[0]["date"] <= start:
                return rows, "cache"
    if not allow_fetch:
        return [], "miss"
    rows = fetch_kline(code, start, end)
    return (rows, "fetch") if rows else ([], "fail")


def main(argv=None):
    ap = argparse.ArgumentParser(description="定投回测批量")
    ap.add_argument("--start", default="2014-01-01")
    ap.add_argument("--end", default="2026-10-28")
    ap.add_argument("--monthly", type=float, default=1000)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-fetch", action="store_true", help="只用已有缓存")
    args = ap.parse_args(argv)

    DIVS = load_divs()
    print(f"分红记录：{len(DIVS)} 只有除权日数据")
    sel = list(csv.DictReader(open(os.path.join(POOL, "selected_a.csv"),
                                   encoding="utf-8-sig")))
    if args.limit:
        sel = sel[:args.limit]

    bench, bsrc = load_prices("000300", args.start, args.end)
    if not bench:
        bench = dy.load_series(os.path.join(CACHE, "px_hs300.csv"), "sh.000300",
                               args.start, args.end, log=lambda *_: None)
    print(f"基准沪深300：{len(bench)} 根（{bsrc}）")
    # 基准必须用**同样的现金流**定投指数再算 XIRR ——
    # 拿 DCA 的累计收益去比指数的累计涨幅是苹果比橘子：
    # DCA 是资金加权（每笔钱持有时间不同），指数涨幅是时间加权。
    bench_xirr = None
    if bench:
        try:
            _br, _bf, _ = dy.simulate(bench, {}, args.start, args.monthly,
                                      reinvest=False)
            _bx = dy.xirr(_bf)
            bench_xirr = round(_bx * 100, 2) if _bx is not None else None
            print(f"  同期定投指数的 XIRR：{bench_xirr}%")
        except Exception:
            pass

    print(f"定投回测：{len(sel)} 只，{args.start} 起，每月 {args.monthly:,.0f} 元")
    out, stat = [], {"cache": 0, "fetch": 0, "miss": 0, "fail": 0}
    for i, r in enumerate(sel, 1):
        code = str(r["code"]).zfill(6)
        rows, src = load_prices(code, args.start, args.end,
                               allow_fetch=not args.no_fetch)
        stat[src] = stat.get(src, 0) + 1
        if len(rows) < 250:
            continue
        rows = dy.add_indicators(rows) if hasattr(dy, "add_indicators") else rows
        try:
            recs, flows, div_total = dy.simulate(rows, DIVS.get(code, {}),
                                                 args.start, args.monthly,
                                                 reinvest=True)
            x = dy.xirr(flows)
            ys = dy.yearly(recs, bench, args.monthly)
        except Exception:
            continue
        if not ys:
            continue
        inv = recs[-1]["invested"]
        val = recs[-1]["value"]
        worst = min(x["mdd"] for x in ys)
        bsum = bench_xirr
        # 实际起点与年数 —— 上市晚的股票，「2014 起」其实等于「上市起」，
        # 窗口太短时 XIRR 是噪声（实测有上市一年算出 -47% 的）。
        _first = recs[0]["date"]
        _yrs = round(len(recs) / 244.0, 1)
        out.append(dict(
            code=code, name=r.get("name", ""), start=args.start,
            first=_first, years=_yrs,
            months=sum(1 for _, a in flows if a < 0),
            invested=round(inv), value=round(val),
            div=round(div_total),
            xirr=(round(x * 100, 2) if x is not None else ""),
            ret=round((val / inv - 1) * 100, 1) if inv else "",
            maxdd=round(worst * 100, 1),
            hs300=(bsum if bsum is not None else ""),
            excess=(round(x * 100 - bsum, 2)
                    if (bsum is not None and x is not None) else "")))
        if i % 50 == 0:
            print(f"  [{i}/{len(sel)}] …")

    os.makedirs(POOL, exist_ok=True)
    cols = ["code", "name", "start", "first", "years", "months", "invested",
            "value", "div", "xirr", "ret", "maxdd", "hs300", "excess"]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    print(f"\n完成 {len(out)} 只（缓存 {stat['cache']}、现抓 {stat['fetch']}、"
          f"缺失 {stat['miss']}、失败 {stat['fail']}）")
    def _summ(rows, label):
        xs = sorted(float(r["xirr"]) for r in rows if r["xirr"] != "")
        ds = [float(r["maxdd"]) for r in rows if r["maxdd"] != ""]
        ex = [float(r["excess"]) for r in rows if r["excess"] != ""]
        if not xs:
            return
        win = sum(1 for v in ex if v > 0)
        print(f"\n  【{label}】{len(rows)} 只")
        print(f"    XIRR 中位 {st.median(xs):.2f}%　"
              f"四分位 {xs[len(xs)//4]:.2f}% ~ {xs[3*len(xs)//4]:.2f}%")
        if ds:
            print(f"    最大回撤 中位 {st.median(ds):.1f}%　最差 {min(ds):.1f}%")
        if ex:
            print(f"    跑赢同期定投指数：{win}/{len(ex)} ({win/len(ex)*100:.0f}%)")

    if out:
        _summ(out, "全部")
        # 窗口够长的才可比 —— 上市晚的股票 XIRR 是噪声
        _long = [r for r in out if float(r.get("years") or 0) >= 8]
        if _long and len(_long) < len(out):
            _summ(_long, "窗口 ≥8 年（可比）")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
