# -*- coding: utf-8 -*-
"""港股财务数据通道（`RPT_HKF10_FN_MAININDICATOR`）。

    python fetch_hk_fundamentals.py --init
    python fetch_hk_fundamentals.py --refresh

## 为什么单独一个脚本

A 股走 `RPT_F10_FINANCE_MAINFINADATA`（85 个季度，字段是中文口径的 `ROEJQ`/`PARENTNETPROFIT`）。
港股是**完全另一套**：一个请求返回该股**全部年度+中期**记录，89 个字段，
字段名是英文的 `ROE_AVG` / `HOLDER_PROFIT` / `OPERATE_INCOME`。

已实测（2026-10-29）：

| 市场 | 报表 | 结果 |
|---|---|---|
| 港股 | `RPT_HKF10_FN_MAININDICATOR` | ✅ 89 字段，含 CURRENT_RATIO/PE_TTM/PB_TTM/DIVIDEND_RATE |
| 港股 | `RPT_HKF10_FN_MAINFINADATA` | ❌ 报表配置不存在 |
| 美股 | `RPT_USF10_FN_MAININDICATOR` | ❌ 报表配置不存在（美股要另找源，SEC EDGAR 可用） |

## 字段映射（港股 → 我们的统一口径）

这是整个接入里最容易出错的一环 —— **同一个概念两套名字**：

| 我们的口径 | A 股字段 | 港股字段 |
|---|---|---|
| 营业收入 | `TOTAL_OPERATE_INCOME` | `OPERATE_INCOME` |
| 归母净利 | `PARENTNETPROFIT` | `HOLDER_PROFIT`（股东应占溢利） |
| ROE | `ROEJQ`（加权） | `ROE_AVG` / `ROE_YEARLY` |
| 总资产 | `TOTAL_ASSETS` | `TOTAL_ASSETS` |
| 归母权益 | `TOTAL_PARENT_EQUITY` | `TOTAL_PARENT_EQUITY` |
| 资产负债率 | `DEBT_ASSET_RATIO` | `DEBT_ASSET_RATIO` |
| 流动比率 | 要自己算 | **直接给 `CURRENT_RATIO`** |
| 每股分红 | 从分红表按财务年度归集 | **直接给 `DPS_HKD`** |
| 股息率 | 自己算 | **直接给 `DIVIDEND_RATE`** |

港股那边给得更全（流动比率和股息率都是现成的），所以港股页面的口径反而更简单。

## 币种

`CURRENCY` 字段给出（多数 HKD，部分 RMB）。**PE/PB/股息率是比率，不受币种影响**；
但「市值」和「营收/净利」的绝对值跨市场不可直接比较 —— 展示时必须标币种。
"""
import argparse
import csv
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.stdout.reconfigure(encoding="utf-8")

import em_dc  # noqa: E402
import fetch_fundamentals as ff  # noqa: E402
import http_util  # noqa: E402

CACHE = os.path.join(BASE_DIR, "_fin_cache")
OUT = os.path.join(CACHE, "hk_fundamentals.csv")

# 保留字段（含 A 股口径对照，方便下游统一处理）
FIELDS = [
    "REPORT_DATE", "REPORT_TYPE", "FISCAL_YEAR", "CURRENCY",
    "OPERATE_INCOME", "HOLDER_PROFIT", "GROSS_PROFIT", "PRETAX_PROFIT",
    "ROE_AVG", "ROA", "ROIC_YEARLY", "NET_PROFIT_RATIO", "GROSS_PROFIT_RATIO",
    "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_PARENT_EQUITY",
    "DEBT_ASSET_RATIO", "CURRENT_RATIO", "EQUITY_MULTIPLIER",
    "BASIC_EPS", "DILUTED_EPS", "EPS_TTM", "BPS",
    "PE_TTM", "PB_TTM", "TOTAL_MARKET_CAP",
    "DPS_HKD", "DIVIDEND_RATE", "DIVI_RATIO",
    "ISSUED_COMMON_SHARES", "ORG_TYPE", "NETCASH_OPERATE",
]
HEADER = ["code", "name"] + FIELDS


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if abs(f) >= 1e6 else f


def fetch_one(session, code):
    """一个请求返回该股全部报告期。"""
    rows = em_dc.query(session, "RPT_HKF10_FN_MAININDICATOR", f"{code}.HK",
                       size=200)
    out = []
    for r in rows:
        d = (r.get("REPORT_DATE") or "")[:10]
        if not d:
            continue
        rec = {"code": code, "name": (r.get("SECURITY_NAME_ABBR") or "").strip()}
        for f in FIELDS:
            v = r.get(f)
            rec[f] = d if f == "REPORT_DATE" else (
                (str(v)[:20] if isinstance(v, str) else _num(v)))
        out.append(rec)
    out.sort(key=lambda x: x["REPORT_DATE"])
    return out


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main(argv=None):
    ap = argparse.ArgumentParser(description="港股财务数据")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    pool = ff.hk_pool_codes()
    if not pool:
        print("港股策展池为空（_pool_cache/universe.json 的 HK）")
        return 1
    if args.limit:
        pool = pool[:args.limit]

    have = {}
    keep = {}
    if not args.init:
        for r in _read(OUT):
            c = str(r["code"]).zfill(5)
            d = (r.get("REPORT_DATE") or "")[:10]
            keep.setdefault(c, []).append(r)
            if d > have.get(c, ""):
                have[c] = d

    todo = [c for c in pool if args.init or not have.get(c)]
    print(f"港股池 {len(pool)} 只；需更新 {len(todo)} 只"
          f"（{len(pool) - len(todo)} 只已有缓存）")
    em_dc.reset_stats()

    session = http_util.make_session({"User-Agent": "Mozilla/5.0",
                                      "Referer": "https://data.eastmoney.com/"})
    ok = fail = 0
    fails = []
    for i, code in enumerate(todo, 1):
        try:
            rows = fetch_one(session, code)
        except em_dc.FetchError as exc:
            fail += 1
            fails.append(f"{code} {exc}")
            continue
        if not rows:
            fail += 1
            fails.append(f"{code} 无数据")
            continue
        keep[code] = rows
        ok += 1
        if i % 25 == 0:
            print(f"  [{i}/{len(todo)}] …")

    all_rows = [r for rs in keep.values() for r in rs]
    all_rows.sort(key=lambda r: (str(r["code"]).zfill(5), r["REPORT_DATE"]))
    os.makedirs(CACHE, exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"完成：成功 {ok}、失败 {fail}；{len(all_rows)} 行 → {OUT}")
    print("  " + em_dc.stats_line())
    if fails:
        print(f"::warning::港股财务 {len(fails)} 只失败，前 3：" + "；".join(fails[:3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
