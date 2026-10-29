# -*- coding: utf-8 -*-
"""格雷厄姆「防御型投资者」7 条选股标准 —— 逐条判定。

## 标准原文（《聪明的投资者》1973 第 4 版，第 14 章，中译本 p.228-230）

1. **适当的企业规模**
   「就工业企业而言，年销售额不低于 1 亿美元；就公用事业企业而言，总资产不低于 5000 万美元。」

2. **足够强劲的财务状况**
   「就工业企业而言，流动资产应该至少是流动负债的两倍，即所谓的二比一的流动比。
     同时，长期债务不应该超过流动资产净额，即『营运资本』。
     就公用事业企业而言，负债不应该超过股权（账面值）的两倍。」

3. **利润的稳定性**
   「过去 10 年中，普通股每年都有一定的利润。」

4. **股息记录**
   「至少有 20 年连续支付股息的记录。」

5. **利润增长**
   「过去 10 年内，每股利润的增长至少要达到三分之一（期初和期末使用三年平均数）。」

6. **适度的市盈率**
   「当期股价不应该高于过去 3 年平均利润的 15 倍。」

7. **适度的股价资产比**
   「当期股价不应该超过最后报告的资产账面值的 1.5 倍。然而，当市盈率低于 15 倍时，
     资产乘数可以相应的更高一些。根据经验法则，我们建议，市盈率与价格账面值之比的
     乘积不应该超过 22.5。（这相当于 15 倍的市盈率，乘以 1.5 倍的账面值。
     同时，也可以是这样的股票：9 倍的市盈率和 2.5 倍的资产价值等等。）」

另有一条**组合层面**的补充（p.231）：

> 「所建立的股票组合的总体利润与价格之比（市盈率的倒数），至少应该与当期高等级债券的
> 利率一样高。这就意味着，不高于 13.3 倍的市盈率，相当于收益率为 7.5% 的 AA 级债券。」

这条是**组合**要求，不是单只要求，所以本模块只作为参考输出，不参与单只判定。

## 三个必须说清的实现选择

**① 币种。** 原文门槛是美元。A 股用人民币，这里按 `USD_CNY` 折算，
默认 7.2。这个汇率是**假设**，改它只影响第 1 条（而第 1 条对 A 股几乎是送分题）。

**② 金融业第 2 条判「不适用」。** 银行/保险/券商没有「流动资产/流动负债」科目
（实测东财 B/I/S 三套模板里根本没有这两个字段），负债率 90%+ 也是行业结构。
格雷厄姆在《证券分析》里对金融企业另有标准。**判 na 而不是判 fail** ——
把「不适用」说成「不合格」是错的。

**③ 第 4 条（20 年股息）要分三种情况。** 全池分红记录中位只有 15 年，
所以：
* 上市不足 20 年 → `na`（不适用）
* 上市 ≥ 20 年但记录不足 → `nodata`（我们的数据没回溯那么远）
* 记录够 → 真判定

把 `nodata` 说成 `fail` 会把「数据不够」误判成「公司不合格」。
"""
import os

USD_CNY = 7.2           # 第 1 条的折算假设
SIZE_USD = 1e8          # 工业企业年销售额 ≥ 1 亿美元
SIZE_UTIL_USD = 5e7     # 公用事业总资产 ≥ 5000 万美元

UTIL_KEYS = ("电力", "热力", "燃气", "水务", "环保", "公用事业", "高速公路",
             "港口", "机场", "铁路", "公交")
FIN_KEYS = ("银行", "保险", "证券", "多元金融", "信托")

PASS, FAIL, NA, NODATA = "pass", "fail", "na", "nodata"
LABEL = {PASS: "通过", FAIL: "不通过", NA: "不适用", NODATA: "数据不足"}


def _is_fin(ind):
    return any(k in (ind or "") for k in FIN_KEYS)


def _is_util(ind):
    return any(k in (ind or "") for k in UTIL_KEYS)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def check(quarters, dividends, balance_rows, meta, ind=None):
    """返回 (rows, summary)。

    rows = [{no, name, status, actual, threshold, note}]
    summary = {pass, fail, na, nodata, verdict}
    """
    ind = ind or meta.get("行业") or ""
    fin, util = _is_fin(ind), _is_util(ind)

    # 年报（Q4）按年份升序
    annual = sorted([q for q in (quarters or []) if q.get("quarter") == 4],
                    key=lambda q: q["year"])
    price = _num(meta.get("最新价"))
    pb = _num(meta.get("PB_MRQ"))
    shares = None
    for q in reversed(annual):
        if q.get("totalShare"):
            shares = q["totalShare"]
            break

    def eps(q):
        if q.get("netProfit") and q.get("totalShare"):
            return q["netProfit"] / q["totalShare"]
        return None

    rows = []

    # ── 1 企业规模 ──
    if util:
        assets = _bal(balance_rows, "TOTAL_ASSETS")
        th = SIZE_UTIL_USD * USD_CNY / 1e8
        rows.append(_row(1, "企业规模", assets,
                         f"总资产 ≥ {th:.1f} 亿（公用事业口径）",
                         f"总资产 {assets:,.0f} 亿" if assets else "无总资产",
                         ok=(assets is not None and assets >= th)))
    else:
        rev = annual[-1].get("revenue") / 1e8 if annual and annual[-1].get("revenue") else None
        th = SIZE_USD * USD_CNY / 1e8
        rows.append(_row(1, "企业规模", rev,
                         f"年销售额 ≥ {th:.1f} 亿（1 亿美元 × {USD_CNY}）",
                         f"营收 {rev:,.1f} 亿" if rev else "无营收",
                         ok=(rev is not None and rev >= th)))

    # ── 2 财务状况 ──
    if fin:
        rows.append(dict(no=2, name="财务状况", status=NA, actual="—",
                         threshold="工业/公用事业口径",
                         note="金融业没有流动资产/流动负债科目，格雷厄姆另有标准"))
    elif util:
        liab = _bal(balance_rows, "TOTAL_LIABILITIES")
        eq = _bal(balance_rows, "TOTAL_PARENT_EQUITY")
        ratio = (liab / eq) if (liab and eq) else None
        rows.append(_row(2, "财务状况", ratio, "负债 ≤ 2 倍账面股权",
                         f"负债/股权 {ratio:.2f} 倍" if ratio else "缺负债或股权",
                         ok=(ratio is not None and ratio <= 2.0)))
    else:
        ca = _bal(balance_rows, "TOTAL_CURRENT_ASSETS")
        cl = _bal(balance_rows, "TOTAL_CURRENT_LIAB")
        ltd = _long_debt(balance_rows)
        cr = (ca / cl) if (ca and cl) else None
        wc = (ca - cl) if (ca and cl) else None
        # 两个子条件都要过
        ok_cr = cr is not None and cr >= 2.0
        ok_ltd = (ltd is not None and wc is not None and ltd <= wc)
        detail = []
        if cr is not None:
            detail.append(f"流动比 {cr:.2f}")
        if ltd is not None and wc is not None:
            detail.append(f"长期债务 {ltd / 1e8:,.0f} 亿 / 营运资本 {wc / 1e8:,.0f} 亿")
        rows.append(_row(2, "财务状况", cr,
                         "流动比 ≥ 2 且长期债务 ≤ 营运资本",
                         "　".join(detail) or "缺资产负债表科目",
                         ok=(ok_cr and ok_ltd)))

    # ── 3 利润稳定性 ──
    last10 = annual[-10:]
    if len(last10) < 10:
        rows.append(dict(no=3, name="利润稳定性", status=NODATA, actual=f"仅 {len(last10)} 年",
                         threshold="过去 10 年每年都有利润",
                         note="年报不足 10 期，无法判定"))
    else:
        neg = [q["year"] for q in last10 if (q.get("netProfit") or 0) <= 0]
        rows.append(_row(3, "利润稳定性", len(neg), "过去 10 年每年都有利润",
                         "10 年全部盈利" if not neg else f"{'、'.join(map(str, neg))} 亏损",
                         ok=(not neg)))

    # ── 4 股息记录 ──
    div_years = sorted({int(d["reportDate"][:4]) for d in (dividends or [])
                        if d.get("reportDate") and (d.get("cashPs") or 0) > 0})
    span = (annual[-1]["year"] - annual[0]["year"] + 1) if annual else 0
    if span < 20:
        rows.append(dict(no=4, name="股息记录", status=NA,
                         actual=f"年报仅 {span} 年", threshold="至少 20 年连续支付股息",
                         note="上市/披露不足 20 年，标准不适用"))
    elif len(div_years) < 20:
        rows.append(dict(no=4, name="股息记录", status=NODATA,
                         actual=f"{len(div_years)} 年有记录", threshold="至少 20 年连续支付股息",
                         note="上市已超 20 年，但我们的分红数据没回溯那么远"))
    else:
        rows.append(_row(4, "股息记录", len(div_years), "至少 20 年连续支付股息",
                         f"{len(div_years)} 年有分红记录（{div_years[0]}~{div_years[-1]}）",
                         ok=(len(div_years) >= 20)))

    # ── 5 利润增长（10 年，期初期末都用 3 年平均）──
    if len(annual) < 10:
        rows.append(dict(no=5, name="利润增长", status=NODATA, actual=f"仅 {len(annual)} 年",
                         threshold="10 年每股利润增长 ≥ 1/3（期初期末用 3 年均值）",
                         note="年报不足 10 期"))
    else:
        eps_first = _mean([eps(q) for q in annual[-10:-7]])
        eps_last = _mean([eps(q) for q in annual[-3:]])
        if eps_first and eps_last and eps_first > 0:
            g = eps_last / eps_first - 1
            rows.append(_row(5, "利润增长", g, "10 年每股利润增长 ≥ 33.3%",
                             f"3 年均 EPS {eps_first:.3f} → {eps_last:.3f}（{g * 100:+.0f}%）",
                             ok=(g >= 1 / 3)))
        else:
            rows.append(dict(no=5, name="利润增长", status=NODATA, actual="—",
                             threshold="10 年每股利润增长 ≥ 1/3",
                             note="期初 EPS 缺失或为负，增长无意义"))

    # ── 6 市盈率（过去 3 年平均利润）──
    eps3 = _mean([eps(q) for q in annual[-3:]])
    pe3 = (price / eps3) if (price and eps3 and eps3 > 0) else None
    rows.append(_row(6, "市盈率", pe3, "股价 ≤ 过去 3 年平均利润的 15 倍",
                     f"PE(3 年平均) {pe3:.1f}" if pe3 else "缺价格或 3 年 EPS",
                     ok=(pe3 is not None and pe3 <= 15)))

    # ── 7 股价资产比 ──
    if pe3 and pb:
        prod = pe3 * pb
        rows.append(_row(7, "股价资产比", prod,
                         "PB ≤ 1.5，且 PE × PB ≤ 22.5",
                         f"PB {pb:.2f}　PE×PB {prod:.1f}",
                         ok=(prod <= 22.5 and (pb <= 1.5 or pe3 <= 15))))
    else:
        rows.append(dict(no=7, name="股价资产比", status=NODATA, actual="—",
                         threshold="PB ≤ 1.5 且 PE×PB ≤ 22.5",
                         note="缺 PB 或 PE"))

    cnt = {k: sum(1 for r in rows if r["status"] == k) for k in (PASS, FAIL, NA, NODATA)}
    if cnt[FAIL] == 0 and cnt[NODATA] == 0:
        verdict = "全部通过"
    elif cnt[FAIL] == 0:
        verdict = "无不合规项，但有数据缺口"
    else:
        verdict = f"{cnt[FAIL]} 项不通过"
    return rows, dict(cnt, verdict=verdict)


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _bal(rows, key):
    """取最新一期资产负债表的某个科目（元）。"""
    if not rows:
        return None
    rs = sorted(rows, key=lambda r: (r.get("reportDate") or ""))
    for r in reversed(rs):
        v = _num(r.get(key))
        if v:
            return v
    return None


def _long_debt(rows):
    """长期债务 = 长期借款 + 应付债券 + 一年内到期非流动负债。"""
    parts = [_bal(rows, k) for k in ("LONG_LOAN", "BOND_PAYABLE",
                                     "NONCURRENT_LIAB_1YEAR")]
    parts = [p for p in parts if p]
    return sum(parts) if parts else None


def _row(no, name, actual, threshold, note, ok):
    return dict(no=no, name=name, status=PASS if ok else FAIL,
                actual=note, threshold=threshold, note="")


def label(status):
    return LABEL.get(status, status)
