# -*- coding: utf-8 -*-
"""抓基本面数据 → 本地缓存（CSV）。供 `build_fundamentals.py` 渲染。

## 为什么不是「每天全抓」

基本面是**慢变量**：季报一年 4 次、分红一年 1~2 次。但**每只股票的披露时间不同**
（2026-10-27 这天，全市场 5576 只里只有 1948 只出了三季报，工行还没出）。
所以正确做法是**按股票追踪各自的报告期**：

    --refresh   → 1 个请求查「全市场最新报告期」，只重抓落后的那几只
    --init      → 首次：池子里每只各抓一次（季频 + 分红，各 1 请求）

日常增量成本：**1 个请求**（无事可做时）。季报季每天只有几只新披露，就只抓那几只。

## 数据源

东财 datacenter（与仓库现有估值数据同源，零新依赖）：

* `RPT_F10_FINANCE_MAINFINADATA` —— 季频指标，一次给该股**全部历史季度**（85 季）
* `RPT_SHAREBONUS_DET` —— 分红送配，带 `REPORT_DATE`（财务年度）
* `RPT_PUBLIC_OP_NEWPREDICT` —— 业绩预告

用法::

    python fetch_fundamentals.py --init       # 首次，全量
    python fetch_fundamentals.py --refresh    # 日常，增量
    python fetch_fundamentals.py 601398       # 单只调试
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "_fin_cache")

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
elif sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

import http_util  # noqa: E402

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

FIELDS = ["code", "year", "quarter", "statDate", "pubDate", "reportName",
          "revenue", "netProfit", "roe", "npMargin", "gpMargin",
          "liabilityToAsset", "cfoToNp", "totalShare",
          "yoyRev", "yoyNi", "qRevYoy", "qNpYoy",
          "nim", "npl", "provision"]
DIV_FIELDS = ["code", "reportDate", "cashPs", "exDate", "yieldAtEx", "d30", "progress"]
YEAR_FIELDS = ["code", "year", "avgClose", "minClose", "maxClose"]
VALU_FIELDS = ["code", "peLo", "peHi", "pbLo", "pbHi"]


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def secucode(code: str) -> str:
    code = str(code).zfill(6)
    return f"{code}.{'SH' if code[0] == '6' else 'SZ'}"


def _fetch(session, report, filt, size=500, sort="REPORT_DATE", asc=True):
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


# ─────────────────────────── 单只 ───────────────────────────
def fetch_one(session, code: str) -> tuple:
    """返回 (季度行, 分红行, 业绩预告行)。"""
    code = str(code).zfill(6)
    sc = secucode(code)

    rows = []
    for r in _fetch(session, "RPT_F10_FINANCE_MAINFINADATA", f'(SECUCODE="{sc}")'):
        rd = (r.get("REPORT_DATE") or "")[:10]
        if len(rd) < 10:
            continue
        m = int(rd[5:7])
        if m not in (3, 6, 9, 12):
            continue
        rows.append(dict(
            code=code, year=int(rd[:4]), quarter=m // 3, statDate=rd,
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
            yoyRev=_num(r.get("TOTALOPERATEREVETZ")),
            yoyNi=_num(r.get("PARENTNETPROFITTZ")),
            qRevYoy=_num(r.get("DJD_TOI_YOY")),
            qNpYoy=_num(r.get("DJD_DPNP_YOY")),
            nim=_num(r.get("NET_INTEREST_MARGIN")),
            npl=_num(r.get("NON_PERFORMING_LOAN")),
            provision=_num(r.get("LOAN_PROVISION_RATIO")),
        ))

    divs = []
    for r in _fetch(session, "RPT_SHAREBONUS_DET", f'(SECUCODE="{sc}")',
                    sort="REPORT_DATE", asc=True):
        cash10 = _num(r.get("PRETAX_BONUS_RMB"))
        rd = (r.get("REPORT_DATE") or "")[:10]
        if cash10 is None or not rd:
            continue
        divs.append(dict(code=code, reportDate=rd, cashPs=round(cash10 / 10, 4),
                         exDate=(r.get("EX_DIVIDEND_DATE") or "")[:10],
                         yieldAtEx=_num(r.get("DIVIDENT_RATIO")),
                         d30=_num(r.get("D30_CLOSE_ADJCHRATE")),
                         progress=r.get("ASSIGN_PROGRESS")))

    # 名称 + 行业：单独查一次业绩报表（BOARD_NAME）。
    # 为什么不从 metrics CSV 拿：top100 抓取失败时会降级成「只有代码+名称」的历史名单，
    # 那天的 metrics CSV 行业列全是空 —— 页面不该跟着一起空。
    info = {}
    try:
        lico = _fetch(session, "RPT_LICO_FN_CPD", f'(SECUCODE="{sc}")', size=1,
                      sort="REPORTDATE", asc=False)
        if lico:
            info = dict(name=lico[0].get("SECURITY_NAME_ABBR"),
                        industry=lico[0].get("BOARD_NAME") or lico[0].get("PUBLISHNAME"))
    except Exception:
        pass

    preds = []
    for r in _fetch(session, "RPT_PUBLIC_OP_NEWPREDICT", f'(SECUCODE="{sc}")',
                    size=50, sort="REPORT_DATE", asc=False)[:8]:
        preds.append(dict(code=code, reportDate=(r.get("REPORT_DATE") or "")[:10],
                          notice=(r.get("NOTICE_DATE") or "")[:10],
                          type=r.get("PREDICT_TYPE"),
                          content=(r.get("PREDICT_CONTENT") or "")[:160],
                          ampLower=_num(r.get("ADD_AMP_LOWER")),
                          ampUpper=_num(r.get("ADD_AMP_UPPER"))))
    return rows, divs, preds, info


def fetch_valuation_agg(session, code):
    """估值序列 → (年度均价行, 区间)。

    年度均价给「当年股息率」用（每股分红 ÷ 年均价）；区间给估值位置条用。
    一年更新 4 次足够：过去年份的均价不变，区间是历史极值、变化极慢。
    """
    import fetch_metrics

    try:
        v = fetch_metrics.fetch_valuation(
            session, code, columns="TRADE_DATE,CLOSE_PRICE,PE_TTM,PB_MRQ")
    except Exception:
        return [], None
    if v is None or getattr(v, "empty", True):
        return [], None

    v = v.sort_values("TRADE_DATE")
    by_year = {}
    for dt, cl in zip(v.get("TRADE_DATE", []), v.get("CLOSE_PRICE", [])):
        d, c = str(dt)[:10], _num(cl)
        if d and c:
            by_year.setdefault(d[:4], []).append(c)
    year_rows = [dict(code=str(code).zfill(6), year=int(y), avgClose=round(sum(cs) / len(cs), 4),
                      minClose=min(cs), maxClose=max(cs))
                 for y, cs in sorted(by_year.items())]

    def _rng(col):
        vals = [_num(x) for x in v.get(col, [])]
        vals = [x for x in vals if x and x > 0]
        return (min(vals), max(vals)) if vals else (None, None)

    pe_lo, pe_hi = _rng("PE_TTM")
    pb_lo, pb_hi = _rng("PB_MRQ")
    rng = dict(code=str(code).zfill(6), peLo=pe_lo, peHi=pe_hi, pbLo=pb_lo, pbHi=pb_hi)
    return year_rows, rng


# ─────────────────────────── 缓存读写 ───────────────────────────
def _path(name):
    return os.path.join(CACHE_DIR, name)


def _read_codes():
    """{code: {name, industry}} —— 与 metrics CSV 解耦。"""
    out = {}
    f = _path("codes.csv")
    if os.path.exists(f):
        for r in pd.read_csv(f, dtype={"code": str}).to_dict("records"):
            out[str(r["code"]).zfill(6)] = dict(name=r.get("name"),
                                                industry=r.get("industry"))
    return out


def _write_codes(m):
    if not m:
        return
    pd.DataFrame([dict(code=k, **v) for k, v in sorted(m.items())]).to_csv(
        _path("codes.csv"), index=False, encoding="utf-8-sig")


def load_cache():
    q = (pd.read_csv(_path("fundamentals.csv"), dtype={"code": str})
         if os.path.exists(_path("fundamentals.csv")) else pd.DataFrame(columns=FIELDS))
    d = (pd.read_csv(_path("dividends.csv"), dtype={"code": str})
         if os.path.exists(_path("dividends.csv")) else pd.DataFrame(columns=DIV_FIELDS))
    p = (pd.read_csv(_path("predicts.csv"), dtype={"code": str})
         if os.path.exists(_path("predicts.csv")) else pd.DataFrame())
    y = (pd.read_csv(_path("prices_year.csv"), dtype={"code": str})
         if os.path.exists(_path("prices_year.csv")) else pd.DataFrame(columns=YEAR_FIELDS))
    v = (pd.read_csv(_path("valu.csv"), dtype={"code": str})
         if os.path.exists(_path("valu.csv")) else pd.DataFrame(columns=VALU_FIELDS))
    idx = {}
    if os.path.exists(_path("_index.json")):
        try:
            with open(_path("_index.json"), encoding="utf-8") as fh:
                idx = json.load(fh)
        except Exception:
            idx = {}
    return q, d, p, y, v, idx


def save_cache(q, d, p, y, v, idx):
    os.makedirs(CACHE_DIR, exist_ok=True)
    q.to_csv(_path("fundamentals.csv"), index=False, encoding="utf-8-sig")
    d.to_csv(_path("dividends.csv"), index=False, encoding="utf-8-sig")
    if len(p):
        p.to_csv(_path("predicts.csv"), index=False, encoding="utf-8-sig")
    y.to_csv(_path("prices_year.csv"), index=False, encoding="utf-8-sig")
    v.to_csv(_path("valu.csv"), index=False, encoding="utf-8-sig")
    with open(_path("_index.json"), "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1, sort_keys=True)


def upsert(q, d, p, rows, divs, preds):
    """按 (code, statDate) / (code, reportDate, exDate) 去重后合并。"""
    if rows:
        q = pd.concat([q, pd.DataFrame(rows)], ignore_index=True)
        q = q.drop_duplicates(subset=["code", "statDate"], keep="last")
    if divs:
        d = pd.concat([d, pd.DataFrame(divs)], ignore_index=True)
        d = d.drop_duplicates(subset=["code", "reportDate", "exDate"], keep="last")
    if preds:
        p = pd.concat([p, pd.DataFrame(preds)], ignore_index=True)
        p = p.drop_duplicates(subset=["code", "reportDate", "notice"], keep="last")
    return q, d, p


# ─────────────────────────── 池子 / 最新报告期 ───────────────────────────
def pool_codes() -> list:
    """观察池 = 各市场 watchlist.json 的并集，只取 A 股 6 位数字代码。"""
    codes = []
    for sub in ("output", "output_etf", "output_hk"):
        f = os.path.join(BASE_DIR, sub, "watchlist.json")
        if os.path.exists(f):
            try:
                with open(f, encoding="utf-8") as fh:
                    codes.extend(json.load(fh).keys())
            except Exception:
                pass
    # 排除 ETF / LOF / 可转债：它们没有财务报表（159/51/56/58 开头是场内基金）
    ETF_PREFIX = ("15", "16", "50", "51", "52", "56", "58", "11", "12", "13")
    return sorted({str(c) for c in codes
                   if len(str(c)) == 6 and str(c).isdigit()
                   and not str(c).startswith(ETF_PREFIX)})


def latest_report_date(session) -> str:
    """全市场最新报告期（1 个请求）。"""
    params = {"reportName": "RPT_F10_FINANCE_MAINFINADATA", "columns": "REPORT_DATE",
              "pageNumber": "1", "pageSize": "1", "source": "WEB", "client": "WEB",
              "sortColumns": "REPORT_DATE", "sortTypes": "-1"}
    r = session.get(URL, params=params, headers=http_util.EM_HEADERS, timeout=(5, 20))
    res = (r.json().get("result") or {})
    data = res.get("data") or []
    return (data[0]["REPORT_DATE"][:10] if data else "")


# ─────────────────────────── 主流程 ───────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓基本面数据 → 本地缓存")
    ap.add_argument("code", nargs="?", help="单只调试：6 位代码")
    ap.add_argument("--init", action="store_true", help="首次：池子里每只各抓一次")
    ap.add_argument("--refresh", action="store_true", help="日常：只抓落后的那几只")
    ap.add_argument("--valu", action="store_true", help="只补估值聚合（年度均价 / PE-PB 区间）")
    ap.add_argument("--limit", type=int, default=0, help="调试：最多抓几只")
    args = ap.parse_args(argv)

    import fetch_metrics
    session = fetch_metrics.get_session()
    q, d, p, y, v, idx = load_cache()

    if args.code:                                  # 单只调试
        rows, divs, preds, info = fetch_one(session, args.code)
        q, d, p = upsert(q, d, p, rows, divs, preds)
        idx[str(args.code).zfill(6)] = max([r["statDate"] for r in rows] or [""])
        if info:
            c0 = _read_codes()
            c0[str(args.code).zfill(6)] = info
            _write_codes(c0)
        save_cache(q, d, p, y, v, idx)
        print(f"{args.code}: {len(rows)} 季 / {len(divs)} 条分红 → 缓存已更新")
        return 0

    codes = pool_codes()
    if not codes:
        print("观察池为空（output*/watchlist.json），跳过")
        return 0

    if args.valu:                                  # 只补估值聚合
        y0, v0 = y, v
        for i, code in enumerate(codes, 1):
            # 估值序列一只 3~5 秒（2000+ 天），只在首次/手动时抓：
            # 年度均价只影响「当年股息率」（未走完的年度本来就不显示），
            # PE/PB 区间是历史极值、变化极慢。
            if args.init:
                yrows, rng = fetch_valuation_agg(session, code)
                if yrows:
                    y = pd.concat([y, pd.DataFrame(yrows)], ignore_index=True)
                    y = y.drop_duplicates(subset=["code", "year"], keep="last")
                if rng:
                    v = pd.concat([v, pd.DataFrame([rng])], ignore_index=True)
                    v = v.drop_duplicates(subset=["code"], keep="last")
            print(f"  [{i}/{len(codes)}] {code} {len(yrows)} 个年度", flush=True)
        save_cache(q, d, p, y, v, idx)
        print(f"完成：年度均价 {len(y)} 行、估值区间 {len(v)} 行")
        return 0

    if args.init:
        todo = codes
        print(f"--init：池子 {len(codes)} 只，全量抓取")
    else:
        latest = latest_report_date(session)
        todo = [c for c in codes if idx.get(c, "") < latest]
        print(f"--refresh：全市场最新报告期 {latest}；池子 {len(codes)} 只，"
              f"落后 {len(todo)} 只")
        if not todo:
            print("全部已是最新，0 个额外请求。")
            return 0

    if args.limit:
        todo = todo[:args.limit]

    codes_info = _read_codes()
    ok = fail = 0
    for i, code in enumerate(todo, 1):
        try:
            rows, divs, preds, info = fetch_one(session, code)
            if not rows:
                print(f"  [{i}/{len(todo)}] {code} 无数据，跳过")
                continue
            q, d, p = upsert(q, d, p, rows, divs, preds)
            yrows, rng = fetch_valuation_agg(session, code)
            if yrows:
                y = pd.concat([y, pd.DataFrame(yrows)], ignore_index=True)
                y = y.drop_duplicates(subset=["code", "year"], keep="last")
            if rng:
                v = pd.concat([v, pd.DataFrame([rng])], ignore_index=True)
                v = v.drop_duplicates(subset=["code"], keep="last")
            idx[code] = max(r["statDate"] for r in rows)
            if info:
                codes_info[code] = info
            ok += 1
            print(f"  [{i}/{len(todo)}] {code} {len(rows)} 季 {len(divs)} 分红", flush=True)
        except Exception as exc:
            fail += 1
            print(f"  [{i}/{len(todo)}] {code} 失败 {type(exc).__name__}: {exc}")

    _write_codes(codes_info)
    save_cache(q, d, p, y, v, idx)
    print(f"完成：成功 {ok}、失败 {fail}；缓存 {len(q)} 行季频 / {len(d)} 行分红 → {CACHE_DIR}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

