# -*- coding: utf-8 -*-
"""交易日历结构自检（纯 stdlib、离线，`python tests/test_calendar.py`）。

不需要权威数据就能抓住录入错误：填到周末、A股与港股额外休市重复、数量离谱、
以及几个已知日期的行为。这样即便将来补 2027 年，也不会因为手滑而静默错判。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trading_calendar as tc  # noqa: E402


def test_entries_are_weekdays() -> None:
    """表里只能有工作日的休市日 —— 周末由 weekday() 判断，填进来会掩盖录入错误。"""
    for label, table in (("A股", tc.A_SHARE_HOLIDAYS), ("港股额外", tc.HK_EXTRA_HOLIDAYS)):
        for year, days in table.items():
            for text in days:
                day = datetime.date.fromisoformat(text)
                assert day.weekday() < 5, f"{label} {text} 是周末，不应出现在表里"
    print("  [PASS] 休市日全部落在工作日（周末不入表）")


def test_no_overlap_between_tables() -> None:
    """港股额外休市不能与 A 股休市重复，否则口径会含混。"""
    for year in set(tc.A_SHARE_HOLIDAYS) | set(tc.HK_EXTRA_HOLIDAYS):
        a = set(tc.A_SHARE_HOLIDAYS.get(year, {}))
        hk = set(tc.HK_EXTRA_HOLIDAYS.get(year, {}))
        assert not (a & hk), f"{year} 两张表重复：{sorted(a & hk)}"
    print("  [PASS] A股与港股额外休市表无重叠")


def test_plausible_counts() -> None:
    """每年 A 股工作日休市大致在 10~25 天之间，明显越界说明漏填或误填。"""
    for year, days in tc.A_SHARE_HOLIDAYS.items():
        n = len(days)
        assert 10 <= n <= 25, f"{year} A股工作日休市 {n} 天，明显不合理"
    print("  [PASS] 各年度休市天数落在合理区间")


def test_known_2026_days() -> None:
    """用已核实的 2026 安排做行为锚点。"""
    national_day = datetime.date(2026, 10, 5)   # 周一，国庆假期
    assert not tc.is_trading_day(national_day), "2026-10-05 是国庆休市日"
    assert tc.reason(national_day) == "国庆节"

    reopen = datetime.date(2026, 10, 8)          # 周四，国庆后首个交易日
    assert tc.is_trading_day(reopen), "2026-10-08 应开市"

    chung_yeung = datetime.date(2026, 10, 19)    # 周一，香港重阳节
    assert tc.is_trading_day(chung_yeung, "A"), "重阳节 A 股照常开市"
    assert not tc.is_trading_day(chung_yeung, "HK"), "重阳节港股通休市"

    saturday = datetime.date(2026, 10, 10)
    assert not tc.is_trading_day(saturday), "周六不该开市"
    assert tc.reason(saturday) == "周末"
    print("  [PASS] 2026 已知日期行为正确（国庆/节后开市/港股重阳/周末）")


def test_check_exit_codes() -> None:
    """check 的三种退出码：跳过 / 可执行 / 年份未覆盖。"""
    code, _ = tc.check(datetime.date(2026, 10, 5), 15, "A", at_hour=23)
    assert code == tc.EXIT_SKIP, "节假日应跳过"

    code, _ = tc.check(datetime.date(2026, 10, 8), 15, "A", at_hour=16)
    assert code == tc.EXIT_PROCEED, "交易日且已过 15 点应放行"

    code, _ = tc.check(datetime.date(2026, 10, 8), 15, "A", at_hour=10)
    assert code == tc.EXIT_SKIP, "交易日但未到 15 点应跳过"

    code, _ = tc.check(datetime.date(2027, 1, 4), 15, "A", at_hour=16)
    assert code == tc.EXIT_UNKNOWN_YEAR, "未覆盖年份应 fail-open 并告警"
    assert tc.is_trading_day(datetime.date(2027, 1, 4)), "fail-open 视为交易日"
    print("  [PASS] check 退出码：跳过 / 放行 / 未覆盖年份 fail-open")


def test_resolve_env_override() -> None:
    """DSM_CALENDAR_DATE 能覆盖「今天」，用于在非假日验证假日分支。"""
    backend = tc.os.environ.get("DSM_CALENDAR_DATE")
    try:
        tc.os.environ["DSM_CALENDAR_DATE"] = "2026-10-05"
        assert tc.resolve() == datetime.date(2026, 10, 5)
        assert tc.resolve("2026-03-02") == datetime.date(2026, 3, 2)
    finally:
        if backend is None:
            tc.os.environ.pop("DSM_CALENDAR_DATE", None)
        else:
            tc.os.environ["DSM_CALENDAR_DATE"] = backend
    print("  [PASS] DSM_CALENDAR_DATE / 显式参数可覆盖「今天」")


def main() -> int:
    print("交易日历结构自检")
    print("=" * 58)
    test_entries_are_weekdays()
    test_no_overlap_between_tables()
    test_plausible_counts()
    test_known_2026_days()
    test_check_exit_codes()
    test_resolve_env_override()
    print("=" * 58)
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())