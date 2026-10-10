# -*- coding: utf-8 -*-
"""backtest.py 定价/收益/净值口径自检（纯离线、合成序列，`python tests/test_backtest.py`）。

覆盖本次修复的每一条：
  1. T+1 开盘建仓（不再用信号日收盘价，也不再静默回退到更早的 bar）
  2. 缓存新鲜度 = 必须覆盖「面板要求的最后一个交易日」，旧格式缓存必须重抓
  3. 缺 bar / 缺价格序列的剔除必须计数，不能静默丢交易
  4. 收益用乘性成本口径，且可由页面上的买入价/卖出价复算
  5. 重叠持有期的累计净值改为不重叠切桶复利
  6. eval_expr 对 PE5年分位% / PB5年分位% 等别名列不再抛 KeyError
  7. 市场交易日历取自价格序列并集（面板会缺日/含非交易日脏数据）
  8. DSM_DEADLINE_SEC 生效时抓取会提前收手（且离线不联网）
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest as bt  # noqa: E402


def _px(rows):
    """rows: [(date, open, close)] -> DataFrame(index=date, columns=[open, close])"""
    df = pd.DataFrame(rows, columns=["date", "open", "close"])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[["open", "close"]].sort_index()


def _panel(dates, code="600000", market="个股"):
    rows = [{
        "市场": market, "日期": pd.Timestamp(d), "代码": f"{market}_{code}", "名称": "测试",
        "排名": 1, "日线J": 10.0, "周线J": 10.0, "月线J": 10.0,
        "PE_TTM": 10.0, "PE历史分位%": 10.0, "PB_MRQ": 1.0, "PB历史分位%": 10.0,
    } for d in dates]
    return pd.DataFrame(rows)


def _log(*_a, **_k):
    pass


# ── 1. T+1 开盘建仓 ────────────────────────────────────────────────

def test_entry_is_next_day_open_not_signal_close():
    panel = _panel(["2026-09-14", "2026-09-15", "2026-09-16"])
    px = {"个股_600000": _px([
        ("2026-09-14", 10.0, 11.0),
        ("2026-09-15", 20.0, 21.0),
        ("2026-09-16", 30.0, 31.0),
    ])}
    stats = {}
    tr = bt.build_trades(panel, [1, 2], px, cost_pct=0.15, stats=stats,
                         last_day=pd.Timestamp("2026-09-16"))
    h1 = tr[tr["持有期"] == 1].iloc[0]
    # 买入价必须是 T+1 的开盘价 20，而不是信号日收盘价 11
    assert float(h1["买入价"]) == 20.0, h1["买入价"]
    assert str(h1["买入日"])[:10] == "2026-09-15"
    assert str(h1["卖出日"])[:10] == "2026-09-15"
    assert float(h1["卖出价"]) == 21.0
    assert int(h1["实际持有交易日"]) == 1
    h2 = tr[tr["持有期"] == 2].iloc[0]
    assert float(h2["买入价"]) == 20.0 and float(h2["卖出价"]) == 31.0
    assert int(h2["实际持有交易日"]) == 2
    print("  [PASS] 买入价 = T+1 开盘价（非信号日收盘），h=1 为 T+1 开盘→当日收盘")


def test_no_silent_fallback_to_earlier_bar():
    """T+1 当天没有 K 线（停牌/次新/序列未覆盖）→ 剔除并计数，绝不回退到更早的 bar。

    注意：交易日历取自全样本 K 线并集，所以「缺 09-15」必须由另一只标的提供
    09-15 这根 bar，才构成「市场开市但该标的停牌」的真实场景。
    """
    panel = pd.concat([_panel(["2026-09-14", "2026-09-15"], code="600000"),
                       _panel(["2026-09-14", "2026-09-15"], code="600001")], ignore_index=True)
    px = {
        "个股_600000": _px([("2026-09-14", 10.0, 11.0), ("2026-09-17", 30.0, 31.0)]),
        "个股_600001": _px([("2026-09-14", 1.0, 1.0), ("2026-09-15", 2.0, 2.0)]),
    }
    stats = {}
    tr = bt.build_trades(panel, [1], px, stats=stats, last_day=pd.Timestamp("2026-09-17"))
    bad = tr[(tr["信号日"] == pd.Timestamp("2026-09-14")) & (tr["代码"] == "600000")]
    assert bad.empty, bad                          # 缺 T+1 的标的不许用 09-14 的价格成交
    assert stats["drop_no_entry_bar"] == 2, stats    # 600000@09-14 与 600001@09-15 都缺建仓 bar
    assert stats["drop_no_exit_bar"] == 0, stats
    assert stats["trades_built"] == 2, stats
    # 直接验证 helper：缺失即 None，不返回 09-14 的价格
    buy, sell, _bi, _si, why = bt._entry_exit_prices(px["个股_600000"],
                                                     pd.Timestamp("2026-09-15"), pd.Timestamp("2026-09-17"))
    assert (buy, sell, why) == (None, None, "entry"), (buy, sell, why)
    buy2, sell2, _b2, _s2, why2 = bt._entry_exit_prices(px["个股_600000"],
                                                        pd.Timestamp("2026-09-14"), pd.Timestamp("2026-09-15"))
    assert (buy2, sell2, why2) == (None, None, "exit"), (buy2, sell2, why2)
    print("  [PASS] 缺 T+1/平仓日 K 线 → 计数剔除，无静默回退")


# ── 2. 成本口径与可复算性 ──────────────────────────────────────────

def test_cost_is_multiplicative_and_reproducible():
    panel = _panel(["2026-09-14", "2026-09-15"])
    px = {"个股_600000": _px([("2026-09-14", 10.0, 11.0), ("2026-09-15", 20.0, 21.0)])}
    c = 0.15
    tr = bt.build_trades(panel, [1], px, cost_pct=c, last_day=pd.Timestamp("2026-09-15"))
    row = tr.iloc[0]
    buy, sell, got = float(row["买入价"]), float(row["卖出价"]), float(row["收益%"])
    exact = ((sell * (1 - c / 100)) / (buy * (1 + c / 100)) - 1) * 100
    additive = (sell / buy - 1) * 100 - c
    assert abs(got - round(exact, 2)) < 1e-9, (got, exact)
    assert got < round(additive, 2), (got, additive)   # 加性近似恒偏乐观
    # 用报告里显示的价格（3 位小数）即可复算出显示的收益
    assert abs(got - round(exact, 2)) < 1e-9
    print(f"  [PASS] 收益={got}% = 乘性口径（加性近似会给 {round(additive, 2)}%，偏乐观）")


def test_price_decimals_are_stored_unrounded_ratio():
    """价格按 PRICE_DECIMALS 舍入后写入，收益直接由这两个值算出。"""
    panel = _panel(["2026-09-14", "2026-09-15"])
    px = {"个股_600000": _px([("2026-09-14", 1.0, 1.0), ("2026-09-15", 1.2345, 1.3456)])}
    tr = bt.build_trades(panel, [1], px, cost_pct=0.0, last_day=pd.Timestamp("2026-09-15"))
    row = tr.iloc[0]
    assert float(row["买入价"]) == round(1.2345, bt.PRICE_DECIMALS) == 1.234
    assert float(row["卖出价"]) == round(1.3456, bt.PRICE_DECIMALS) == 1.346
    assert float(row["收益%"]) == round((1.346 / 1.234 - 1) * 100, 2)
    print("  [PASS] 买入/卖出价按 3 位小数存储，收益% 可由存储价复算")


# ── 3. 市场交易日历 / 面板缺日 / 脏数据 ─────────────────────────────

def test_panel_gap_uses_real_market_calendar():
    """面板缺了中间的交易日时，T+1 必须取真实下一个交易日（K线并集），不是下一个面板日。"""
    panel = _panel(["2026-09-17", "2026-09-18", "2026-10-08"])
    px = {"个股_600000": _px([
        ("2026-09-17", 1.0, 2.0),
        ("2026-09-18", 3.0, 4.0),
        ("2026-09-21", 5.0, 6.0),   # 面板里缺这一天
        ("2026-09-22", 7.0, 8.0),
        ("2026-10-08", 9.0, 10.0),
    ])}
    tr = bt.build_trades(panel, [1, 3], px, last_day=pd.Timestamp("2026-10-08"))
    r1 = tr[(tr["信号日"] == pd.Timestamp("2026-09-18")) & (tr["持有期"] == 1)].iloc[0]
    assert str(r1["买入日"])[:10] == "2026-09-21", r1["买入日"]
    assert float(r1["买入价"]) == 5.0 and float(r1["卖出价"]) == 6.0
    r3 = tr[(tr["信号日"] == pd.Timestamp("2026-09-17")) & (tr["持有期"] == 3)].iloc[0]
    assert str(r3["买入日"])[:10] == "2026-09-18" and str(r3["卖出日"])[:10] == "2026-09-22"
    print("  [PASS] 面板缺日时 T+1 仍取真实下一个交易日（并集日历）")


def test_non_trading_panel_day_is_counted_not_priced():
    """面板里的非交易日（脏数据，如 2026-10-02 国庆假期）不参与定价。"""
    panel = _panel(["2026-09-30", "2026-10-02", "2026-10-08"])
    px = {"个股_600000": _px([
        ("2026-09-30", 1.0, 2.0), ("2026-10-08", 3.0, 4.0), ("2026-10-09", 5.0, 6.0),
    ])}
    stats = {}
    tr = bt.build_trades(panel, [1], px, stats=stats, last_day=pd.Timestamp("2026-10-08"))
    assert stats["skip_non_trading_day"] == 1, stats
    assert (tr["信号日"] == pd.Timestamp("2026-10-02")).sum() == 0
    print("  [PASS] 非交易日面板信号被计数剔除（旧版会回退到 09-30 收盘价定价）")


def test_required_last_date_and_dirty_days():
    dirty = _panel(["2026-09-30", "2026-10-02"])
    assert bt._required_last_date(dirty, "个股", log=_log) == pd.Timestamp("2026-09-30")
    assert bt._non_trading_panel_days(dirty, "个股") == ["2026-10-02"]
    clean = _panel(["2026-09-30", "2026-10-08"])
    assert bt._required_last_date(clean, "个股", log=_log) == pd.Timestamp("2026-10-08")
    assert bt._non_trading_panel_days(clean, "个股") == []
    print("  [PASS] 新鲜度门槛/脏数据识别走交易日历（非交易日不当门槛）")


# ── 4. 缓存失效 ────────────────────────────────────────────────────

def test_cache_freshness_rules():
    tmp = tempfile.mkdtemp()
    req = pd.Timestamp("2026-10-08")
    fresh = os.path.join(tmp, "fresh.csv")
    pd.DataFrame({"date": pd.to_datetime(["2026-09-30", "2026-10-08"]),
                  "open": [1.0, 2.0], "close": [1.1, 2.1]}).to_csv(fresh, index=False)
    df, why = bt._read_cache(fresh, req)
    assert df is not None and why is None and list(df.columns) == ["open", "close"]

    stale = os.path.join(tmp, "stale.csv")
    pd.DataFrame({"date": pd.to_datetime(["2026-09-25", "2026-09-30"]),
                  "open": [1.0, 2.0], "close": [1.1, 2.1]}).to_csv(stale, index=False)
    df2, why2 = bt._read_cache(stale, req)
    assert df2 is None and "2026-09-30" in why2, (df2, why2)

    old = os.path.join(tmp, "old.csv")
    pd.DataFrame({"date": pd.to_datetime(["2026-09-30", "2026-10-08"]),
                  "close": [1.1, 2.1]}).to_csv(old, index=False)
    df3, why3 = bt._read_cache(old, req)
    assert df3 is None and "open" in why3, (df3, why3)
    print("  [PASS] 缓存须含 open 且覆盖所需最后交易日；旧格式/过期一律重抓")


def test_cache_roundtrip_matches_writer_format():
    """模拟 _fetch_one 的写盘方式，确认 _read_cache 能原样读回（列名/日期对齐）。"""
    tmp = tempfile.mkdtemp()
    raw_df = pd.DataFrame({"date": ["2026-09-30", "2026-10-08"], "open": [1.0, 2.0], "close": [1.1, 2.1]})
    s = raw_df.set_index(pd.to_datetime(raw_df["date"]))[["open", "close"]].sort_index()
    s.index.name = "date"
    path = os.path.join(tmp, "rt.csv")
    s.reset_index().to_csv(path, index=False)
    df, why = bt._read_cache(path, pd.Timestamp("2026-10-08"))
    assert df is not None, why
    assert list(df.columns) == ["open", "close"]
    assert df.index.max() == pd.Timestamp("2026-10-08")
    print("  [PASS] 缓存写读往返一致（date/open/close）")


# ── 5. 剔除计数 ────────────────────────────────────────────────────

def test_drop_counters_and_suspension_holding_days():
    days = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    panel = pd.concat([_panel(["2026-09-14"], code=c) for c in
                       ("600000", "600001", "600002", "600003")], ignore_index=True)
    px = {
        # 600000：中途停牌（缺 09-16）→ 建仓/平仓 bar 都在，但实际持有交易日 < h
        "个股_600000": _px([("2026-09-14", 1.0, 1.0), ("2026-09-15", 1.0, 1.0),
                            ("2026-09-17", 1.0, 1.0), ("2026-09-18", 1.0, 1.0)]),
        # 600001：T+1（09-15）缺 bar → 计数剔除
        "个股_600001": _px([("2026-09-14", 1.0, 1.0), ("2026-09-16", 1.0, 1.0)]),
        # 600002：完全没有价格序列 → 计数剔除
        # 600003：完整序列，负责定义市场交易日历
        "个股_600003": _px([(d, 1.0, 1.0) for d in days]),
    }
    stats = {}
    tr = bt.build_trades(panel, [3], px, stats=stats, last_day=pd.Timestamp("2026-09-18"))
    assert stats["drop_no_price_series"] == 1, stats
    assert stats["drop_no_entry_bar"] == 1, stats
    assert stats["trades_built"] == 2, stats
    r = tr[tr["代码"] == "600000"].iloc[0]
    assert int(r["实际持有交易日"]) == 2, r["实际持有交易日"]   # 缺 09-16，h=3 只交易了 2 天
    assert int(tr[tr["代码"] == "600003"].iloc[0]["实际持有交易日"]) == 3
    print("  [PASS] 缺序列/缺 T+1 计数剔除；停牌使实际持有交易日 < h（h=3 → 2）")


# ── 6. 不重叠净值 ──────────────────────────────────────────────────

def test_bucket_nav_is_not_overlapping():
    """每笔交易总收益固定 1%：h=3 时一段行情只能计入一次，不能被复利 3 遍。"""
    daily = pd.Series([1.0] * 9)
    nav1 = bt._bucket_nav(daily, 1)
    nav3 = bt._bucket_nav(daily, 3)
    old3 = (1 + daily / 100).cumprod()     # 旧口径：把重叠仓位逐日复利
    assert abs(nav1.iloc[-1] - 1.01 ** 9) < 1e-12
    assert abs(nav3.iloc[-1] - 1.01 ** 3) < 1e-12, nav3.iloc[-1]
    assert nav3.iloc[-1] < old3.iloc[-1] - 1e-6
    print(f"  [PASS] h=3 不重叠净值 {nav3.iloc[-1]:.6f} < 旧重叠口径 {old3.iloc[-1]:.6f}"
          f"（旧口径把同一段行情重复计入 3 次）")


def test_summary_has_drawdown_for_all_horizons():
    trades = pd.DataFrame({
        "策略": ["S"] * 6, "持有期": [3] * 6,
        "信号日": pd.to_datetime(["2026-09-14", "2026-09-15", "2026-09-16",
                                  "2026-09-17", "2026-09-18", "2026-09-21"]),
        "收益%": [3.0, 3.0, 3.0, -3.0, -3.0, -3.0],
    })
    summary, equity = bt.summarize(trades, [3])
    row = summary.iloc[0]
    assert "不重叠累计净值" in summary.columns
    assert pd.notna(row["最大回撤%"]) and row["最大回撤%"] < 0
    assert equity == {}
    print("  [PASS] h>1 也有最大回撤（基于不重叠净值），净值列名已改为不重叠口径")


# ── 7. eval_expr 别名列 ────────────────────────────────────────────

def test_eval_expr_alias_columns():
    full = pd.DataFrame({
        "日线J": [10.0, 90.0], "周线J": [10.0, 90.0], "月线J": [10.0, 90.0],
        "PE_TTM": [10.0, 10.0], "PE历史分位%": [10.0, 90.0], "PB_MRQ": [1.0, 1.0],
        "PB历史分位%": [10.0, 90.0], "涨跌幅": [0.0, 0.0], "最新价": [1.0, 2.0],
        "MA20": [1.0, 2.0], "MA60": [1.0, 1.0], "双均线多头": [1.0, 1.0],
        "价距MA20%": [0.0, 0.0], "量比": [0.5, 2.0],
        "PE5年分位%": [10.0, 90.0], "PB5年分位%": [10.0, 90.0],
    })
    for expr, want in [("PE5年分位% < 30", [True, False]),
                       ("PB5年分位% > 80", [False, True]),
                       ("日线J<30 and 量比>1.5", [False, False]),
                       ("价距MA20% < 1 and MA20 > MA60", [False, True])]:
        got = list(bt.eval_expr(full, expr))
        assert got == want, (expr, got, want)
    short = full.drop(columns=["PE5年分位%", "PB5年分位%", "MA20", "MA60", "双均线多头", "价距MA20%", "量比"])
    assert list(bt.eval_expr(short, "PE5年分位% < 30")) == [False, False]   # 缺列 → 全 False，不抛 KeyError
    print("  [PASS] PE5年分位%/PB5年分位% 等别名列可用；缺列退化为全 False 而非崩溃")


# ── 8. DSM_DEADLINE_SEC ────────────────────────────────────────────

def test_deadline_gate_stops_fetching_offline():
    """DSM_DEADLINE_SEC 生效时，两次抓取之间提前收手（用桩 Deadline，完全不联网）。"""
    import http_util

    panel = _panel(["2026-09-14", "2026-09-15"])
    orig_dl, orig_fetch = bt._run_deadline, bt._fetch_ohlc

    class _Exhausted:
        def remaining(self):
            return -1.0

    class _Live:
        def remaining(self):
            return 1e9

    try:
        bt._run_deadline = lambda: _Exhausted()
        stats = {}
        px = bt.build_price_map(panel, "个股", tempfile.mkdtemp(), bars=10, stats=stats, log=_log)
        assert px == {}, px
        assert stats["budget_exhausted"] == 1, stats

        # 抓取过程中预算耗尽：立刻收手，不再退避重试 3 次
        calls = []

        def _boom(*_a, **_k):
            calls.append(1)
            raise http_util.DeadlineExceeded("预算耗尽")

        bt._run_deadline = lambda: _Live()
        bt._fetch_ohlc = _boom
        stats2 = {}
        px2 = bt.build_price_map(panel, "个股", tempfile.mkdtemp(), bars=10, stats=stats2, log=_log)
        assert px2 == {} and stats2["budget_exhausted"] == 1 and len(calls) == 1, (stats2, len(calls))
    finally:
        bt._run_deadline, bt._fetch_ohlc = orig_dl, orig_fetch

    # 未设置/为 0 时不限制（离线重放缓存不受影响）
    old = os.environ.get("DSM_DEADLINE_SEC")
    try:
        os.environ.pop("DSM_DEADLINE_SEC", None)
        assert bt._run_deadline().remaining() == float("inf")
        os.environ["DSM_DEADLINE_SEC"] = "0"
        assert bt._run_deadline().remaining() == float("inf")
        os.environ["DSM_DEADLINE_SEC"] = "600"
        assert 0 < bt._run_deadline().remaining() <= 600
    finally:
        if old is None:
            os.environ.pop("DSM_DEADLINE_SEC", None)
        else:
            os.environ["DSM_DEADLINE_SEC"] = old
    print("  [PASS] 预算耗尽时不再发起抓取（只试 1 次，不重试）；未设置预算时不限制")


def main() -> int:
    print("backtest.py 定价/收益/净值口径自检（离线合成数据）")
    print("=" * 62)
    test_entry_is_next_day_open_not_signal_close()
    test_no_silent_fallback_to_earlier_bar()
    test_cost_is_multiplicative_and_reproducible()
    test_price_decimals_are_stored_unrounded_ratio()
    test_panel_gap_uses_real_market_calendar()
    test_non_trading_panel_day_is_counted_not_priced()
    test_required_last_date_and_dirty_days()
    test_cache_freshness_rules()
    test_cache_roundtrip_matches_writer_format()
    test_drop_counters_and_suspension_holding_days()
    test_bucket_nav_is_not_overlapping()
    test_summary_has_drawdown_for_all_horizons()
    test_eval_expr_alias_columns()
    test_deadline_gate_stops_fetching_offline()
    print("=" * 62)
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
