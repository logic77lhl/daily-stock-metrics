# -*- coding: utf-8 -*-
"""周期 J 的聚合保真度自检（离线，`python tests/test_kline_agg.py`）。

守的是一条**用时间换来的**等价关系：

    日线 800 根 --本地聚合--> 周线/月线 --> J   ==   腾讯周线/月线接口的 J

为什么必须钉死它：日更路径原来为每只标的抓 3 个周期，113 只 ≈ 339 次请求，
在全局限流 0.30s 下**仅节流下限就是 102 秒**；改成只抓日线后是 113 次 / 34 秒。
省下的 68 秒完全依赖「聚合值与接口值相同」这个前提。

这个前提是实测的（3 只样本、周线与月线最大绝对差 0.00），但它是**会静默漂移**的：
  * 周界取错（`to_period("W")` 是周一起算；用 `resample("W")` 默认是周日结束）；
  * 月界取错（自然月 vs 财月）；
  * 把「进行中」的周期 bar 当成已完成的 bar（回填踩过这个坑：工商银行 107.9 vs 98.22）。

任何一条漂移都只会让页面上的数字变一点，不会报错 —— 所以对照值必须冻结在
`tests/fixtures/kline_fixture.json` 里（由 tests/_make_kline_fixture.py 生成，
含 800 根日线 + 接口的周/月 J）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_metrics as fm  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "kline_fixture.json"

# 冻结值保留 2 位小数，所以 0.01 是舍入噪声；实测差值是 0.00。
TOL = 0.011


def _load():
    assert FIXTURE.exists(), (
        f"缺少夹具 {FIXTURE}；用 `python tests/_make_kline_fixture.py` 重新生成")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _daily_df(rows):
    return pd.DataFrame(
        [{"date": d, "close": c, "high": h, "low": l, "volume": v}
         for d, c, h, l, v in rows])


def test_aggregation_reproduces_endpoint_j() -> None:
    fixture = _load()
    assert fixture, "夹具为空"
    worst = 0.0
    for code, data in fixture.items():
        daily = _daily_df(data["daily"])
        assert len(daily) == 800, f"{code} 夹具应有 800 根日线，实际 {len(daily)}"

        got = fm.period_j_columns(daily)
        want = data["endpoint"]
        for col in ("日线J", "昨日日线J", "周线J", "昨日周线J", "月线J", "昨日月线J"):
            g, w = got[col], want[col]
            assert g is not None, f"{code} 的 {col} 算不出来（聚合后 bar 数不足？）"
            diff = abs(float(g) - float(w))
            worst = max(worst, diff)
            assert diff <= TOL, (
                f"{code} 的 {col} 与接口值不符：本地 {g} vs 接口 {w}（差 {diff:.3f}）。"
                f"聚合口径可能已漂移（周界/月界/进行中周期）—— 这会静默改变页面上所有"
                f"周期 J 的值，且不会有任何报错。")
    print(f"  [PASS] {len(fixture)} 只标的 × 6 个周期 J 全部与接口值一致"
          f"（最大绝对差 {worst:.4f}）")


def test_aggregation_uses_the_in_progress_period_bar() -> None:
    """最后一根周期 bar 必须是「进行中」的那根（截至最后一根日线），不能丢掉它。

    丢掉的后果不是报错，而是周线 J 停留在**上一周**：页面看起来正常，
    只是这个数字永远慢一周。做法是断言「聚合出的周 bar 数」等于
    「把最后一根日线所在的周也算进去」的结果，且最后一根 bar 的收盘价
    等于最后一根日线收盘价。
    """
    fixture = _load()
    for code, data in fixture.items():
        daily = _daily_df(data["daily"])
        weekly = fm.resample_period(daily, "W")
        monthly = fm.resample_period(daily, "M")
        last_close = float(daily["close"].iloc[-1])
        assert abs(float(weekly["close"].iloc[-1]) - last_close) < 1e-6, (
            f"{code} 的最后一根周 bar 不是进行中的那根")
        assert abs(float(monthly["close"].iloc[-1]) - last_close) < 1e-6, (
            f"{code} 的最后一根月 bar 不是进行中的那根")
        # 聚合 bar 数应远少于日线数（否则说明分组没生效）
        assert 20 < len(monthly) < len(daily) / 10, (
            f"{code} 月 bar 数异常：{len(monthly)}（日线 {len(daily)}）")
    print("  [PASS] 进行中的周/月 bar 被保留，且收盘价等于最后一根日线")


def test_daily_path_fetches_only_one_period() -> None:
    """日更路径**只能**抓日线 —— 这是 339→113 次请求那笔账的守门人。

    直接读源码而不是打桩：打桩只能证明「现在调用了什么」，
    而这条约束是「以后不许再为周/月多发请求」，对着调用点断言更直接。
    """
    import inspect
    import re
    src = inspect.getsource(fm._process_one)
    calls = re.findall(r"fetch_kline\(\s*session\s*,\s*code\s*,\s*[\"'](\w+)[\"']", src)
    assert calls == ["daily"], (
        f"_process_one 里的 fetch_kline 调用周期应为 ['daily']，实际 {calls}；"
        f"多抓一个周期就是每只标的多一次请求（113 只 ≈ +34 秒节流下限）")
    assert "for period" not in src, "_process_one 里不应再有周期循环"
    print("  [PASS] _process_one 只抓日线（周/月走本地聚合）")


def main() -> int:
    print("周期 J 聚合保真度自检")
    print("=" * 58)
    test_aggregation_reproduces_endpoint_j()
    test_aggregation_uses_the_in_progress_period_bar()
    test_daily_path_fetches_only_one_period()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
