# -*- coding: utf-8 -*-
"""把港股财务数据适配成 A 股那套 payload 形状，让渲染器不用改。

## 为什么要适配层

`build_fundamentals.py` 的渲染逻辑是按 A 股 payload 写的：

    {code, quarters:[{year,quarter,statDate,revenue,netProfit,roe,...}],
     dividends:[{reportDate,cashPs}], yr_avg:{year: 年均价}, ...}

港股（`_fin_cache/hk_fundamentals.csv`）是另一套字段名和另一种报表节奏：

| 概念 | A 股 | 港股 |
|---|---|---|
| 营收 | `TOTALOPERATEREVE` | `OPERATE_INCOME` |
| 归母净利 | `PARENTNETPROFIT` | `HOLDER_PROFIT`（股东应占溢利） |
| ROE | `ROEJQ`（已是百分数） | `ROE_AVG`（已是百分数） |
| 资产负债率 | `ZCFZL` | `DEBT_ASSET_RATIO`（已是百分数） |
| 每股分红 | 分红表按财务年度归集 | 直接给 `DPS_HKD` |
| 股息率 | 自己算 | 直接给 `DIVIDEND_RATE` |

**所以这里做字段映射，不改渲染器。** 一份渲染代码服务两个市场，
差异全部收敛在这个文件里 —— 否则两套渲染会慢慢跑偏。

## 三个诚实的处理

1. **港股没有「季度」**，只有年度和中期。年度 → `quarter=4`，中期 → `quarter=2`。
   所以港股页面的季度表会稀疏，这是事实，不是缺数据。

2. **港股的每股分红历史拿不到。** 实测 `DPS_HKD` 与 `DIVIDEND_RATE` 在**所有报告期
   完全相同**（新华保险 2017~2026 都是 3.1031 / 8.18%），它们是**当前快照值**，
   不是当期数据。拿它按年铺开会让分红率算出 719%、股息率 32.7% 这种荒谬数字。

   所以港股**不产出 `dividends`**，年度表的「每股分红」「股息率」两列显示「—」——
   这是事实（拿不到），不是缺数据。

3. **`DIVI_RATIO` 也不可靠。** 实测它并非派息率：新华保险 2017 年报给 1.63（真实约 30%），
   南山铝业 2021 年报给 21390519。同一字段在不同股票上含义不一致，**不能用来算分红率**。

   结论：**港股不显示分红率/每股分红/股息率的历史序列**，三列一律「—」并在表下说明。
   唯一可靠的是**当前股息率**（`DIVIDEND_RATE` 快照），放在页头显示。
"""
import csv
import os

HK_ANNUAL = "年报"


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_hk_payloads(cache_dir):
    """读 hk_fundamentals.csv，返回 {5位代码: payload}。"""
    rows = _read(os.path.join(cache_dir, "hk_fundamentals.csv"))
    by = {}
    for r in rows:
        code = str(r["code"]).zfill(5)
        by.setdefault(code, []).append(r)

    out = {}
    for code, rs in by.items():
        rs.sort(key=lambda x: (x.get("REPORT_DATE") or ""))
        name = (rs[-1].get("name") or "").strip()
        cur = (rs[-1].get("CURRENCY") or "HKD").strip()
        quarters, dividends, yr_avg = [], [], {}

        for r in rs:
            rd = (r.get("REPORT_DATE") or "")[:10]
            if len(rd) < 10:
                continue
            y = int(rd[:4])
            is_annual = HK_ANNUAL in str(r.get("REPORT_TYPE") or "")
            q = 4 if is_annual else 2
            rev = _num(r.get("OPERATE_INCOME"))
            npf = _num(r.get("HOLDER_PROFIT"))
            roe = _num(r.get("ROE_AVG"))
            margin = _num(r.get("NET_PROFIT_RATIO"))
            # 净利率口径与 A 股对齐：归母净利 ÷ 营收（不用源给的 NET_PROFIT_RATIO）
            if rev and npf:
                margin = npf / rev * 100
            quarters.append(dict(
                year=y, quarter=q, statDate=rd, pubDate="",
                reportName=(r.get("REPORT_TYPE") or ""),
                revenue=rev, netProfit=npf,
                roe=(roe / 100) if roe is not None else None,
                npMargin=(margin / 100) if margin is not None else None,
                gpMargin=(_num(r.get("GROSS_PROFIT_RATIO")) or 0) / 100
                if r.get("GROSS_PROFIT_RATIO") else None,
                liabilityToAsset=(_num(r.get("DEBT_ASSET_RATIO")) or 0) / 100
                if r.get("DEBT_ASSET_RATIO") else None,
                cfoToNp=None,
                totalShare=_num(r.get("ISSUED_COMMON_SHARES")),
                yoyRev=None, yoyNi=None, qRevYoy=None, qNpYoy=None,
                nim=None, npl=None, provision=None,
                # 港股特有，渲染器不认识也不影响
                currentRatio=_num(r.get("CURRENT_RATIO")),
                pe=_num(r.get("PE_TTM")), pb=_num(r.get("PB_TTM")),
                mcap=_num(r.get("TOTAL_MARKET_CAP")),
                # 分红历史：拿不到可靠序列（见文件头说明 2/3），一律 None
                payout=None,
            ))

        _last = rs[-1] if rs else {}
        out[code] = dict(code=code, name=name, market="HK", currency=cur,
                         quarters=quarters, dividends=[],
                         predicts=[], yr_avg={}, buybacks=[],
                         pe_range=(None, None), pb_range=(None, None),
                         yld_now=_num(_last.get("DIVIDEND_RATE")),
                         mcap=_num(_last.get("TOTAL_MARKET_CAP")))
    return out
