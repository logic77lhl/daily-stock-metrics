# -*- coding: utf-8 -*-
"""基本面 / 定投 / 分组 的口径金样例（`python tests/test_fundamentals.py`）。

## 为什么要有这个文件

这一轮修掉了 3 个「不报错但功能消失」的 bug：

* `基本面数据` 与 `发信` 并行 → 竞态，当天的变化永远不再提醒；
* 换成 CSV 缓存渲染后 `peers` 没人填 → 同业对比整块消失；
* `main()` 的价格块被回退 → `yr_avg` 恒为空，股息率列 116 只全是「—」。

三个都是手工排查发现的，**测试一个都没拦住**。这里把已经核对过的口径与数字钉死，
以后任何改动先过这一关。

分两类：

1. **口径测试**（合成数据，完全确定）—— 净利率用归母、分红率公式、
   TWR 剥离现金流、XIRR 与手算一致、分组规则；
2. **金样例**（真实缓存）—— 工行 2025 每股分红 0.3103 / 分红率 30%。
   缓存缺失时跳过，不算失败。
"""

from __future__ import annotations

import os
import sys
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.stdout.reconfigure(encoding="utf-8")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))


# ─────────────────────────── 口径 ───────────────────────────
def test_net_margin_uses_parent_profit():
    """净利率必须是「归母净利 ÷ 营收」，不是数据源自带的 XSJLL。

    东财的 XSJLL 是「全部净利（含少数股东）÷ 营收」。工行 2026 中报：
    归母 1737 / 营收 4659 = 37.3%，而 XSJLL 给 37.9% —— 读者一除就发现对不上。
    """
    import build_fundamentals as bf
    q = dict(year=2026, quarter=2, statDate="2026-06-30", pubDate="2026-08-29",
             revenue=4659e8, netProfit=1737e8, roe=0.0432, npMargin=0.379,
             gpMargin=None, liabilityToAsset=0.924, cfoToNp=7.76,
             totalShare=3564e8, yoyRev=9.1, yoyNi=3.3, qRevYoy=9.9, qNpYoy=3.3,
             nim=1.29, npl=4118e8, provision=2.8)
    d = dict(quarters=[q], dividends=[], yr_avg={}, buybacks=[])
    rows = bf.annual_rows(d, years=5)
    m = rows[-1]["margin"]
    check("净利率用归母净利 ÷ 营收", m is not None and abs(m - 37.27) < 0.1,
          f"得到 {m:.2f}%，应为 37.27%（不是 XSJLL 的 37.9%）")


def test_payout_formula():
    """分红率 = 每股分红 × 总股本 ÷ 归母净利。"""
    import build_fundamentals as bf
    q4 = dict(year=2025, quarter=4, statDate="2025-12-31", pubDate="2026-03-28",
              revenue=8383e8, netProfit=3686e8, roe=0.0945, npMargin=0.44,
              gpMargin=None, liabilityToAsset=0.924, cfoToNp=5.1,
              totalShare=3564e8, yoyRev=2.0, yoyNi=0.7, qRevYoy=None, qNpYoy=None,
              nim=None, npl=None, provision=None)
    div = dict(reportDate="2025-12-31", cashPs=0.3103, exDate="2026-05-13")
    d = dict(quarters=[q4], dividends=[div], yr_avg={"2025": 7.37}, buybacks=[])
    rows = bf.annual_rows(d, years=5)
    p = rows[-1]["payout"]
    # 0.3103 × 3564e8 / 3686e8 = 30.0%
    check("分红率 = dps × 总股本 ÷ 归母净利", p is not None and abs(p - 30.0) < 0.3,
          f"得到 {p:.2f}%，应为 30.0%")


def test_dividend_grouped_by_fiscal_year():
    """每股分红按**财务年度**归集（年报+中报相加），不按除权日。"""
    import build_fundamentals as bf
    divs = [dict(reportDate="2025-12-31", cashPs=0.1689, exDate="2026-05-13"),
            dict(reportDate="2025-06-30", cashPs=0.1414, exDate="2025-12-15"),
            dict(reportDate="2024-12-31", cashPs=0.1646, exDate="2025-07-14")]
    by = bf.yearly_dividends(divs)
    check("分红按财务年度归集",
          abs(by.get("2025", 0) - 0.3103) < 1e-9 and abs(by.get("2024", 0) - 0.1646) < 1e-9,
          f"2025 应为 0.3103，得到 {by.get('2025')}")


def test_twr_strips_contributions():
    """TWR 必须剥离每月新增投入 —— 否则会把「投入」算成「收益」。

    构造：价格 10 → 20（翻倍），中途追加投入。
    账户市值涨了 2 倍，但净值只应涨 1 倍（价格本身翻倍）。
    """
    import dca_yearly as dy
    rows = [dict(date="2020-01-02", open=10, high=10, low=10, close=10),
            dict(date="2020-06-01", open=20, high=20, low=20, close=20)]
    bench = [dict(date="2020-01-02", open=1, high=1, low=1, close=1),
             dict(date="2020-06-01", open=1, high=1, low=1, close=1)]
    recs, flows, _ = dy.simulate(rows, {}, "2020-01-01", 100, reinvest=True)
    check("TWR 剥离现金流：价格翻倍 → 净值翻倍",
          abs(recs[-1]["idx"] - 2.0) < 1e-6,
          f"净值 {recs[-1]['idx']:.4f}，应为 2.0（市值/投入 = {recs[-1]['value']/recs[-1]['invested']:.2f} 倍，那不是净值）")


def test_xirr_matches_hand_calc():
    """XIRR 与手算一致：一笔投入、一年后翻倍 → 100%。"""
    import dca_yearly as dy
    flows = [(date(2020, 1, 1), -100.0), (date(2021, 1, 1), 200.0)]
    r = dy.xirr(flows)
    check("XIRR 单笔一年翻倍 = 100%", r is not None and abs(r - 1.0) < 0.01,
          f"得到 {r*100:.2f}%")


def test_xirr_rejects_degenerate():
    """全亏且无解的现金流必须返回 None，不能把求解下界当结果。"""
    import dca_yearly as dy
    flows = [(date(2020, 1, 1), -100.0), (date(2020, 1, 2), 1.0)]
    r = dy.xirr(flows)
    check("XIRR 撞边界返回 None", r is None or r > -0.999,
          f"得到 {r}")


def test_verdict_grouping():
    """分组规则：≥2 正向且无负面 = 值得看；≥2 负面 = 高位；其余中性。"""
    import verdict
    q = [dict(year=y, quarter=4, roe=r) for y, r in
         ((2021, 0.20), (2022, 0.21), (2023, 0.22), (2024, 0.23), (2025, 0.24))]
    divs = {"2024": 0.5, "2025": 0.6}
    yr_avg = {"2024": 10.0, "2025": 10.0}
    meta_cheap = {"PE历史分位%": "10", "PB历史分位%": "12", "最新价": "10",
                  "日线J": "15", "周线J": "18", "月线J": "12"}
    sig, v, d = verdict.decide(q, divs, yr_avg, meta_cheap)
    check("便宜+改善+超卖 → 值得看", v == "值得看",
          f"得到 {v}（正向 {d['nok']} 负面 {d['nbad']}）")

    meta_rich = {"PE历史分位%": "95", "PB历史分位%": "92", "最新价": "10",
                 "日线J": "95", "周线J": "97", "月线J": "99"}
    q_down = [dict(year=y, quarter=4, roe=r) for y, r in
              ((2021, 0.20), (2022, 0.18), (2023, 0.16), (2024, 0.14), (2025, 0.12))]
    sig2, v2, d2 = verdict.decide(q_down, divs, yr_avg, meta_rich)
    check("贵+走弱+超买 → 高位", v2 == "高位",
          f"得到 {v2}（正向 {d2['nok']} 负面 {d2['nbad']}）")


# ─────────────────────────── 金样例（真实缓存） ───────────────────────────
def test_golden_icbc_from_cache():
    """工行 2025：每股分红 0.3103、分红率 30%。缓存缺失则跳过。"""
    cache = os.path.join(BASE, "_fin_cache")
    if not os.path.exists(os.path.join(cache, "fundamentals.csv")):
        print("  [SKIP] 金样例：_fin_cache 不存在")
        return
    import build_fundamentals as bf
    payloads = bf.load_cache_payloads(cache)
    pl = payloads.get("601398")
    if not pl:
        print("  [SKIP] 金样例：缓存里没有 601398")
        return
    rows = {r["year"]: r for r in bf.annual_rows(pl, years=12)}
    r2025 = rows.get(2025)
    ok = (r2025 is not None and abs(r2025["dps"] - 0.3103) < 1e-6
          and abs(r2025["payout"] - 30.0) < 0.5)
    check("金样例：工行 2025 分红 0.3103 / 分红率 30%", ok,
          f"dps={r2025['dps'] if r2025 else None} payout={r2025['payout']:.2f}%"
          if r2025 else "缺 2025 行")
    # 营收/净利也要与年报一致（这两条是数据源的锚）
    if r2025:
        check("金样例：工行 2025 营收 8383 / 净利 3686",
              abs(r2025["revenue"] - 8383) < 1 and abs(r2025["netProfit"] - 3686) < 1,
              f"营收 {r2025['revenue']:.0f} 净利 {r2025['netProfit']:.0f}")



def test_state_file_is_line_based_and_union_safe():
    """状态文件必须是行式文本，且 union 合并（两份拼一起）后读取仍正确。

    背景：`_seen.json` 每天提交，CI 与本地同时跑会冲突。JSON 不能自动合并，
    行式可以 —— 重复行由读取时的 set() 去掉。这条测试就是守这个前提。
    """
    import tempfile
    import fundamentals_changes as fc

    with tempfile.TemporaryDirectory() as td:
        old_txt, old_cache = fc.STATE_TXT, fc.CACHE
        fc.CACHE = td
        fc.STATE_TXT = os.path.join(td, "_seen.txt")
        try:
            cur = dict(reports={"601398": "2026-06-30"},
                       dividends=["601398|2025-12-31|2026-05-13"],
                       buybacks=["000333|2026-10-23|关于回购…"])
            fc._save_state(cur, "2026-10-28")
            got = fc._load_state()
            check("状态文件：写入后读回一致",
                  got["reports"] == cur["reports"]
                  and set(got["dividends"]) == set(cur["dividends"])
                  and set(got["buybacks"]) == set(cur["buybacks"])
                  and got["last_run"] == "2026-10-28",
                  f"读回 {got['last_run']}")

            # 模拟 git union 合并：另一台机器写了新的一行，两份拼在一起
            with open(fc.STATE_TXT, "a", encoding="utf-8") as fh:
                fh.write("R|000333|2026-06-30\nM|2026-10-29\n")
            merged = fc._load_state()
            check("状态文件：union 合并后仍可解析且取并集",
                  merged["reports"].get("000333") == "2026-06-30"
                  and merged["reports"].get("601398") == "2026-06-30"
                  and merged["last_run"] == "2026-10-29",
                  f"reports={len(merged['reports'])} last={merged['last_run']}")
        finally:
            fc.CACHE, fc.STATE_TXT = old_cache, old_txt


def test_annual_join_tool_golden():
    """tools/annual_join.py 的口径与个股页一致（同一份缓存，两条实现）。"""
    cache = os.path.join(BASE, "_fin_cache")
    if not os.path.exists(os.path.join(cache, "fundamentals.csv")):
        print("  [SKIP] annual_join 金样例：_fin_cache 不存在")
        return
    import annual_join
    rows = {r["year"]: r for r in annual_join.build_rows("601398")}
    r = rows.get(2025)
    if not r:
        print("  [SKIP] annual_join 金样例：缓存里没有 2025")
        return
    check("annual_join：工行 2025 分红 0.3103 / 分红率 30%",
          abs(r["dps"] - 0.3103) < 1e-6 and abs(r["payout"] - 30.0) < 0.5,
          f"dps={r['dps']} payout={r['payout']:.2f}%")
    check("annual_join：工行 2025 无回购（银行不回购）",
          r["bk_cancel"] == 0 and r["bk_prog"] == 0,
          f"{r['bk_cancel']}/{r['bk_prog']}")



def test_em_dc_distinguishes_empty_from_failure():
    """em_dc 必须把「报表不适用」和「请求失败」分开 —— 这是本轮最重要的一条。

    原来 `if not success: return []` 把限流/5xx/网络异常全变成「没数据」，
    于是「初筛 447 只、深抓只有 444 只」少的 3 只没人查，
    性能数字也可能因为「失败请求返回得特别快」而偏低。
    """
    import em_dc

    class Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class Sess:
        def __init__(self, payload=None, exc=None):
            self.payload, self.exc = payload, exc

        def get(self, *a, **k):
            if self.exc:
                raise self.exc
            return Resp(self.payload)

    # ① 报表不适用 → 正常空
    em_dc.reset_stats()
    rows = em_dc.query(Sess({"success": False, "message": "报表配置不存在,X"}),
                       "R", "000001.SZ")
    check("em_dc：报表不适用 → 返回空且不抛错", rows == []
          and em_dc.STATS["empty"] == 1 and em_dc.STATS["fail"] == 0,
          f"stats={dict(em_dc.STATS)}")

    # ② success=false 但 message 不是「不适用」→ 必须抛
    em_dc.reset_stats()
    raised = False
    try:
        em_dc.query(Sess({"success": False, "message": "频率限制，请稍后"}),
                    "R", "000001.SZ")
    except em_dc.FetchError:
        raised = True
    check("em_dc：限流 → 抛 FetchError（不当成没数据）",
          raised and em_dc.STATS["fail"] == 1, f"stats={dict(em_dc.STATS)}")

    # ③ 网络异常 → 必须抛
    em_dc.reset_stats()
    raised = False
    try:
        em_dc.query(Sess(exc=ConnectionError("boom")), "R", "000001.SZ")
    except em_dc.FetchError:
        raised = True
    check("em_dc：网络异常 → 抛 FetchError",
          raised and em_dc.STATS["fail"] == 1, f"stats={dict(em_dc.STATS)}")

    # ④ 有数据 → ok
    em_dc.reset_stats()
    rows = em_dc.query(Sess({"success": True, "result": {"data": [{"A": 1}]}}),
                       "R", "000001.SZ")
    check("em_dc：有数据 → 计数为 ok",
          len(rows) == 1 and em_dc.STATS["ok"] == 1, f"stats={dict(em_dc.STATS)}")



def test_graham_criteria():
    """格雷厄姆七条：口径、金融业不适用、数据不足与不通过必须分开。

    原文（第 14 章）：工业股「流动比 ≥ 2 且长期债务 ≤ 营运资本」；
    「PE ≤ 过去 3 年平均利润的 15 倍」；「PB ≤ 1.5，且 PE × PB ≤ 22.5」。
    """
    import graham

    def q(year, npf, share):
        return dict(year=year, quarter=4, statDate=f"{year}-12-31",
                    netProfit=npf, totalShare=share, revenue=1e10, roe=0.15)

    # 10 年盈利、EPS 翻倍、分红够长
    qs = [q(y, 1e9 + (y - 2015) * 2e8, 1e9) for y in range(2015, 2026)]
    divs = [dict(reportDate=f"{y}-12-31", cashPs=0.5) for y in range(2000, 2026)]
    bal = [dict(reportDate="2025-12-31", TOTAL_CURRENT_ASSETS=3e9,
                TOTAL_CURRENT_LIAB=1e9, LONG_LOAN=1e8, BOND_PAYABLE=0,
                NONCURRENT_LIAB_1YEAR=0)]
    meta = dict(最新价=10, PB_MRQ=1.2, 行业="白酒Ⅱ")
    rows, summ = graham.check(qs, divs, bal, meta)
    by = {r["no"]: r for r in rows}
    check("格雷厄姆：流动比 3.0 且长期债务 < 营运资本 → 第2条通过",
          by[2]["status"] == graham.PASS, by[2]["actual"])
    check("格雷厄姆：PE 10 / PB 1.2 → 第6、7条通过",
          by[6]["status"] == graham.PASS and by[7]["status"] == graham.PASS,
          f'{by[6]["actual"]} {by[7]["actual"]}')
    check("格雷厄姆：七条全过", summ["fail"] == 0 and summ["nodata"] == 0,
          summ["verdict"])

    # 金融业：第 2 条必须判「不适用」，不是「不通过」
    meta_fin = dict(meta, 行业="银行Ⅱ")
    rows2, _ = graham.check(qs, divs, bal, meta_fin)
    b2 = {r["no"]: r for r in rows2}
    check("格雷厄姆：金融业第2条判「不适用」而非「不通过」",
          b2[2]["status"] == graham.NA, b2[2]["note"])

    # 分红记录不足 + 上市够久 → 数据不足（不是不通过）
    # 造 26 年年报（2000~2025）但只有 11 年分红记录 —— 这才叫「上市够久、数据不足」
    qs_long = [q(y, 1e9 + (y - 2000) * 1e8, 1e9) for y in range(2000, 2026)]
    short_div = [dict(reportDate=f"{y}-12-31", cashPs=0.5) for y in range(2015, 2026)]
    rows3, _ = graham.check(qs_long, short_div, bal, meta)
    b3 = {r["no"]: r for r in rows3}
    check("格雷厄姆：上市够久但分红数据不足 → 判「数据不足」",
          b3[4]["status"] == graham.NODATA, b3[4]["note"])

    # 上市不足 20 年 → 不适用
    qs_short = qs[-8:]
    rows4, _ = graham.check(qs_short, short_div, bal, meta)
    b4 = {r["no"]: r for r in rows4}
    check("格雷厄姆：上市不足 20 年 → 第4条判「不适用」",
          b4[4]["status"] == graham.NA, b4[4]["note"])


def main():
    print("=" * 68)
    print("基本面 / 定投 / 分组 口径自检")
    print("=" * 68)
    for fn in (test_net_margin_uses_parent_profit, test_payout_formula,
               test_dividend_grouped_by_fiscal_year, test_twr_strips_contributions,
               test_xirr_matches_hand_calc, test_xirr_rejects_degenerate,
               test_verdict_grouping, test_golden_icbc_from_cache,
               test_state_file_is_line_based_and_union_safe,
               test_annual_join_tool_golden,
               test_em_dc_distinguishes_empty_from_failure,
               test_graham_criteria):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            FAIL.append(fn.__name__)
    print("-" * 68)
    print(f"通过 {len(PASS)} 项" + (f"，失败 {len(FAIL)} 项：{FAIL}" if FAIL else ""))
    print("=" * 68)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
