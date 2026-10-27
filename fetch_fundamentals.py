# -*- coding: utf-8 -*-
"""抓一只股票的基本面数据 → JSON，供 `build_fundamentals.py` 渲染。

**为什么用东财 datacenter 而不是 baostock**（实测对比，2026-10）：

| | baostock | 东财 datacenter（本模块） |
|---|---|---|
| 工行全部历史季度 | 4 张表 × 20 季 = **80 次请求** | **1 次请求拿 85 季**（2003Q4~2026Q2） |
| 营收 | 银行只有半年报/年报有 → 得靠「净利÷净利率」反推 | **每季都有** |
| 单季同比 | 要自己差分再算 | **`DJD_TOI_YOY` / `DJD_DPNP_YOY` 现成** |
| 负债率 | 有脏点（2024Q2~2025Q4 返回 0.9%） | **`ZCFZL` 正确** |
| 分红归集年度 | 要按公告日猜 | **`REPORT_DATE` 直接给** |
| 银行专属 | 无 | **净息差 / 不良余额 / 拨贷比** |
| 业绩预告 | 无 | `RPT_PUBLIC_OP_NEWPREDICT` |
| 新增依赖 | baostock（1 个） | **0 个**（复用 requests + http_util） |

用法::

    python fetch_fundamentals.py 601398 --out _fin_cache/601398.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import http_util  # noqa: E402

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _secucode(code: str) -> str:
    code = str(code).zfill(6)
    return f"{code}.{'SH' if code[0] == '6' else 'SZ'}"


def _fetch(session, report, filt, size=500, sort="REPORT_DATE", asc=True):
    """按 reportName 取全部分页。东财单页上限 5000，这里 500 足够一只股票。"""
    out, page = [], 1
    while True:
        params = {"reportName": report, "columns": "ALL", "pageNumber": str(page),
                  "pageSize": str(size), "source": "WEB", "client": "WEB",
                  "filter": filt, "sortColumns": sort,
                  "sortTypes": "1" if asc else "-1"}
        r = session.get(URL, params=params, headers=http_util.EM_HEADERS, timeout=(5, 30))
        r.raise_for_status()
        res = (r.json().get("result") or {})
        data = res.get("data") or []
        out.extend(data)
        if page >= (res.get("pages") or 1) or not data:
            break
        page += 1
    return out


def fetch(code: str, log=print) -> dict:
    import fetch_metrics

    session = fetch_metrics.get_session()
    secucode = _secucode(code)

    fin = _fetch(session, "RPT_F10_FINANCE_MAINFINADATA", f'(SECUCODE="{secucode}")')
    log(f"  季频指标 {len(fin)} 期")

    quarters = []
    for r in fin:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if len(rd) < 10:
            continue
        y, m = int(rd[:4]), int(rd[5:7])
        if m not in (3, 6, 9, 12):
            continue
        quarters.append(dict(
            year=y, quarter=m // 3, statDate=rd,
            pubDate=(r.get("NOTICE_DATE") or "")[:10],
            reportName=r.get("REPORT_DATE_NAME"),
            revenue=_num(r.get("TOTALOPERATEREVE")),
            netProfit=_num(r.get("PARENTNETPROFIT")),
            roe=(_num(r.get("ROEJQ")) or 0) / 100 if r.get("ROEJQ") is not None else None,
            npMargin=(_num(r.get("XSJLL")) or 0) / 100 if r.get("XSJLL") is not None else None,
            gpMargin=(_num(r.get("XSMLL")) or 0) / 100 if r.get("XSMLL") is not None else None,
            liabilityToAsset=(_num(r.get("ZCFZL")) or 0) / 100
            if r.get("ZCFZL") is not None else None,
            cfoToNp=_num(r.get("NCO_NETPROFIT")),
            totalShare=_num(r.get("TOTAL_SHARE")),
            # 东财直接给的同比：TZ = 累计，DJD = **单季**
            yoyRev=_num(r.get("TOTALOPERATEREVETZ")), yoyNi=_num(r.get("PARENTNETPROFITTZ")),
            qRevYoy=_num(r.get("DJD_TOI_YOY")), qNpYoy=_num(r.get("DJD_DPNP_YOY")),
            # 银行专属（其它行业为 None）
            nim=_num(r.get("NET_INTEREST_MARGIN")),
            npl=_num(r.get("NON_PERFORMING_LOAN")),
            provision=_num(r.get("LOAN_PROVISION_RATIO")),
        ))
    quarters.sort(key=lambda x: (x["year"], x["quarter"]))

    divs = []
    for r in _fetch(session, "RPT_SHAREBONUS_DET", f'(SECUCODE="{secucode}")',
                    sort="REPORT_DATE", asc=True):
        cash10, rd = _num(r.get("PRETAX_BONUS_RMB")), (r.get("REPORT_DATE") or "")[:10]
        if cash10 is None or not rd:
            continue
        divs.append(dict(reportDate=rd, cashPs=round(cash10 / 10, 4),   # 东财是每 10 股
                         exDate=(r.get("EX_DIVIDEND_DATE") or "")[:10],
                         yieldAtEx=_num(r.get("DIVIDENT_RATIO")),
                         d30=_num(r.get("D30_CLOSE_ADJCHRATE")),
                         progress=r.get("ASSIGN_PROGRESS")))
    log(f"  分红 {len(divs)} 条")

    preds = []
    for r in _fetch(session, "RPT_PUBLIC_OP_NEWPREDICT", f'(SECUCODE="{secucode}")',
                    size=50, sort="REPORT_DATE", asc=False)[:8]:
        preds.append(dict(reportDate=(r.get("REPORT_DATE") or "")[:10],
                          notice=(r.get("NOTICE_DATE") or "")[:10],
                          type=r.get("PREDICT_TYPE"),
                          content=(r.get("PREDICT_CONTENT") or "")[:160],
                          ampLower=_num(r.get("ADD_AMP_LOWER")),
                          ampUpper=_num(r.get("ADD_AMP_UPPER")),
                          latest=r.get("IS_LATEST")))
    log(f"  业绩预告 {len(preds)} 条")

    # 估值序列：复用仓库既有的 RPT_VALUEANALYSIS_DET（同一份数据源）
    prices = dict(dates=[], close=[], pe=[], pb=[])
    try:
        v = fetch_metrics.fetch_valuation(session, code,
                                          columns="TRADE_DATE,CLOSE_PRICE,PE_TTM,PB_MRQ")
        if v is not None and not v.empty:
            v = v.sort_values("TRADE_DATE")
            prices = dict(dates=[str(x)[:10] for x in v["TRADE_DATE"]],
                          close=[_num(x) for x in v.get("CLOSE_PRICE", [])] or
                          [None] * len(v),
                          pe=[_num(x) for x in v["PE_TTM"]],
                          pb=[_num(x) for x in v["PB_MRQ"]])
            log(f"  估值序列 {len(v)} 个交易日")
    except Exception as exc:
        log(f"  [警告] 估值序列抓取失败（不影响其它区块）：{type(exc).__name__}: {exc}")

    return dict(code=str(code).zfill(6), secucode=secucode, source="eastmoney-datacenter",
                quarters=quarters, dividends=divs, predicts=preds, prices=prices)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓一只股票的基本面数据 → JSON")
    ap.add_argument("code", help="6 位代码，如 601398")
    ap.add_argument("--out", default=None, help="输出 JSON 路径")
    args = ap.parse_args(argv)

    payload = fetch(args.code)
    out = args.out or os.path.join(BASE_DIR, "_fin_cache", f"{payload['code']}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

