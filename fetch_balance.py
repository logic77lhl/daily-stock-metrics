# -*- coding: utf-8 -*-
"""补资产负债表 + 送转股历史 —— 为格雷厄姆口径准备数据。

    python fetch_balance.py --init       # 首次全量
    python fetch_balance.py --refresh    # 增量（只抓有新报告期的）

## 为什么要单独一个脚本

`fetch_fundamentals.py` 抓的是「主要财务指标」（营收/净利/ROE），够做趋势，
但格雷厄姆的几条硬标准要的是**资产负债表科目**：

    流动比率 = 流动资产 ÷ 流动负债
    长期债务 vs 营运资金（流动资产 − 流动负债）
    NCAV = 流动资产 − 总负债
    有形账面 = 归母权益 − 无形资产 − 商誉

这些在 `RPT_F10_FINANCE_MAINFINADATA` 里都没有。

## 四套模板（这是关键坑）

A 股财报不是一套模板。同一只股票，用错报表名会**返回空数据且不报错**：

| 行业 | reportName | 字段数 | 实测 |
|---|---|---|---|
| 一般工商业 | `RPT_F10_FINANCE_GBALANCE` | 319 | 美的/茅台/五粮液 ✓ |
| 银行 | `RPT_F10_FINANCE_BBALANCE` | 221 | 工行 ✓ |
| 保险 | `RPT_F10_FINANCE_IBALANCE` | 253 | 平安/太保/人寿 ✓ |
| 券商 | `RPT_F10_FINANCE_SBALANCE` | 215 | 中信/华泰/广发 ✓ |

所以按 G→B→I→S 顺序探测，第一个有数据的即为该股模板，结果缓存进
`_balance_tpl.json`，下次直接用。

**注意**：银行没有「流动资产/流动负债」概念，它的 221 个字段里根本没有这两项。
这不是数据缺失，是会计科目本身不存在 —— 格雷厄姆对银行另有标准
（看账面价值、贷款质量、存款结构），不能套工业股的流动比率。

## 存储策略

资产负债表 85 个季度 × 26 字段 × 116 只 ≈ 5MB，太大。只留：

* **全部 Q4 行**（年报，做 10 年趋势）
* **每只最新一行**（算当期流动比率）

实测 116 只约 200KB。
"""
import argparse
import csv
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.stdout.reconfigure(encoding="utf-8")

import http_util  # noqa: E402

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CACHE = os.path.join(BASE_DIR, "_fin_cache")
TPL_PATH = os.path.join(CACHE, "_balance_tpl.json")
OUT = os.path.join(CACHE, "balance.csv")
SPLIT_OUT = os.path.join(CACHE, "splits.csv")

# 探测顺序：一般工商业 → 银行 → 保险 → 券商
TEMPLATES = [
    ("G", "RPT_F10_FINANCE_GBALANCE"),
    ("B", "RPT_F10_FINANCE_BBALANCE"),
    ("I", "RPT_F10_FINANCE_IBALANCE"),
    ("S", "RPT_F10_FINANCE_SBALANCE"),
]

# 保留字段：四套模板的并集，缺的写空
FIELDS = [
    # 通用
    "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_PARENT_EQUITY", "TOTAL_EQUITY",
    "FIXED_ASSET", "MONETARYFUNDS", "INTANGIBLE_ASSET", "GOODWILL",
    # 一般工商业（格雷厄姆工业股标准用）
    "TOTAL_CURRENT_ASSETS", "TOTAL_CURRENT_LIAB",
    "TOTAL_NONCURRENT_ASSETS", "TOTAL_NONCURRENT_LIAB",
    "SHORT_LOAN", "LONG_LOAN", "BOND_PAYABLE", "NONCURRENT_LIAB_1YEAR",
    "INVENTORY", "ACCOUNTS_RECE", "ACCOUNTS_PAYABLE", "NOTE_ACCOUNTS_RECE",
    # 银行
    "LOAN_ADVANCE", "ACCEPT_DEPOSIT", "LOAN_LOSS_PROVISION",
    "CASH_DEPOSIT_PBC", "SELL_REPO_FINASSET",
]
HEADER = ["code", "template", "reportDate"] + FIELDS


def _get(session, report, secucode, page=1, size=200):
    params = {"reportName": report, "columns": "ALL", "pageNumber": str(page),
              "pageSize": str(size), "filter": f'(SECUCODE="{secucode}")',
              "sortColumns": "REPORT_DATE", "sortTypes": "-1"}
    r = session.get(URL, params=params, headers=http_util.EM_HEADERS, timeout=(5, 30))
    j = r.json()
    if not j.get("success"):
        return []
    return (j.get("result") or {}).get("data") or []


def detect_template(session, secucode, tpl_cache):
    """按 G→B→I→S 探测，第一个有数据的即为该股模板。"""
    hit = tpl_cache.get(secucode)
    if hit:
        for key, report in TEMPLATES:
            if key == hit:
                rows = _get(session, report, secucode, size=200)
                if rows:
                    return key, rows
                break
    for key, report in TEMPLATES:
        rows = _get(session, report, secucode, size=200)
        if rows:
            tpl_cache[secucode] = key
            return key, rows
    return None, []


def _num(v):
    """金额取整到元 —— 资产负债表科目以元为单位，小数没有意义，
    但会让 CSV 每格多出十几个字符（全池 578KB → 取整后约 300KB）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if abs(f) >= 1 else f


def fetch_one(session, code, tpl_cache):
    secucode = f"{code}.{'SH' if code[0] == '6' else 'SZ'}"
    key, rows = detect_template(session, secucode, tpl_cache)
    if not rows:
        return None, []
    out = []
    for r in rows:
        d = (r.get("REPORT_DATE") or "")[:10]
        if not d:
            continue
        out.append(dict(code=code, template=key, reportDate=d,
                        **{f: _num(r.get(f)) for f in FIELDS}))
    return key, out


def fetch_splits(session, code):
    """送转股历史。算 10 年 EPS 增长必须用它还原股本变动。"""
    secucode = f"{code}.{'SH' if code[0] == '6' else 'SZ'}"
    rows = _get(session, "RPT_SHAREBONUS_DET", secucode, size=200)
    out = []
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if not rd:
            continue
        out.append(dict(
            code=code, reportDate=rd,
            exDate=(r.get("EX_DIVIDEND_DATE") or "")[:10],
            cashPs=_num(r.get("PRETAX_BONUS_RMB")),
            bonusRatio=_num(r.get("BONUS_RATIO")),
            itRatio=_num(r.get("IT_RATIO")),
            plan=(r.get("IMPL_PLAN_PROFILE") or "")[:40]))
    return out


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _latest_reports(csv_name):
    """一次读完 fundamentals.csv，返回 {code: 最新报告期}。

    原来是每只股票各读一遍 —— 116 次 × 1.7MB 的 CSV 解析，实测 11 秒里
    绝大部分耗在这里。读一次建索引后降到 1 秒级。
    """
    best = {}
    for r in _read(os.path.join(CACHE, csv_name)):
        c = str(r.get("code", "")).zfill(6)
        d = (r.get("statDate") or "")[:10]
        if d > best.get(c, ""):
            best[c] = d
    return best


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓资产负债表与送转股")
    ap.add_argument("--init", action="store_true", help="全量重抓")
    ap.add_argument("--refresh", action="store_true", help="增量（默认）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    import fetch_fundamentals as ff
    pool = ff.pool_codes()
    if not pool:
        print("观察池为空")
        return 1
    if args.limit:
        pool = pool[:args.limit]

    tpl_cache = {}
    if os.path.exists(TPL_PATH):
        try:
            with open(TPL_PATH, encoding="utf-8") as fh:
                tpl_cache = json.load(fh)
        except Exception:
            tpl_cache = {}

    # 已有缓存：code -> 最大报告期
    have = {}
    if not args.init:
        for r in _read(OUT):
            c = str(r["code"]).zfill(6)
            d = (r.get("reportDate") or "")[:10]
            if d > have.get(c, ""):
                have[c] = d

    keep_rows = [] if args.init else _read(OUT)
    keep = {str(r["code"]).zfill(6): [] for r in keep_rows}
    for r in keep_rows:
        keep.setdefault(str(r["code"]).zfill(6), []).append(r)

    split_rows = [] if args.init else _read(SPLIT_OUT)

    session = http_util.session() if hasattr(http_util, "session") else None
    if session is None:
        import requests
        session = requests.Session()

    latest_fund = _latest_reports("fundamentals.csv")
    todo = []
    for code in pool:
        code = str(code).zfill(6)
        target = latest_fund.get(code, "")
        if not args.init and have.get(code, "") >= target and target:
            continue
        todo.append(code)

    print(f"观察池 {len(pool)} 只；需更新 {len(todo)} 只"
          f"（{len(pool) - len(todo)} 只已是最新）")

    ok = fail = 0
    for i, code in enumerate(todo, 1):
        try:
            key, rows = fetch_one(session, code, tpl_cache)
            if not rows:
                fail += 1
                print(f"  [{i}/{len(todo)}] {code} 四套模板都为空")
                continue
            # 只留 Q4（年报）+ 最新一行
            latest = rows[0]["reportDate"]
            keep[code] = [r for r in rows
                          if r["reportDate"][5:7] == "12" or r["reportDate"] == latest]
            sp = fetch_splits(session, code)
            split_rows = [r for r in split_rows
                          if str(r.get("code", "")).zfill(6) != code] + sp
            ok += 1
            if i % 20 == 0:
                print(f"  [{i}/{len(todo)}] …")
        except Exception as exc:
            fail += 1
            print(f"  [{i}/{len(todo)}] {code} 失败 {type(exc).__name__}: {exc}")

    all_rows = [r for rs in keep.values() for r in rs]
    all_rows.sort(key=lambda r: (str(r["code"]).zfill(6), r["reportDate"]))
    os.makedirs(CACHE, exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    split_rows.sort(key=lambda r: (str(r["code"]).zfill(6), r["reportDate"]))
    with open(SPLIT_OUT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "reportDate", "exDate", "cashPs",
                                           "bonusRatio", "itRatio", "plan"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(split_rows)

    with open(TPL_PATH, "w", encoding="utf-8") as fh:
        json.dump(tpl_cache, fh, ensure_ascii=False, indent=1, sort_keys=True)

    from collections import Counter
    cnt = Counter(tpl_cache.get(f"{str(c).zfill(6)}.{'SH' if str(c).zfill(6)[0] == '6' else 'SZ'}") for c in pool)
    print(f"完成：成功 {ok}、失败 {fail}；"
          f"{len(all_rows)} 行资产负债表 / {len(split_rows)} 行分红送转")
    print("模板分布：" + "　".join(f"{k} {v} 只" for k, v in sorted(cnt.items()) if k))
    print(f"→ {OUT}\n→ {SPLIT_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
